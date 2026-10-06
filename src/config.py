import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv
from sqlalchemy.engine import URL

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yml"
ENV_PATH = PROJECT_ROOT / ".env"

REQUIRED_ENV_VARS = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "SCRAPER_CONTACT",
)
DB_ENV_VARS = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
)
PLACEHOLDER_VALUES = {"", "change_me"}
REQUIRED_SECTIONS = ("project", "paths", "database", "required_packages")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    raw: dict
    paths: dict


def load_settings(settings_path=SETTINGS_PATH, env_path=ENV_PATH):
    load_dotenv(env_path, override=False)
    try:
        with open(settings_path, encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"settings file not found: {settings_path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"settings file is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("settings file must contain a mapping at the top level")

    missing = [section for section in REQUIRED_SECTIONS if section not in raw]
    if missing:
        raise ConfigError(f"settings file is missing sections: {', '.join(missing)}")

    paths = {}
    for name, value in raw["paths"].items():
        if Path(value).is_absolute():
            raise ConfigError(f"path '{name}' must be relative to the project root")
        paths[name] = PROJECT_ROOT / value

    return Settings(raw=raw, paths=paths)


def missing_env_vars(environ=None, names=REQUIRED_ENV_VARS):
    environ = os.environ if environ is None else environ
    missing = []
    for name in names:
        value = environ.get(name)
        if value is None or value.strip().lower() in PLACEHOLDER_VALUES:
            missing.append(name)
    return missing


def build_db_url(environ=None):
    environ = os.environ if environ is None else environ
    missing = missing_env_vars(environ, DB_ENV_VARS)
    if missing:
        raise ConfigError(f"missing or placeholder environment variables: {', '.join(missing)}")
    return URL.create(
        drivername="postgresql+psycopg2",
        username=environ["POSTGRES_USER"],
        password=environ["POSTGRES_PASSWORD"],
        host=environ["POSTGRES_HOST"],
        port=int(environ["POSTGRES_PORT"]),
        database=environ["POSTGRES_DB"],
    )
