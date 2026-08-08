# HITL Data Cleaner — Project Report

**Author:** Zain
**Date:** August 2026
**Repo:** `hitl-data-cleaner`
**Status:** v1 complete — 43 automated checks passing end to end

---

## 1. The problem

Automated data cleaning fails *quietly*.

A tool that reads `03/04/2025` as March 4th when the source meant 3 April does
not throw an error. It returns a clean-looking dataset that is wrong, and the
error surfaces three dashboards later — usually in front of someone senior.

Every mainstream tool works the same way: **clean first, inspect afterwards.**
Power Query applies a transform and shows you the result. OpenRefine clusters
values and applies the merge. Alteryx runs the workflow. In each case the
inspection step is optional, happens after the damage, and leaves no record of
who decided what.

The gap isn't a better guesser. It's making the guess **visible and refusable
before it is applied**, and keeping a record of the answer.

## 2. What was built

A local Python application with two modes.

**Review mode** — upload a CSV. The tool detects issues and presents them one at
a time in plain language with the evidence attached:

> **`order_date` mixes 4 date formats**
> 148 values are ISO (YYYY-MM-DD), 40 are slash-style. Of the slash ones, 22 can
> only be day-first (day > 12), 11 can only be month-first, and 7 could be read
> either way. Parsed with the wrong assumption those ambiguous rows land on the
> wrong date silently, which is why this needs your call.

Each proposal offers several strategies with the trade-off spelled out. Choosing
the recommended one logs `approved`; choosing another logs `modified`; declining
logs `skipped`. Nothing is written to the data until a button is pressed.

**Replay mode** — upload the recipe produced by a previous review plus a new
file. Steps the recipe already covers run automatically. Anything it has never
seen stops the run.

### Architecture

| Module | Responsibility |
|--------|---------------|
| `cleaner.py` | Detection and application. Pure functions, no UI dependency — the engine is testable and reusable on its own. |
| `decision_log.py` | Decision records, JSON log, markdown provenance report, recipe extraction. |
| `replay.py` | Recipe planning, drift classification, replay execution. |
| `sql_export.py` | Type inference and SQLite output for Power BI. |
| `app.py` | Streamlit UI for both modes. |
| `test_pipeline.py` | 43 headless checks covering all ten stages. |

Detection and application are separated deliberately. `detect()` never mutates
its input — verified by test — so the proposal screen can never have a side
effect. Only `apply()` changes data, and it is only ever called with a strategy
a human selected.

### Rules in v1

Four rules, each with real depth rather than a single fix:

| Rule | Strategies |
|------|-----------|
| Date format mismatch | day-first, month-first, infer per value, standardise to UK, flag only |
| Duplicate rows | exact, normalised, one-per-key keep first/last, flag only |
| Missing values | normalise to null, fill median / zero / mode / `Unknown`, drop rows |
| Casing & whitespace | trim + canonical spelling, trim only, title / lower / upper, flag only |

The scope is one domain — e-commerce orders. Generic cleaning across arbitrary
datasets is the decision that kills projects like this, so it was ruled out at
the start.

## 3. The three differentiators

### 3.1 Approve before apply

The inversion of the normal order. Other tools ask you to *undo*; this one asks
you to *authorise*. That single change is what makes the tool usable on data
where being wrong is expensive — finance, compliance, anything that feeds a
board pack.

It also makes the tool honest about ambiguity. The date rule doesn't pick a
convention and hope; it counts the evidence (`22 rows can only be day-first,
11 can only be month-first, 7 are genuinely ambiguous`) and hands the judgement
to the person who knows where the file came from.

### 3.2 A decision log an auditor could read

Every decision records the rule, column, chosen strategy, the person who
approved it, the timestamp, rows changed, and rows before/after — alongside the
SHA-256 of the source file, so the log can be tied back to the exact input.

The provenance report also has a section most tools have no equivalent for:
**Known issues left in the data.** What was deliberately *not* fixed is as
visible as what was. A dataset arriving with "these 7 email addresses are still
missing and here's who decided that" is a fundamentally different artefact from
one that arrives silently clean.

### 3.3 Replay with drift detection

Approved decisions become `recipe.json`. Next month's file runs against it
automatically — but the run **stops** rather than guessing when it meets:

| Drift | Meaning | Blocking |
|-------|---------|----------|
| `new_column` | A column nothing has ever been approved for | Yes |
| `missing_column` | A column the recipe depends on has gone | Yes |
| `new_issue` | A kind of problem this recipe has never been shown | Yes |
| `invalid_strategy` | The recorded strategy no longer applies | Yes |
| `step_not_triggered` | The issue is gone, or you skipped it before | No |

`replay.run(..., on_drift="stop")` raises `DriftStop`. It does not proceed
partially by default and it does not extrapolate.

This is the piece that turns a one-off cleaning session into a repeatable
monthly process without turning it into an unsupervised one. A saved recipe that
silently applies itself to data it was never approved for would reintroduce
exactly the failure the project exists to prevent.

One subtlety worth noting: **skips propagate**. If you deliberately left the
missing emails alone in month 1, month 2 does not re-raise it as a new decision.
The recipe remembers that "leave it alone" was itself a decision.

## 4. How it compares

| | This tool | Power Query | OpenRefine | Alteryx | Parabola |
|---|---|---|---|---|---|
| Proposes before applying | **Yes** | No | No | No | No |
| Plain-language explanation of *why* | **Yes** | No | No | No | Partial |
| Permanent decision log with approver | **Yes** | No | No | Partial (workflow file) | No |
| Records what was deliberately *not* fixed | **Yes** | No | No | No | No |
| Reusable recipe | Yes | Yes | Yes | Yes | Yes |
| **Stops when the recipe doesn't fit** | **Yes** | No | No | No | No |
| Runs fully locally | **Yes** | Yes | Yes | Yes | No (cloud) |
| Cost | Free | Bundled with Excel/PBI | Free | ~£4k+/user/yr | Subscription |

The columns that matter are rows 1, 4 and 6. Every tool in the market has
recipes; none of them refuse to run one. Every tool has an undo; none of them
have an approval record.

**Where the competitors are genuinely better:** Alteryx and Power Query handle
far more data sources, larger volumes, joins, and complex reshaping. This tool
does none of that. It does one narrow thing — supervised cleaning with an audit
trail — and the honest positioning is a complement to those tools, not a
replacement.

## 5. Who it's for

- **Regulated or audited reporting** — finance, healthcare, compliance — where
  "the data was cleaned" is not an acceptable answer and "here is who approved
  each change, when, and what was left alone" is.
- **Analysts inheriting someone else's pipeline**, where the decisions embedded
  in a transform are invisible and undocumented.
- **Recurring monthly reporting**, where the file mostly stays the same but
  occasionally doesn't — and no one notices until the numbers move.

## 6. Privacy position

Everything runs locally. No upload, no API call, no telemetry. For teams that
cannot send customer data to a cloud service, this is a capability the
subscription tools structurally cannot match — and it costs nothing to provide,
because it's just a consequence of the architecture.

All sample data is fabricated: invented names, and every address on the reserved
`.invalid` domain. No real data was used at any point in development or testing.

## 7. Testing

`test_pipeline.py` runs the whole system headlessly — no Streamlit required —
in ten stages, 43 assertions, exiting non-zero on any failure:

1. Every injected issue type is detected
2. `detect()` does not mutate its input
3. The approval loop applies only approved changes
4. The output is actually clean (dates ISO, no duplicates, one spelling per
   value, no stray whitespace)
5. Skipped issues remain unfixed — a skip must never silently clean
6. Log, provenance report and recipe all serialise correctly
7. Replay plans before applying, and detects new columns and unseen issues
8. Replay raises `DriftStop` rather than guessing
9. Replay runs correctly once drift is resolved, and re-running is clean
10. SQL export produces typed columns and carries provenance with the data

Two real bugs were found and fixed by these tests rather than by inspection:
skip decisions were being lost across replay generations, and schema-drift notes
were leaking into the recipe as executable steps.

## 8. Honest limitations

- **Synonyms are not merged.** `uk`, `UK ` and `UK` collapse to one value; `UK`
  and `United Kingdom` do not. That needs a vocabulary the user supplies — a
  separate rule, deliberately not in v1.
- **Canonical form is the most frequent form.** If `PAKISTAN` outnumbers
  `Pakistan`, the shouty one wins.
- **One domain only.** E-commerce orders. HR data is the intended second domain.
- **Single user, single file, in memory.** No accounts, no concurrency, no
  billing, and no attempt at large-file streaming.
- **Power BI is not embedded** — that requires Premium capacity or PPU licences,
  which is not justifiable for a solo project. The tool ships typed data plus
  the decision log to SQLite and the user connects their own Power BI.

## 9. What's next

1. Category/synonym mapping rule with a user-supplied vocabulary
2. Postgres target alongside SQLite
3. HR data as the second domain, to prove the rule layer is genuinely pluggable
4. A 30-second demo GIF — for a portfolio repo this matters more than the README

---

### One-paragraph summary

A local, human-in-the-loop CSV cleaning tool for e-commerce order data. It
detects data quality issues, explains each one in plain language with the
supporting evidence, and applies nothing until a person approves a specific
strategy. Every decision — including the decision to change nothing — is
recorded with the approver, timestamp, affected row count and the source file's
hash, and exported as both a machine-readable log and a human-readable
provenance report. Approved decisions become a reusable recipe that runs the
next month's file automatically but refuses to proceed when it encounters a
column or an issue it has never been approved for. Clean data and its full
provenance are exported to SQLite with proper types for Power BI. Built in
Python with pandas and Streamlit; 43 automated end-to-end checks.
