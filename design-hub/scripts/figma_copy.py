# -*- coding: utf-8 -*-
"""figma_copy.py — 라이브 페이지의 문구를 Figma Locale 변수 형태로 뽑는다.

`figma_inventory.py` 가 화면이 **있는지**를 보고 `figma_diff.py` 가 토큰 **값**을 본다면,
이쪽은 화면 **안의 글자**를 옮긴다. 정본은 코드다([[decision:code-is-canon-figma-follows]]).

왜 도구가 필요한가. 두 쪽의 키 이름이 안 맞는다 — 사이트는 `data-i="sub"`, Figma 는
`dl/heroBody` 다. 35쪽을 손으로 대응시키면 반드시 몇 개가 샌다. 실제로 랜딩 하나에서만
48개 중 39개가 달랐고 그중 둘은 **카피 게이트가 금지한 문구**였다(`air gap` ·
`never touches your data`). Figma 에서 문구를 가져다 쓰면 그게 마케팅으로 되돌아간다.

    python -X utf8 scripts/figma_copy.py --emit /download/     # 적용할 값을 JSON 으로
    python -X utf8 scripts/figma_copy.py --routes              # 대응표에 있는 라우트 목록

`--emit` 의 출력은 `{ "<Figma 변수 이름>": {"EN": …, "KO": …} }` 이고, 적용은 에이전트가
`use_figma` 로 한다(이 스크립트는 Figma 에 쓰지 않는다).

## 급소

- **EN 은 마크업에, KO 는 페이지 하단 JS 딕셔너리에 있다.** 사이트는 영어를 HTML 에
  인라인으로 두고 한국어를 `data-i` 키로 덮어쓴다. 그래서 추출도 두 경로다.
- **`<br>` 은 줄바꿈으로 바꾼다.** Figma 텍스트는 `\n` 을 그대로 받는다.
- 대응표에 없는 라이브 키는 **Figma 에 변수가 없다는 뜻**이고, 그건 결손이지 오류가
  아니다. `--emit` 이 `missing_in_figma` 로 함께 보고한다.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

HUB = pathlib.Path(__file__).resolve().parent.parent
SITE = HUB.parent.parent / "Vinc-homepage" / "site"

#: 라우트 → (Figma 변수 접두, {Figma 키 꼬리: 라이브 data-i 키}).
#: 값이 None 이면 "라이브에서 사라진 Figma 변수"다 — 지우기 전에 눈으로 확인하라고 남긴다.
MAP: dict[str, tuple[str, dict[str, str | None]]] = {
    "/download/": ("dl/", {
        "h1": "h1", "heroBody": "sub", "versionBadge": "verLabel", "primaryBtn": "dlGeneric",
        "detected": "detectGeneric", "notice": "notice",
        "macReq": "macReq", "macNote": "macFoot",
        "winReq": "winReq", "winFmt": "winExe", "winNote": "winFoot",
        "linuxReq": "linuxReq", "linuxNote": "linuxFoot",
        "assure": "assure", "firstRunT": "frT", "firstRunS": "frLead",
        "macStep1": "frMac1", "macStep2": "frMac2", "macStep3": "frMac3",
        "winStep1": "frWin1", "winStep2": "frWin2", "winStep3": "frWin3",
        "yourDevice": None,
    }),
    "/pricing/": ("pricing/", {
        "h1": "h1", "heroBody": "sub",
        "plan1Name": "tFree",
        # ⚠ 라이브의 `pFree` 는 태그라인이 아니라 **가격(₩0)** 이다. 이름이 비슷해서
        # 한 번 잘못 이었다. Figma 의 태그라인 줄은 라이브에 대응이 없으므로 결손이고,
        # 가격 줄은 Figma 에 변수가 없다(정적 텍스트).
        "plan1Tagline": None,
        "plan1Item1": "fFree1", "plan1Item2": "fFree2", "plan1Item3": "fFree3", "plan1Item4": "fFree4",
        "plan1Cta": "ctaFree",
        "plan2Name": "tPro", "plan2Badge": "tagPro", "plan2Period": "perYear", "plan2Tagline": "trialOn",
        "plan2Item1": "fPro1", "plan2Item2": "fPro2", "plan2Item3": "fPro3", "plan2Cta": "ctaPro",
        "plan3Name": "tTeam", "plan3Period": "perSeatYear", "plan3Tagline": "perTeam",
        "plan3Item1": "fTeam1", "plan3Item2": "fTeam2", "plan3Item3": "fTeam3", "plan3Item4": "fTeam4",
        "plan3Cta": "ctaTeam",
        "taxNote": "note", "faqTitle": "faqT",
        "faq1Q": "q1", "faq1A": "a1", "faq2Q": "q2", "faq2A": "a2",
        "faq3Q": "q3", "faq3A": "a3", "faq4Q": "q4", "faq4A": "a4",
        "segYearly": "cyYear", "segMonthly": "cyMonth",
    }),
    "/community/": ("community/", {
        "h1a": "h1", "heroBody": "lede",
        "shareCta": "shareCta", "shareNote": "shareNote",
        "sortRecent": "sRecent",
        "cardBadge": "mkOfficial", "cardImport": "mGo",
        "h1b": None, "searchPlaceholder": None, "resultCount": None,
        "tag1": None, "tag2": None, "tag3": None, "tag4": None, "cardAuthor": None,
        "card1Domain": None, "card1Title": None, "card1Meta": None, "card1Imports": None,
        "card2Domain": None, "card2Title": None, "card2Meta": None, "card2Imports": None,
        "card3Domain": None, "card3Title": None, "card3Meta": None, "card3Imports": None,
        "card4Domain": None, "card4Title": None, "card4Meta": None, "card4Imports": None,
    }),
}


def _strip(h: str) -> str:
    h = re.sub(r"<br\s*/?>", "\n", h)
    h = re.sub(r"<[^>]+>", "", h)
    h = h.replace("&amp;", "&").replace("&nbsp;", " ")
    return re.sub(r"[ \t]+", " ", h).strip()


def extract(path: pathlib.Path) -> dict:
    """한 페이지에서 (EN, KO) 를 뽑는다. EN 은 마크업, KO 는 딕셔너리."""
    src = path.read_text(encoding="utf-8")
    en: dict[str, str] = {}
    for m in re.finditer(r'<(\w+)([^>]*\bdata-i="([A-Za-z0-9_]+)"[^>]*)>', src):
        tag, key, start = m.group(1), m.group(3), m.end()
        depth, i = 1, start
        while depth and i < len(src):
            nxt = re.search(r"<(/?)" + tag + r"\b", src[i:])
            if not nxt:
                break
            i += nxt.end()
            depth += -1 if nxt.group(1) else 1
        close = src.rfind("<", start, i)
        en.setdefault(key, _strip(src[start:close if close > start else start]))
    bs = chr(92)
    ko: dict[str, str] = {}
    for m in re.finditer(r"(\w+)\s*:\s*'((?:[^'" + bs + bs + r"]|" + bs + bs + r".)*)'", src):
        v = m.group(2)
        if re.search(r"[가-힣]", v):
            ko.setdefault(m.group(1), _strip(v.replace(bs + "'", "'")))
    return {"en": en, "ko": ko}


def page_path(route: str) -> pathlib.Path:
    return SITE / (route.strip("/") + "/index.html" if route != "/" else "index.html")


def emit(route: str) -> dict:
    prefix, keymap = MAP[route]
    live = extract(page_path(route))
    out, gone, missing = {}, [], []
    for tail, livekey in keymap.items():
        if livekey is None:
            gone.append(prefix + tail)
            continue
        en, ko = live["en"].get(livekey), live["ko"].get(livekey)
        if en is None and ko is None:
            gone.append(prefix + tail + " (라이브 키 " + livekey + " 없음)")
            continue
        out[prefix + tail] = {"EN": en, "KO": ko}
    mapped = {v for v in keymap.values() if v}
    for k in sorted(set(live["en"]) | set(live["ko"])):
        if k not in mapped and not k.startswith("f"):   # f* 는 공용 푸터 링크다
            missing.append(k)
    return {"route": route, "values": out, "figma_only": gone, "missing_in_figma": missing}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="라이브 문구를 Figma Locale 형태로 뽑는다")
    ap.add_argument("--emit", metavar="ROUTE")
    ap.add_argument("--routes", action="store_true")
    a = ap.parse_args(argv)
    if a.routes:
        for r in MAP:
            print(r)
        return 0
    if not a.emit:
        ap.print_help()
        return 2
    # ⚠ Git Bash 의 MSYS 경로 변환이 `/download/` 를 `D:/git/download/` 로 바꿔치기한다
    # (CLAUDE.md §5 의 그 함정). 그래서 슬래시 없는 이름도 받고, 받은 값의 마지막
    # 두 조각만 보고 라우트를 되찾는다.
    want = a.emit.strip().rstrip("/").split("/")
    route = None
    for r in MAP:
        parts = r.strip("/").split("/")
        if want[-len(parts):] == parts:
            route = r
            break
    if route is None:
        print(f"[ERROR] 대응표에 없는 라우트: {a.emit}\n        가능한 값: {', '.join(MAP)}",
              file=sys.stderr)
        return 2
    print(json.dumps(emit(route), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
