"""HITL Data Cleaner - approval screen and recipe replay.

Two modes:
  Review    upload a CSV, decide on each proposal, export data + log + recipe
  Replay    upload a recipe + a new CSV; known steps run automatically,
            anything the recipe has not seen stops and asks

Run:  streamlit run app.py
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

import cleaner
import replay as replay_mod
import sql_export
from decision_log import DecisionLog, sha256_bytes

st.set_page_config(page_title="HITL Data Cleaner", page_icon="check",
                   layout="wide")

S = st.session_state
SESSION_KEYS = ("df", "original", "proposals", "idx", "log", "filename",
                "mode_state", "plan", "recipe", "drift_idx", "drift_choices")


def reset():
    for k in SESSION_KEYS:
        S.pop(k, None)


def strategy_picker(p: cleaner.Proposal, key_prefix: str):
    """Radio over a proposal's strategies. Returns (strategy, is_default)."""
    labels = [s.label for s in p.strategies]
    default_i = next((i for i, s in enumerate(p.strategies)
                      if s.id == p.default_strategy), 0)
    i = st.radio("Strategy", range(len(labels)),
                 format_func=lambda i: labels[i], index=default_i,
                 label_visibility="collapsed", key=f"{key_prefix}_{p.id}")
    st.caption(p.strategies[i].detail)
    if i == default_i:
        st.caption(":green[This is the recommended option.]")
    else:
        st.caption(":orange[Differs from the recommendation - "
                   "this will be logged as *modified*.]")
    return p.strategies[i], i == default_i


def export_tabs(df_out, original_rows, log, filename, extra_note=""):
    tabs = st.tabs(["Cleaned data", "Decision log", "Provenance report",
                    "Replay recipe", "SQL export"])

    with tabs[0]:
        c1, c2, c3 = st.columns(3)
        c1.metric("Rows in", original_rows)
        c2.metric("Rows out", len(df_out))
        c3.metric("Rows removed", original_rows - len(df_out))
        if extra_note:
            st.caption(extra_note)
        st.dataframe(df_out, use_container_width=True, height=400)
        st.download_button("Download cleaned CSV",
                           df_out.to_csv(index=False).encode(),
                           file_name=f"cleaned_{filename}",
                           mime="text/csv", type="primary")

    with tabs[1]:
        st.dataframe(pd.DataFrame(log.to_dict()["decisions"]),
                     use_container_width=True)
        st.download_button("Download decision_log.json", log.to_json().encode(),
                           file_name="decision_log.json",
                           mime="application/json")

    with tabs[2]:
        st.markdown(log.to_markdown())
        st.download_button("Download provenance_report.md",
                           log.to_markdown().encode(),
                           file_name="provenance_report.md",
                           mime="text/markdown")

    with tabs[3]:
        st.caption("Feed this back in on the Replay tab next month. Anything "
                   "it does not cover will stop and ask you.")
        recipe = log.to_recipe()
        st.json(recipe)
        st.download_button("Download recipe.json",
                           json.dumps(recipe, indent=2).encode(),
                           file_name="recipe.json", mime="application/json")

    with tabs[4]:
        st.caption("Power BI is not embedded - that needs Premium capacity or "
                   "PPU licences. Instead the clean data and its decision log "
                   "go to SQLite and you point Power BI at the file.")
        st.dataframe(sql_export.preview_schema(df_out),
                     use_container_width=True)
        db_name = st.text_input("Database file name", value="cleaned_data.db")
        if st.button("Write SQLite database"):
            info = sql_export.export(df_out, log, db_name)
            st.success(f"Wrote {db_name}: " +
                       ", ".join(f"{t} ({n} rows)"
                                 for t, n in info["tables"].items()))
            with open(db_name, "rb") as f:
                st.download_button("Download .db", f.read(),
                                   file_name=db_name,
                                   mime="application/octet-stream")


# --------------------------------------------------------------------------
# sidebar
# --------------------------------------------------------------------------

with st.sidebar:
    st.title("HITL Data Cleaner")
    st.caption("Propose, then apply. Never the other way round.")

    mode = st.radio("Mode", ["Review a file", "Replay a recipe"],
                    key="mode", on_change=reset)
    reviewer = st.text_input("Reviewer name", value="Zain",
                             help="Recorded against every decision.")

    if "df" in S:
        st.divider()
        st.metric("Rows", len(S.df), delta=len(S.df) - len(S.original))
        if st.button("Start over", use_container_width=True):
            reset()
            st.rerun()

# ==========================================================================
# MODE 1 - review a file
# ==========================================================================

if mode == "Review a file":
    with st.sidebar:
        upload = st.file_uploader("Orders CSV", type=["csv"], key="rev_csv")

    if upload is not None and S.get("filename") != upload.name:
        raw = upload.getvalue()
        df = cleaner.load_csv(upload)
        reset()
        S.filename, S.original, S.df = upload.name, df.copy(), df.copy()
        S.proposals, S.idx = cleaner.detect(df), 0
        S.log = DecisionLog(source_file=upload.name,
                            source_sha256=sha256_bytes(raw),
                            source_rows=len(df), source_columns=list(df.columns),
                            approved_by=reviewer or "unknown")

    if "df" not in S:
        st.title("Upload a CSV to begin")
        st.markdown(
            "This tool **proposes** cleaning steps in plain language and waits "
            "for your decision on each one. It does not clean first and ask "
            "later.\n\n"
            "- Every approved change is written to a decision log\n"
            "- The log doubles as a replay recipe for next month's file\n"
            "- Your file never leaves this machine\n\n"
            "No file handy? Run `python make_sample_data.py`.")
        st.stop()

    proposals, log = S.proposals, S.log
    log.approved_by = reviewer or "unknown"
    st.progress(S.idx / max(len(proposals), 1),
                text=f"Proposal {min(S.idx + 1, len(proposals))} "
                     f"of {len(proposals)}")

    def commit(p, decision, strategy, note):
        before = len(S.df)
        changed = 0
        if strategy is not None:
            S.df, changed = cleaner.apply(S.df, p, strategy)
        log.record(p, decision, strategy, changed, before, len(S.df), note)
        S.idx += 1
        st.rerun()

    if S.idx < len(proposals):
        p = proposals[S.idx]
        st.subheader(p.title)
        st.info(p.body)

        left, right = st.columns([3, 2], gap="large")
        with left:
            st.markdown("**How would you like to handle it?**")
            chosen, is_default = strategy_picker(p, "strategy")
            note = st.text_input("Note (optional)", key=f"note_{p.id}",
                                 placeholder="Why this choice? Goes in the log.")
            c1, c2, c3 = st.columns(3)
            if c1.button("Approve", type="primary", use_container_width=True):
                commit(p, "approved" if is_default else "modified",
                       chosen.id, note)
            if c2.button("Skip", use_container_width=True):
                commit(p, "skipped", None, note or "left as-is")
            if c3.button("Skip all remaining", use_container_width=True):
                for rest in proposals[S.idx:]:
                    log.record(rest, "skipped", None, 0, len(S.df), len(S.df),
                               "bulk skip")
                S.idx = len(proposals)
                st.rerun()
        with right:
            st.markdown(f"**Affected rows: {p.rows_affected}**")
            if not p.preview.empty:
                st.dataframe(p.preview, use_container_width=True, height=280)
            st.caption("Sample of the rows this proposal touches.")
    else:
        st.success("All proposals reviewed.")
        export_tabs(S.df, len(S.original), log, S.filename)
        if st.button("Re-scan the cleaned data"):
            S.proposals, S.idx = cleaner.detect(S.df), 0
            st.rerun()

# ==========================================================================
# MODE 2 - replay a recipe
# ==========================================================================

else:
    with st.sidebar:
        recipe_up = st.file_uploader("recipe.json", type=["json"],
                                     key="rep_recipe")
        upload = st.file_uploader("New month's CSV", type=["csv"],
                                  key="rep_csv")

    if not (recipe_up and upload):
        st.title("Replay an approved recipe")
        st.markdown(
            "Upload the `recipe.json` from a previous review plus this "
            "month's file.\n\n"
            "Steps the recipe already covers are applied automatically. "
            "Anything it has **not** seen - a new column, a new kind of issue, "
            "a strategy that no longer applies - stops the run and asks you.\n\n"
            "A recipe that silently applies itself to data it was never "
            "approved for is the exact failure this tool exists to prevent.\n\n"
            "Try it: `python make_sample_data.py` writes a month-2 file with "
            "deliberate drift.")
        st.stop()

    if S.get("filename") != upload.name or "plan" not in S:
        raw = upload.getvalue()
        df = cleaner.load_csv(upload)
        recipe = replay_mod.load_recipe(recipe_up.getvalue())
        reset()
        S.filename, S.original, S.df = upload.name, df.copy(), df.copy()
        S.recipe, S.raw = recipe, raw
        S.plan = replay_mod.plan(df, recipe)
        S.drift_choices, S.drift_idx = {}, 0

    p_plan: replay_mod.ReplayPlan = S.plan
    blocking = p_plan.blocking_drift

    c1, c2, c3 = st.columns(3)
    c1.metric("Recipe steps matched", len(p_plan.matched))
    c2.metric("Needs your decision", len(blocking))
    c3.metric("Informational", len(p_plan.drift) - len(blocking))

    if p_plan.clean_run:
        st.success(p_plan.summary())
    else:
        st.warning(p_plan.summary())

    with st.expander("What the recipe will apply automatically",
                     expanded=not blocking):
        if p_plan.matched:
            st.dataframe(pd.DataFrame([
                {"rule": s["rule"], "column": s.get("column") or "(all)",
                 "strategy": s["strategy"], "rows now affected": pr.rows_affected}
                for s, pr in p_plan.matched]), use_container_width=True)
        else:
            st.caption("Nothing matched - this file looks nothing like the one "
                       "the recipe was built from.")

    non_blocking = [d for d in p_plan.drift if not d.blocking]
    if non_blocking:
        with st.expander(f"Informational notes ({len(non_blocking)})"):
            for d in non_blocking:
                st.markdown(f"- **{d.label}** - {d.message}")

    pending = [d for d in blocking if d.proposal
               and d.proposal.id not in S.drift_choices]
    schema_only = [d for d in blocking if d.proposal is None]

    if schema_only:
        st.error("**Schema drift - cannot be auto-resolved**")
        for d in schema_only:
            st.markdown(f"- **{d.label}: `{d.column}`** - {d.message}")
        st.caption("These are recorded in the provenance report. Fix the "
                   "upstream export, or run a fresh review to approve "
                   "decisions for the new shape.")

    if pending:
        d = pending[0]
        st.divider()
        st.subheader(f"{d.label}: {d.proposal.title}")
        st.warning(d.message)
        st.info(d.proposal.body)

        left, right = st.columns([3, 2], gap="large")
        with left:
            chosen, _ = strategy_picker(d.proposal, "drift")
            b1, b2 = st.columns(2)
            if b1.button("Approve and add to recipe", type="primary",
                         use_container_width=True):
                S.drift_choices[d.proposal.id] = chosen.id
                st.rerun()
            if b2.button("Leave this one alone", use_container_width=True):
                S.drift_choices[d.proposal.id] = "__skip__"
                st.rerun()
        with right:
            if not d.proposal.preview.empty:
                st.dataframe(d.proposal.preview, use_container_width=True,
                             height=280)
        st.stop()

    st.divider()
    if st.button("Run the recipe", type="primary"):
        S.result = replay_mod.run(
            S.df, S.recipe, source_file=S.filename, source_bytes=S.raw,
            approved_by=reviewer or "unknown", on_drift="partial",
            decisions=S.drift_choices)
        st.rerun()

    if "result" in S:
        out, log, _ = S.result
        st.success("Replay complete.")
        note = ("Steps from the recipe were applied without asking. "
                "Anything new was either decided by you above or left "
                "untouched and flagged in the log.")
        export_tabs(out, len(S.original), log, S.filename, extra_note=note)
