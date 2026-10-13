import itertools

import pandas as pd


class JsonStatError(Exception):
    pass


def category_order(category):
    index = category.get("index")
    labels = category.get("label") or {}
    if index is None:
        codes = list(labels)
    elif isinstance(index, list):
        codes = list(index)
    elif isinstance(index, dict):
        codes = [code for code, _ in sorted(index.items(), key=lambda item: item[1])]
    else:
        raise JsonStatError("category index must be a list or an object")
    if not codes:
        raise JsonStatError("a dimension has no categories")
    return [(str(code), str(labels.get(code, code))) for code in codes]


def jsonstat2_frame(document):
    if not isinstance(document, dict) or document.get("class") != "dataset":
        raise JsonStatError("expected a JSON-stat 2.0 object with class 'dataset'")
    ids = document.get("id")
    sizes = document.get("size")
    dimensions = document.get("dimension")
    if not isinstance(ids, list) or not isinstance(sizes, list) or len(ids) != len(sizes) or not isinstance(dimensions, dict):
        raise JsonStatError("'id', 'size' and 'dimension' are missing or inconsistent")
    categories = []
    for dimension, size in zip(ids, sizes):
        if dimension not in dimensions:
            raise JsonStatError(f"dimension '{dimension}' is listed in 'id' but not described")
        order = category_order(dimensions[dimension].get("category") or {})
        if len(order) != size:
            raise JsonStatError(f"dimension '{dimension}' has {len(order)} categories but size {size}")
        categories.append(order)
    total = 1
    for size in sizes:
        total *= size
    raw_values = document.get("value")
    if isinstance(raw_values, list):
        if len(raw_values) != total:
            raise JsonStatError(f"expected {total} values, found {len(raw_values)}")
        values = raw_values
    elif isinstance(raw_values, dict):
        values = [raw_values.get(str(position)) for position in range(total)]
    else:
        raise JsonStatError("'value' must be a list or an object")
    rows = []
    for combination, value in zip(itertools.product(*categories), values):
        row = {}
        for dimension, (code, label) in zip(ids, combination):
            row[f"{dimension}_code"] = code
            row[dimension] = label
        row["value"] = value
        rows.append(row)
    frame = pd.DataFrame(rows)
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame.attrs["dimensions"] = list(ids)
    frame.attrs["labels"] = {dimension: str(dimensions[dimension].get("label", dimension)) for dimension in ids}
    frame.attrs["updated"] = document.get("updated")
    return frame
