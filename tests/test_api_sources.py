import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from src.cli import build_parser
from src.config import Settings, load_settings
from src.extract.api_source import api_config, request_payload, run_api_extract
from src.extract.file_source import FileSourceError, extract_sources, lane_root, run_file_extract
from src.extract.http_client import PoliteClient, build_user_agent
from src.extract.raw_store import RawRun, sha256_bytes
from src.transform import curated
from src.transform.curated import CuratedError
from src.transform.jsonstat import JsonStatError, jsonstat2_frame
from src.transform.population import CURATED_POPULATION_FILE, build_region_population, population_settings
from src.transform.pxweb import PxWebError, pxweb_json_frame
from tests.fakes import FakeClock, FakeResponse, FakeSession
from tests.test_curated import PSGC_ROWS

FIXED = datetime(2026, 10, 13, 2, 0, 0, tzinfo=timezone.utc)
FILE_URL = "https://files.example.test/dataset/resolve/abc123/file.parquet"
FILE_ROBOTS = "https://files.example.test/robots.txt"
API_URL = "https://stats.example.test/PXWeb/api/v1/en/DB/POP.px"
API_ROBOTS = "https://stats.example.test/robots.txt"
CONTENT = b"PAR1-downloaded-bytes-PAR1"


class TickingNow:
    def __init__(self):
        self.moment = FIXED

    def __call__(self):
        self.moment += timedelta(seconds=1)
        return self.moment


def silent(message):
    return None


def population_document(labels=None, values=None, years=("2024",)):
    labels = labels or ["Philippines", "National Capital Region (NCR)", "..City of Manila", "Region I - Ilocos Region", "Region IV-A - CALABARZON"]
    values = values if values is not None else [112729484, 14001751, 1846513, 5301139, 16926599]
    region_codes = [f"R{position:02d}" for position in range(len(labels))]
    year_codes = [str(position) for position in range(len(years))]
    flat = []
    for value in values:
        flat.extend([value] * len(years))
    return {
        "class": "dataset",
        "version": "2.0",
        "label": "Total Population by Region",
        "updated": "2025-07-17T00:00:00Z",
        "id": ["Geolocation", "Year"],
        "size": [len(labels), len(years)],
        "dimension": {
            "Geolocation": {"label": "Geographic Location", "category": {"index": region_codes, "label": dict(zip(region_codes, labels))}},
            "Year": {"label": "Year", "category": {"index": {code: position for position, code in enumerate(year_codes)}, "label": dict(zip(year_codes, years))}},
        },
        "value": flat,
    }


def regions_frame():
    rows = [(code, name) for code, name, level in PSGC_ROWS if level == "Reg"]
    return pd.DataFrame(rows, columns=["psgc_code", "region_name"]).sort_values("psgc_code").reset_index(drop=True)


def client_for(routes):
    clock = FakeClock()
    session = FakeSession(routes)
    client = PoliteClient(
        user_agent=build_user_agent("TestBot/1.0", "team@example.test"),
        request_delay_seconds=1.5,
        timeout_seconds=5,
        max_retries=1,
        backoff_seconds=1,
        stop_on_status=[401, 403],
        accept="*/*",
        session=session,
        clock=clock.clock,
        sleep=clock.sleep,
    )
    return client, session


def file_settings(tmp_path, expected, download_allowed=True):
    entry = {
        "path": str(tmp_path / "source" / "file.parquet"),
        "publisher": "Example Publisher",
        "dataset_url": "https://files.example.test/dataset",
        "file_url": FILE_URL,
        "revision": "abc123",
        "licence": "CC0-1.0",
        "expected_sha256": expected,
        "collection_method": "Published by the example publisher.",
        "download_allowed": download_allowed,
    }
    raw = {"file_sources": {"example": entry}, "scraper": {}, "source": {}}
    return Settings(raw=raw, paths={"raw": tmp_path / "raw"})


def api_settings(tmp_path, expected=""):
    entry = {
        "publisher": "Example Statistics Office",
        "dataset_url": "https://stats.example.test/table/POP",
        "api_url": API_URL,
        "terms_url": "https://stats.example.test/Terms",
        "api_documentation_url": "https://stats.example.test/API-Documentation",
        "rate_limit": "10 requests per 10 seconds",
        "query": [{"code": "Year", "selection": {"filter": "item", "values": ["0"]}}],
        "response_format": "json-stat2",
        "file_name": "population_by_region.json",
        "revision": "2024 census table",
        "licence": "CC-BY-4.0",
        "attribution": "Example attribution",
        "checksum_origin": "recorded_at_download",
        "expected_sha256": expected,
        "collection_method": "Retrieved through the documented API.",
        "region_dimension": "Geolocation",
    }
    raw = {"api_sources": {"population": entry}, "scraper": {}, "source": {}}
    return Settings(raw=raw, paths={"raw": tmp_path / "raw"})


def api_routes(body, status=200):
    return {
        API_ROBOTS: [FakeResponse(404, b"", {"content-type": "text/plain"})],
        API_URL: [FakeResponse(status, body, {"content-type": "application/json"})],
    }


def test_post_sends_the_payload_after_checking_robots():
    client, session = client_for(api_routes(b"{}"))
    response = client.post(API_URL, {"query": [], "response": {"format": "json-stat2"}})
    assert response.status_code == 200
    assert [url for url, _ in session.calls] == [API_ROBOTS, API_URL]
    assert session.posted == [{"query": [], "response": {"format": "json-stat2"}}]
    assert session.calls[1][1]["Accept"] == "*/*"


def test_post_stops_when_robots_disallow_the_api():
    routes = {API_ROBOTS: [FakeResponse(200, b"User-agent: *\nDisallow: /PXWeb/", {"content-type": "text/plain"})], API_URL: [FakeResponse(200, b"{}")]}
    client, session = client_for(routes)
    with pytest.raises(Exception, match="robots.txt disallows"):
        client.post(API_URL, {})
    assert [url for url, _ in session.calls] == [API_ROBOTS]


def test_jsonstat_frame_reads_list_and_object_indexes():
    frame = jsonstat2_frame(population_document())
    assert list(frame.columns) == ["Geolocation_code", "Geolocation", "Year_code", "Year", "value"]
    assert len(frame) == 5
    assert frame.loc[1, "Geolocation"] == "National Capital Region (NCR)"
    assert frame.loc[1, "value"] == 14001751
    assert frame.attrs["dimensions"] == ["Geolocation", "Year"]


def test_jsonstat_frame_reads_sparse_values_and_rejects_bad_documents():
    document = population_document()
    document["value"] = {"0": 10, "3": 30}
    frame = jsonstat2_frame(document)
    assert frame["value"].isna().sum() == 3
    with pytest.raises(JsonStatError, match="class 'dataset'"):
        jsonstat2_frame({"class": "collection"})
    broken = population_document()
    broken["size"] = [4, 1]
    with pytest.raises(JsonStatError, match="categories but size"):
        jsonstat2_frame(broken)


def test_download_saves_a_matching_file_and_stores_it(tmp_path):
    settings = file_settings(tmp_path, sha256_bytes(CONTENT))
    client, session = client_for({FILE_ROBOTS: [FakeResponse(404, b"", {"content-type": "text/plain"})], FILE_URL: [FakeResponse(200, CONTENT, {"content-type": "application/octet-stream"})]})
    run, status = run_file_extract(settings, "example", project_root=tmp_path, now=TickingNow(), log=silent, download=True, client=client)
    assert status == "stored"
    assert (tmp_path / "source" / "file.parquet").read_bytes() == CONTENT
    metadata = run.read_metadata()
    assert metadata["downloaded_this_run"] is True
    assert metadata["automated_download_available"] is True
    assert [url for url, _ in session.calls] == [FILE_ROBOTS, FILE_URL]


def test_download_with_wrong_bytes_saves_nothing(tmp_path):
    settings = file_settings(tmp_path, "a" * 64)
    client, _ = client_for({FILE_ROBOTS: [FakeResponse(404, b"", {"content-type": "text/plain"})], FILE_URL: [FakeResponse(200, CONTENT, {"content-type": "application/octet-stream"})]})
    with pytest.raises(FileSourceError, match="does not match the expected SHA-256"):
        run_file_extract(settings, "example", project_root=tmp_path, now=TickingNow(), log=silent, download=True, client=client)
    assert not (tmp_path / "source" / "file.parquet").exists()
    assert not (tmp_path / "source" / "file.parquet.part").exists()
    assert not lane_root(tmp_path / "raw", "example").exists()


def test_download_is_skipped_when_the_verified_file_is_present(tmp_path):
    settings = file_settings(tmp_path, sha256_bytes(CONTENT))
    (tmp_path / "source").mkdir()
    (tmp_path / "source" / "file.parquet").write_bytes(CONTENT)
    client, session = client_for({})
    messages = []
    _, status = run_file_extract(settings, "example", project_root=tmp_path, now=TickingNow(), log=messages.append, download=True, client=client)
    assert status == "stored"
    assert session.calls == []
    assert any(message.startswith("Already present") for message in messages)


def test_download_stops_on_bot_protection(tmp_path):
    settings = file_settings(tmp_path, sha256_bytes(CONTENT))
    challenge = FakeResponse(403, b"<html>challenge</html>", {"content-type": "text/html"})
    client, _ = client_for({FILE_ROBOTS: [FakeResponse(404, b"", {"content-type": "text/plain"})], FILE_URL: [challenge]})
    with pytest.raises(FileSourceError, match="automated download stopped"):
        run_file_extract(settings, "example", project_root=tmp_path, now=TickingNow(), log=silent, download=True, client=client)


def test_manual_source_is_not_downloaded(tmp_path):
    settings = file_settings(tmp_path, sha256_bytes(CONTENT), download_allowed=False)
    client, session = client_for({})
    messages = []
    with pytest.raises(FileSourceError, match="source file not found"):
        run_file_extract(settings, "example", project_root=tmp_path, now=TickingNow(), log=messages.append, download=True, client=client)
    assert session.calls == []
    assert any("download it by hand" in message for message in messages)


def test_api_source_without_recorded_checksum_reports_it_and_writes_nothing(tmp_path):
    body = json.dumps(population_document()).encode("utf-8")
    client, _ = client_for(api_routes(body))
    with pytest.raises(FileSourceError, match=f"SHA-256 {sha256_bytes(body)}"):
        run_api_extract(api_settings(tmp_path), "population", client=client, now=TickingNow(), log=silent)
    assert not lane_root(tmp_path / "raw", "population").exists()


def test_api_source_stores_then_reports_unchanged(tmp_path):
    body = json.dumps(population_document()).encode("utf-8")
    settings = api_settings(tmp_path, sha256_bytes(body))
    client, session = client_for(api_routes(body))
    run, status = run_api_extract(settings, "population", client=client, now=TickingNow(), log=silent)
    assert status == "stored"
    assert (run.directory / "population_by_region.json").read_bytes() == body
    metadata = run.read_metadata()
    assert metadata["collected_by_team"] is True
    assert metadata["request"]["response"]["format"] == "json-stat2"
    assert metadata["values"] == 5
    assert session.posted[0]["query"][0]["code"] == "Year"
    client, _ = client_for(api_routes(body))
    again, status = run_api_extract(settings, "population", client=client, now=TickingNow(), log=silent)
    assert status == "unchanged"
    assert again.run_id == run.run_id


def test_api_source_with_revised_data_stops(tmp_path):
    body = json.dumps(population_document()).encode("utf-8")
    client, _ = client_for(api_routes(body))
    with pytest.raises(FileSourceError, match="may have revised the table"):
        run_api_extract(api_settings(tmp_path, "b" * 64), "population", client=client, now=TickingNow(), log=silent)


def test_api_source_rejects_responses_that_are_not_jsonstat(tmp_path):
    client, _ = client_for(api_routes(b"<html>maintenance</html>"))
    with pytest.raises(FileSourceError, match="not a valid json-stat2 dataset"):
        run_api_extract(api_settings(tmp_path, "c" * 64), "population", client=client, now=TickingNow(), log=silent)


def test_api_source_stops_on_refusal(tmp_path):
    client, _ = client_for(api_routes(b'{"error": "refused"}', status=403))
    with pytest.raises(FileSourceError, match="API request stopped"):
        run_api_extract(api_settings(tmp_path, "c" * 64), "population", client=client, now=TickingNow(), log=silent)


def test_extract_sources_routes_api_sources(tmp_path):
    body = json.dumps(population_document()).encode("utf-8")
    settings = api_settings(tmp_path, sha256_bytes(body))
    client, _ = client_for(api_routes(body))
    results = extract_sources(settings=settings, environ={}, client=client, now=TickingNow(), log=silent)
    assert results["population"][1] == "stored"
    with pytest.raises(FileSourceError, match="unknown source 'nope'"):
        extract_sources(settings=settings, sources=["nope"], environ={}, log=silent)


def test_population_matches_regions_and_lists_the_missing_ones():
    table, summary = build_region_population(jsonstat2_frame(population_document()), regions_frame(), "Geolocation", {"Region IV-B": "MIMAROPA"})
    assert table["psgc_code"].tolist() == ["0100000000", "0400000000", "1300000000"]
    assert table.loc[table["psgc_code"] == "1300000000", "population"].iloc[0] == 14001751
    assert summary["psgc_regions_without_population"] == ["MIMAROPA Region"]
    assert summary["population_period"] == "Year: 2024"
    assert summary["population_labels_not_regions"] == 2


def test_population_query_must_select_one_year():
    with pytest.raises(CuratedError, match="exactly one value of 'Year'"):
        build_region_population(jsonstat2_frame(population_document(years=("2020", "2024"))), regions_frame(), "Geolocation")


def test_population_rejects_two_rows_for_one_region():
    labels = ["National Capital Region (NCR)", "NCR", "Region I - Ilocos Region"]
    with pytest.raises(CuratedError, match="matched the same PSGC region"):
        build_region_population(jsonstat2_frame(population_document(labels=labels, values=[1, 2, 3])), regions_frame(), "Geolocation")


def test_population_needs_the_region_dimension():
    with pytest.raises(CuratedError, match="no dimension 'Region'"):
        build_region_population(jsonstat2_frame(population_document()), regions_frame(), "Region")


def test_cli_parses_download_flag():
    args = build_parser().parse_args(["extract-file", "--source", "all", "--download"])
    assert args.download is True
    assert build_parser().parse_args(["extract-file"]).download is False


def add_population(settings, tmp_path, document=None):
    body = json.dumps(document or population_document()).encode("utf-8")
    entry = dict(api_settings(tmp_path, sha256_bytes(body)).raw["api_sources"]["population"])
    settings.raw["api_sources"] = {"population": entry}
    settings.raw["curated"]["population_source"] = "population"
    run = RawRun.create(lane_root(tmp_path / "raw", "population"), now=lambda: FIXED)
    run.write("population_by_region.json", body, API_URL, 200, FIXED)
    return run


def test_curate_writes_region_population_and_validation_checks_it(tmp_path):
    from src.validate import pipeline
    from tests import test_downstream
    from tests.test_curated import make_psgc_run
    from tests.test_staging import make_raw_run
    from src.transform import staging

    raw_run = make_raw_run(tmp_path, test_downstream.frame_with_costs())
    psgc_run = make_psgc_run(tmp_path)
    settings = test_downstream.make_settings(tmp_path, raw_run.manifest_entries()[0]["sha256"], psgc_run.manifest_entries()[0]["sha256"])
    population_run = add_population(settings, tmp_path)
    staging.run_staging(settings, now=lambda: FIXED, log=silent, environ={})
    report, status = curated.run_curated(settings, now=TickingNow(), log=silent, environ={})
    assert status == "curated"
    assert f"curated/{CURATED_POPULATION_FILE}" in report["outputs"]
    assert report["outputs"][f"curated/{CURATED_POPULATION_FILE}"]["rows"] == 3
    assert report["population_run_id"] == population_run.run_id
    from src.transform import partition

    partition.build_partitions(settings, now=lambda: FIXED, log=silent, environ={})
    results = pipeline.run_validation(settings, skip_db=True, log=silent)
    messages = {message: status for status, message in results.items}
    assert results.failed() == 0
    assert any(message.startswith("Lineage: region population was built from raw population run") for message in messages)
    population_check = [message for message in messages if message.startswith("Population:")][0]
    assert messages[population_check] == pipeline.WARN
    assert "MIMAROPA Region" in population_check

    from src.analytics import full_analysis

    full_analysis.run_analytics(settings, log=silent)
    metrics = json.loads((settings.paths["analytics"] / "analytics_metrics.json").read_text(encoding="utf-8"))
    insights = (settings.paths["analytics"] / "insights.md").read_text(encoding="utf-8")
    ncr = [row for row in metrics["per_capita"] if row["psgc_code"] == "1300000000"][0]
    assert ncr["ongoing"] == 2
    assert ncr["delayed"] == 1
    assert ncr["delayed_per_100k"] == round(1 / 14001751 * 100000, 2)
    assert "## Per-capita view" in insights
    assert "Exploratory" in insights
    assert (settings.paths["analytics"] / "05_delayed_per_100k.png").exists()


def test_curate_without_population_source_is_unchanged_in_shape(tmp_path):
    from tests import test_downstream

    settings = test_downstream.build_pipeline(tmp_path)
    _, report = curated.read_curated_report(settings)
    assert f"curated/{CURATED_POPULATION_FILE}" not in report["outputs"]
    assert "population_source" not in report


def challenge_page():
    return FakeResponse(403, b"<!DOCTYPE html><title>Just a moment...</title>", {"content-type": "text/html; charset=UTF-8"})


def test_robots_challenge_stops_by_default():
    client, session = client_for({API_ROBOTS: [challenge_page()], API_URL: [FakeResponse(200, b"{}")]})
    with pytest.raises(Exception, match="bot-protection challenge"):
        client.post(API_URL, {})
    assert [url for url, _ in session.calls] == [API_ROBOTS]


def test_documented_api_continues_past_a_robots_challenge_but_not_an_api_challenge():
    client, session = client_for({API_ROBOTS: [challenge_page()], API_URL: [FakeResponse(200, b"{}")]})
    client.robots_challenge = "documented_api"
    assert client.post(API_URL, {}).status_code == 200
    assert client.robots_log["https://stats.example.test"]["status"] == "challenge"
    client, session = client_for({API_ROBOTS: [challenge_page()], API_URL: [challenge_page()]})
    client.robots_challenge = "documented_api"
    with pytest.raises(Exception, match="bot-protection challenge on https://stats.example.test/PXWeb"):
        client.post(API_URL, {})


def test_api_source_records_the_robots_challenge_in_run_metadata(tmp_path):
    body = json.dumps(population_document()).encode("utf-8")
    settings = api_settings(tmp_path, sha256_bytes(body))
    settings.raw["api_sources"]["population"]["robots_txt_challenge"] = "documented_api"
    client, _ = client_for({API_ROBOTS: [challenge_page()], API_URL: [FakeResponse(200, body)]})
    client.robots_challenge = "documented_api"
    run, status = run_api_extract(settings, "population", client=client, now=TickingNow(), log=silent)
    assert status == "stored"
    assert run.read_metadata()["robots"]["https://stats.example.test"]["status"] == "challenge"


def test_documented_api_exception_needs_api_documentation(tmp_path):
    settings = api_settings(tmp_path, "c" * 64)
    settings.raw["api_sources"]["population"]["robots_txt_challenge"] = "documented_api"
    del settings.raw["api_sources"]["population"]["api_documentation_url"]
    with pytest.raises(FileSourceError, match="only use robots_txt_challenge: documented_api with an api_documentation_url"):
        run_api_extract(settings, "population", client=client_for({})[0], now=TickingNow(), log=silent)


def test_project_settings_configure_the_openstat_population_api():
    settings = load_settings()
    config = api_config(settings, "psa_openstat_population")
    assert config["robots_txt_challenge"] == "documented_api"
    assert config["api_url"].startswith("https://openstat.psa.gov.ph/PXWeb/api/v1/")
    assert request_payload(config)["response"] == {"format": "json"}
    assert config["region_match"] == "code"
    selections = {item["code"]: item["selection"] for item in config["query"]}
    assert selections["Parameter"] == {"filter": "item", "values": ["0"]}
    assert config["region_dimension"] in selections
    assert settings.raw["curated"]["population_source"] == "psa_openstat_population"
    assert population_settings(settings)[0] == "psa_openstat_population"


def test_population_matches_openstat_labels_and_skips_provinces_and_cities():
    labels = ["Philippines a", "..National Capital Region (NCR)", "....City of Manila", "....City of Makati 1/", "..Region I (Ilocos Region)", "....Ilocos Norte", "..Region IV-A (CALABARZON)", "..MIMAROPA Region", "....Benguet *"]
    document = population_document(labels=labels, values=list(range(1, len(labels) + 1)), years=("Total Population",))
    document["id"] = ["Geographic Location", "Parameter"]
    document["dimension"] = {
        "Geographic Location": document["dimension"]["Geolocation"],
        "Parameter": {"label": "Parameter", "category": {"index": {"0": 0}, "label": {"0": "Total Population"}}},
    }
    table, summary = build_region_population(jsonstat2_frame(document), regions_frame(), "Geographic Location", {"Region IV-B": "MIMAROPA"})
    assert dict(zip(table["psgc_code"], table["population"])) == {"0100000000": 5, "0400000000": 7, "1300000000": 2, "1700000000": 8}
    assert summary["psgc_regions_without_population"] == []
    assert summary["population_labels_not_regions"] == 5
    assert summary["population_period"] == "Parameter: Total Population"


PX_METADATA = {
    "title": "Total Population by Region",
    "variables": [
        {"code": "Geographic Location", "text": "Geographic Location", "values": ["0000000000", "1300000000", "1380600000", "0100000000", "0400000000", "1700000000"], "valueTexts": ["Philippines a", "..National Capital Region (NCR)", "....City of Manila", "..Region I (Ilocos Region)", "..Region IV-A (CALABARZON)", "..MIMAROPA Region"]},
        {"code": "Parameter", "text": "Parameter", "values": ["0", "1"], "valueTexts": ["Total Population", "Household Population"]},
    ],
}
PX_QUERY = [
    {"code": "Geographic Location", "selection": {"filter": "all", "values": ["*"]}},
    {"code": "Parameter", "selection": {"filter": "item", "values": ["0"]}},
]


def px_data(values=None):
    codes = PX_METADATA["variables"][0]["values"]
    values = values or ["112729484", "14001751", "1846513", "5342453", "16926599", ".."]
    return {
        "columns": [
            {"code": "Geographic Location", "text": "Geographic Location", "type": "d"},
            {"code": "Parameter", "text": "Parameter", "type": "d"},
            {"code": "Total Population", "text": "Total Population", "type": "c"},
        ],
        "comments": [],
        "data": [{"key": [code, "0"], "values": [value]} for code, value in zip(codes, values)],
    }


def px_settings(tmp_path, expected=""):
    settings = api_settings(tmp_path, expected)
    settings.raw["api_sources"]["population"].update({"response_format": "json", "query": PX_QUERY, "region_dimension": "Geographic Location", "region_match": "code", "metadata_file_name": "table_metadata.json"})
    return settings


def px_routes(data_body):
    return {
        API_ROBOTS: [FakeResponse(404, b"", {"content-type": "text/plain"})],
        API_URL: [FakeResponse(200, json.dumps(PX_METADATA).encode("utf-8")), FakeResponse(200, data_body)],
    }


def test_pxweb_frame_uses_metadata_labels_and_reads_missing_values():
    frame = pxweb_json_frame(px_data(), PX_METADATA, PX_QUERY)
    assert frame.attrs["dimensions"] == ["Geographic Location", "Parameter"]
    assert frame.loc[frame["Geographic Location_code"] == "1300000000", "Geographic Location"].iloc[0] == "..National Capital Region (NCR)"
    assert frame.loc[frame["Geographic Location_code"] == "1300000000", "value"].iloc[0] == 14001751
    assert frame["value"].isna().sum() == 1
    assert frame["Parameter"].unique().tolist() == ["Total Population"]


def test_pxweb_frame_rejects_a_response_with_missing_rows():
    document = px_data()
    document["data"] = document["data"][:1]
    with pytest.raises(PxWebError, match="expected 6 data rows for the query, found 1"):
        pxweb_json_frame(document, PX_METADATA, PX_QUERY)


def test_pxweb_frame_rejects_codes_outside_the_metadata():
    document = px_data()
    document["data"][0]["key"] = ["9999999999", "0"]
    with pytest.raises(PxWebError, match="not in the table metadata"):
        pxweb_json_frame(document, PX_METADATA, PX_QUERY)


def test_population_matches_pxweb_rows_on_psgc_codes():
    frame = pxweb_json_frame(px_data(), PX_METADATA, PX_QUERY)
    table, summary = build_region_population(frame, regions_frame(), "Geographic Location", match_on="code")
    assert table["psgc_code"].tolist() == ["0100000000", "0400000000", "1300000000", "1700000000"]
    assert table.loc[table["psgc_code"] == "1300000000", "source_label"].iloc[0] == "National Capital Region (NCR)"
    assert summary["population_regions_missing_value"] == 1
    assert summary["population_labels_not_regions"] == 2
    assert summary["population_region_match"] == "code"


def test_pxweb_api_source_stores_data_and_metadata(tmp_path):
    body = json.dumps(px_data()).encode("utf-8")
    settings = px_settings(tmp_path, sha256_bytes(body))
    client, session = client_for(px_routes(body))
    run, status = run_api_extract(settings, "population", client=client, now=TickingNow(), log=silent)
    assert status == "stored"
    assert (run.directory / "population_by_region.json").read_bytes() == body
    assert json.loads((run.directory / "table_metadata.json").read_text(encoding="utf-8")) == PX_METADATA
    assert {entry["file"] for entry in run.manifest_entries()} == {"population_by_region.json", "table_metadata.json"}
    assert session.posted[0]["response"] == {"format": "json"}
    assert run.read_metadata()["values"] == 6


def test_pxweb_api_source_first_run_reports_the_data_checksum(tmp_path):
    body = json.dumps(px_data()).encode("utf-8")
    client, _ = client_for(px_routes(body))
    with pytest.raises(FileSourceError, match=f"SHA-256 {sha256_bytes(body)}"):
        run_api_extract(px_settings(tmp_path), "population", client=client, now=TickingNow(), log=silent)


def test_pxweb_api_source_rejects_the_single_value_reply(tmp_path):
    document = px_data()
    document["data"] = [{"key": ["0000000000", "0"], "values": ["1"]}]
    client, _ = client_for(px_routes(json.dumps(document).encode("utf-8")))
    with pytest.raises(FileSourceError, match="expected 6 data rows"):
        run_api_extract(px_settings(tmp_path, "d" * 64), "population", client=client, now=TickingNow(), log=silent)


def test_curate_and_validate_with_a_pxweb_population_source(tmp_path):
    from src.transform import partition, staging
    from src.validate import pipeline
    from tests import test_downstream
    from tests.test_curated import make_psgc_run
    from tests.test_staging import make_raw_run

    raw_run = make_raw_run(tmp_path, test_downstream.frame_with_costs())
    psgc_run = make_psgc_run(tmp_path)
    settings = test_downstream.make_settings(tmp_path, raw_run.manifest_entries()[0]["sha256"], psgc_run.manifest_entries()[0]["sha256"])
    body = json.dumps(px_data()).encode("utf-8")
    settings.raw["api_sources"] = {"population": dict(px_settings(tmp_path, sha256_bytes(body)).raw["api_sources"]["population"])}
    settings.raw["curated"]["population_source"] = "population"
    run = RawRun.create(lane_root(tmp_path / "raw", "population"), now=lambda: FIXED)
    run.write("population_by_region.json", body, API_URL, 200, FIXED)
    run.write("table_metadata.json", json.dumps(PX_METADATA).encode("utf-8"), API_URL, 200, FIXED)
    staging.run_staging(settings, now=lambda: FIXED, log=silent, environ={})
    report, _ = curated.run_curated(settings, now=TickingNow(), log=silent, environ={})
    assert report["outputs"][f"curated/{CURATED_POPULATION_FILE}"]["rows"] == 4
    assert report["population_region_match"] == "code"
    table = pd.read_parquet(settings.paths["curated"] / f"run_id={report['staging_run_id']}" / CURATED_POPULATION_FILE)
    assert table.loc[table["psgc_code"] == "1300000000", "population"].iloc[0] == 14001751
    partition.build_partitions(settings, now=lambda: FIXED, log=silent, environ={})
    results = pipeline.run_validation(settings, skip_db=True, log=silent)
    assert results.failed() == 0


def test_per_capita_insights_leave_out_regions_without_contracts():
    from src.analytics.full_analysis import build_insights

    summary = {"curated_run_id": "R", "as_of": "2026-01-22", "ongoing": 10, "rows": 20, "delayed": 5, "delay_rate_pct": 50.0, "median_cost_delayed": None, "median_cost_on_schedule": None}
    per_capita = pd.DataFrame({
        "psgc_code": ["1400000000", "1300000000", "1900000000"],
        "region": ["CAR", "NCR", "BARMM"],
        "population": [1808985, 14001751, 4545486],
        "contracts": [9944, 16908, 0],
        "ongoing": [10, 20, 0],
        "delayed": [1087, 1560, 0],
        "delayed_per_100k": [60.09, 11.14, 0.0],
    })
    insights = build_insights(summary, pd.DataFrame(), None, None, per_capita, "Parameter: Total Population")
    assert "fewest: NCR at 11.14" in insights
    assert "No contracts in the source for: BARMM" in insights

