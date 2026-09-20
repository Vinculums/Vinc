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

급소 둘, 둘 다 실측에서 나왔다:

  · `as` 는 **탐색으로** 찾는다(`parts.indexOf`). 그래서 뒤에 세그먼트를 붙여도
    안 깨지고, `d2` 를 꼬리에 두는 것이 그 때문이다 — 구 클라이언트는 그냥 무시한다.
  · 첫 세그먼트가 예약어가 **아니면 전부 주제 목록으로 읽힌다.** 그래서 `ids`·`doc`
    같은 새 키워드는 반드시 여기 `_KEYWORDS` 에 들어가야 한다. 안 넣으면
    "ids 라는 이름의 주제"로 조용히 읽히고 오류조차 안 난다.
"""
from __future__ import annotations

import re
from urllib.parse import unquote

#: 첫 세그먼트가 이것들이면 주제 목록이 아니라 지시어다. **새 형태를 더할 때 여기부터**
#: — 빠뜨리면 그 키워드가 주제 이름이 되고, 아무 오류 없이 빈 화면이 나온다.
_KEYWORDS = ("trace", "ids", "doc", "src", "as")

#: 머리 한 칸이 정하는 표면. 선택 문법은 셋이 **같은 것**을 쓴다 — 표면마다 문법을
#: 복제하면 `#read/node/<id>` 와 `#view/trace/<id>` 가 같은 것을 다르게 적게 되고
#: 거울 둘이 넷이 된다. 도구는 표면을 읽고 **선택만 쓴다**: 화면이 그것을 읽기로 보든
#: 지도로 보든 그래프 조회의 답은 같기 때문이다.
#: ⚠ 여기 없는 머리는 `none` 이다. 구 표면명(F8 금지어)이 그 자리이고, 조용히 틀리지
#: 않으므로 그것으로 충분하다.
_HEADS: dict[str, str] = {"#view": "view", "#read": "read", "#agents": "agents"}

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
    # 0.9.4 — 머리가 셋이 됐다(리더 · Agents · 지도). 선택 문법은 그대로다.
    "#read",
    "#read/vinc/engineering",
    "#read/trace/concept:optimistic-locking",
    "#read/doc/d4ab94561078498e3",
    # 노드 하나에 소스가 여럿일 때 **어느 것**을 펴고 있나. 머리의 `doc` 은 노드 없이
    # 선 독립 문서라 뜻이 다르다.
    "#read/trace/concept:x/src/d4ab94561078498e3",
    "#read/trace/concept:x/src/d4ab94561078498e3/d2/as/timeline",
    "#agents",
    "#agents/trace/concept:agent-dev-core",
)


def parse(url: str) -> dict:
    """View 주소 → `{kind, seed, depth, domains, ids, doc}`. 못 읽으면 `kind='none'`.

    전체 URL 이든 해시 조각이든 받는다 — 사람이 복사하는 것은 주소창의 전체 URL 이다.
    관점(`/as/…`)은 읽고 **버린다**: 화면이 무엇을 그리느냐는 도구가 알 바 아니다.
    """
    out: dict = {"kind": "none", "seed": "", "depth": 0,
                 "domains": [], "ids": [], "doc": "",
                 # 머리 한 칸이 정하는 표면(노드 읽기 · Agents · 지도). 도구는 선택만
                 # 쓰지만, 모르는 머리를 `none` 으로 떨구지 않으려면 읽어야 한다.
                 "surface": "", "source_doc": ""}
    if not isinstance(url, str) or "#" not in url:
        return out
    frag = url[url.index("#"):]
    parts = [unquote(p) for p in frag.split("/")]
    # 표면명 하나만 받는다. 구 이름의 딥링크는 여기서 `none` 이 되고 도구가 "못 읽었다"고
    # 말한다 — 조용히 틀리지 않으므로 그것으로 충분하고, 그 이름은 F8 금지어다.
    surface = _HEADS.get(parts[0] if parts else "")
    if not surface:
        return out
    out["surface"] = surface
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
    # 소스 꼬리도 **탐색으로** 뗀다(`as` 와 같은 이유). 떼지 않으면 body 에 남아
    # 선택 해석을 틀어 놓는다.
    if "src" in body:
        i = body.index("src")
        out["source_doc"] = body[i + 1] if len(body) > i + 1 else ""
        body = body[:i]
    if not body:
        out["kind"] = "all"
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
