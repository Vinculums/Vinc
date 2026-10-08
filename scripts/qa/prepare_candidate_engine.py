"""후보 엔진의 공식 확장 바이트를 식별하고 동결 빌드 입력을 준비한다.

출하 핀은 변경하지 않는다. 근거: [[vinc.ops.ladybug-upgrade-review-2026-09-24]].
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
from pathlib import Path
import shutil
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--engine", choices=("0.20.3", "0.20.4", "0.21.2"), default="0.21.2")
    parser.add_argument("--no-export", action="store_true")
    parser.add_argument("--vendor-dir", type=Path, help="Previous release assets checked against the tracked manifest")
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=False)
    root = args.root.resolve()
    home = root / "build-home"
    home.mkdir()
    os.environ.update(HOME=str(home), USERPROFILE=str(home))
    openssl = None
    if sys.platform == "win32":
        names = ("libssl-3-x64.dll", "libcrypto-3-x64.dll")
        candidates = [Path(os.environ.get("VINC_OPENSSL_DIR", "")),
                      Path("C:/Program Files/Git/mingw64/bin"),
                      Path("C:/Program Files/OpenSSL/bin"),
                      Path("C:/Program Files/OpenSSL-Win64/bin")]
        openssl = next((p for p in candidates if all((p / n).is_file() for n in names)), None)
        if openssl is None:
            raise RuntimeError("OpenSSL 3 build inputs missing")
        spec = importlib.util.find_spec("ladybug")
        package = Path(spec.origin).parent
        for name in names:
            shutil.copy2(openssl / name, package / name)
    elif sys.platform == "darwin":
        openssl = next((p for p in (Path("/opt/homebrew/opt/openssl@3/lib"),
                                   Path("/usr/local/opt/openssl@3/lib"))
                        if (p / "libssl.3.dylib").is_file()), None)
        if openssl is None:
            raise RuntimeError("OpenSSL 3 build inputs missing")
    import ladybug
    if ladybug.__version__ != args.engine:
        raise RuntimeError("qualification requires the exact requested engine")
    bundle = root / "extensions"
    bundle.mkdir()
    release_assets = None
    candidate_checksums = None
    if args.engine == "0.21.2":
        # 실제 검증한 바이트를 고정한다. 상류가 같은 URL을 바꾸면 새 검증 없이 넘기지 않는다.
        checksum_path = Path(__file__).resolve().parents[2] / "packaging/lbug-ext-0.21.2.sha256"
        candidate_checksums = dict(line.split()[::-1] for line in checksum_path.read_text().splitlines() if line.strip())
        release_assets = root / "release-assets"
        release_assets.mkdir()
        shutil.copy2(checksum_path, release_assets / checksum_path.name)
    manifest = []
    db = connection = None
    try:
        if args.vendor_dir:
            if args.engine not in ("0.20.3", "0.20.4"):
                raise RuntimeError("vendored release assets only belong to the previous engine")
            target_platform = {"win32": "win_amd64", "linux": "linux_amd64", "darwin": "osx_arm64"}[sys.platform]
            expected_machine = "arm64" if sys.platform == "darwin" else "amd64"
            machine = platform.machine().lower().replace("x86_64", "amd64").replace("aarch64", "arm64")
            if machine != expected_machine:
                raise RuntimeError("release assets do not match the host architecture")
            checksums = Path(__file__).resolve().parents[2] / "packaging/lbug-ext-0.20.4.sha256"
            expected = dict(line.split()[::-1] for line in checksums.read_text().splitlines() if line.strip())
        else:
            db = ladybug.Database(str(root / "install.db"), buffer_pool_size=128 * 1024**2,
                                  max_num_threads=2)
            connection = ladybug.Connection(db)
        for name in ("fts", "vector", "algo"):
            if args.vendor_dir:
                asset = f"{target_platform}-lib{name}.lbug_extension"
                source = args.vendor_dir / asset
                digest = hashlib.sha256(source.read_bytes()).hexdigest()
                if digest != expected[asset]:
                    raise RuntimeError(f"previous release {name} checksum mismatch")
                target_cache = home / ".lbdb/extension/0.20.0" / target_platform / name / f"lib{name}.lbug_extension"
                target_cache.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target_cache)
                source = target_cache
            else:
                connection.execute(f"INSTALL {name}")
                matches = list((home / ".lbdb" / "extension").glob(f"*/*/{name}/lib{name}.lbug_extension"))
                if len(matches) != 1:
                    raise RuntimeError(f"ambiguous or absent {name} extension")
                source = matches[0]
            if source.stat().st_size < 10000:
                raise RuntimeError(f"invalid {name} extension size")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            if candidate_checksums is not None:
                asset = f"{source.parent.parent.name}-{source.name}"
                if digest != candidate_checksums.get(asset):
                    raise RuntimeError(f"candidate {asset} checksum changed; requalification required")
                shutil.copy2(source, release_assets / asset)
            target = bundle / source.name
            shutil.copy2(source, target)
            manifest.append({"extension": name, "abi_path": str(source.relative_to(home)),
                             "bytes": source.stat().st_size,
                             "sha256": digest})
    finally:
        if connection is not None:
            connection.close()
        if db is not None:
            db.close()
    receipt = {"candidate": ladybug.__version__, "source_sha": os.environ.get("GITHUB_SHA"),
               "platform": sys.platform, "python": sys.version,
               "extensions": manifest, "production_pins_changed": False,
               "extension_source": "ladybug-ext-0.20.4" if args.vendor_dir else "official INSTALL"}
    (root / "manifest.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    export = {"HOME": str(home), "USERPROFILE": str(home), "VINC_LBUG_EXT_DIR": str(bundle),
              "VINC_BUFFER_POOL_MB": "128", "VINC_MAX_DB_SIZE": str(1024**3)}
    if openssl:
        export["VINC_OPENSSL_DIR"] = str(openssl)
    if os.environ.get("GITHUB_ENV") and not args.no_export:
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as output:
            for key, value in export.items():
                output.write(f"{key}={value}\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
