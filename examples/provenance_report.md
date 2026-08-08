# Data provenance report

**Source file:** `sample_orders_messy.csv`  
**SHA-256:** `441a4b6fe4350ddd2f7293b2240a12a6d938bafd6632a83c6f0bd0be44c532d6`  
**Rows in:** 194  
**Reviewed by:** test-runner  
**Session:** 2026-08-08T16:46:18+00:00 to 2026-08-08T16:46:18+00:00  
**Decisions:** 9 approved, 1 modified, 1 skipped

Every change below was proposed by the tool and explicitly approved by a person. Nothing was applied automatically.

| # | Rule | Column | Decision | Strategy | Rows changed | Rows after |
|---|------|--------|----------|----------|--------------|------------|
| 1 | casing_whitespace | customer_name | approved | Trim, then map each variant to its most common spelling | 33 | 194 |
| 2 | casing_whitespace | email | approved | Trim, then map each variant to its most common spelling | 2 | 194 |
| 3 | casing_whitespace | country | approved | Trim, then map each variant to its most common spelling | 130 | 194 |
| 4 | casing_whitespace | status | approved | Trim, then map each variant to its most common spelling | 94 | 194 |
| 5 | missing_values | email | skipped | - | 0 | 194 |
| 6 | missing_values | country | approved | Normalise placeholders to real nulls | 12 | 194 |
| 7 | missing_values | quantity | approved | Normalise placeholders to real nulls | 5 | 194 |
| 8 | missing_values | unit_price | approved | Normalise placeholders to real nulls | 4 | 194 |
| 9 | missing_values | status | approved | Normalise placeholders to real nulls | 7 | 194 |
| 10 | date_format | order_date | modified | Read slash dates as DD/MM/YYYY -> ISO | 40 | 194 |
| 11 | duplicates | (all) | approved | Drop duplicates after trimming/lowercasing text | 14 | 180 |

## Known issues left in the data

- **`email` has 7 missing values (3.6%)** - skipped, 7 rows still affected.

## Reviewer notes

- Step 5 (missing_values): keep gap
