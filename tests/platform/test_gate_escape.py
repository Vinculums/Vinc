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


def test_scope_is_every_ts_file_under_app_src():
    """표면 밖(문서 편집기·시트·첫 실행·명령 팔레트)도 훑는다."""
    got = {p.relative_to(gate_escape.ROOT).as_posix() for p in gate_escape.FILES}
    on_disk = {p.relative_to(gate_escape.ROOT).as_posix()
               for p in (gate_escape.ROOT / "app" / "src").rglob("*.ts")}
    assert got == on_disk and len(got) > 100
    for rel in ("app/src/main.ts", "app/src/node-basics.ts", "app/src/surfaces/view.ts"):
        assert rel in got, rel


def test_without_the_allow_list_the_scan_reaches_files_outside_surfaces(monkeypatch):
    """허용 항목을 비우면 표면 밖 파일에서 걸린다 — 범위가 실제로 넓어졌다는 증거."""
    monkeypatch.setattr(gate_escape, "ALLOW", {})
    hits = gate_escape.scan()
    files = {h.split(":")[0] for h in hits}
    assert {"app/src/main.ts", "app/src/firstrun.ts", "app/src/node-basics.ts",
            "app/src/agent-writes.ts"} <= files, hits


def test_allow_entries_are_live():
    """허용 항목마다 그 줄이 파일에 그대로 있고, 허용 없이 훑으면 실제로 걸린다.
    줄이 바뀌었거나 더는 걸리지 않는 항목은 죽은 항목이라 지운다."""
    assert gate_escape.ALLOW
    for (rel, line), why in gate_escape.ALLOW.items():
        assert why.strip(), (rel, line)
        lines = (gate_escape.ROOT / rel).read_text(encoding="utf-8").split("\n")
        assert sum(1 for x in lines if x.strip() == line) == 1, (rel, line)
        bare = gate_escape.scan_lines(rel, lines, allow={})
        n = next(i for i, x in enumerate(lines, 1) if x.strip() == line)
        assert any(h.startswith("%s:%d " % (rel, n)) for h in bare), (rel, line)


def test_allow_covers_only_its_own_line():
    """같은 파일의 다른 줄에 같은 모양의 구멍이 생기면 허용이 덮지 못한다."""
    rel = "app/src/firstrun.ts"
    other = ['el.innerHTML = `',
             '<h1 class="si-title" id="signin-title">${c.title}</h1> ',
             '<h2>${node.title}</h2>`;']
    hits = gate_escape.scan_lines(rel, other)
    assert len(hits) == 1 and "node.title" in hits[0], hits
