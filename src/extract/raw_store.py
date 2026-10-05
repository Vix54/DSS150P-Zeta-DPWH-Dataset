import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

MANIFEST_NAME = "manifest.jsonl"
METADATA_NAME = "run.json"
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]+")


class RawStoreError(Exception):
    pass


def utc_now():
    return datetime.now(timezone.utc)


def utc_iso(moment):
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_bytes(content):
    return hashlib.sha256(content).hexdigest()


class RawRun:
    def __init__(self, root, run_id):
        self.root = Path(root)
        self.run_id = run_id
        self.directory = self.root / f"run_id={run_id}"
        self.manifest_path = self.directory / MANIFEST_NAME
        self.metadata_path = self.directory / METADATA_NAME

    @classmethod
    def create(cls, root, now=utc_now, run_id=None):
        run_id = run_id or now().strftime("%Y%m%dT%H%M%SZ")
        if not RUN_ID_PATTERN.fullmatch(run_id):
            raise RawStoreError(f"run_id '{run_id}' may only contain letters, digits, '.', '_' and '-'")
        run = cls(root, run_id)
        try:
            run.directory.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise RawStoreError(f"run directory already exists: {run.directory}") from exc
        return run

    @classmethod
    def open_existing(cls, root, run_id):
        run = cls(root, run_id)
        if not run.directory.is_dir():
            raise RawStoreError(f"no raw run found for run_id {run_id} under {run.root}")
        return run

    def manifest_entries(self):
        if not self.manifest_path.exists():
            return []
        entries = []
        for line in self.manifest_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append(json.loads(line))
        return entries

    def verified_files(self):
        verified = set()
        for entry in self.manifest_entries():
            path = self.directory / entry["file"]
            if not path.exists():
                raise RawStoreError(f"manifest lists {entry['file']} but the file is missing")
            if sha256_bytes(path.read_bytes()) != entry["sha256"]:
                raise RawStoreError(f"checksum mismatch for {entry['file']}; raw data was modified")
            verified.add(entry["file"])
        return verified

    def write(self, relative_path, content, url, status_code, fetched_at):
        target = self.directory / relative_path
        if target.exists():
            raise RawStoreError(f"refusing to overwrite raw file {relative_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".part")
        temporary.write_bytes(content)
        os.replace(temporary, target)
        entry = {
            "file": relative_path,
            "url": url,
            "status_code": status_code,
            "bytes": len(content),
            "sha256": sha256_bytes(content),
            "fetched_at_utc": utc_iso(fetched_at),
        }
        with open(self.manifest_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def read_metadata(self):
        if not self.metadata_path.exists():
            return {}
        return json.loads(self.metadata_path.read_text(encoding="utf-8"))

    def write_metadata(self, metadata):
        temporary = self.metadata_path.with_name(METADATA_NAME + ".part")
        temporary.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self.metadata_path)
