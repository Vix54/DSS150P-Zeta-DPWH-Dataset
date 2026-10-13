import json

import pandas as pd

from src.extract.file_source import lane_root
from src.extract.raw_store import RawRun
from src.transform.curated import CuratedError, latest_run_id, match_regions
from src.transform.jsonstat import JsonStatError, jsonstat2_frame
from src.transform.pxweb import PxWebError, pxweb_json_frame

CURATED_POPULATION_FILE = "region_population.parquet"
MATCH_MODES = ("label", "code")
POPULATION_COLUMNS = ["psgc_code", "psgc_name", "source_label", "population", "period"]


def population_settings(settings):
    name = (settings.raw.get("curated") or {}).get("population_source")
    if not name:
        return None, None
    config = (settings.raw.get("api_sources") or {}).get(name)
    if not config:
        raise CuratedError(f"curated.population_source is '{name}' but api_sources has no entry with that name")
    if not config.get("region_dimension"):
        raise CuratedError(f"API source '{name}' needs region_dimension (the dimension that holds the regions)")
    if config.get("region_match", "label") not in MATCH_MODES:
        raise CuratedError(f"API source '{name}' has region_match '{config['region_match']}'; use one of: {', '.join(MATCH_MODES)}")
    return name, config


def latest_population_file(settings, name, config):
    root = lane_root(settings.paths["raw"], name)
    run = RawRun.open_existing(root, latest_run_id(root, f"raw {name}"))
    run.verified_files()
    entries = [entry for entry in run.manifest_entries() if entry["file"] == config["file_name"]]
    if len(entries) != 1:
        raise CuratedError(f"expected one {config['file_name']} in raw run {run.run_id} of {name}, found {len(entries)}")
    return run, entries[0]


def code_matches(frame, region_dimension, regions):
    names = dict(zip(regions["psgc_code"].astype(str), regions["region_name"].astype(str)))
    codes = frame[f"{region_dimension}_code"].astype(str)
    return codes.map(lambda code: code if code in names else None), codes.map(lambda code: names.get(code))


def clean_label(label):
    return str(label).strip().lstrip(".").strip()


def build_region_population(frame, regions, region_dimension, aliases=None, match_on="label"):
    dimensions = frame.attrs.get("dimensions") or []
    if region_dimension not in dimensions:
        raise CuratedError(f"population data has no dimension '{region_dimension}'; dimensions found: {', '.join(dimensions) or 'none'}")
    others = [dimension for dimension in dimensions if dimension != region_dimension]
    for dimension in others:
        chosen = frame[f"{dimension}_code"].unique()
        if len(chosen) != 1:
            raise CuratedError(f"population query must select exactly one value of '{dimension}', found {len(chosen)}; narrow the query in config/settings.yml")
    labels = frame.attrs.get("labels") or {}
    period = "; ".join(f"{labels.get(dimension, dimension)}: {frame[dimension].iloc[0]}" for dimension in others) if len(frame) else ""
    work = frame[[region_dimension, "value"]].copy()
    work["source_label"] = work[region_dimension].map(clean_label)
    if match_on == "code":
        work["psgc_code"], work["psgc_name"] = code_matches(frame, region_dimension, regions)
        matches = {label: {"status": "matched" if pd.notna(code) else "unmatched"} for label, code in zip(work["source_label"], work["psgc_code"])}
    else:
        matches = match_regions(work["source_label"].unique(), regions, aliases)
        work["psgc_code"] = work["source_label"].map(lambda label: matches.get(label, {}).get("psgc_code"))
        work["psgc_name"] = work["source_label"].map(lambda label: matches.get(label, {}).get("psgc_name"))
    matched = work.loc[work["psgc_code"].notna()].copy()
    duplicated = matched.loc[matched["psgc_code"].duplicated(keep=False)]
    if len(duplicated):
        pairs = ", ".join(f"{row.source_label} -> {row.psgc_name}" for row in duplicated.itertuples())
        raise CuratedError(f"more than one population row matched the same PSGC region: {pairs}; add region_aliases or narrow the query")
    matched["population"] = pd.to_numeric(matched["value"], errors="coerce").round().astype("Int64")
    matched["period"] = period
    table = matched[POPULATION_COLUMNS].astype({"psgc_code": "string", "psgc_name": "string", "source_label": "string", "period": "string"})
    table = table.sort_values("psgc_code").reset_index(drop=True)
    covered = set(table["psgc_code"].dropna())
    missing = sorted(str(row.region_name) for row in regions.itertuples(index=False) if row.psgc_code not in covered)
    ambiguous = sorted(label for label, value in matches.items() if value["status"] == "ambiguous")
    summary = {
        "population_rows_read": int(len(frame)),
        "population_regions_matched": int(len(table)),
        "population_regions_missing_value": int(table["population"].isna().sum()),
        "psgc_regions_without_population": missing,
        "population_ambiguous_labels": ambiguous,
        "population_labels_not_regions": int(sum(1 for value in matches.values() if value["status"] == "unmatched")),
        "population_period": period,
        "population_region_match": match_on,
    }
    if table.empty:
        raise CuratedError("no population row matched a PSGC region; check region_dimension and the query")
    return table, summary


def region_population_for_curation(settings, regions, aliases):
    name, config = population_settings(settings)
    if name is None:
        return None, {}
    run, entry = latest_population_file(settings, name, config)
    try:
        if config.get("response_format") == "json":
            from src.extract.api_source import metadata_file_name

            metadata_path = run.directory / metadata_file_name(config)
            if not metadata_path.exists():
                raise CuratedError(f"raw run {run.run_id} of {name} has no {metadata_file_name(config)}")
            frame = pxweb_json_frame(
                json.loads((run.directory / entry["file"]).read_text(encoding="utf-8-sig")),
                json.loads(metadata_path.read_text(encoding="utf-8-sig")),
                config["query"],
            )
        else:
            frame = jsonstat2_frame(json.loads((run.directory / entry["file"]).read_text(encoding="utf-8")))
    except (ValueError, JsonStatError, PxWebError) as exc:
        raise CuratedError(f"population file in raw run {run.run_id} of {name} could not be read: {exc}") from exc
    table, summary = build_region_population(frame, regions, config["region_dimension"], aliases, config.get("region_match", "label"))
    summary.update({"population_source": name, "population_run_id": run.run_id, "population_sha256": entry["sha256"]})
    return table, summary
