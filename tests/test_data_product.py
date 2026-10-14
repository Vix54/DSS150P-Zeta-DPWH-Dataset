import pandas as pd
import pytest

from src.cli import build_parser
from src.extract.raw_store import sha256_bytes


def silent(message):
    return None


def test_sample_exports_a_seeded_reviewable_product(tmp_path):
    from src.analytics import data_product, full_analysis
    from tests import test_downstream

    settings = test_downstream.build_pipeline(tmp_path)
    with pytest.raises(data_product.DataProductError, match="run 'python -m src.cli analyze' first"):
        data_product.export_data_product(settings, out_dir=tmp_path / "product", log=silent)
    full_analysis.run_analytics(settings, log=silent)
    (settings.paths["analytics"] / "06_delay_by_rainfall.png").write_bytes(b"left over from an older run")
    out = tmp_path / "product"
    manifest = data_product.export_data_product(settings, out_dir=out, rows=3, log=silent)
    sample = pd.read_csv(out / "sample_contracts.csv")
    assert len(sample) == 3 == manifest["sample_rows"]
    assert manifest["random_state"] == 42
    assert list(sample.columns) == manifest["columns"]
    again = data_product.export_data_product(settings, out_dir=tmp_path / "again", rows=3, log=silent)
    assert pd.read_csv(tmp_path / "again" / "sample_contracts.csv")["contract_id"].tolist() == sample["contract_id"].tolist()
    assert again["files"] == manifest["files"]
    sums = (out / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    assert len(sums) == len(manifest["files"]) + 1
    for line in sums:
        digest, name = line.split("  ", 1)
        assert sha256_bytes((out / name).read_bytes()) == digest
    assert (out / "charts").is_dir() and any((out / "charts").glob("*.png"))
    assert not (out / "charts" / "06_delay_by_rainfall.png").exists()
    written_charts = sorted(path.name for path in settings.paths["analytics"].glob("*.png") if path.name.startswith("0") and not path.name.startswith("06"))
    assert written_charts == sorted(name for name in data_product.CHART_FILES if (settings.paths["analytics"] / name).exists())
    assert build_parser().parse_args(["sample", "--rows", "50"]).rows == 50
