import re

import pandas as pd

MEMBER_SEPARATOR = " / "
TRAILING_ID = re.compile(r"\s*\((\[REVOKED\])?\s*(\d+)\)\s*$")
FORMER_NAME = re.compile(r"\(\s*(?:FORMERLY|PREVIOUS\w*|FOR\.)\s*:?\s*", re.IGNORECASE)


def parse_member(raw):
    text = raw.strip()
    match = TRAILING_ID.search(text)
    if match:
        display_name = text[: match.start()].strip()
        source_id = int(match.group(2))
        revoked = match.group(1) is not None
    else:
        display_name, source_id, revoked = text, None, False
    former = FORMER_NAME.search(display_name)
    if former:
        name = display_name[: former.start()].strip()
        former_name = display_name[former.end():].strip()
        if former_name.endswith(")"):
            former_name = former_name[:-1].strip()
    else:
        name, former_name = display_name, None
    return {
        "member_raw": text,
        "display_name": display_name,
        "contractor_name": name or display_name,
        "former_name": former_name or None,
        "contractor_source_id": source_id,
        "has_revoked_marker": revoked,
        "name_truncated": display_name.count("(") != display_name.count(")"),
    }


def parse_contractor(raw):
    if raw is None or raw is pd.NA or (isinstance(raw, float) and pd.isna(raw)):
        return []
    text = str(raw).strip()
    if not text:
        return []
    return [parse_member(part) for part in text.split(MEMBER_SEPARATOR) if part.strip()]


def collation_key(name):
    return re.sub(r"[^0-9A-Z]", "", name.upper())


def rebuild_winner_names(members):
    names = [member["display_name"] for member in members]
    return ", ".join(sorted(names, key=lambda name: (collation_key(name), name)))
