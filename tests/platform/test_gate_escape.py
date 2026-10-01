# -*- coding: utf-8 -*-
"""gate_escape — 감사 M9 의 실제 구멍을 잡는가, 그리고 지금은 깨끗한가."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import gate_escape  # noqa: E402


def test_repo_is_clean():
    assert gate_escape.main() == 0


def test_catches_the_holes_it_was_written_for():
    """고치기 **전** 코드 그대로. 다섯 중 view 계열 셋을 재현한다."""
    before = [
        'box.innerHTML = `<p class="hint">x</p>` +',
        '  sug.map((s) => `<span class="title">${s.title}',
        '    ${s.shared_tags.length ? `<span>· ${s.shared_tags.join(", ")}</span>` : ""}</span>`);',
    ]
    hits = gate_escape.scan_lines("view.ts", before)
    assert len(hits) >= 2, hits
    assert any("s.title" in h for h in hits)
    assert any("shared_tags" in h for h in hits)


def test_attribute_break_out_is_caught():
    before = ['badgeBox.innerHTML = hubs.map((n) =>',
              '  ` data-tip="${COPY.ui.viewx.badgeTip(n.label, 0)}">x</span>`).join("");']
    assert gate_escape.scan_lines("view2d.ts", before), "속성 탈출을 놓쳤다"


def test_escaped_form_passes():
    after = ['box.innerHTML = `<span>${esc(s.title)}</span>`;']
    assert gate_escape.scan_lines("view.ts", after) == []


def test_internally_escaping_helper_is_not_flagged():
    """underlineTerms 는 안에서 esc 한다 — 거짓 양성이면 게이트가 무시당한다."""
    ok = ['el.innerHTML = `<p>${underlineTerms(e.excerpt, nd.label)}</p>`;']
    assert gate_escape.scan_lines("view.ts", ok) == []
