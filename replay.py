"""Replay + drift detection.

Last month you approved a set of decisions. Those became a recipe.
This month's file runs against that recipe automatically - but the run
stops and asks whenever it meets something the recipe does not cover.

That "stop and ask" is the whole point. A recipe that silently applies
itself to data it was never approved for is exactly the failure mode this
project exists to avoid.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

import cleaner
from decision_log import DecisionLog, sha256_bytes

# Drift kinds, in the order a reviewer should care about them.
DRIFT_ORDER = ["missing_column", "new_column", "new_issue",
               "invalid_strategy", "step_not_triggered"]

DRIFT_LABEL = {
    "missing_column": "Column disappeared",
    "new_column": "New column appeared",
    "new_issue": "Issue the recipe has never seen",
    "invalid_strategy": "Recorded strategy no longer valid",
    "step_not_triggered": "Recipe step found nothing to do",
}


@dataclass
class Drift:
    kind: str
    column: str | None
    rule: str | None
    message: str
    blocking: bool                    # True = needs a human before we proceed
    proposal: cleaner.Proposal | None = None

    @property
    def label(self) -> str:
        return DRIFT_LABEL.get(self.kind, self.kind)


@dataclass
class ReplayPlan:
    """What replay intends to do, before it does any of it."""
    matched: list[tuple[dict, cleaner.Proposal]] = field(default_factory=list)
    drift: list[Drift] = field(default_factory=list)
    recipe: dict[str, Any] = field(default_factory=dict)

    @property
    def blocking_drift(self) -> list[Drift]:
        return [d for d in self.drift if d.blocking]

    @property
    def clean_run(self) -> bool:
        return not self.blocking_drift

    def summary(self) -> str:
        if self.clean_run:
            return (f"{len(self.matched)} recipe steps matched, no drift that "
                    "needs a decision. Safe to run unattended.")
        return (f"{len(self.matched)} recipe steps matched, but "
                f"{len(self.blocking_drift)} thing(s) need your decision "
                "before this file can be trusted.")


# --------------------------------------------------------------------------
# planning
# --------------------------------------------------------------------------

def _step_key(step: dict) -> tuple[str, str | None]:
    return (step["rule"], step.get("column"))


def plan(df: pd.DataFrame, recipe: dict) -> ReplayPlan:
    """Compare this file against the recipe. Applies nothing."""
    p = ReplayPlan(recipe=recipe)
    proposals = cleaner.detect(df)
    by_key: dict[tuple[str, str | None], cleaner.Proposal] = {
        (pr.rule, pr.column): pr for pr in proposals
    }

    # 1. schema drift - columns that came or went
    old_cols = list(recipe.get("source_columns") or [])
    new_cols = list(df.columns)
    for c in old_cols:
        if c not in new_cols:
            p.drift.append(Drift(
                "missing_column", c, None,
                f"`{c}` was in the file the recipe was built from but is not "
                "in this one. Any recipe step for it cannot run.",
                blocking=True))
    for c in new_cols:
        if old_cols and c not in old_cols:
            p.drift.append(Drift(
                "new_column", c, None,
                f"`{c}` is new since the recipe was approved. Nothing has "
                "ever been approved for it, so it is being left untouched.",
                blocking=True))

    # 2. recipe steps -> matching proposals
    covered: set[tuple[str, str | None]] = set()
    for step in recipe.get("steps", []):
        key = _step_key(step)
        covered.add(key)
        pr = by_key.get(key)
        if pr is None:
            p.drift.append(Drift(
                "step_not_triggered", step.get("column"), step["rule"],
                f"Recipe expects a `{step['rule']}` step on "
                f"`{step.get('column') or '(all columns)'}` but this file has "
                "no such issue. Nothing to do - most likely the upstream "
                "system was fixed.",
                blocking=False))
            continue
        valid = {s.id for s in pr.strategies}
        if step["strategy"] not in valid:
            p.drift.append(Drift(
                "invalid_strategy", pr.column, pr.rule,
                f"Recipe says `{step['strategy']}` for {pr.rule} on "
                f"`{pr.column}`, but that strategy is not offered for this "
                "file's version of the issue.",
                blocking=True, proposal=pr))
            continue
        p.matched.append((step, pr))

    # 3. issues in this file the recipe has never seen
    previously_skipped = {(s["rule"], s.get("column"))
                          for s in recipe.get("skipped", [])}
    for key, pr in by_key.items():
        if key in covered:
            continue
        if key in previously_skipped:
            p.drift.append(Drift(
                "step_not_triggered", pr.column, pr.rule,
                f"{pr.title} - you deliberately skipped this last time, so "
                "it is being left alone again.",
                blocking=False, proposal=pr))
            continue
        p.drift.append(Drift(
            "new_issue", pr.column, pr.rule,
            f"{pr.title}. The recipe has no approved decision for this. "
            "Replay will not guess - it needs your call.",
            blocking=True, proposal=pr))

    p.drift.sort(key=lambda d: DRIFT_ORDER.index(d.kind))
    return p


# --------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------

def run(df: pd.DataFrame, recipe: dict, *, source_file: str,
        source_bytes: bytes = b"", approved_by: str = "recipe-replay",
        on_drift: str = "stop", decisions: dict[str, str] | None = None
        ) -> tuple[pd.DataFrame, DecisionLog, ReplayPlan]:
    """Run a recipe against a new file.

    on_drift:
      "stop"      - refuse to run if anything blocking is unresolved (default)
      "partial"   - apply the matched steps, leave drift untouched and logged
    decisions: proposal_id -> strategy id, for drift the user just resolved.
               Pass "__skip__" to record a skip.
    """
    decisions = decisions or {}
    p = plan(df, recipe)

    unresolved = [d for d in p.blocking_drift
                  if not (d.proposal and d.proposal.id in decisions)]
    if unresolved and on_drift == "stop":
        raise DriftStop(p, unresolved)

    log = DecisionLog(
        source_file=source_file,
        source_sha256=sha256_bytes(source_bytes) if source_bytes else "n/a",
        source_rows=len(df),
        source_columns=list(df.columns),
        approved_by=approved_by,
    )

    work = df.copy()
    for step, pr in p.matched:
        before = len(work)
        work, changed = cleaner.apply(work, pr, step["strategy"])
        log.record(pr, "approved", step["strategy"], changed, before,
                   len(work), note=f"replayed from recipe "
                                   f"({recipe.get('created_from', 'unknown')})")

    # anything the human just resolved during this run
    for d in p.drift:
        if not d.proposal:
            continue
        choice = decisions.get(d.proposal.id)
        if choice is None:
            # Non-blocking drift with a proposal means "you skipped this
            # last time". Re-record the skip so the memory of that decision
            # survives into the next recipe instead of resurfacing as new.
            note = (f"unresolved drift: {d.kind}" if d.blocking
                    else "previously skipped, left alone again")
            log.record(d.proposal, "skipped", None, 0, len(work), len(work),
                       note=note)
            continue
        if choice == "__skip__":
            log.record(d.proposal, "skipped", None, 0, len(work), len(work),
                       note=f"drift ({d.kind}) reviewed, left as-is")
            continue
        before = len(work)
        work, changed = cleaner.apply(work, d.proposal, choice)
        log.record(d.proposal, "modified", choice, changed, before, len(work),
                   note=f"new decision for drift: {d.kind}")

    # non-proposal drift still belongs in the provenance record
    for d in p.drift:
        if d.proposal is None:
            log.decisions.append(_schema_note(d, len(log.decisions) + 1,
                                              approved_by, len(work)))

    return work, log, p


def _schema_note(d: Drift, seq: int, who: str, rows: int):
    from decision_log import Decision, _now
    return Decision(
        seq=seq, timestamp=_now(), rule=f"drift:{d.kind}", column=d.column,
        proposal_id=f"drift::{d.kind}::{d.column}",
        proposal_title=d.label, decision="flagged", strategy_id=None,
        strategy_label=None, default_strategy="-", rows_flagged=0,
        rows_changed=0, rows_before=rows, rows_after=rows,
        approved_by=who, note=d.message)


class DriftStop(Exception):
    """Raised when replay refuses to proceed without a human decision."""

    def __init__(self, plan: ReplayPlan, unresolved: list[Drift]):
        self.plan = plan
        self.unresolved = unresolved
        super().__init__(
            f"Replay stopped: {len(unresolved)} issue(s) the recipe does not "
            "cover. " + "; ".join(d.message for d in unresolved[:3]))


# --------------------------------------------------------------------------
# io
# --------------------------------------------------------------------------

def load_recipe(path_or_str) -> dict:
    if hasattr(path_or_str, "read"):
        return json.load(path_or_str)
    if isinstance(path_or_str, (bytes, bytearray)):
        return json.loads(path_or_str.decode())
    if isinstance(path_or_str, str) and path_or_str.lstrip().startswith("{"):
        return json.loads(path_or_str)
    with open(path_or_str) as f:
        return json.load(f)


def save_recipe(recipe: dict, path: str) -> None:
    with open(path, "w") as f:
        json.dump(recipe, f, indent=2)
