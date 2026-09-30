import pytest

from src import config
from src.cli import normalize_name, read_pins


def full_environ():
    return {name: "value" for name in config.REQUIRED_ENV_VARS}


def test_load_settings_resolves_relative_paths():
    settings = config.load_settings()
    assert settings.paths["raw"] == config.PROJECT_ROOT / "data" / "raw"
    assert set(settings.paths) >= {"source", "raw", "staging", "curated", "quarantine", "benchmarks", "partitioned"}


def test_load_settings_rejects_absolute_paths(tmp_path):
    settings_file = tmp_path / "settings.yml"
    settings_file.write_text(
        "project: {}\npaths:\n  raw: /var/data/raw\ndatabase: {}\nrequired_packages: []\n",
        encoding="utf-8",
    )
    with pytest.raises(config.ConfigError):
        config.load_settings(settings_path=settings_file, env_path=tmp_path / ".env")


def test_load_settings_rejects_missing_sections(tmp_path):
    settings_file = tmp_path / "settings.yml"
    settings_file.write_text("project: {}\n", encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load_settings(settings_path=settings_file, env_path=tmp_path / ".env")


def test_missing_env_vars_reports_missing_and_placeholders():
    environ = full_environ()
    environ["POSTGRES_PASSWORD"] = "change_me"
    del environ["POSTGRES_DB"]
    assert set(config.missing_env_vars(environ)) == {"POSTGRES_PASSWORD", "POSTGRES_DB"}


def test_build_db_url_keeps_password_but_hides_it_when_rendered():
    environ = full_environ()
    environ["POSTGRES_PORT"] = "5432"
    environ["POSTGRES_PASSWORD"] = "s3cret!@"
    url = config.build_db_url(environ)
    assert url.password == "s3cret!@"
    assert "s3cret" not in url.render_as_string(hide_password=True)


def test_build_db_url_fails_without_required_variables():
    with pytest.raises(config.ConfigError):
        config.build_db_url({})


def test_read_pins_parses_exact_pins_only(tmp_path):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("pandas==3.0.6\nPyYAML==6.0.3\nrequests>=2\n\n# note\n", encoding="utf-8")
    pins = read_pins(requirements)
    assert pins == {"pandas": "3.0.6", "pyyaml": "6.0.3"}


def test_normalize_name_treats_separators_alike():
    assert normalize_name("psycopg2_binary") == normalize_name("psycopg2-binary")
