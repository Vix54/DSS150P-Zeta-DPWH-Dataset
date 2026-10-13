import pandas as pd

MISSING_MARKERS = {"", ".", "..", "...", "-"}


class PxWebError(Exception):
    pass


def variable_lookup(metadata):
    variables = (metadata or {}).get("variables")
    if not isinstance(variables, list) or not variables:
        raise PxWebError("table metadata has no 'variables' list")
    lookup = {}
    for variable in variables:
        codes = [str(code) for code in variable.get("values") or []]
        texts = [str(text) for text in variable.get("valueTexts") or codes]
        if len(codes) != len(texts):
            raise PxWebError(f"variable '{variable.get('code')}' has {len(codes)} codes but {len(texts)} labels")
        lookup[str(variable.get("code"))] = {"text": str(variable.get("text", variable.get("code"))), "values": dict(zip(codes, texts))}
    return lookup


def expected_count(query, lookup):
    total = 1
    for item in query:
        code = str(item.get("code"))
        selection = item.get("selection") or {}
        values = [str(value) for value in selection.get("values") or []]
        if code not in lookup:
            raise PxWebError(f"query selects '{code}', which the table metadata does not list")
        if selection.get("filter") == "all" and values == ["*"]:
            total *= len(lookup[code]["values"])
        elif selection.get("filter") == "item":
            unknown = [value for value in values if value not in lookup[code]["values"]]
            if unknown:
                raise PxWebError(f"query selects unknown values of '{code}': {', '.join(unknown)}")
            total *= len(values)
        else:
            return None
    return total


def numeric(value):
    text = str(value).strip()
    if text in MISSING_MARKERS:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise PxWebError(f"value '{text}' is not a number") from exc


def pxweb_json_frame(document, metadata, query):
    if not isinstance(document, dict) or not isinstance(document.get("columns"), list) or not isinstance(document.get("data"), list):
        raise PxWebError("expected a PX-Web JSON object with 'columns' and 'data'")
    lookup = variable_lookup(metadata)
    dimensions = [str(column["code"]) for column in document["columns"] if column.get("type") in ("d", "t")]
    contents = [column for column in document["columns"] if column.get("type") == "c"]
    if len(contents) != 1:
        raise PxWebError(f"expected one content column, found {len(contents)}")
    if not document["data"]:
        raise PxWebError("the response has no data rows")
    rows = []
    for item in document["data"]:
        key = [str(part) for part in item.get("key") or []]
        values = item.get("values") or []
        if len(key) != len(dimensions) or len(values) != 1:
            raise PxWebError(f"data row {item!r} does not match the {len(dimensions)} dimension columns and one content column")
        row = {}
        for dimension, code in zip(dimensions, key):
            labels = lookup.get(dimension, {}).get("values", {})
            if labels and code not in labels:
                raise PxWebError(f"code '{code}' of '{dimension}' is not in the table metadata")
            row[f"{dimension}_code"] = code
            row[dimension] = labels.get(code, code)
        row["value"] = numeric(values[0])
        rows.append(row)
    expected = expected_count(query, lookup)
    if expected is not None and expected != len(rows):
        raise PxWebError(f"expected {expected} data rows for the query, found {len(rows)}")
    frame = pd.DataFrame(rows)
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame.attrs["dimensions"] = dimensions
    frame.attrs["labels"] = {dimension: lookup.get(dimension, {}).get("text", dimension) for dimension in dimensions}
    frame.attrs["updated"] = None
    return frame
