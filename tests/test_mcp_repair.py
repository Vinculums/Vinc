# -*- coding: utf-8 -*-
"""`mcp repair` — 우리가 적은 항목이 없어진 파일을 가리킬 때만 고쳐 쓴다.

이 파일이 있는 이유: 2026-09-18 에 repair 를 실제 기계에서 한 번 돌렸다가 사람이
손으로 적어 둔 **원격 항목**(`{"url": ...}`)을 로컬 실행 항목으로 덮었다. 백업에서
되돌렸지만, 그 판정("command 가 없으면 우리 것이 아니다")이 코드에만 있으면 다음에
또 없어진다. 근거: [[record:a-code-comment-is-not-evidence-for-a-documentation-claim-2026-09-18]]
와 같은 종류의 실패다 — 규칙은 실행되는 자리에 둔다.
"""
import json
import sys

from vinc_mcp.install import repair_json


def _write(tmp_path, entry):
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps({"mcpServers": {"vinc": entry, "other": {"command": "keep-me"}}}),
                 encoding="utf-8")
    return p


def test_repair_rewrites_a_command_whose_file_is_gone(tmp_path):
    cfg = _write(tmp_path, {"command": str(tmp_path / "gone" / "vinc-sidecar.exe"),
                            "args": ["mcp", "serve"]})
    out = repair_json("cursor", cfg)
    assert out["repaired"] is True
    got = json.loads(cfg.read_text(encoding="utf-8"))
    assert got["mcpServers"]["vinc"]["command"] == sys.executable
    # 남의 항목은 그대로 둔다
    assert got["mcpServers"]["other"] == {"command": "keep-me"}


def test_repair_leaves_a_remote_entry_alone(tmp_path):
    """`url` 만 있는 항목은 사람이 적은 것이다. 덮으면 되던 연결이 끊긴다."""
    remote = {"url": "https://mcp.vincs.io/mcp"}
    cfg = _write(tmp_path, dict(remote))
    out = repair_json("cursor", cfg)
    assert out["repaired"] is False
    assert json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]["vinc"] == remote


def test_repair_leaves_a_live_path_alone(tmp_path):
    """가리키는 파일이 실제로 있으면 손대지 않는다(멱등)."""
    cfg = _write(tmp_path, {"command": sys.executable, "args": ["mcp", "serve"]})
    before = cfg.read_text(encoding="utf-8")
    out = repair_json("cursor", cfg)
    assert out["repaired"] is False
    assert cfg.read_text(encoding="utf-8") == before


def test_repair_makes_a_backup_before_it_writes(tmp_path):
    cfg = _write(tmp_path, {"command": str(tmp_path / "gone"), "args": []})
    out = repair_json("cursor", cfg)
    assert out["repaired"] is True
    backup = out["backup"]
    assert json.loads(open(backup, encoding="utf-8").read())["mcpServers"]["vinc"]["command"] \
        == str(tmp_path / "gone")


def test_repair_is_quiet_when_there_is_no_config(tmp_path):
    out = repair_json("cursor", tmp_path / "nope.json")
    assert out["repaired"] is False
