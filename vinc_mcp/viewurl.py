# -*- coding: utf-8 -*-
"""View 주소를 도구 인자로 — 사람이 복사한 것을 그대로 받는다.

정본: [[decision:a-selection-becomes-an-address-you-paste]].

화면에서 무언가를 고르면 그 선택이 **주소**가 되고, 붙여넣으면 어시스턴트가 같은
곳을 본다(Figma 가 노드 URL 로 하는 것과 같다). 이 모듈은 그 주소를 읽는다.

⚠ **문법은 `app/src` 가 만들고 여기가 읽는다 — 거울이 둘이다.** 이 리포는 같은
모양으로 여러 번 물렸고(역할표·op 표·MCP 계약) 처방도 정해져 있다: 거울을 두되
**테스트가 대조한다**. `GRAMMAR` 가 그 접점이다 — 여기 적힌 형태 전부를 파서가
받아야 하고, 앱이 새 형태를 만들면 여기부터 늘린다.

읽는 형태:

    #view                           전체 지도 — 시드가 없다
    #view/<a,b>                     주제 렌즈(쉼표 구분)
    #view/<a,b>/d2                  렌즈 + 깊이
    #view/trace/<node-id>           노드 하나
    #view/trace/<node-id>/d2        노드 + 깊이
    #view/ids/<id,id,id>            임의 선택(올가미) — 명시 목록
    #view/doc/<doc-id>              문서
    …/as/<viewpoint>                관점. **도구는 무시한다** — 화면의 것이다

스페이스는 해시가 아니라 **해시 앞의 쿼리**에 탄다: `…/app/?space=<id>#view/…`.
값은 `personal` · 팀 id(uuid) · `local`(이 기기에만 있는 그래프) 셋 중 하나다.
해시 안에 두지 않은 이유: 첫 세그먼트는 예약어가 아니면 주제로 읽히고(아래 급소),
꼬리는 `/d<n>` 판정을 가린다 — 어느 쪽이든 구버전 파서가 조용히 틀린다. 쿼리는
구버전 앱도 이 파서도 **읽지 않고 지나간다**.
근거: [[decision:a-node-address-names-its-space]]

급소 둘, 둘 다 실측에서 나왔다:

  · `as` 는 **탐색으로** 찾는다(`parts.indexOf`). 그래서 뒤에 세그먼트를 붙여도
    안 깨지고, `d2` 를 꼬리에 두는 것이 그 때문이다 — 구 클라이언트는 그냥 무시한다.
  · 첫 세그먼트가 예약어가 **아니면 전부 주제 목록으로 읽힌다.** 그래서 `ids`·`doc`
    같은 새 키워드는 반드시 여기 `_KEYWORDS` 에 들어가야 한다. 안 넣으면
    "ids 라는 이름의 주제"로 조용히 읽히고 오류조차 안 난다.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote

#: 첫 세그먼트가 이것들이면 주제 목록이 아니라 지시어다. **새 형태를 더할 때 여기부터**
#: — 빠뜨리면 그 키워드가 주제 이름이 되고, 아무 오류 없이 빈 화면이 나온다.
_KEYWORDS = ("trace", "ids", "doc", "as")

#: 깊이 꼬리. 앞에 `d` 를 붙이는 이유는 숫자만 두면 주제 이름과 구분이 안 되기 때문이다.
_DEPTH_RE = re.compile(r"\Ad([0-9]{1,2})\Z")

#: 파서가 받아야 하는 형태 전부. `app/src` 의 포맷터가 만들 수 있는 것과 같아야 하고,
#: 테스트가 이 목록을 돌며 확인한다. 앱이 형태를 늘리면 여기도 늘어난다.
GRAMMAR: tuple[str, ...] = (
    "#view",
    "#view/vinc/engineering",
    "#view/vinc/engineering,vinc/design",
    "#view/vinc/engineering/d3",
    "#view/trace/concept:optimistic-locking",
    "#view/trace/concept:optimistic-locking/d1",
    "#view/ids/concept:a,record:b,decision:c",
    "#view/doc/d4ab94561078498e3",
    "#view/doc/vinc.spec.dataplane",
    "#view/as/2d",
    "#view/trace/concept:x/d2/as/timeline",
    # 스페이스를 실은 전체 주소 — 복사 버튼이 실제로 만드는 모양이다.
    "https://vincs.io/app/?space=personal#view/trace/concept:x/d1",
    "https://vincs.io/app/?space=0198c0de-1111-7222-8333-444455556666#view/doc/vinc.spec.dataplane",
    "http://tauri.localhost/?space=local#view/trace/concept:x/d1/as/2d",
)


#: 주소가 가리킬 수 있는 스페이스 이름. 팀은 id(uuid)로 온다.
PERSONAL, LOCAL = "personal", "local"


def space_of(url) -> str:
    """주소의 `?space=` 값. 없으면 "". 소문자로 접어 돌려준다.

    판정(팀 id 모양이 맞나·멤버인가)은 여기서 안 한다 — 그건 스페이스 인자와 같은
    자리(허브의 `_auth_for`)가 한다. 여기서 흉내 내면 판정표가 둘이 된다."""
    if not isinstance(url, str):
        return ""
    head = url.split("#", 1)[0]          # 해시 안의 `?` 는 쿼리가 아니다
    if "?" not in head:
        return ""
    query = head.split("?", 1)[1]
    got = parse_qs(query).get("space") or [""]
    return got[0].strip().lower()


def parse(url: str) -> dict:
    """View 주소 → `{kind, seed, depth, domains, ids, doc}`. 못 읽으면 `kind='none'`.

    전체 URL 이든 해시 조각이든 받는다 — 사람이 복사하는 것은 주소창의 전체 URL 이다.
    관점(`/as/…`)은 읽고 **버린다**: 화면이 무엇을 그리느냐는 도구가 알 바 아니다.
    """
    out: dict = {"kind": "none", "seed": "", "depth": 0,
                 "domains": [], "ids": [], "doc": ""}
    if not isinstance(url, str) or "#" not in url:
        return out
    frag = url[url.index("#"):]
    parts = [unquote(p) for p in frag.split("/")]
    # 표면명 하나만 받는다. 구 이름의 딥링크는 여기서 `none` 이 되고 도구가 "못 읽었다"고
    # 말한다 — 조용히 틀리지 않으므로 그것으로 충분하고, 그 이름은 F8 금지어다.
    if not parts or parts[0] != "#view":
        return out
    body = parts[1:]
    # 관점을 떼어 낸다 — **탐색으로** 찾는다. 위치로 찾으면 꼬리를 붙이는 순간 깨진다.
    if "as" in body:
        body = body[:body.index("as")]
    # 깊이 꼬리.
    if body:
        m = _DEPTH_RE.match(body[-1])
        if m:
            out["depth"] = int(m.group(1))
            body = body[:-1]
    if not body:
        out["kind"] = "all" if out["depth"] or frag.startswith("#view") else "none"
        return out
    head = body[0]
    if head == "trace":
        out.update(kind="node", seed=body[1] if len(body) > 1 else "")
        return out if out["seed"] else {**out, "kind": "none"}
    if head == "ids":
        out.update(kind="ids",
                   ids=[i for i in (body[1] if len(body) > 1 else "").split(",") if i])
        return out if out["ids"] else {**out, "kind": "none"}
    if head == "doc":
        out.update(kind="doc", doc=body[1] if len(body) > 1 else "")
        return out if out["doc"] else {**out, "kind": "none"}
    if head in _KEYWORDS:
        return out            # `as` 만 있던 경우 — 위에서 이미 잘려 여기 안 온다
    # 예약어가 아니면 주제 목록이다. 세그먼트가 여럿이면 슬래시가 든 주제다
    # (`vinc/engineering` 은 `#view/vinc/engineering` 으로 두 조각이 된다).
    joined = "/".join(body)
    out.update(kind="domains",
               domains=[d for d in joined.split(",") if d])
    return out if out["domains"] else {**out, "kind": "none"}
