# -*- coding: utf-8 -*-
"""노드 원장 검사기 — `ledger-nodes.tsv` 의 형상·값공간·참조 무결성.

문서 축에서 배운 것: 도구가 못 보는 결함은 반드시 다시 생긴다. `lint.py` 는 문서
원장과 재작성본만 보므로 노드 원장은 아무도 안 봤고, 그래서 별도로 둔다.

검사하는 것
  1. 열 9개 고정 · 헤더 일치
  2. kind 값공간 4종 · disposition 값공간 3종
  3. new_domain 은 SCHEME 7종. 비어도 되는 경우는 bridge(구조 노드)와 drop 뿐
  4. title_en·note_en 필수 · em-dash/en-dash/이모지 금지 · title_en 유일
  5. note_en 은 영어여야 한다(한글이 남아 있으면 번역 누락)
  6. extra 의 attach 대상이 실재하는 id 여야 한다
     (이 원장 · ADR 원장의 구 id · 문서 스텁 concept:doc.*)
  7. 인벤토리와 id 집합 1:1(dec·doc 스텁 제외분)

검사하지 않는 것: 의미. 제목이 내용에 맞는지는 사람이 본다.
"""
from __future__ import annotations

import io
import pathlib
import re
import sys
import unicodedata

BASE = pathlib.Path(__file__).parent
HEADER = ["id", "kind", "new_domain", "title_en", "note_en",
          "disposition", "old_title", "old_domain", "extra"]
KINDS = {"concept", "bridge", "episode", "record"}
DISPOSITIONS = {"keep", "rewrite", "drop"}
DOMAINS = {"vinc/design", "vinc/infra", "vinc/engineering", "vinc/product",
           "vinc/governance", "vinc/ai", "vinc/security"}
BANNED_CHARS = {"—": "em-dash", "–": "en-dash"}


def rows(path: pathlib.Path) -> list:
    return [l.rstrip("\n").split("\t") for l in io.open(path, encoding="utf-8") if l.strip()]


def is_emoji(ch: str) -> bool:
    return unicodedata.category(ch) == "So"


def main() -> int:
    problems = []

    def bad(line_no, node_id, msg):
        problems.append("L%-4d %-52s %s" % (line_no, node_id, msg))

    data = rows(BASE / "ledger-nodes.tsv")
    if data[0] != HEADER:
        print("헤더 불일치:\n  기대 %s\n  실제 %s" % (HEADER, data[0]))
        return 1

    # 참조 가능한 id 집합
    known = {r[0] for r in data[1:]}
    known |= {r[0] for r in rows(BASE / "ledger-adr.tsv")[1:]}          # 구 dec- id
    known |= {"concept:" + r[0] for r in rows(BASE / "ledger-adr.tsv")[1:]}
    known |= {"concept:doc." + r[0] for r in rows(BASE / "ledger-documents.tsv")[1:]}

    titles = {}
    for n, r in enumerate(data[1:], start=2):
        nid = r[0]
        if len(r) != 9:
            bad(n, nid, "열이 %d 개(9 이어야 함)" % len(r))
            continue
        _, kind, dom, title, note, disp, _old_t, _old_d, extra = r

        if kind not in KINDS:
            bad(n, nid, "kind 값공간 밖: %r" % kind)
        if disp not in DISPOSITIONS:
            bad(n, nid, "disposition 값공간 밖: %r" % disp)

        if dom:
            if dom not in DOMAINS:
                bad(n, nid, "new_domain 이 SCHEME 7종 밖: %r" % dom)
        elif not (kind == "bridge" or disp == "drop"):
            bad(n, nid, "new_domain 이 비었다(bridge·drop 만 허용)")

        if not title.strip():
            bad(n, nid, "title_en 비었음")
        if not note.strip():
            bad(n, nid, "note_en 비었음")

        for field, val in (("title_en", title), ("note_en", note)):
            for ch, name in BANNED_CHARS.items():
                if ch in val:
                    bad(n, nid, "%s 에 %s" % (field, name))
            for ch in val:
                if is_emoji(ch):
                    bad(n, nid, "%s 에 이모지 %r" % (field, ch))
            if re.search(r"[가-힣]", val):
                bad(n, nid, "%s 에 한글이 남아 있다(번역 누락)" % field)

        if title.strip():
            if title in titles:
                bad(n, nid, "title_en 이 L%d 과 중복" % titles[title])
            else:
                titles[title] = n

        for part in extra.split(";"):
            part = part.strip()
            if part.startswith("attach="):
                part = part[len("attach="):]
            elif "=" in part or not part.startswith(("concept:", "record:", "episode:", "bridge:")):
                continue
            if part and part not in known:
                bad(n, nid, "attach 대상 미해결: %s" % part)

    # 인벤토리 대조
    inv = rows(BASE / "inventory" / "nodes.tsv")[1:]
    expected = {r[0] for r in inv
                if not r[0].startswith("concept:dec-") and not r[0].startswith("concept:doc.")}
    have = {r[0] for r in data[1:]}
    for missing in sorted(expected - have):
        problems.append("%-57s 인벤토리에 있는데 원장에 없다" % missing)
    for extra_id in sorted(have - expected):
        problems.append("%-57s 원장에 있는데 인벤토리에 없다" % extra_id)

    if problems:
        print("위반 %d 건\n" % len(problems))
        for p in problems:
            print("  " + p)
        return 1

    print("노드 원장 %d 행 · 위반 0" % (len(data) - 1))
    from collections import Counter
    for k, v in Counter(r[1] for r in data[1:]).most_common():
        print("  %-9s %3d" % (k, v))
    return 0


if __name__ == "__main__":
    sys.exit(main())
