"""HITL cleaner - detection engine and apply layer.

Four rules, each with several strategies. Nothing is applied automatically:
detect() returns proposals in plain language, apply() only runs on a
strategy the user explicitly approved.

Domain: e-commerce orders.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd
from pandas.api.types import is_numeric_dtype, is_string_dtype

# Strings that mean "missing" but are not real nulls.
NULL_TOKENS = {"", "n/a", "na", "null", "none", "-", "--", "nan", "?"}

DATE_COL_HINTS = ("date", "_at", "time", "day")
ID_COL_HINTS = ("_id", "id", "order_no", "invoice")


# --------------------------------------------------------------------------
# data model
# --------------------------------------------------------------------------

@dataclass
class Strategy:
    """One way of resolving a proposal."""
    id: str
    label: str
    detail: str


@dataclass
class Proposal:
    """A detected issue, described in plain language, awaiting a decision."""
    id: str
    rule: str                      # rule family, e.g. "date_format"
    column: str | None
    title: str                     # one-line plain-language summary
    body: str                      # the fuller explanation
    rows_affected: int
    strategies: list[Strategy]
    default_strategy: str
    preview: pd.DataFrame = field(default_factory=pd.DataFrame)
    meta: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _is_text_series(s: pd.Series) -> bool:
    """True for a column of text, regardless of pandas version.

    Do NOT test `dtype == object` here. pandas 2 reads text columns as
    `object`, but pandas 3 gives them a dedicated string dtype, so the
    object check silently returns False and every text-based rule stops
    firing without raising anything. That failure is invisible - the app
    just stops proposing date and casing fixes - so it is worth the extra
    helper.
    """
    if is_numeric_dtype(s):
        return False
    return s.dtype == object or is_string_dtype(s)


def _is_blank(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and pd.isna(v):
        return True
    return str(v).strip().lower() in NULL_TOKENS


def _looks_like_date_col(name: str, s: pd.Series) -> bool:
    if any(h in name.lower() for h in DATE_COL_HINTS):
        return True
    sample = [str(v) for v in s.dropna().head(40) if not _is_blank(v)]
    if not sample:
        return False
    hits = sum(bool(_classify_date(v)) for v in sample)
    return hits / len(sample) > 0.7


ISO_RE = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*$")
SLASH_RE = re.compile(r"^\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\s*$")


def _classify_date(v: Any) -> str | None:
    """Return 'iso', 'dayfirst', 'monthfirst', 'ambiguous', or None."""
    s = str(v).strip()
    if ISO_RE.match(s):
        return "iso"
    m = SLASH_RE.match(s)
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    if a > 12 and b <= 12:
        return "dayfirst"
    if b > 12 and a <= 12:
        return "monthfirst"
    return "ambiguous"


def _parse_date(v: Any, dayfirst: bool) -> pd.Timestamp | None:
    s = str(v).strip()
    m = ISO_RE.match(s)
    if m:
        return pd.Timestamp(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = SLASH_RE.match(s)
    if not m:
        return None
    a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    day, month = (a, b) if dayfirst else (b, a)
    if day > 31 or month > 12:
        day, month = month, day
    try:
        return pd.Timestamp(y, month, day)
    except ValueError:
        return None


def _text_cols(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if _is_text_series(df[c])]


def _canonical_form(values: pd.Series) -> dict[str, str]:
    """Map each raw variant to the most common form of its normalised key."""
    norm = values.astype(str).str.strip().str.lower()
    mapping: dict[str, str] = {}
    for key, group in values.astype(str).groupby(norm):
        if _is_blank(key):
            continue
        winner = group.str.strip().value_counts().idxmax()
        for raw in group.unique():
            mapping[raw] = winner
    return mapping


# --------------------------------------------------------------------------
# rule 1 - date format mismatch
# --------------------------------------------------------------------------

def detect_date_format(df: pd.DataFrame) -> list[Proposal]:
    out = []
    for col in df.columns:
        s = df[col]
        if not _is_text_series(s) or not _looks_like_date_col(col, s):
            continue

        kinds = s.map(lambda v: None if _is_blank(v) else _classify_date(v))
        counts = kinds.value_counts().to_dict()
        parseable = sum(counts.get(k, 0) for k in
                        ("iso", "dayfirst", "monthfirst", "ambiguous"))
        if parseable == 0:
            continue
        distinct = {k for k in counts if k in
                    ("iso", "dayfirst", "monthfirst", "ambiguous")}
        if len(distinct) < 2:
            continue

        non_iso = parseable - counts.get("iso", 0)
        dayfirst_ev = counts.get("dayfirst", 0)
        monthfirst_ev = counts.get("monthfirst", 0)
        ambiguous = counts.get("ambiguous", 0)

        body = (
            f"{counts.get('iso', 0)} values are ISO (YYYY-MM-DD), "
            f"{non_iso} are slash-style. Of the slash ones, "
            f"{dayfirst_ev} can only be day-first (day > 12), "
            f"{monthfirst_ev} can only be month-first, and "
            f"{ambiguous} could be read either way. "
            "Parsed with the wrong assumption those ambiguous rows land on "
            "the wrong date silently, which is why this needs your call."
        )

        preview = pd.DataFrame({
            col: s[kinds.notna() & (kinds != "iso")].head(8).values
        })
        preview["reading"] = [_classify_date(v) for v in preview[col]]

        default = "infer" if (dayfirst_ev and monthfirst_ev) else (
            "iso_dayfirst" if dayfirst_ev >= monthfirst_ev else "iso_monthfirst")

        out.append(Proposal(
            id=f"date::{col}",
            rule="date_format",
            column=col,
            title=f"`{col}` mixes {len(distinct)} date formats",
            body=body,
            rows_affected=int(non_iso),
            strategies=[
                Strategy("iso_dayfirst", "Read slash dates as DD/MM/YYYY -> ISO",
                         "UK convention. Ambiguous rows treated as day-first."),
                Strategy("iso_monthfirst", "Read slash dates as MM/DD/YYYY -> ISO",
                         "US convention. Ambiguous rows treated as month-first."),
                Strategy("infer", "Infer per value, ambiguous follow the majority",
                         "Unambiguous rows decide themselves; ambiguous rows use "
                         "whichever convention the unambiguous ones favour."),
                Strategy("to_uk", "Standardise everything to DD/MM/YYYY",
                         "Keeps UK display format instead of ISO."),
                Strategy("flag_only", "Change nothing, add a format flag column",
                         f"Adds `{col}__format` so you can filter later."),
            ],
            default_strategy=default,
            preview=preview,
            meta={"counts": counts},
        ))
    return out


def apply_date_format(df: pd.DataFrame, p: Proposal, strategy: str
                      ) -> tuple[pd.DataFrame, int]:
    col = p.column
    df = df.copy()
    kinds = df[col].map(lambda v: None if _is_blank(v) else _classify_date(v))
    target = kinds.notna() & (kinds != "iso")

    if strategy == "flag_only":
        df[f"{col}__format"] = kinds.fillna("unparsed")
        return df, int(target.sum())

    counts = p.meta.get("counts", {})
    if strategy == "iso_dayfirst":
        dayfirst = True
    elif strategy == "iso_monthfirst":
        dayfirst = False
    else:  # infer / to_uk
        dayfirst = counts.get("dayfirst", 0) >= counts.get("monthfirst", 0)

    def convert(v):
        if _is_blank(v):
            return v
        kind = _classify_date(v)
        if kind is None:
            return v
        use_dayfirst = dayfirst
        if strategy in ("infer", "to_uk"):
            if kind == "dayfirst":
                use_dayfirst = True
            elif kind == "monthfirst":
                use_dayfirst = False
        ts = _parse_date(v, use_dayfirst)
        if ts is None:
            return v
        return ts.strftime("%d/%m/%Y" if strategy == "to_uk"
                           else "%Y-%m-%d")

    changed_mask = target if strategy != "to_uk" else kinds.notna()
    df[col] = df[col].map(convert)
    return df, int(changed_mask.sum())


# --------------------------------------------------------------------------
# rule 2 - duplicates
# --------------------------------------------------------------------------

def detect_duplicates(df: pd.DataFrame) -> list[Proposal]:
    exact = int(df.duplicated(keep="first").sum())

    norm = df.copy()
    for c in _text_cols(norm):
        norm[c] = norm[c].astype(str).str.strip().str.lower()
    fuzzy = int(norm.duplicated(keep="first").sum())

    key_col = next((c for c in df.columns
                    if any(h in c.lower() for h in ID_COL_HINTS)), None)
    key_dupes = int(df.duplicated(subset=[key_col], keep="first").sum()) \
        if key_col else 0

    if exact == 0 and fuzzy == 0 and key_dupes == 0:
        return []

    body_parts = [f"{exact} rows are byte-for-byte identical to an earlier row."]
    if fuzzy > exact:
        body_parts.append(
            f"{fuzzy - exact} more become identical once text is trimmed and "
            "lowercased - same record typed inconsistently.")
    if key_col and key_dupes > fuzzy:
        body_parts.append(
            f"{key_dupes} rows repeat an existing `{key_col}` while differing "
            "in other columns - could be a genuine amendment, not a duplicate.")

    preview = df[df.duplicated(keep=False)].head(8)

    strategies = [
        Strategy("drop_exact", "Drop exact duplicates only",
                 f"Removes {exact} rows. Safest option."),
        Strategy("drop_normalised", "Drop duplicates after trimming/lowercasing text",
                 f"Removes {fuzzy} rows, including inconsistently typed repeats. "
                 "Keeps the first occurrence as written."),
    ]
    if key_col:
        strategies += [
            Strategy("drop_by_key_first", f"One row per `{key_col}`, keep first",
                     f"Removes {key_dupes} rows. Aggressive - discards later "
                     "amendments to the same order."),
            Strategy("drop_by_key_last", f"One row per `{key_col}`, keep last",
                     f"Removes {key_dupes} rows, keeping the most recent version."),
        ]
    strategies.append(
        Strategy("flag_only", "Change nothing, add an `is_duplicate` column",
                 "Nothing is deleted; you review them downstream."))

    return [Proposal(
        id="dupes::rows",
        rule="duplicates",
        column=None,
        title=f"{fuzzy} duplicate rows found ({exact} exact)",
        body=" ".join(body_parts),
        rows_affected=max(exact, fuzzy, key_dupes),
        strategies=strategies,
        default_strategy="drop_normalised" if fuzzy > exact else "drop_exact",
        preview=preview,
        meta={"key_col": key_col, "exact": exact, "fuzzy": fuzzy,
              "key_dupes": key_dupes},
    )]


def apply_duplicates(df: pd.DataFrame, p: Proposal, strategy: str
                     ) -> tuple[pd.DataFrame, int]:
    before = len(df)
    key_col = p.meta.get("key_col")

    if strategy == "drop_exact":
        out = df.drop_duplicates(keep="first")
    elif strategy == "drop_normalised":
        norm = df.copy()
        for c in _text_cols(norm):
            norm[c] = norm[c].astype(str).str.strip().str.lower()
        out = df[~norm.duplicated(keep="first")]
    elif strategy == "drop_by_key_first" and key_col:
        out = df.drop_duplicates(subset=[key_col], keep="first")
    elif strategy == "drop_by_key_last" and key_col:
        out = df.drop_duplicates(subset=[key_col], keep="last")
    elif strategy == "flag_only":
        out = df.copy()
        out["is_duplicate"] = df.duplicated(keep="first")
        return out, int(out["is_duplicate"].sum())
    else:
        return df, 0

    return out.reset_index(drop=True), before - len(out)


# --------------------------------------------------------------------------
# rule 3 - missing values
# --------------------------------------------------------------------------

def _numeric_like(s: pd.Series) -> bool:
    vals = [v for v in s if not _is_blank(v)]
    if not vals:
        return False
    ok = 0
    for v in vals[:60]:
        try:
            float(str(v).replace(",", "").replace("£", "").replace("$", ""))
            ok += 1
        except ValueError:
            pass
    return ok / min(len(vals), 60) > 0.9


def detect_missing(df: pd.DataFrame) -> list[Proposal]:
    out = []
    for col in df.columns:
        blanks = df[col].map(_is_blank)
        n = int(blanks.sum())
        if n == 0:
            continue

        tokens = (df.loc[blanks, col].astype(str).str.strip()
                  .replace("", "(empty)").value_counts().to_dict())
        token_desc = ", ".join(f"{k!r} x{v}" for k, v in list(tokens.items())[:5])
        numeric = _numeric_like(df[col])
        pct = 100 * n / len(df)

        body = (
            f"{n} of {len(df)} rows ({pct:.1f}%) are missing in `{col}`, "
            f"written as: {token_desc}. These are not real nulls - a naive "
            "load would treat 'N/A' as a valid category and quietly skew "
            "counts."
        )

        strategies = [
            Strategy("to_null", "Normalise placeholders to real nulls",
                     "Keeps the gap visible instead of inventing a value. "
                     "Recommended when the gap is meaningful."),
        ]
        if numeric:
            strategies += [
                Strategy("fill_median", "Fill with the column median",
                         "Preserves row count; distorts totals if you sum it."),
                Strategy("fill_zero", "Fill with 0",
                         "Only correct if missing genuinely means none."),
            ]
        else:
            strategies += [
                Strategy("fill_unknown", "Fill with 'Unknown'",
                         "Keeps the row usable in group-bys as an explicit bucket."),
                Strategy("fill_mode", "Fill with the most common value",
                         f"Would use "
                         f"{df.loc[~blanks, col].astype(str).str.strip().mode().iloc[0] if (~blanks).any() else 'n/a'!r}. "
                         "Fabricates data - use only for low-stakes columns."),
            ]
        strategies.append(
            Strategy("drop_rows", "Drop the affected rows",
                     f"Loses {n} rows ({pct:.1f}%) including their other columns."))

        out.append(Proposal(
            id=f"missing::{col}",
            rule="missing_values",
            column=col,
            title=f"`{col}` has {n} missing values ({pct:.1f}%)",
            body=body,
            rows_affected=n,
            strategies=strategies,
            default_strategy="to_null",
            preview=df[blanks].head(6),
            meta={"numeric": numeric, "tokens": tokens},
        ))
    return out


def apply_missing(df: pd.DataFrame, p: Proposal, strategy: str
                  ) -> tuple[pd.DataFrame, int]:
    col = p.column
    df = df.copy()
    blanks = df[col].map(_is_blank)
    n = int(blanks.sum())

    if strategy == "to_null":
        df.loc[blanks, col] = pd.NA
    elif strategy == "fill_unknown":
        df.loc[blanks, col] = "Unknown"
    elif strategy == "fill_mode":
        mode = df.loc[~blanks, col].astype(str).str.strip().mode()
        df.loc[blanks, col] = mode.iloc[0] if len(mode) else "Unknown"
    elif strategy in ("fill_median", "fill_zero"):
        clean = pd.to_numeric(
            df.loc[~blanks, col].astype(str).str.replace(r"[£$,]", "", regex=True),
            errors="coerce")
        fill = 0 if strategy == "fill_zero" else clean.median()
        df[col] = pd.to_numeric(
            df[col].astype(str).str.replace(r"[£$,]", "", regex=True),
            errors="coerce")
        df.loc[blanks, col] = fill
    elif strategy == "drop_rows":
        df = df[~blanks].reset_index(drop=True)
    else:
        return df, 0
    return df, n


# --------------------------------------------------------------------------
# rule 4 - casing and whitespace
# --------------------------------------------------------------------------

def detect_casing(df: pd.DataFrame) -> list[Proposal]:
    out = []
    for col in _text_cols(df):
        s = df[col].astype(str)
        if _looks_like_date_col(col, df[col]):
            continue
        # A numeric column stored as text has no meaningful "spelling" to
        # canonicalise - proposing one on `quantity` is noise.
        if _numeric_like(df[col]):
            continue

        ws = int((s != s.str.strip()).sum())
        stripped = s.str.strip()
        mapping = _canonical_form(df[col].astype(str))

        # count rows whose value differs from the canonical form of its key
        canon_series = s.map(lambda v: mapping.get(v, v.strip()))
        differs = int((stripped != canon_series).sum())

        groups = {}
        for raw in stripped.unique():
            groups.setdefault(raw.lower(), set()).add(raw)
        multi = {k: v for k, v in groups.items() if len(v) > 1 and k not in NULL_TOKENS}

        if ws == 0 and not multi:
            continue

        examples = "; ".join(
            f"{sorted(v)!r} -> all one value" for v in list(multi.values())[:3])
        body = (
            f"{ws} values carry leading/trailing whitespace and "
            f"{len(multi)} distinct values differ only by capitalisation "
            f"({examples}). Left alone these split into separate rows in any "
            "group-by, so your category counts come out wrong."
        )

        out.append(Proposal(
            id=f"case::{col}",
            rule="casing_whitespace",
            column=col,
            title=f"`{col}` has {len(multi)} case variants and {ws} whitespace issues",
            body=body,
            rows_affected=max(ws, differs),
            strategies=[
                Strategy("trim_canonical",
                         "Trim, then map each variant to its most common spelling",
                         "Data-driven: 'uk', 'UK ' and 'united kingdom' all become "
                         "whichever form appears most often. Preserves real casing "
                         "like 'USB-C'."),
                Strategy("trim_only", "Trim whitespace only",
                         "Conservative. Leaves case variants alone."),
                Strategy("trim_title", "Trim and Title Case everything",
                         "Uniform but mangles acronyms ('USB-C' -> 'Usb-C')."),
                Strategy("trim_lower", "Trim and lowercase everything",
                         "Best for join keys and emails, ugly in reports."),
                Strategy("trim_upper", "Trim and uppercase everything",
                         "Best for codes and country abbreviations."),
                Strategy("flag_only", "Change nothing, add a variant flag column",
                         f"Adds `{col}__nonstandard`."),
            ],
            default_strategy="trim_canonical",
            preview=pd.DataFrame({
                "value": [repr(v) for group in list(multi.values())[:6]
                          for v in sorted(group)][:12]
            }),
            meta={"whitespace": ws, "variant_groups": len(multi)},
        ))
    return out


def apply_casing(df: pd.DataFrame, p: Proposal, strategy: str
                 ) -> tuple[pd.DataFrame, int]:
    col = p.column
    df = df.copy()
    orig = df[col].astype(str)
    stripped = orig.str.strip()

    if strategy == "flag_only":
        mapping = _canonical_form(orig)
        df[f"{col}__nonstandard"] = orig != orig.map(
            lambda v: mapping.get(v, v.strip()))
        return df, int(df[f"{col}__nonstandard"].sum())

    if strategy == "trim_only":
        new = stripped
    elif strategy == "trim_title":
        new = stripped.str.title()
    elif strategy == "trim_lower":
        new = stripped.str.lower()
    elif strategy == "trim_upper":
        new = stripped.str.upper()
    else:  # trim_canonical
        mapping = _canonical_form(orig)
        new = orig.map(lambda v: mapping.get(v, v.strip()))

    changed = int((orig != new).sum())
    # keep genuine nulls null rather than the string "nan"
    was_na = df[col].isna()
    df[col] = new
    df.loc[was_na, col] = pd.NA
    return df, changed


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------

APPLIERS: dict[str, Callable[..., tuple[pd.DataFrame, int]]] = {
    "date_format": apply_date_format,
    "duplicates": apply_duplicates,
    "missing_values": apply_missing,
    "casing_whitespace": apply_casing,
}

RULE_ORDER = ["casing_whitespace", "missing_values", "date_format", "duplicates"]


def detect(df: pd.DataFrame) -> list[Proposal]:
    """Return every proposal, ordered so later rules see cleaner input."""
    proposals = (detect_casing(df) + detect_missing(df)
                 + detect_date_format(df) + detect_duplicates(df))
    proposals.sort(key=lambda p: RULE_ORDER.index(p.rule))
    return proposals


def apply(df: pd.DataFrame, p: Proposal, strategy: str
          ) -> tuple[pd.DataFrame, int]:
    """Apply one approved decision. Never called without an explicit strategy."""
    return APPLIERS[p.rule](df, p, strategy)


def load_csv(path_or_buffer) -> pd.DataFrame:
    """Read everything as text so nothing is silently coerced on load."""
    return pd.read_csv(path_or_buffer, dtype=str, keep_default_na=False)
