"""Materialize a share snapshot through the product's acknowledged-generation recovery path."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from store import persistence  # noqa: E402


def restore_downloaded(source: Path, destination: Path, *, durable_ack: bool) -> dict:
    """Never open the downloaded backup or retreat behind its latest committed ACK."""
    if destination.exists() or Path(f"{destination}.wal").exists():
        raise RuntimeError("recovery destination already exists")
    previous = os.environ.get(persistence.DURABLE_ENV)
    os.environ[persistence.DURABLE_ENV] = "on" if durable_ack else "off"
    try:
        state = persistence.restore_newest(str(destination), str(source))
    finally:
        if previous is None:
            os.environ.pop(persistence.DURABLE_ENV, None)
        else:
            os.environ[persistence.DURABLE_ENV] = previous
    if not state.startswith("restored") or not destination.is_file() or not destination.stat().st_size:
        raise RuntimeError(f"snapshot recovery refused: {state}")
    files = []
    for path in (destination, Path(f"{destination}.wal")):
        if path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            files.append({"part": "wal" if path != destination else "base",
                          "bytes": path.stat().st_size, "sha256": digest.hexdigest()})
    return {"state": state, "durable_ack": durable_ack, "files": files}


def download_snapshot(account: str, share: str, snapshot: str, source: str, raw: Path) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", source) or ".." in source:
        raise ValueError("source must be a single safe file name")
    if not snapshot:
        raise ValueError("an immutable share snapshot is required")
    raw.mkdir(parents=True, exist_ok=False)
    environment = dict(os.environ)
    # Keys never enter argv, printed reports, or command-error diagnostics.
    if environment.get("AZ_KEY"):
        environment["AZURE_STORAGE_KEY"] = environment["AZ_KEY"]
    common = ["--account-name", account, "--snapshot", snapshot, "--only-show-errors"]

    def az(arguments: list[str]):
        result = subprocess.run(["az", "storage", *arguments, *common], env=environment,
                                capture_output=True, text=True, check=False)
        if result.returncode:
            raise RuntimeError(f"share snapshot {arguments[0]} {arguments[1]} failed")
        return result.stdout

    listing = json.loads(az(["file", "list", "--share-name", share, "--output", "json"]))
    if not isinstance(listing, list):
        raise RuntimeError("unexpected share snapshot listing")
    names = {item["name"]: item for item in listing}
    for suffix in ("", ".wal", ".prev", ".prev.wal", ".generation-seen"):
        name = source + suffix
        if name in names:
            if names[name].get("isDirectory"):
                raise RuntimeError("file recovery cannot use a directory database")
            az(["file", "download", "--share-name", share, "--path", name,
                "--dest", str(raw / name), "--output", "none"])
    generation = source + persistence.GENERATIONS_SUFFIX
    if generation in names:
        if not names[generation].get("isDirectory"):
            raise RuntimeError("generation root is not a directory")
        az(["file", "download-batch", "--source", share, "--destination", str(raw),
            "--pattern", generation + "/*", "--no-progress", "--output", "none"])
    return raw / source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True)
    parser.add_argument("--share", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--durable-ack", choices=("on", "off"), required=True)
    args = parser.parse_args()
    # A fresh raw directory and destination retain failed downloads for diagnosis.
    source = download_snapshot(args.account, args.share, args.snapshot, args.source,
                               args.out.parent / "share-snapshot")
    report = restore_downloaded(source, args.out, durable_ack=args.durable_ack == "on")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
