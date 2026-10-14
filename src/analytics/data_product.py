import json
import shutil
from pathlib import Path

import pandas as pd

from src.config import PROJECT_ROOT
from src.transform.curated import CURATED_CONTRACTS_FILE, read_curated_contracts
from src.transform.population import CURATED_POPULATION_FILE
from src.transform.staging import sha256_file

SAMPLE_ROWS = 1000
RANDOM_STATE = 42
DEFAULT_DIR = PROJECT_ROOT / "docs" / "data_product"
ANALYTICS_FILES = ("insights.md", "analytics_metrics.json")
CHART_FILES = (
    "01_status_counts.png",
    "02_delay_rate_by_region.png",
    "03_cost_distribution.png",
    "04_feature_importance.png",
    "05_delayed_per_100k.png",
)
SUMS_FILE = "SHA256SUMS"
MANIFEST_FILE = "sample_manifest.json"


class DataProductError(Exception):
    pass


def export_data_product(settings, out_dir=DEFAULT_DIR, rows=SAMPLE_ROWS, run_id=None, log=print):
    out_dir = Path(out_dir)
    analytics_dir = Path(settings.paths["analytics"])
    missing = [name for name in ANALYTICS_FILES if not (analytics_dir / name).exists()]
    charts = [analytics_dir / name for name in CHART_FILES if (analytics_dir / name).exists()]
    if missing or not charts:
        raise DataProductError(f"analytics outputs are missing in {analytics_dir}; run 'python -m src.cli analyze' first")

    curated_run_id, curated, report = read_curated_contracts(settings, run_id)
    df = curated.copy()
    size = min(int(rows), len(df))
    sample = df.sample(n=size, random_state=RANDOM_STATE).sort_values("contract_id").reset_index(drop=True)

    out_dir.mkdir(parents=True, exist_ok=True)
    charts_dir = out_dir / "charts"
    charts_dir.mkdir(exist_ok=True)
    written = []
    sample.to_csv(out_dir / "sample_contracts.csv", index=False)
    sample.to_parquet(out_dir / "sample_contracts.parquet", index=False)
    written += ["sample_contracts.csv", "sample_contracts.parquet"]

    population_key = f"curated/{CURATED_POPULATION_FILE}"
    if population_key in report.get("outputs", {}):
        population_path = Path(settings.paths["curated"]) / f"run_id={curated_run_id}" / CURATED_POPULATION_FILE
        if sha256_file(population_path) != report["outputs"][population_key]["sha256"]:
            raise DataProductError(f"checksum mismatch for curated file {CURATED_POPULATION_FILE}; curated output was modified")
        pd.read_parquet(population_path).to_csv(out_dir / "region_population.csv", index=False)
        written.append("region_population.csv")

    for name in ANALYTICS_FILES:
        shutil.copyfile(analytics_dir / name, out_dir / name)
        written.append(name)
    for chart in charts:
        shutil.copyfile(chart, charts_dir / chart.name)
        written.append(f"charts/{chart.name}")

    manifest = {
        "curated_run_id": curated_run_id,
        "curated_contracts_sha256": report["outputs"][f"curated/{CURATED_CONTRACTS_FILE}"]["sha256"],
        "curated_rows": int(len(df)),
        "sample_rows": int(size),
        "random_state": RANDOM_STATE,
        "columns": list(sample.columns),
        "population_period": report.get("population_period"),
        "delay_as_of_date": report.get("delay_as_of_date"),
        "files": list(written),
    }
    (out_dir / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    written.append(MANIFEST_FILE)
    sums = "".join(f"{sha256_file(out_dir / name)}  {name}\n" for name in sorted(written))
    (out_dir / SUMS_FILE).write_text(sums, encoding="utf-8")
    log(f"Sample of {size} of {len(df)} curated contracts (random_state={RANDOM_STATE}) from curated run {curated_run_id}")
    log(f"Wrote {len(written)} files and {SUMS_FILE} to {out_dir}")
    return manifest
