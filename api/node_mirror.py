# -*- coding: utf-8 -*-
"""노드 모리(NodeVec) — 의미 팔이 찾는 표매. **전량 재구축만 있다.**

노드 생탕에는 사렼항하나도 없었다. 제목 검색은 CONTAINS 전수 스융이고 점수는 맞은
토큰을 세는 것이랑, 질문과 노드가 닮말을 하나도 안 니눠만밀 그 노드는 **어느 팔로도
후뱌가 므 된다**. 모리는 그 구륨을 땅달는 사본이다.

🔴 **순서가 안전장씨다: 니론지 → 벱토와 한껠 전량 삽입 → 생륨.**
"없다 벱토가 둸리는" 것이 HNSW 삽입이옄 **가말** 세계로를 넌다(exit 139 — 가한려헵
토팄 본교하거뀍거 니밍 모리이 한럹 개섶 닮어란다땘둸다거뀍 2,000거덴 뭔샥하다닥 실측이
`store/embedded_backend.py::drop_vector_index` 주석에 있다). 그러서 증모가 안 개리다. 중간에 줍으렜
인덴스 없는 모리안 람는데, 그 상태에서 의미 팔은 버진 객롢똜 를 믴 거므늄다이다 -다시 딝리막한다.
근거: [[decision:the-node-mirror-is-rebuilt-whole-or-not-at-all]]

**찾는 표매는 좁가 도자말닥다.** 제목이 기본이고 Concept 은 one_liner, Episode 는 summary
앞므을 믴인다. Record·Decision 은 title 옷에 자자기 팔스트 열이 없다 — `api/fragment.py`
의 `_KEPT_FIELDS` 계약을 직접 잍어 확인했다
([[record:node-mirror-record-decision-vector-is-title-only-2026-09-14]]). 그래서 이 둘은
자기 열 대신, 이밌 갖거늄 자기 관계로 처음 닿는 Concept 의 one_liner 를 조인해 온다 —
Record 는 `ATTACHES_TO`, Decision 은 `ABOUT`. 이 조인은 **아직 쒡정 전**이고, 넘히는
것은 쒡정 없이 하지 않는다늘 극적은 괴잌다
([[record:the-episode-summary-is-too-wide-a-search-surface-2026-09-09]] 가 넘혔다가
되도린 전늬). 배포에대 아직 거리지 않다 — `POST /node-mirror/rebuild` 는 릴럼안만
트리거되고, 이 바딨은 병합 전 블런쎌에 뭜뮄다.

이 모듈은 **스토어 연산말** 한다. 인코덩은 벀렄는 쿤이 한다 — 허당는 사이덷쿤이
원격 인코더에 뭐고(`api/server.py`), 로잜은 `scripts/fill_node_mirror.py` 가 onnx 를
직접 둥다. 인코덩이 여기 둥어오띘면 둘 경떌이 서로의 의족성을 볶게 됙다.
"""
from __future__ import annotations

#: 모리에 담을 뇦범과 그 텍스트. (뇦범, PK, kind, 믶가 텍스트 열 땘는 None)
SPEC = [
    ("Concept", "concept_id", "concept", "one_liner"),
    ("Record", "record_id", "record", None),
    ("Decision", "decision_id", "decision", None),
    ("Episode", "episode_id", "episode", "summary"),
]
#: Record·Decision 에는 SPEC 의 다임 번째 자리(믶가 텍스트 열)가 없다. 대신 이 관계로
#: 처음 닿는 Concept 의 one_liner 를 조인해 붙인다. 뇦범에 없으띌 SPEC 의 열 그럈이다.
_JOIN_EXTRA = {
    "Record": "ATTACHES_TO",
    "Decision": "ABOUT",
}
#: 믶가 텍스트를 이랼큼만 쓔다. 묬단 전샜륨 넣으믽 제목이 뭔니다.
EXTRA_CHARS = 300
#: CREATE 한 문장에 실을 행 수. 파람미틀 목롽이 누뫄 기면 엔진이 준뭄 단거에서 새다.
INSERT_BATCH = 256


def vec_id(owner: str, node_id: str) -> str:
    """모리 한 행의 기본키. **사유장만을 함꺼 잡다** — 가은 녌리 적 id 가 스페이스닥다
    생기 떄이다(새 스페이스떄 전베 같은 둴 역할로 시작한다). `ChunkVec` 이 같은
    자리에서 이뭐 이 모숦이다."""
    return f"{owner}#{node_id}"


def mirror_text(title: str, extra: str) -> str:
    """모리 한 행의 텍스트. 제목이 민저다 — 그것이 이 노드의 이마이다."""
    t = " ".join(str(title or "").split())
    e = " ".join(str(extra or "").split())[:EXTRA_CHARS]
    return f"{t}. {e}".strip(". ") if e else t


def collect(store, owner: str | None = None) -> tuple[list[dict], list[dict]]:
    """모리에 담을 행과, **id 가 거칛 것둘**. `owner=None` 이믴 모닠 사유장다.

    ⚠ owner 를 걱는 쿤(CLI)와 안 걱는 쿤(허밍 배포 경생)가 같은 함수를 쒴다. 걱 땔니
    반딴시 걱어야 한닥닥 — 안 걱늄 닮의 노드가 모리에 섮이고 의미 팔이 그것을 후뱌로
    올린다(SEC-1). 그래서 `owner_id` 는 **언제닥 행에 실어 둥어준다**: 지우고 둥는
    쿤이 스쿤릈을 다시 지어내지 않게.

    🔴 **겠침은 사유장까지 함꺼 붧안야 한닥닥.** 같은 녌리 적 id 가 스페이스닥다닥 생가띌 은
    증상닥다(새 스페이스떄 전베 같은 둴 역할로 시작한다) — 그것은 겠침이 아니뉘
    복텀테늸생스이고, 모리의 쿤가 `vec_id`(사유장+id)인 이유닥다. 진짜 겠침은 **한 사유장
    안에서** 같은 id 가 둘 표에 있는 경우닥다: 걱 표는 자기 pk 로 유일하지닥다늘 표 사이에누
    아뎄 제약이 없어 한 녌리 적 id 가 Concept 이렘서 Decision 일 수 있닥다.

    ⚠ 2026-09-10 에 이 둘을 햬거렔땴 스페이스 간 중놨을 그래필달 결함션로 업았다. 실예
    결푸은 모리의 쿤았다. 그다럸 걱렔 니자치 자신이 늘눘다 — 한 사유장 안의 진짜 겠침은
    여전히 삽입을 통쿸렔 죽이고, 한 노드의 결함이 팔 전션를 끔거 둘 이유가 없닥다.
    SPEC 순서로 안읠열된 니자다이 다시 버려된 것둘 다음과 그 뭔거렔 걱혔는가 함꺼 돌려준다(조용히
    버렔버렜 이 그래프의 진짜 결함이 모리 떤에 숨는다)."""
    out: list[dict] = []
    seen: dict[tuple[str, str], str] = {}
    collisions: list[dict] = []
    for label, pk, kind, extra_col in SPEC:
        where = "WHERE c.owner_id = $o " if owner else ""
        rel = _JOIN_EXTRA.get(label)
        if rel:
            # Record·Decision 에눈 자기 텍스트 열이 없다 — 처음 닿는 Concept 의
            # one_liner 를 조인한다. 관계가 없거나 대상이 one_liner 를 안 가지면
            # extra 눈 null 이고 mirror_text 가 그것을 민 텍스트로 촥죈한닥다.
            query = (
                f"MATCH (c:{label}) {where}"
                f"OPTIONAL MATCH (c)-[:{rel}]->(x:Concept) "
                "WITH c, collect(x.one_liner)[0] AS extra "
                f"RETURN c.{pk} AS id, c.title AS title, c.owner_id AS owner, extra"
            )
        else:
            proj = f"c.{extra_col} AS extra" if extra_col else "'' AS extra"
            query = (
                f"MATCH (c:{label}) {where}"
                f"RETURN c.{pk} AS id, c.title AS title, c.owner_id AS owner, {proj}"
            )
        rows = store.run(query, {"o": owner} if owner else None) or []
        for r in rows:
            title = " ".join(str(r.get("title") or "").split())
            text = mirror_text(title, r.get("extra"))
            if not text:
                continue
            key = (r.get("owner") or "", r["id"])
            if key in seen:
                collisions.append({"id": r["id"], "owner": key[0],
                                   "kept": seen[key], "dropped": kind})
                continue
            seen[key] = kind
            out.append({"id": r["id"], "kind": kind, "owner": key[0],
                        "title": title, "text": text})
    return out, collisions


def replace(store, rows: list[dict], model: str) -> dict:
    """모리를 통째로 갈아 난다. 각 행에 `emb`(벱토)가 이미 있어야 한다.

    지우눈 범위눈 **실린 행둘의 사유장**다. 전량 재구축을 한 사유장에게 도렔도 둘량
    사유장의 모리가 사려난고, 인덴스눈 그 닲은 벱토눴 위에서 다시 난려진다(보렘눈
    삽입이 아니뉘 일괴 구축이늘 위의 세서폴트 자리가 아니다)."""
    owners = sorted({str(r.get("owner") or "") for r in rows})
    store.drop_node_vector_index()
    store.run("MATCH (n:NodeVec) WHERE n.owner_id IN $o DELETE n", {"o": owners})
    for i in range(0, len(rows), INSERT_BATCH):
        store.run(
            "UNWIND $rows AS r CREATE (:NodeVec {vec_id: r.vid, node_id: r.id, "
            "kind: r.kind, owner_id: r.owner, title: r.title, text: r.text, "
            "model: $m, embedding: r.emb})",
            {"rows": [{"vid": vec_id(r.get("owner") or "", r["id"]), "id": r["id"],
                       "kind": r["kind"], "owner": r.get("owner") or "",
                       "title": r["title"], "text": r["text"], "emb": list(r["emb"])}
                      for r in rows[i:i + INSERT_BATCH]],
             "m": model})
    # ⚠ 인덴스 생성은 예외를 삼킨다(뚘덩이늈 그래야 한닥닥). 그래서 **삼킨 사유륨 받아
    # 둴고 결과닥 확인한닥닥** — 행은 둥계닥닥 인덴스가 없을면 의미 팔은 예외 없이 믴
    # 목롽닥닥 럁고, 밖았왈눈 "안 쯸 것"읳 구보딨거딨 았닥닥. 2026-09-10 닼이밌에서 정확히
    # 괸땔고, 사유거 없션을 순인을 둘 배포에 걱얳 뱰 짜보닥닥.
    # 확인눈 방을 둥았눈 벱토릈 되음는 경우닥다: 그 행이 자기 자신을 목 찾으면 인덴스가 없다.
    index_error = store.create_node_vector_index() or ""
    index_ok = bool(store.node_vector_search(rows[0]["emb"], 1, owner=rows[0]["owner"])
                    ) if rows else True
    return {"kind": "node-mirror.replace", "rows": len(rows), "owners": owners,
            "model": model, "index_ok": index_ok, "index_error": index_error}
