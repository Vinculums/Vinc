"""잘못된 AppImage fallback과 서명 누락을 릴리스 전에 막는다."""
import json

import pytest

from scripts.linux_updater_fragment import complete_linux_fragment


def setup_assets(tmp_path):
    generic = {"url": "https://example.invalid/Vinc_0.9.7_amd64.AppImage", "signature": "synthetic-appimage-signature"}
    (tmp_path / "latest-linux-x86_64.json").write_text(json.dumps({"linux-x86_64": generic}))
    for suffix in ("deb", "rpm"):
        (tmp_path / f"Vinc_0.9.7_amd64.{suffix}").write_bytes(b"synthetic-payload")
        (tmp_path / f"Vinc_0.9.7_amd64.{suffix}.sig").write_text(f"synthetic-{suffix}-signature")
    (tmp_path / "Vinc-amd64.deb").write_bytes(b"synthetic-alias")
    return generic


def test_format_keys_select_the_signed_matching_package_and_keep_fallback(tmp_path):
    generic = setup_assets(tmp_path)
    platforms = complete_linux_fragment(tmp_path, "v0.9.7", "Vinculums/Vincs-app")
    assert platforms["linux-x86_64"] == platforms["linux-x86_64-appimage"] == generic
    for suffix in ("deb", "rpm"):
        entry = platforms[f"linux-x86_64-{suffix}"]
        assert entry["url"] == f"https://github.com/Vinculums/Vincs-app/releases/download/v0.9.7/Vinc_0.9.7_amd64.{suffix}"
        assert entry["signature"] == f"synthetic-{suffix}-signature"
    assert complete_linux_fragment(tmp_path, "v0.9.7", "Vinculums/Vincs-app") == platforms


@pytest.mark.parametrize("suffix", ["deb", "rpm"])
def test_missing_or_empty_format_signature_blocks_release(tmp_path, suffix):
    setup_assets(tmp_path)
    signature = tmp_path / f"Vinc_0.9.7_amd64.{suffix}.sig"
    signature.unlink()
    with pytest.raises(ValueError, match=f"signed {suffix}"):
        complete_linux_fragment(tmp_path, "v0.9.7", "Vinculums/Vincs-app")
    signature.write_text("")
    with pytest.raises(ValueError, match=f"empty {suffix}"):
        complete_linux_fragment(tmp_path, "v0.9.7", "Vinculums/Vincs-app")
