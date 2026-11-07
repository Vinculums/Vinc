# -*- coding: utf-8 -*-
"""gate_design_sync.py — 토큰 생성물이 **손으로 고쳐졌는지** 본다.

`app/src/{theme.ts,tokens.desktop.css,tokens.web.css}` 는 `scripts/design/build_tokens.py` 가
토큰 스냅샷(`scripts/design/tokens.json`, 값의 정본은 그래프)에서 만들어 쓴 사본이다. 헤더에
"Do not hand-edit" 이 적혀 있지만 **헤더는 강제하지 않는다** — 실제로 아무 CI 도 이 파일들을
보지 않았고, 누가 색 하나를 여기서 고치면 다음 재생성 때 조용히 되돌아간다(그리고
그 사이에 배포된 것은 SSoT 와 다른 색이다).

`scripts/design/sync_out.py --apply` 가 쓸 때 헤더에 본문 해시를 각인한다. 이 게이트는
그 각인이 본문과 맞는지만 본다 — 같은 게이트가 이웃 리포(Vinc-homepage)에도 있어서,
거기서는 Vinc 를 체크아웃하지 않고 이것 하나로 판정한다.

무엇을 잡고 무엇을 못 잡는가(정직하게):

  ✅ 사본을 손으로 고친 것 — 각인과 본문이 어긋난다
  ✅ 각인이 사라진 것 — 생성기를 거치지 않고 파일이 만들어졌다는 뜻
  ❌ 스냅샷이 앞서갔는데 동기화를 안 한 것 — 사본은 제 안에서 일관되므로 여기선 안
     보인다. 그건 `python -X utf8 scripts/design/sync_out.py --check` 가 잡는다.

    python scripts/gate_design_sync.py

exit 0 = 이상 없음 · 1 = 위반.
"""
from __future__ import annotations

import hashlib
import re
import sys

try:
    from scripts._root import ROOT
except ImportError:  # 직접 실행: 리포 루트가 아직 sys.path 에 없다
    from _root import ROOT

#: 생성기가 쓰는 파일들. scripts/design/sync_out.py 의 MAP 과 짝이다 — 한쪽만 늘리면
#: 새 파일이 검사 밖에 남으므로, 여기 없는 생성물이 app/src 에 있으면 경고한다.
SYNCED = ["app/src/theme.ts", "app/src/tokens.desktop.css", "app/src/tokens.web.css"]

STAMP_KEY = "sync-sha256"
_STAMP_RE = re.compile(rf"^[ \t]*(?://|/\*)[ \t]*{STAMP_KEY}:[ \t]*([0-9a-f]{{64}}).*\r?\n",
                       re.MULTILINE)
_GEN_MARK = "AUTO-GENERATED"


def _body_hash(text: str) -> str:
    return hashlib.sha256(_STAMP_RE.sub("", text).encode("utf-8")).hexdigest()


def main() -> int:
    bad = 0
    for rel in SYNCED:
        fp = ROOT / rel
        if not fp.exists():
            print(f"[MISSING] {rel} — scripts/design/sync_out.py --apply 로 써야 한다")
            bad += 1
            continue
        text = fp.read_text(encoding="utf-8")
        m = _STAMP_RE.search(text)
        if not m:
            print(f"[NO-STAMP] {rel} — 각인이 없다. 생성기를 거치지 않고 만들어졌거나 "
                  f"각인 줄이 지워졌다 (scripts/design/sync_out.py --apply)")
            bad += 1
            continue
        got = _body_hash(text)
        if got != m.group(1):
            print(f"[TAMPERED] {rel} — 각인 {m.group(1)[:12]}… 와 본문 {got[:12]}… 이 다르다. "
                  f"이 파일은 생성물이라 여기서 고치면 안 된다: "
                  f"값은 그래프의 토큰 노드에서 바꾸고 pull_tokens.py → sync_out.py --apply")
            bad += 1
        else:
            print(f"[OK] {rel}")

    # 등재되지 않은 생성물이 섞여 들어오는 것도 본다 — SYNCED 가 낡으면 검사에 구멍이 난다.
    known = {(ROOT / r).resolve() for r in SYNCED}
    for fp in sorted((ROOT / "app" / "src").rglob("*")):
        if not fp.is_file() or fp.suffix not in (".ts", ".css") or fp.resolve() in known:
            continue
        try:
            head = "".join(fp.open(encoding="utf-8", errors="ignore").readlines()[:3])
        except OSError:
            continue
        if _GEN_MARK in head:
            print(f"[UNLISTED] {fp.relative_to(ROOT)} 가 생성물인데 SYNCED 목록에 없다 "
                  f"— 이 게이트와 sync_out.py MAP 에 함께 등재하라")
            bad += 1

    print(f"\n{'디자인 동기화 이상 없음' if not bad else f'문제 {bad}건'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
