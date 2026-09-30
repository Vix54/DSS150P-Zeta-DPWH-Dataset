import argparse
import importlib.metadata as metadata
import os
import re
import subprocess
import sys

from sqlalchemy import create_engine, text

from src.config import PROJECT_ROOT, ConfigError, build_db_url, load_settings, missing_env_vars

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


def normalize_name(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def read_pins(requirements_path):
    pins = {}
    for line in requirements_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;]+)$", line)
        if match:
            pins[normalize_name(match.group(1))] = match.group(2)
    return pins


def running_in_container():
    return os.path.exists("/.dockerenv")


def check_python_version(settings):
    minimum = tuple(int(part) for part in str(settings.raw["project"]["python_min_version"]).split("."))
    current = sys.version_info[: len(minimum)]
    found = ".".join(str(part) for part in sys.version_info[:3])
    needed = ".".join(str(part) for part in minimum)
    status = PASS if current >= minimum else FAIL
    return status, f"Python version {found} (minimum required {needed})"


def check_packages(settings):
    requirements_path = PROJECT_ROOT / "requirements.txt"
    if not requirements_path.exists():
        return [(FAIL, "requirements.txt not found, cannot verify pinned packages")]
    pins = read_pins(requirements_path)
    results = []
    for package in settings.raw["required_packages"]:
        pinned = pins.get(normalize_name(package))
        try:
            installed = metadata.version(package)
        except metadata.PackageNotFoundError:
            results.append((FAIL, f"Package {package} is not installed"))
            continue
        if pinned is None:
            results.append((FAIL, f"Package {package} {installed} is installed but not pinned in requirements.txt"))
        elif installed != pinned:
            results.append((FAIL, f"Package {package} installed {installed} but pinned {pinned}"))
        else:
            results.append((PASS, f"Package {package}=={installed} matches requirements.txt"))
    return results


def check_environment_variables():
    missing = missing_env_vars()
    if missing:
        return FAIL, f"Environment variables missing or still placeholders: {', '.join(missing)}"
    return PASS, "All required environment variables are set"


def check_data_directories(settings):
    results = []
    for name, path in settings.paths.items():
        label = path.relative_to(PROJECT_ROOT)
        if path.is_dir():
            results.append((PASS, f"Directory {label} exists"))
        else:
            results.append((FAIL, f"Directory {label} is missing"))
    return results


def check_env_file_ignored():
    if not (PROJECT_ROOT / ".git").exists():
        return SKIP, ".env git-ignore check skipped (not a git checkout)"
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", ".env"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return SKIP, ".env git-ignore check skipped (git unavailable)"
    if result.returncode == 0:
        return PASS, ".env is ignored by git"
    if result.returncode == 1:
        return FAIL, ".env is NOT ignored by git (or is already tracked)"
    return SKIP, ".env git-ignore check skipped (git returned an unexpected status)"


def check_database(settings):
    try:
        url = build_db_url()
    except ConfigError as exc:
        return FAIL, f"Database check cannot start: {exc}"
    timeout = int(settings.raw["database"]["connect_timeout_seconds"])
    target = url.render_as_string(hide_password=True)
    engine = create_engine(url, connect_args={"connect_timeout": timeout})
    try:
        with engine.connect() as connection:
            version = connection.execute(text("SELECT version()")).scalar()
    except Exception as exc:
        reason = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        return FAIL, f"Database connection to {target} failed: {reason}"
    finally:
        engine.dispose()
    return PASS, f"Database connection to {target} succeeded ({version.split(',')[0]})"


def report(results):
    for status, message in results:
        print(f"[{status}] {message}")
    passed = sum(1 for status, _ in results if status == PASS)
    failed = sum(1 for status, _ in results if status == FAIL)
    skipped = sum(1 for status, _ in results if status == SKIP)
    print(f"Result: {passed} passed, {failed} failed, {skipped} skipped")
    return failed


def validate_env(args):
    environment = "container" if running_in_container() else "local host"
    print(f"Execution environment: {environment}")
    print(f"Project root: {PROJECT_ROOT}")

    try:
        settings = load_settings()
    except ConfigError as exc:
        return report([(FAIL, f"Settings could not be loaded: {exc}")])

    results = [(PASS, "config/settings.yml loaded and validated")]
    results.append(check_python_version(settings))
    results.extend(check_packages(settings))
    results.append(check_environment_variables())
    results.extend(check_data_directories(settings))
    results.append(check_env_file_ignored())
    if args.check_db:
        results.append(check_database(settings))
    else:
        results.append((SKIP, "Database connection check skipped (use --check-db)"))
    return report(results)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m src.cli",
        description="Unified command line entry point for the DPWH data pipeline.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser(
        "validate-env",
        help="Verify the Python environment, pinned packages, configuration, and optionally the database.",
    )
    validate_parser.add_argument(
        "--check-db",
        action="store_true",
        help="Also attempt a PostgreSQL connection using the .env settings.",
    )
    validate_parser.set_defaults(handler=validate_env)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    failed = args.handler(args)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
