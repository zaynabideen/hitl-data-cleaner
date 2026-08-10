"""Headless end-to-end test: detect -> approve -> apply -> log -> replay -> SQL.

Runs without Streamlit so the core logic can be verified in CI.

    python test_pipeline.py
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile

import pandas as pd

import cleaner
import make_sample_data
import replay as replay_mod
import sql_export
from decision_log import DecisionLog, sha256_bytes

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" +
          (f" - {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def section(title):
    print(f"\n{title}")


# --------------------------------------------------------------------------

def part_one():
    """Review pass on month 1."""
    make_sample_data.main("sample_orders_messy.csv")
    raw = open("sample_orders_messy.csv", "rb").read()
    df = cleaner.load_csv("sample_orders_messy.csv")
    original = df.copy()

    section("1. Detection - every injected issue type is found")
    proposals = cleaner.detect(df)
    for rule in ("casing_whitespace", "missing_values", "date_format",
                 "duplicates"):
        check(f"{rule} detected", rule in {p.rule for p in proposals})
    check("proposals have plain-language bodies",
          all(len(p.body) > 60 for p in proposals))
    check("every proposal offers >= 3 strategies",
          all(len(p.strategies) >= 3 for p in proposals))
    check("every default strategy is a real strategy id",
          all(p.default_strategy in {s.id for s in p.strategies}
              for p in proposals))

    section("2. Detection does not mutate the input")
    check("input unchanged after detect", df.equals(original))

    section("3. Approval loop - apply only what is approved")
    log = DecisionLog(source_file="sample_orders_messy.csv",
                      source_sha256=sha256_bytes(raw), source_rows=len(df),
                      source_columns=list(df.columns), approved_by="test-runner")
    work = df.copy()
    for p in proposals:
        if p.rule == "missing_values" and p.column == "email":
            log.record(p, "skipped", None, 0, len(work), len(work), "keep gap")
            continue
        strategy, decision = p.default_strategy, "approved"
        if p.rule == "date_format":
            strategy, decision = "iso_dayfirst", "modified"
        before = len(work)
        work, changed = cleaner.apply(work, p, strategy)
        log.record(p, decision, strategy, changed, before, len(work))

    check("log has an entry per proposal",
          len(log.decisions) == len(proposals),
          f"{len(log.decisions)} vs {len(proposals)}")
    check("skipped decision recorded",
          any(d.decision == "skipped" for d in log.decisions))
    check("modified decision recorded",
          any(d.decision == "modified" for d in log.decisions))

    section("4. Cleaned output is actually clean")
    dates = work["order_date"].dropna()
    check("all dates now ISO",
          all(cleaner._classify_date(v) == "iso" for v in dates))
    check("no exact duplicate rows remain", int(work.duplicated().sum()) == 0)
    for col in ("country", "status"):
        seen, clashes = {}, []
        for v in work[col].dropna().astype(str).unique():
            k = v.strip().lower()
            if k in seen and seen[k] != v:
                clashes.append((seen[k], v))
            seen[k] = v
        check(f"{col}: one spelling per distinct value", not clashes,
              str(clashes))
    ws = sum((work[c].astype(str) != work[c].astype(str).str.strip()).sum()
             for c in work.columns if work[c].dtype == object)
    check("no leading/trailing whitespace left", ws == 0, f"{ws} left")
    check("skipped column still has its gaps",
          work["email"].map(cleaner._is_blank).sum() > 0)
    check("row count only shrank via duplicate removal",
          len(work) == len(original) - log.decisions[-1].rows_changed)

    section("5. Log, report and recipe serialise")
    payload = json.loads(log.to_json())
    check("json has schema_version", payload["schema_version"] == "1.0")
    check("json records source hash",
          payload["source"]["sha256"] == sha256_bytes(raw))
    check("every decision names who approved it",
          all(d["approved_by"] == "test-runner" for d in payload["decisions"]))
    check("every decision is timestamped",
          all(d["timestamp"] for d in payload["decisions"]))
    md = log.to_markdown()
    check("provenance report lists skipped issues",
          "Known issues left in the data" in md)
    recipe = log.to_recipe()
    check("recipe excludes skipped steps",
          len(recipe["steps"]) == len(proposals) - 1)
    check("recipe records the skip separately", len(recipe["skipped"]) == 1)
    check("recipe carries source columns",
          recipe["source_columns"] == list(df.columns))

    work.to_csv("cleaned_orders.csv", index=False)
    open("decision_log.json", "w").write(log.to_json())
    open("provenance_report.md", "w").write(md)
    replay_mod.save_recipe(recipe, "recipe.json")
    return work, log, recipe


def part_two(recipe):
    """Replay the recipe against a drifted month-2 file."""
    section("6. Replay - plan before applying anything")
    make_sample_data.main_month2("sample_orders_month2.csv")
    raw2 = open("sample_orders_month2.csv", "rb").read()
    df2 = cleaner.load_csv("sample_orders_month2.csv")
    before_copy = df2.copy()

    plan = replay_mod.plan(df2, recipe)
    check("plan does not mutate input", df2.equals(before_copy))
    check("recipe steps matched the new file", len(plan.matched) > 0,
          f"{len(plan.matched)} matched")

    kinds = {d.kind for d in plan.drift}
    check("new column detected as drift", "new_column" in kinds, str(kinds))
    check("unseen issue detected as drift", "new_issue" in kinds, str(kinds))
    check("drift is flagged as blocking", len(plan.blocking_drift) > 0)
    check("every drift item explains itself in prose",
          all(len(d.message) > 40 for d in plan.drift))

    new_issue_cols = {d.column for d in plan.drift if d.kind == "new_issue"}
    check("the new `product` issue is the one flagged",
          "product" in new_issue_cols, str(new_issue_cols))

    section("7. Replay refuses to run over unresolved drift")
    stopped = False
    try:
        replay_mod.run(df2, recipe, source_file="m2", source_bytes=raw2,
                       on_drift="stop")
    except replay_mod.DriftStop as e:
        stopped = True
        check("DriftStop names the unresolved items", len(e.unresolved) > 0)
    check("run() raised DriftStop instead of guessing", stopped)

    section("8. Replay runs once the human resolves the drift")
    choices = {}
    for d in plan.blocking_drift:
        if d.proposal is None:
            continue
        choices[d.proposal.id] = ("__skip__" if d.column == "discount_code"
                                  else d.proposal.default_strategy)
    out, log2, plan2 = replay_mod.run(
        df2, recipe, source_file="sample_orders_month2.csv",
        source_bytes=raw2, approved_by="test-runner", on_drift="partial",
        decisions=choices)

    check("recipe steps logged as approved",
          any(d.decision == "approved" for d in log2.decisions))
    check("resolved drift logged as modified",
          any(d.decision == "modified" for d in log2.decisions))
    check("schema drift recorded in the log",
          any(d.rule.startswith("drift:") for d in log2.decisions))
    check("replay log notes where each step came from",
          any("replayed from recipe" in d.note for d in log2.decisions))

    dates2 = out["order_date"].dropna()
    check("month-2 dates normalised by the recipe",
          all(cleaner._classify_date(v) == "iso" for v in dates2))
    check("month-2 duplicates removed by the recipe",
          int(out.duplicated().sum()) == 0)
    check("newly approved product fix was applied",
          all(str(v) == str(v).strip() for v in out["product"].dropna()))
    check("untouched new column kept its raw values",
          "discount_code" in out.columns)

    section("9. Second replay of the same file is clean")
    recipe2 = log2.to_recipe()
    plan3 = replay_mod.plan(out, recipe2)
    check("re-running over already-clean data raises no new_issue drift",
          not any(d.kind == "new_issue" for d in plan3.drift),
          str({d.kind for d in plan3.drift}))
    return out, log2


def part_three(out, log2):
    section("10. SQL export for Power BI")
    # SQLite needs a filesystem that supports its locking. Some network /
    # container mounts do not, so build in a temp dir and copy the result.
    db = os.path.join(tempfile.gettempdir(), "cleaned_data.db")
    if os.path.exists(db):
        os.remove(db)
    info = sql_export.export(out, log2, db)
    check("database written", os.path.exists(db))
    check("all three tables present",
          set(info["tables"]) == {"orders_clean", "decision_log",
                                  "run_metadata"}, str(info["tables"]))
    con = sqlite3.connect(db)
    n = con.execute("SELECT COUNT(*) FROM orders_clean").fetchone()[0]
    meta = con.execute("SELECT source_sha256, approved_by FROM run_metadata"
                       ).fetchone()
    logn = con.execute("SELECT COUNT(*) FROM decision_log").fetchone()[0]
    con.close()
    check("orders_clean row count matches", n == len(out), f"{n} vs {len(out)}")
    check("decision log travelled with the data", logn == len(log2.decisions))
    check("run metadata keeps the source hash and reviewer",
          bool(meta[0]) and meta[1] == "test-runner", str(meta))
    check("numeric column exported as a number",
          "int" in info["dtypes"]["quantity"].lower()
          or "float" in info["dtypes"]["quantity"].lower(),
          info["dtypes"]["quantity"])
    check("date column exported as a datetime",
          "datetime" in info["dtypes"]["order_date"].lower(),
          info["dtypes"]["order_date"])
    try:
        shutil.copy(db, "cleaned_data.db")
    except OSError as e:
        print(f"  (note: could not copy .db into this folder - {e})")


def part_four():
    """Regression: text rules must fire on a non-`object` string dtype.

    pandas 2 reads text as `object`; pandas 3 uses a dedicated string
    dtype. Code that tests `dtype == object` silently stops proposing date
    and casing fixes under pandas 3 - no error, just missing proposals.
    This reproduces that dtype on either version.
    """
    section("11. Text rules survive a pandas-3 style string dtype")
    df = cleaner.load_csv("sample_orders_messy.csv")
    baseline = {p.rule for p in cleaner.detect(df)}

    typed = df.copy()
    for c in typed.columns:
        typed[c] = typed[c].astype("string")   # not `object`
    check("test fixture really is a non-object dtype",
          typed["country"].dtype != object, str(typed["country"].dtype))

    rules = {p.rule for p in cleaner.detect(typed)}
    check("casing rule still fires", "casing_whitespace" in rules, str(rules))
    check("date rule still fires", "date_format" in rules, str(rules))
    check("same rules as with object dtype", rules == baseline,
          f"{rules} vs {baseline}")

    # infer_types only converts when nearly every value parses, so use an
    # already-clean column here - the point is the dtype, not the content.
    clean = pd.DataFrame({
        "order_date": pd.Series(["2025-01-05", "2025-02-11", "2025-03-02"],
                                dtype="string"),
        "unit_price": pd.Series(["10.50", "3.25", "88.00"], dtype="string"),
    })
    dtypes = sql_export.infer_types(clean).dtypes
    check("SQL export types dates from a string dtype",
          "datetime" in str(dtypes["order_date"]).lower(),
          str(dtypes["order_date"]))
    check("SQL export types numbers from a string dtype",
          "float" in str(dtypes["unit_price"]).lower(),
          str(dtypes["unit_price"]))


def part_five():
    """Real-world file quirks that would otherwise look like 'tool broken'."""
    import io
    section("12. Awkward input files are handled or explained")

    df = cleaner.load_csv(io.StringIO("id;name;city\n1;Ali;London\n2;Sara;Leeds"))
    check("semicolon-separated file parses into real columns",
          list(df.columns) == ["id", "name", "city"], str(list(df.columns)))

    df = cleaner.load_csv(io.StringIO("id\tname\n1\tAli\n2\tSara"))
    check("tab-separated file parses into real columns",
          list(df.columns) == ["id", "name"], str(list(df.columns)))

    raw = io.BytesIO("id,name\n1,Renée\n2,José\n".encode("cp1252"))
    try:
        df = cleaner.load_csv(raw)
        ok = "Ren" in str(df["name"].iloc[0])
    except Exception as e:                                   # noqa: BLE001
        ok, df = False, None
        print(f"      (raised {type(e).__name__})")
    check("non-UTF-8 (Excel/Windows) file still loads", ok)

    # A genuinely clean file must not have issues invented for it.
    clean = cleaner.load_csv(io.StringIO(
        "id,name,city,amount\n1,Ali,London,10.5\n2,Sara,Leeds,22.0"))
    check("clean file produces no proposals",
          cleaner.detect(clean) == [], "must not invent work")

    # Different domain entirely - rules are column-driven, not e-commerce-only.
    hr = cleaner.load_csv(io.StringIO(
        "employee_id,full_name,department,join_date,salary\n"
        "E1,  Ali Raza ,Sales,2023-04-01,45000\n"
        "E2,ALI RAZA,sales,01/05/2023,52000\n"
        "E3,Sara Khan,Engineering,13/06/2023,N/A\n"
        "E1,  Ali Raza ,Sales,2023-04-01,45000\n"))
    rules = {p.rule for p in cleaner.detect(hr)}
    check("all four rules fire on a non-e-commerce file",
          rules == {"casing_whitespace", "missing_values", "date_format",
                    "duplicates"}, str(rules))

    # No date column -> the date rule simply stays quiet, it does not error.
    nodate = cleaner.load_csv(io.StringIO(
        "sku,warehouse,qty\nA-1,LDN,12\nA-2,ldn,\nA-1,LDN,12\n"))
    rules = {p.rule for p in cleaner.detect(nodate)}
    check("no date column means no date proposal, not a crash",
          "date_format" not in rules and rules, str(rules))


def main():
    work, log, recipe = part_one()
    out, log2 = part_two(recipe)
    part_three(out, log2)
    part_four()
    part_five()

    print("\nArtefacts: cleaned_orders.csv, decision_log.json, "
          "provenance_report.md, recipe.json, cleaned_data.db")
    print("\n" + ("ALL CHECKS PASSED" if not FAILS
                  else f"{len(FAILS)} FAILED: {FAILS}"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
