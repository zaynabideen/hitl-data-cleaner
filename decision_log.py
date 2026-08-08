"""Decision log - the permanent record of what was changed and who said so.

Two outputs:
  * decision_log.json - machine readable, and the seed of a replay recipe
  * provenance report (markdown) - human readable, ships with the clean data
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = "1.0"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Decision:
    seq: int
    timestamp: str
    rule: str
    column: str | None
    proposal_id: str
    proposal_title: str
    decision: str            # approved | modified | skipped
    strategy_id: str | None
    strategy_label: str | None
    default_strategy: str
    rows_flagged: int
    rows_changed: int
    rows_before: int
    rows_after: int
    approved_by: str
    note: str = ""


@dataclass
class DecisionLog:
    source_file: str
    source_sha256: str
    source_rows: int
    source_columns: list[str]
    approved_by: str
    started_at: str = field(default_factory=_now)
    decisions: list[Decision] = field(default_factory=list)

    # ---- recording -------------------------------------------------------

    def record(self, proposal, decision: str, strategy_id: str | None,
               rows_changed: int, rows_before: int, rows_after: int,
               note: str = "") -> Decision:
        label = None
        if strategy_id:
            match = next((s for s in proposal.strategies
                          if s.id == strategy_id), None)
            label = match.label if match else strategy_id

        entry = Decision(
            seq=len(self.decisions) + 1,
            timestamp=_now(),
            rule=proposal.rule,
            column=proposal.column,
            proposal_id=proposal.id,
            proposal_title=proposal.title,
            decision=decision,
            strategy_id=strategy_id,
            strategy_label=label,
            default_strategy=proposal.default_strategy,
            rows_flagged=proposal.rows_affected,
            rows_changed=rows_changed,
            rows_before=rows_before,
            rows_after=rows_after,
            approved_by=self.approved_by,
            note=note,
        )
        self.decisions.append(entry)
        return entry

    # ---- output ----------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "source": {
                "file": self.source_file,
                "sha256": self.source_sha256,
                "rows": self.source_rows,
                "columns": self.source_columns,
            },
            "session": {
                "approved_by": self.approved_by,
                "started_at": self.started_at,
                "completed_at": _now(),
                "decisions_total": len(self.decisions),
                "approved": sum(1 for d in self.decisions
                                if d.decision == "approved"),
                "modified": sum(1 for d in self.decisions
                                if d.decision == "modified"),
                "skipped": sum(1 for d in self.decisions
                               if d.decision == "skipped"),
            },
            "decisions": [asdict(d) for d in self.decisions],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def to_recipe(self) -> dict[str, Any]:
        """The replayable subset: rule + column + chosen strategy, in order.

        Feeding this back in next month is what makes run 2 automatic.
        Anything the recipe does not cover must stop and ask.
        """
        return {
            "schema_version": SCHEMA_VERSION,
            "created_at": _now(),
            "created_from": self.source_file,
            "created_by": self.approved_by,
            "source_columns": self.source_columns,
            # Only real, replayable decisions. "flagged" entries (schema
            # drift notes) describe the file, not an action, so they must
            # not become steps.
            "steps": [
                {"rule": d.rule, "column": d.column,
                 "proposal_id": d.proposal_id, "strategy": d.strategy_id}
                for d in self.decisions
                if d.decision in ("approved", "modified") and d.strategy_id
            ],
            # Skips are decisions too: they are how the next run knows an
            # issue was seen and deliberately left alone.
            "skipped": [
                {"rule": d.rule, "column": d.column,
                 "proposal_id": d.proposal_id, "note": d.note}
                for d in self.decisions if d.decision == "skipped"
            ],
        }

    def to_markdown(self) -> str:
        s = self.to_dict()["session"]
        lines = [
            "# Data provenance report",
            "",
            f"**Source file:** `{self.source_file}`  ",
            f"**SHA-256:** `{self.source_sha256}`  ",
            f"**Rows in:** {self.source_rows}  ",
            f"**Reviewed by:** {self.approved_by}  ",
            f"**Session:** {self.started_at} to {_now()}  ",
            f"**Decisions:** {s['approved']} approved, "
            f"{s['modified']} modified, {s['skipped']} skipped",
            "",
            "Every change below was proposed by the tool and explicitly "
            "approved by a person. Nothing was applied automatically.",
            "",
            "| # | Rule | Column | Decision | Strategy | Rows changed | Rows after |",
            "|---|------|--------|----------|----------|--------------|------------|",
        ]
        for d in self.decisions:
            lines.append(
                f"| {d.seq} | {d.rule} | {d.column or '(all)'} | "
                f"{d.decision} | {d.strategy_label or '-'} | "
                f"{d.rows_changed} | {d.rows_after} |")

        skipped = [d for d in self.decisions if d.decision == "skipped"]
        if skipped:
            lines += ["", "## Known issues left in the data", ""]
            for d in skipped:
                lines.append(f"- **{d.proposal_title}** - skipped, "
                             f"{d.rows_flagged} rows still affected.")

        notes = [d for d in self.decisions if d.note]
        if notes:
            lines += ["", "## Reviewer notes", ""]
            for d in notes:
                lines.append(f"- Step {d.seq} ({d.rule}): {d.note}")
        return "\n".join(lines) + "\n"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
