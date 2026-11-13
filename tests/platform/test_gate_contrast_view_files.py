# -*- coding: utf-8 -*-
"""gate_contrast 의 물러남 리터럴 검사가 지금 그리는 표면을 실제로 읽는가.

- `_VIEW_FILES` 의 이름은 모두 `app/src/surfaces/` 에 있어야 한다. 없는 이름이 섞이면 그 몫은
  아무것도 읽지 않는다.
- 목록의 파일이 없으면 건너뛰지 않고 발견으로 내고, 게이트는 1 로 끝난다.
- 목록의 파일에 심은 물러남 리터럴은 잡힌다(읽기가 살아 있다).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import gate_contrast  # noqa: E402

APP_SRC = ROOT / "app" / "src"
THEME = APP_SRC / "theme.ts"


def _app_src(tmp_path: Path, files: dict[str, str]) -> Path:
    """`tmp_path/surfaces/` 에 주어진 파일만 둔 가짜 app/src 를 만든다."""
    surfaces = tmp_path / "surfaces"
    surfaces.mkdir()
    for name, text in files.items():
        (surfaces / name).write_text(text, encoding="utf-8")
    return tmp_path


def test_every_listed_view_file_exists_in_the_app():
    missing = [n for n in gate_contrast._VIEW_FILES if not (APP_SRC / "surfaces" / n).is_file()]
    assert missing == [], f"_VIEW_FILES 에 없는 파일이 있다: {missing}"


def test_the_app_view_files_carry_no_recede_literal():
    assert gate_contrast.run_recede(APP_SRC) == []


def test_a_missing_listed_file_is_a_finding(tmp_path):
    first, *rest = gate_contrast._VIEW_FILES
    findings = gate_contrast.run_recede(_app_src(tmp_path, {n: "" for n in rest}))
    assert len(findings) == 1, findings
    assert findings[0].startswith(first + ":"), findings


def test_the_gate_fails_when_a_listed_file_is_missing(tmp_path, capsys):
    code = gate_contrast.main(["--theme", str(THEME), "--app-src", str(_app_src(tmp_path, {}))])
    assert code == 1
    out = capsys.readouterr().out
    for name in gate_contrast._VIEW_FILES:
        assert name in out


def test_a_recede_literal_in_a_listed_file_is_caught(tmp_path):
    *_, last = gate_contrast._VIEW_FILES
    files = {n: "" for n in gate_contrast._VIEW_FILES}
    files[last] = "mesh.material.opacity = base * 0.15;\n"
    findings = gate_contrast.run_recede(_app_src(tmp_path, files))
    assert len(findings) == 1, findings
    assert findings[0].startswith(f"{last}:1 "), findings
