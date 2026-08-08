# Example outputs

Produced by `python test_pipeline.py` against the synthetic month-1 sample.
Committed so the repo shows what the tool actually emits without anyone having
to run it first.

| File | What it shows |
|------|---------------|
| `provenance_report.md` | The human-readable audit trail, including a *Known issues left in the data* section for what was deliberately skipped |
| `decision_log.json` | The machine-readable version — rule, strategy, reviewer, timestamps, rows changed, source SHA-256 |
| `recipe.json` | The replayable subset: approved steps plus the skips, ready for next month's file |
| `cleaned_orders.csv` | The resulting data |

All source data is fabricated. Names are invented and every address uses the
reserved `.invalid` domain.
