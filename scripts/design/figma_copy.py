# -*- coding: utf-8 -*-
"""figma_copy.py — 라이브 페이지의 문구를 Figma Locale 변수 형태로 뽑는다.

`figma_inventory.py` 가 화면이 **있는지**를 보고 `figma_diff.py` 가 토큰 **값**을 본다면,
이쪽은 화면 **안의 글자**를 옮긴다. 정본은 코드다([[decision:code-is-canon-figma-follows]]).

왜 도구가 필요한가. 두 쪽의 키 이름이 안 맞는다 — 사이트는 `data-i="sub"`, Figma 는
`dl/heroBody` 다. 35쪽을 손으로 대응시키면 반드시 몇 개가 샌다. 실제로 랜딩 하나에서만
48개 중 39개가 달랐고 그중 둘은 **카피 게이트가 금지한 문구**였다(`air gap` ·
`never touches your data`). Figma 에서 문구를 가져다 쓰면 그게 마케팅으로 되돌아간다.

    python -X utf8 scripts/design/figma_copy.py --emit /download/     # 적용할 값을 JSON 으로
    python -X utf8 scripts/design/figma_copy.py --routes              # 대응표에 있는 라우트 목록

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
import html
import json
import os
import pathlib
import re
import sys

HUB = pathlib.Path(__file__).resolve().parent
#: 사이트가 어디 있는가. 기본은 두 리포가 나란히 놓인 로컬 배치다. **CI 는 배치가 다르다** —
#: 소비 리포의 잡은 자기 리포를 루트에, Vinc 를 그 아래 하위 디렉터리로 체크아웃하므로
#: 상대경로가 성립하지 않는다. 그래서 밖에서 지정할 수 있어야 한다. 이 한 줄이 없으면
#: 게이트는 "사이트가 없다"며 영원히 SKIP 하고, 초록인 채로 아무것도 안 지킨다.
SITE = pathlib.Path(os.environ["VINC_SITE_DIR"]).resolve() if os.environ.get("VINC_SITE_DIR")     else HUB.parent.parent.parent / "Vinc-homepage" / "site"

#: 라우트 → (Figma 변수 접두, {Figma 키 꼬리: 라이브 data-i 키}).
#: 값이 None 이면 "라이브에서 사라진 Figma 변수"다 — 지우기 전에 눈으로 확인하라고 남긴다.
#: 값이 키의 튜플이면 그 조각들을 페이지 순서대로 빈 줄 하나(`\n\n`)로 이어 한 변수로 본다
#: (`_joined`). 라이브는 문단마다 키가 따로이고 Figma 는 절마다 한 덩어리인 자리에 쓴다.
MAP: dict[str, tuple[str, dict[str, str | tuple[str, ...] | None]]] = {
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
        # 2026-11 에 요금제가 셋으로 다시 짜였다(Curious · Genius · Enterprise). 카드는
        # **자리 순서**로 잇는다 — plan1 은 첫 카드, plan2 는 둘째, plan3 은 셋째다. 그래야
        # 카드에 묶인 노드가 이름을 바꾸지 않고 새 문구를 받는다.
        # ⚠ 라이브의 `pCur`·`pEnt` 는 태그라인이 아니라 **가격**이다(정적 텍스트, 변수 없음).
        # 태그라인은 `per*` 줄이다.
        "plan1Name": "tCur", "plan1Tagline": "perCur",
        "plan1Item1": "fCur1", "plan1Item2": "fCur2", "plan1Item3": "fCur3", "plan1Item4": "fCur4",
        "plan1Item5": "fCur5", "plan1Cta": "ctaCur",
        "plan2Name": "tGen", "plan2Tagline": "perGen",
        "plan2Item1": "fGen1", "plan2Item2": "fGen2", "plan2Item3": "fGen3", "plan2Item4": "fGen4",
        "plan2Item5": "fGen5", "plan2Cta": "ctaGenSoon",
        "plan3Name": "tEnt", "plan3Tagline": "perEnt",
        "plan3Item1": "fEnt1", "plan3Item2": "fEnt2", "plan3Item3": "fEnt3", "plan3Cta": "ctaEnt",
        # Figma 전용: 라이브 문구가 없고, 90 · Archive 의 보드가 이 변수를 묶는다.
        "plan3Period": None, "plan3Item4": None,
        # 체험 배너는 애초에 카드의 태그라인이 아니라 **페이지 레벨 배너**다.
        "trialBanner": "trialOn",
        "taxNote": "note", "faqTitle": "faqT",
        "faq1Q": "q1", "faq1A": "a1", "faq2Q": "q2", "faq2A": "a2",
        "faq3Q": "q3", "faq3A": "a3", "faq4Q": "q4", "faq4A": "a4",
        "faq5Q": "q5", "faq5A": "a5", "faq6Q": "q6", "faq6A": "a6",
        "faq7Q": "q7", "faq7A": "a7", "faq8Q": "q8", "faq8A": "a8",
        "segYearly": "cyYear", "segMonthly": "cyMonth",
    }),
    "/login/": ("login/", {
        "signinTitle": "h1", "signinBody": "sub", "signinFine": "note",
        "sentTitle": "sentT", "sentBody": "sentB",
    }),
    # ⚠ 환영 화면의 변수는 `welcome/` 이 아니라 `login/` 접두를 쓴다. 두 라우트가
    # Figma 에서 한 프레임(Auth · Login & Welcome)에 살기 때문이다.
    "/welcome/": ("login/", {
        "welcomeTitle": "h1", "welcomeBody": "sub", "usernameRule": "rules",
        "skipFine": "foot", "loading": "loading",
        "usernameAvailable": None,
    }),
    "/auth/verify/": ("verify/", {
        "confirmTitle": "confirmT", "confirmBody": "confirmB", "confirmFine": "confirmNote",
        "verifying": "verifyingT", "contextTitle": "ctxT",
        "expiredTitle": "expiredT", "expiredBody": "expiredB",
        "invalidTitle": "invalidT", "invalidBody": "invalidB",
        "errorTitle": "errT", "errorBody": "errB",
        "contextBody": None,
    }),
    "/subscribe/confirm/": ("sub/", {
        "confirmTitle": "confirmT", "confirmBody": "confirmB", "confirmFine": "confirmNote",
        "confirming": "confirmingT", "confirmedTitle": "doneT",
        "expiredTitle": "expiredT", "expiredBody": "expiredB",
        "errorTitle": "errT", "errorBody": "errB",
    }),
    "/unsubscribe/": ("sub/", {
        "unsubTitle": "confirmT", "unsubBody": "confirmB",
        "unsubscribing": "unsubbingT", "unsubbedTitle": "doneT", "unsubbedBody": "doneB",
        "unsubInvalidTitle": "invalidT", "unsubInvalidBody": "invalidB",
        "unsubErrorTitle": "errT", "unsubErrorBody": "errB",
    }),
    "/account/delete/": ("del/", {
        "confirmTitle": "confirmT", "confirmBody": "confirmB",
        "deleting": "deletingT", "doneTitle": "deletedT", "doneBody": "deletedB",
        "expiredTitle": "expiredT", "expiredBody": "expiredB",
        "errorTitle": "errT", "errorBody": "errB",
    }),
    # ⚠ `/connect/`(어시스턴트 커넥터)와 `/connect-app/`(데스크탑 앱 링크)는 Figma 에서
    # 한 프레임에 나란히 살고 접두도 `connect/` 하나를 공유한다. 그래서 두 라우트가
    # 같은 접두로 서로 다른 꼬리를 쓴다.
    "/connect/": ("connect/", {
        "loadTitle": "loadT", "loadBody": "loadB",
        "askTitle": "askT", "askBody": "askB",
        "kvApp": "kApp", "kvReturn": "kSend", "askWarn": "warnB",
        "loginTitle": "loginT", "loginBody": "loginB",
        "doneTitle": "doneT", "doneBody": "doneB",
        "errTitle": "errT", "errBody": "errB",
        "footNote": "foot",
    }),
    "/connect-app/": ("connect/", {
        "appAskTitle": "askT", "appAskBody": "askB", "appAskWarn": "warnB",
        "kvAccount": "kAcct", "kvDelivered": "kDest", "kvKey": "kKey",
        "appDoneBody": "doneB",
        "manualTitle": "manT", "manualBody": "manB", "manualWarn": "manW",
    }),
    "/account/": ("account/", {
        "usernameDesc": "unB", "packsTitle": "cpT", "packsDesc": "cpB",
        "planTitle": "proT", "connectorDesc": "mkB", "keyWarn": "onceLabel",
        "newsDesc": "nlB", "deleteTitle": "delT", "deleteDesc": "delB",
        # 아래는 Figma 가 표본 데이터로 들고 있는 것이라 라이브 문구가 없다.
        # 플랜 설명은 이제 플랜마다 스크립트가 고른다(bodyCur · bodyGen) — 정적 문구가 없다.
        # planDesc 는 라이브 문구가 없고, 90 · Archive 의 보드가 이 변수를 묶는다.
        "planBadge": None, "usernameTitle": None, "packName": None, "packImports": None,
        "planDesc": None, "connectorTitle": None, "keyRowTitle": None, "keyRowMeta": None,
        "newsTitle": None,
    }),
    "/account/team/": ("team/", {
        "sub": "lead", "inviteCardTitle": "acT", "inviteCardDesc": "acB",
        "createDesc": "crB", "inviteDesc": "ivB", "dangerDesc": "dgB",
        "h1": None, "createTitle": None, "inputName": None, "inputSeats": None,
        "seatMeta": None, "roleAdmin": None, "roleEditor": None, "roleViewer": None,
        "membersTitle": None, "inviteTitle": None, "dangerTitle": None,
    }),
    "/404": ("err404/", {"body": "sub"}),
    "/release/": ("release/", {"h1": "h1", "heroSub": "sub"}),
    "/privacy/": ("privacy/", {
        "h1": "h1", "sub": "sub", "updated": "updated",
        # 2026-10 개정으로 절이 열하나가 됐다. 제목은 **페이지 순서**로 잇는다(s1 이 첫 절).
        "s1Title": "h2who", "s2Title": "h2items", "s3Title": "h2app", "s4Title": "h2entrust",
        "s5Title": "h2third", "s6Title": "h2rights", "s7Title": "h2delete", "s8Title": "h2cookies",
        "s9Title": "h2safe", "s10Title": "h2kids", "s11Title": "h2changes",
        # 본문은 절의 문단과 목록 줄을 페이지 순서로 잇는다. 표(itemsT · entrustT)는 칸이
        # 한 줄로 붙어 읽을 수 없으므로 본문에 넣지 않는다. 맺음 줄(vow)은 절 밖이다.
        "s1Body": ("who1", "who2"),
        "s2Body": ("items1", "items2"),
        "s3Body": ("app1", "app2", "app3"),
        "s4Body": ("entrust1", "entrust2", "entrust3"),
        "s5Body": ("third1", "third2", "third3", "third4", "third5"),
        "s6Body": ("rights1", "rights2", "rights3"),
        "s7Body": tuple(f"delete{i}" for i in range(1, 8)),
        "s8Body": ("cookies1",),
        "s9Body": tuple(f"safe{i}" for i in range(1, 6)),
        "s10Body": ("kids1",),
        "s11Body": ("changes1", "changes2"),
        "showKoBlock": None,
    }),
    "/community/": ("community/", {
        # 2026-11 부터 /community/ 는 앱 셸 하나다. 제목만 정적이고 나머지는 셸이 그린다.
        "h1a": "heading",
        # 빈 상태 두 줄은 셸이 그린다. 90 · Archive 의 보드가 이 변수를 묶는다.
        "empty": None, "browseDocs": None,
        # 공개 팩 중단으로 사라진 문구는 역사 프레임에만 남는다.
        "heroBody": None, "shareCta": None, "shareNote": None,
        "sortRecent": None, "cardBadge": None, "cardImport": None,
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
    # ⚠ 페이지마다 따옴표가 다르다. 대부분은 `key: '...'` 인데 welcome·verify 는
    # `key: "..."` 를 쓴다. 작은따옴표만 보다가 그 두 쪽의 한국어를 통째로 놓쳤고,
    # 하마터면 영어만 덮어써서 반쪽짜리로 만들 뻔했다. 두 형태를 다 받는다.
    bs = chr(92)
    ko: dict[str, str] = {}
    for q in ("'", '"'):
        pat = r"(\w+)\s*:\s*" + q + r"((?:[^" + q + bs + bs + r"]|" + bs + bs + r".)*)" + q
        for m in re.finditer(pat, src):
            v = m.group(2)
            if re.search(r"[가-힣]", v):
                ko.setdefault(m.group(1), _strip(v.replace(bs + q, q)))
    return {"en": en, "ko": ko}


def page_path(route: str) -> pathlib.Path:
    if route == "/":
        return SITE / "index.html"
    rel = route.strip("/")
    # 대부분은 디렉터리 + index.html 이지만 404 만 파일 하나다(정적 호스팅 관례).
    flat = SITE / (rel + ".html")
    return flat if flat.exists() else SITE / rel / "index.html"


def _joined(live: dict, keys: tuple[str, ...]) -> dict:
    """조각 여럿을 한 변수 값으로 잇는다. 엔티티(`&quot;` 등)는 화면 글자로 푼다.

    한국어가 없는 조각은 화면처럼 영어로 떨어진다. 조각 모두에 한국어가 없으면 KO 는
    None 이고, 그러면 `check` 가 KO 를 대조하지 않는다(한 키짜리와 같은 규칙).
    """
    en = [html.unescape(live["en"].get(k, "")) for k in keys]
    has_ko = any(k in live["ko"] for k in keys)
    ko = [html.unescape(live["ko"].get(k, live["en"].get(k, ""))) for k in keys]
    return {"EN": "\n\n".join(en), "KO": "\n\n".join(ko) if has_ko else None}


def emit(route: str) -> dict:
    prefix, keymap = MAP[route]
    live = extract(page_path(route))
    out, gone, missing = {}, [], []
    for tail, livekey in keymap.items():
        if livekey is None:
            gone.append(prefix + tail)
            continue
        if isinstance(livekey, tuple):
            absent = [k for k in livekey if k not in live["en"] and k not in live["ko"]]
            if absent:
                gone.append(prefix + tail + " (라이브 키 " + " · ".join(absent) + " 없음)")
                continue
            out[prefix + tail] = _joined(live, livekey)
            continue
        en, ko = live["en"].get(livekey), live["ko"].get(livekey)
        if en is None and ko is None:
            gone.append(prefix + tail + " (라이브 키 " + livekey + " 없음)")
            continue
        out[prefix + tail] = {"EN": en, "KO": ko}
    mapped = {k for v in keymap.values() if v for k in ((v,) if isinstance(v, str) else v)}
    for k in sorted(set(live["en"]) | set(live["ko"])):
        if k not in mapped and not k.startswith("f"):   # f* 는 공용 푸터 링크다
            missing.append(k)
    return {"route": route, "values": out, "figma_only": gone, "missing_in_figma": missing}


#: **Figma 를 코드와 맞춘 시점의 문구 서명.** 스냅샷을 쓸 때 Figma 를 읽지는 않는다 —
#: 읽을 수 있으면 게이트가 오프라인이 아니게 된다. 대신 "지금 둘이 같다"를 사람이 확인한
#: 뒤 라이브 값을 찍는다. 그래서 이 파일이 잡는 것은 **그 뒤에 코드 문구가 바뀌었는데
#: Figma 를 안 고친 경우**이고, 그게 실제로 생기는 드리프트다(2026-09-06 실측: 162건 중
#: 161건 일치, 남은 하나는 한국어 없는 키의 정상 폴백이었다).
#: Figma 쪽 값의 **서명 스냅샷**. 전문을 커밋하지 않는 이유는 이 리포의 규율이다 —
#: 카피의 정본은 코드이고, 파일에 사본을 두면 곧 두 벌이 갈린다
#: (계약은 [[decision:code-is-canon-figma-follows]]). 서명은 길이와
#: 체크섬뿐이라 사본이 아니고, 다름을 잡는 데는 충분하다.
SIG = HUB / "figma" / "figma-copy-sig.json"


def sig(s: str | None) -> str:
    """길이:체크섬. 값을 복원할 수 없고 한 글자만 달라도 바뀐다."""
    s = "" if s is None else str(s)
    h = 0
    for ch in s:
        h = (h * 31 + ord(ch)) % 99991
    return f"{len(s)}:{h}"


def live_signatures() -> dict[str, str]:
    """대응표의 모든 라우트에서 라이브 문구를 뽑아 서명으로 만든다."""
    out: dict[str, str] = {}
    for route in MAP:
        for name, v in emit(route)["values"].items():
            out[name] = sig(v.get("EN")) + "|" + sig(v.get("KO"))
    return out


def check() -> int:
    """라이브 문구가 Figma 스냅샷과 같은가.

    ⚠ 한국어가 없는 키(라이브 KO 가 `None`)는 화면에서 영어로 떨어진다. Figma 는 그
    자리에 영어를 넣어 두는 것이 맞으므로, **라이브 KO 가 비었으면 KO 는 대조하지
    않는다** — 안 그러면 정상 폴백이 매번 어긋남으로 잡힌다(2026-09-06 에 `pricing/faqTitle`
    하나가 정확히 그렇게 잡혔다).
    """
    if not SIG.exists():
        print(f"[SKIP] {SIG.relative_to(HUB)} 가 없다 — --write-sig 로 스냅샷을 먼저 만들 것")
        return 0
    if not SITE.exists():
        print("[SKIP] 문구 대조 — Vinc-homepage 가 체크아웃돼 있지 않다")
        return 0
    snap = json.loads(SIG.read_text(encoding="utf-8"))["signatures"]
    live = live_signatures()
    bad, gone = [], []
    for name, s in sorted(live.items()):
        if name not in snap:
            gone.append(name)
            continue
        want_en, want_ko = snap[name].split("|")
        got_en, got_ko = s.split("|")
        if got_en != want_en:
            bad.append((name, "EN"))
        elif got_ko != want_ko and not got_ko.startswith("0:"):
            bad.append((name, "KO"))
    stale = sorted(set(snap) - set(live))
    for name, lang in bad:
        print(f"  [FAIL] {name} ({lang}) — 라이브 문구가 Figma 와 다르다")
    for name in gone:
        print(f"  [FAIL·미선언] {name} — 스냅샷에 없다(변수를 새로 만들었으면 --write-sig)")
    for name in stale:
        print(f"  [FAIL·잔여] {name} — 라이브에서 사라졌다")
    n = len(bad) + len(gone) + len(stale)
    print(f"문구 대조 {len(live)}건 · 어긋남 {n}건")
    if n:
        print("Figma 를 고치고 나서 --write-sig 로 스냅샷을 갱신할 것(정본은 코드다)",
              file=sys.stderr)
    return 1 if n else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="라이브 문구를 Figma Locale 형태로 뽑는다")
    ap.add_argument("--emit", metavar="ROUTE")
    ap.add_argument("--routes", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="라이브 문구가 Figma 스냅샷과 같은가(CI 용, exit 1 이면 다름)")
    ap.add_argument("--write-sig", action="store_true",
                    help="Figma 값을 반영한 뒤 스냅샷을 다시 쓴다(같은 커밋에 넣을 것)")
    a = ap.parse_args(argv)
    if a.check:
        return check()
    if a.write_sig:
        SIG.parent.mkdir(parents=True, exist_ok=True)
        SIG.write_text(json.dumps({
            "note": "Figma Locale 값의 서명(길이:체크섬). 값 자체가 아니다 — 카피의 정본은 코드다.",
            "written": "figma_copy.py --write-sig",
            "signatures": live_signatures(),
        }, ensure_ascii=False, indent=1) + chr(10), encoding="utf-8")
        print(f"스냅샷 {len(live_signatures())}건 → {SIG.relative_to(HUB)}")
        return 0
    if a.routes:
        for r in MAP:
            print(r)
        return 0
    if not a.emit:
        ap.print_help()
        return 2
    # ⚠ Git Bash 의 MSYS 경로 변환이 `/download/` 를 `D:/git/download/` 로 바꿔치기한다
    # ([[vinc.manual.platform]] §2 의 그 함정). 그래서 슬래시 없는 이름도 받고, 받은 값의 마지막
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
