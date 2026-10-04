"""다운로드 바이트·별칭·재실행과 불완전한 릴리스를 검증한다."""
import base64
import hashlib
import json

import pytest

from scripts.release_checksums import write_checksums


def fixture_config(tmp_path):
    config = tmp_path / "config.json"
    key = base64.b64encode(b"untrusted comment: synthetic fixture\nfixture-public-key\n").decode()
    config.write_text(json.dumps({"plugins": {"updater": {"pubkey": key}}}))
    return config


def test_all_payloads_and_aliases_are_hashed_and_repeat_is_stable(tmp_path):
    config = fixture_config(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    for name in ("Vinc_0.9.7_amd64.deb", "Vinc-amd64.deb", "Vinc-cli-linux-x86_64.tar.gz", "latest.json"):
        (assets / name).write_bytes(b"synthetic downloaded bytes")
    write_checksums(assets, config)
    first = (assets / "SHA256SUMS").read_bytes()
    (assets / "SHA256SUMS.sig").write_bytes(b"old signature")
    (assets / "SHA256SUMS.minisig").write_bytes(b"old clear signature")
    write_checksums(assets, config)
    assert (assets / "SHA256SUMS").read_bytes() == first
    entries = dict(line.split("  ", 1)[::-1] for line in first.decode().splitlines())
    assert set(entries) == {"Vinc_0.9.7_amd64.deb", "Vinc-amd64.deb", "Vinc-cli-linux-x86_64.tar.gz", "latest.json", "release-signing.pub"}
    for name, digest in entries.items():
        assert digest == hashlib.sha256((assets / name).read_bytes()).hexdigest()
    (assets / "Vinc-amd64.deb").write_bytes(b"tampered")
    assert entries["Vinc-amd64.deb"] != hashlib.sha256((assets / "Vinc-amd64.deb").read_bytes()).hexdigest()


def test_incomplete_release_is_rejected(tmp_path):
    config = fixture_config(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    with pytest.raises(ValueError, match="final updater manifest"):
        write_checksums(assets, config)
    (assets / "latest.json").write_text("{}")
    (assets / "latest-linux-x86_64.json").write_text("{}")
    with pytest.raises(ValueError, match="intermediate updater"):
        write_checksums(assets, config)
