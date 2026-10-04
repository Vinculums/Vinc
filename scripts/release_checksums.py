"""최종 릴리스 자산의 체크섬과 기존 업데이터 공개키를 내보낸다."""
import base64
import hashlib
import json
from pathlib import Path
import sys


def write_checksums(directory: Path, config: Path) -> None:
    key = json.loads(config.read_text(encoding="utf-8"))["plugins"]["updater"]["pubkey"]
    public = base64.b64decode(key, validate=True)
    if not public.startswith(b"untrusted comment:"):
        raise ValueError("updater public key must be a minisign public key")
    (directory / "release-signing.pub").write_bytes(public)
    excluded = {"SHA256SUMS", "SHA256SUMS.sig", "SHA256SUMS.minisig"}
    rows = []
    for asset in sorted(directory.iterdir()):
        if asset.name in excluded:
            continue
        if not asset.is_file() or asset.is_symlink() or any(c in asset.name for c in "\r\n\\"):
            raise ValueError(f"unsafe release asset name: {asset.name!r}")
        if asset.name.startswith("latest-") and asset.suffix == ".json":
            raise ValueError("release still contains intermediate updater fragments")
        with asset.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        rows.append(f"{digest}  {asset.name}\n")
    if not any(row.endswith("  latest.json\n") for row in rows):
        raise ValueError("final updater manifest is missing")
    (directory / "SHA256SUMS").write_text("".join(rows), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    write_checksums(Path(sys.argv[1]), Path(sys.argv[2]))
