# HITL Data Cleaner

A data cleaning tool that **proposes** changes in plain language and waits for a
human decision on each one. Every approved change is permanently logged, and the
log becomes a reusable recipe — which stops and asks the moment next month's file
contains something it has never seen.

Other tools clean first and let you inspect afterwards. This one doesn't touch
your data until you say so.

```
CSV upload → detect → proposal in plain English → Approve / Modify / Skip
          → apply only what was approved
          → cleaned CSV + decision log + provenance report + recipe + SQLite
```

## Why this exists

Automated cleaning fails quietly. A tool that parses `03/04/2025` as March 4th
when it meant 3 April doesn't error — it hands you a clean-looking dataset that
is wrong, and you find out three dashboards later. The fix isn't a better
guesser. It's making the guess visible and asking before it's applied.

## The three things that make it different

**1. Approve before apply.** Every issue is described in plain language with the
evidence behind it, and nothing happens until you choose:

> **`order_date` mixes 4 date formats**
> 148 values are ISO (YYYY-MM-DD), 40 are slash-style. Of the slash ones, 22 can
> only be day-first (day > 12), 11 can only be month-first, and 7 could be read
> either way. Parsed with the wrong assumption those ambiguous rows land on the
> wrong date silently, which is why this needs your call.

Five strategies are offered, the recommended one is preselected, and picking a
different one is recorded as `modified` rather than `approved`.

**2. A decision log you can hand to an auditor.** Every decision records the
rule, column, chosen strategy, who approved it, when, rows changed, and rows
before/after — plus the SHA-256 of the source file. Skipped issues get their own
**Known issues left in the data** section, so what *wasn't* fixed is as visible
as what was.

**3. Replay with drift detection.** Approved decisions become `recipe.json`.
Next month's file runs against it automatically — but the run **stops** on:

| Drift | Meaning |
|-------|---------|
| `new_column` | A column nothing has ever been approved for |
| `missing_column` | A column the recipe depends on has gone |
| `new_issue` | A kind of problem this recipe has never been shown |
| `invalid_strategy` | The recorded strategy no longer applies |
| `step_not_triggered` | Informational — the issue is gone, or you skipped it before |

`replay.run(..., on_drift="stop")` raises `DriftStop` rather than guessing. A
recipe that silently applies itself to data it was never approved for is the
exact failure this project exists to prevent.

## Run it

```bash
pip install -r requirements.txt
python make_sample_data.py     # writes month-1 and month-2 sample files
streamlit run app.py
```

**Review mode** — upload `sample_orders_messy.csv`, work through the proposals,
download the cleaned CSV, decision log, provenance report and recipe.

**Replay mode** — upload that `recipe.json` plus `sample_orders_month2.csv`. The
month-2 file has deliberate drift: a new `discount_code` column and a new casing
problem in `product`. Replay applies the ten steps it knows and stops on the
rest.

## Rules in v1 (e-commerce orders)

| Rule | Strategies |
|------|-----------|
| Date format mismatch | day-first, month-first, infer per value, standardise to UK, flag only |
| Duplicate rows | exact, normalised, one-per-key keep first/last, flag only |
| Missing values | normalise to null, fill median/zero/mode/`Unknown`, drop rows |
| Casing & whitespace | trim + canonical spelling, trim only, title/lower/upper, flag only |

Scope is deliberately one domain. Generic cleaning across arbitrary datasets is
what kills projects like this.

## Outputs

| File | What it is |
|------|-----------|
| `cleaned_<file>.csv` | The data |
| `decision_log.json` | Every decision: rule, column, strategy, who, when, rows changed, source hash |
| `provenance_report.md` | Human-readable version, including what was left unfixed |
| `recipe.json` | Approved decisions as replayable steps, plus deliberate skips |
| `cleaned_data.db` | SQLite: `orders_clean`, `decision_log`, `run_metadata` |

### On Power BI

Power BI is **not** embedded. Embedding requires Premium capacity or PPU
licences, which isn't justifiable for a solo project. Instead the cleaned data
and its decision log go to SQLite with proper types (dates as dates, numbers as
numbers) and you point your own Power BI at the file. The provenance travels
with the data rather than living in a separate document.

## Files

| File | What it is |
|------|-----------|
| `cleaner.py` | Detection engine and apply layer. No UI dependency. |
| `replay.py` | Recipe planning, drift detection, replay execution. |
| `decision_log.py` | Decision record, JSON log, provenance report, recipe. |
| `sql_export.py` | Type inference and SQLite export. |
| `app.py` | Streamlit UI — review mode and replay mode. |
| `make_sample_data.py` | Synthetic sample generator (fabricated data only). |
| `test_pipeline.py` | Headless end-to-end test — 43 checks across all ten stages. |

```bash
python test_pipeline.py
```

Runs the whole thing without Streamlit: detect → approve → apply → log → recipe
→ replay against drifted data → SQL export. Exits non-zero on any failure.

## Privacy

Everything runs locally. No file is uploaded anywhere, no API is called, no
telemetry is sent. The sample data is entirely fabricated — invented names and
`@example.invalid` addresses.

## Known limitations

- **Synonyms are not merged.** `trim_canonical` collapses case and whitespace
  variants of the same string (`uk`, `UK `, `UK` → `UK`) but will not merge `UK`
  into `United Kingdom`. That needs a category-mapping rule with a user-supplied
  vocabulary — deliberately out of scope for v1.
- **Canonical form = most frequent form.** If `PAKISTAN` outnumbers `Pakistan`,
  `PAKISTAN` wins. Use `trim_title` if you want fixed casing.
- **Proposals are detected once per scan**, so counts reflect the file at scan
  time. Use *Re-scan the cleaned data* after a pass.
- **SQLite export needs a filesystem that supports SQLite locking.** Some network
  and container mounts do not; write to local disk.
- **Single-file, single-user.** No accounts, no concurrency, no billing. Those
  are product problems, not portfolio problems.

## Roadmap

1. Category/synonym mapping rule with a user-supplied vocabulary
2. Postgres target alongside SQLite
3. Column-level rules configurable per domain (HR data as the second domain)

## Licence

MIT — see `LICENSE`.
