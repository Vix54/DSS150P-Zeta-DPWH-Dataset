import json
import math

import numpy as np
import pandas as pd

TRUE_VALUES = {"true", "t", "1", "yes"}
FALSE_VALUES = {"false", "f", "0", "no"}


def clean_text(series):
    text = series.astype("string").str.strip()
    return text.mask((text == "").fillna(False), pd.NA)


def parse_number(series):
    text = clean_text(series)
    numbers = pd.to_numeric(text.str.replace(",", "", regex=False), errors="coerce").astype("Float64")
    bad = text.notna() & numbers.isna()
    return numbers, bad


def parse_integer(series):
    numbers, bad = parse_number(series)
    fractional = numbers.notna() & (numbers != numbers.round())
    integers = numbers.mask(fractional, pd.NA).round().astype("Int64")
    return integers, bad | fractional.fillna(False)


ALTERNATE_DATETIME_FORMAT = "%m/%d/%Y %I:%M:%S %p"
PLACEHOLDER_DATE = pd.Timestamp("1900-01-01")


def parse_iso(text):
    try:
        parsed = pd.to_datetime(text, errors="coerce", format="ISO8601")
    except (ValueError, TypeError):
        parsed = pd.to_datetime(text, errors="coerce", format="ISO8601", utc=True)
    if parsed.dt.tz is not None:
        parsed = parsed.dt.tz_convert("UTC").dt.tz_localize(None)
    return parsed


def parse_datetime(series):
    text = clean_text(series).str.replace(r"(\.\d{6})\d+", r"\1", regex=True)
    parsed = parse_iso(text)
    pending = (text.notna() & parsed.isna()).fillna(False)
    alternate = pd.to_datetime(text.where(pending), errors="coerce", format=ALTERNATE_DATETIME_FORMAT)
    used_alternate = (pending & alternate.notna()).fillna(False)
    parsed = parsed.mask(used_alternate, alternate)
    placeholder = (parsed.dt.normalize() == PLACEHOLDER_DATE).fillna(False)
    parsed = parsed.mask(placeholder, pd.NaT)
    bad = (text.notna() & parsed.isna()).fillna(False) & ~placeholder
    return parsed, bad, used_alternate, placeholder


def to_dates(parsed):
    return pd.Series([None if pd.isna(value) else value.date() for value in parsed], index=parsed.index, dtype="object")


def parse_bool(series):
    text = clean_text(series).str.lower()
    result = pd.Series(pd.NA, index=series.index, dtype="boolean")
    result = result.mask(text.isin(TRUE_VALUES).fillna(False), True)
    result = result.mask(text.isin(FALSE_VALUES).fillna(False), False)
    bad = text.notna() & ~text.isin(TRUE_VALUES | FALSE_VALUES).fillna(False)
    return result, bad


def to_plain(value):
    if isinstance(value, np.ndarray):
        return [to_plain(item) for item in value.tolist()]
    if isinstance(value, (list, tuple)):
        return [to_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_plain(item) for key, item in value.items()}
    if isinstance(value, np.generic):
        return to_plain(value.item())
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def nested_to_json(series):
    texts = []
    counts = []
    for value in series:
        plain = to_plain(value)
        if plain is None:
            texts.append(None)
            counts.append(None)
            continue
        texts.append(json.dumps(plain, ensure_ascii=False, sort_keys=True, default=str))
        counts.append(len(plain) if isinstance(plain, (list, dict)) else None)
    return (
        pd.Series(texts, index=series.index, dtype="string"),
        pd.Series(counts, index=series.index, dtype="Int64"),
    )


def same_values(left, right):
    left_text = clean_text(left)
    right_text = clean_text(right)
    left_numbers = pd.to_numeric(left_text, errors="coerce")
    right_numbers = pd.to_numeric(right_text, errors="coerce")
    both_missing = left_text.isna() & right_text.isna()
    text_equal = (left_text == right_text).fillna(False)
    number_equal = (left_numbers == right_numbers).fillna(False)
    return both_missing | text_equal | number_equal
