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


def extract(args):
    from src.extract.dpwh_projects import ExtractError, run_extract
    from src.extract.raw_store import RawStoreError

    try:
        settings = load_settings()
        _, status = run_extract(settings, max_pages=args.max_pages, resume_run_id=args.resume)
    except (ConfigError, ExtractError, RawStoreError) as exc:
        print(f"[FAIL] {exc}")
        return 1
    return 0 if status in ("complete", "capped") else 1


def extract_file(args):
    from src.extract.file_source import FileSourceError, extract_sources
    from src.extract.raw_store import RawStoreError

    try:
        settings = load_settings()
        extract_sources(settings=settings, sources=None if args.source == "all" else [args.source])
    except (ConfigError, FileSourceError, RawStoreError) as exc:
        print(f"[FAIL] {exc}")
        return 1
    return 0


def stage(args):
    from src.extract.raw_store import RawStoreError
    from src.transform.staging import StagingError, run_staging

    try:
        settings = load_settings()
        run_staging(settings, source_name=args.source, run_id=args.run_id, rebuild=args.rebuild)
    except (ConfigError, RawStoreError, StagingError) as exc:
        print(f"[FAIL] {exc}")
        return 1
    return 0


def curate(args):
    from src.extract.raw_store import RawStoreError
    from src.transform.curated import CuratedError, run_curated

    try:
        settings = load_settings()
        run_curated(settings, run_id=args.run_id, rebuild=args.rebuild)
    except (ConfigError, RawStoreError, CuratedError) as exc:
        print(f"[FAIL] {exc}")
        return 1
    return 0

def describe(exc):
    message = str(exc).strip()
    first = message.splitlines()[0] if message else ""
    return f"{type(exc).__name__}: {first}" if first else type(exc).__name__


def load(args):
    from src.load.postgres import LoadError, load_to_postgres

    try:
        settings = load_settings()
        load_to_postgres(settings, run_id=args.run_id)
    except (ConfigError, LoadError) as exc:
        print(f"[FAIL] {exc}")
        return 1
    except Exception as exc:
        print(f"[FAIL] Database load failed: {describe(exc)}")
        return 1
    return 0


def init_db(args):
    from src.load.postgres import LoadError, apply_schema

    try:
        apply_schema(load_settings())
    except (ConfigError, LoadError) as exc:
        print(f"[FAIL] {exc}")
        return 1
    except Exception as exc:
        print(f"[FAIL] Schema could not be applied: {describe(exc)}")
        return 1
    return 0


def partition(args):
    from src.transform.partition import PartitionError, build_partitions

    try:
        build_partitions(load_settings(), run_id=args.run_id, rebuild=args.rebuild)
    except (ConfigError, PartitionError) as exc:
        print(f"[FAIL] {exc}")
        return 1
    return 0


def load_partition(args):
    from src.load.partitions import load_partition as run_load_partition

    try:
        run_load_partition(load_settings(), args.year, args.month)
    except Exception as exc:
        print(f"[FAIL] Partition load failed: {describe(exc)}")
        return 1
    return 0


def validate(args):
    from src.validate.pipeline import run_validation

    if args.month is not None and args.year is None:
        print("[FAIL] --month needs --year")
        return 1
    try:
        results = run_validation(load_settings(), skip_db=args.skip_db, year=args.year, month=args.month)
    except ConfigError as exc:
        print(f"[FAIL] {exc}")
        return 1
    return results.failed()


def benchmark(args):
    from src.benchmark.formats import run_benchmarks

    try:
        run_benchmarks(load_settings(), run_id=args.run_id, skip_db=args.skip_db)
    except Exception as exc:
        print(f"[FAIL] Benchmarking failed: {describe(exc)}")
        return 1
    return 0


def analyze(args):
    from src.analytics.full_analysis import run_analytics

    try:
        run_analytics(load_settings(), run_id=args.run_id)
    except Exception as exc:
        print(f"[FAIL] Analytics failed: {describe(exc)}")
        return 1
    return 0


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be 1 or greater")
    return number


def month_number(value):
    number = int(value)
    if not 1 <= number <= 12:
        raise argparse.ArgumentTypeError("must be between 1 and 12")
    return number


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
    extract_parser = subparsers.add_parser(
        "extract",
        help="Fetch DPWH project listing pages politely and store them unchanged in the raw layer.",
    )
    extract_parser.add_argument(
        "--max-pages",
        type=positive_int,
        default=None,
        help="Stop after this many new pages in this session (use a small number for test runs).",
    )
    extract_parser.add_argument(
        "--resume",
        metavar="RUN_ID",
        default=None,
        help="Continue an earlier run, skipping pages already stored and verified.",
    )
    extract_parser.set_defaults(handler=extract)
    file_parser = subparsers.add_parser(
        "extract-file",
        help="Verify a published data file against its recorded SHA-256 and store it unchanged in the raw layer.",
    )
    file_parser.add_argument(
        "--source",
        default="bettergov_hf",
        help="Name of the file source in config/settings.yml, or 'all' (default: bettergov_hf).",
    )
    file_parser.set_defaults(handler=extract_file)
    stage_parser = subparsers.add_parser(
        "stage",
        help="Type, normalise and validate a raw run into the staging layer, sending unusable rows to quarantine.",
    )
    stage_parser.add_argument(
        "--source",
        default="bettergov_hf",
        help="Raw source lane to stage (default: bettergov_hf).",
    )
    stage_parser.add_argument(
        "--run-id",
        default=None,
        help="Raw run to stage (default: the latest run in the source lane).",
    )
    stage_parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Stage the run again even if staging output already exists.",
    )
    stage_parser.set_defaults(handler=stage)
    curate_parser = subparsers.add_parser(
        "curate",
        help="Build the curated layer from a staging run: PSGC region check, metrics, delay flag and record_hash.",
    )
    curate_parser.add_argument(
        "--run-id",
        default=None,
        help="Staging run to curate (default: the latest staging run of the primary source).",
    )
    curate_parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Curate the run again even if curated output already exists.",
    )
    curate_parser.set_defaults(handler=curate)
    load_parser = subparsers.add_parser(
        "load",
        help="Load the curated contracts into PostgreSQL with a hash-guarded upsert; rows the table cannot accept go to quarantine.",
    )
    load_parser.add_argument("--run-id", default=None, help="Curated run to load (default: the latest).")
    load_parser.set_defaults(handler=load)
    init_parser = subparsers.add_parser(
        "init-db",
        help="Apply the SQL files in sql/init to the database (idempotent; Docker applies them automatically on a new volume).",
    )
    init_parser.set_defaults(handler=init_db)
    partition_parser = subparsers.add_parser(
        "partition",
        help="Write the curated contracts as Hive-style Parquet partitions by start year and month.",
    )
    partition_parser.add_argument("--run-id", default=None, help="Curated run to partition (default: the latest).")
    partition_parser.add_argument("--rebuild", action="store_true", help="Write the partitions again even if they are current.")
    partition_parser.set_defaults(handler=partition)
    load_partition_parser = subparsers.add_parser(
        "load-partition",
        help="Load one partition slice into PostgreSQL and log the run to audit.partition_loads.",
    )
    load_partition_parser.add_argument("--year", required=True, type=int, help="Start year of the slice (YYYY).")
    load_partition_parser.add_argument("--month", type=month_number, default=None, help="Start month of the slice (1-12); omit to load the whole year.")
    load_partition_parser.set_defaults(handler=load_partition)
    check_parser = subparsers.add_parser(
        "validate",
        help="Check raw manifests, cross-layer row counts, calculation invariants, partitions and database hashes.",
    )
    check_parser.add_argument("--skip-db", action="store_true", help="Skip the database checks.")
    check_parser.add_argument("--year", type=int, default=None, help="Limit the database check to one partition year.")
    check_parser.add_argument("--month", type=month_number, default=None, help="Limit the database check to one partition month (needs --year).")
    check_parser.set_defaults(handler=validate)
    bench_parser = subparsers.add_parser(
        "benchmark",
        help="Benchmark CSV, JSON Lines, Parquet (Snappy, Zstandard) and PostgreSQL on the curated contracts.",
    )
    bench_parser.add_argument("--run-id", default=None, help="Curated run to benchmark (default: the latest).")
    bench_parser.add_argument("--skip-db", action="store_true", help="Skip the PostgreSQL benchmark.")
    bench_parser.set_defaults(handler=benchmark)
    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Descriptive statistics, charts and a delay model on the curated contracts (outputs in data/analytics).",
    )
    analyze_parser.add_argument("--run-id", default=None, help="Curated run to analyse (default: the latest).")
    analyze_parser.set_defaults(handler=analyze)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    failed = args.handler(args)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
