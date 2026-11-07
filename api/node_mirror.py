# -*- coding: utf-8 -*-
"""노드 미러(NodeVec) — 의미 팔이 찾는 표면. **색인은 통째로만 다시 만든다.**

노드 층에는 색인이 하나도 없었다. 제목 검색은 CONTAINS 전수 스캔이고 점수는 맞은
토큰을 세는 것이라, 질문과 노드가 낱말을 하나도 안 나누면 그 노드는 **어떤 팔로도
후보가 못 된다**. 미러는 그 구멍을 메우는 사본이다.

🔴 **순서가 안전장치다: 드롭 → 벡터와 함께 삽입 → 생성.**
"없던 벡터가 생기는" 것이 HNSW 삽입이고 **그것만** 세그폴트를 낸다(exit 139 — 같은
행의 값 교체와 벡터 없는 행 삽입은 각각 2,000건까지 무사하다는 실측이
`store/embedded_backend.py::drop_vector_index` 주석에 있다). 그래서 색인을 살려 둔 채
넣는 증분은 없다. 바뀐 행만 갈 때도(:func:`plan`) 같은 순서를 밟는다 — 아끼는 것은
인코딩과 삭제·삽입의 행 수뿐이고, 색인은 언제나 통째로 다시 선다.
중간에 죽으면 인덱스 없는 미러가 남는데, 그 상태에서 의미 팔은 빈 목록을 낼 뿐이라
검색은 멀쩡하다 — 다시 돌리면 된다.
근거: [[decision:the-node-mirror-is-rebuilt-whole-or-not-at-all]] ·
[[decision:the-node-mirror-keeps-unchanged-rows-and-rebuilds-its-index-whole-2026-10-02]]

**찾는 표면은 좁게 담는다.** 제목이 기본이고 Concept 은 one_liner, Episode 는 summary
앞부분을, Record·Decision 은 props 본문(brief 의 답 본문 규칙, 기술 필드 제외)을 붙인다.
넓히는 것은 측정 없이 하지 않는다. Record·Decision 본문은 봉인 셋 8에서 재고 넣었다
([[vinc.spec.retrieval-knobs]] §2). 에피소드 요약을 통째로 넣지 않는 근거는
[[record:the-episode-summary-is-too-wide-a-search-surface-2026-09-09]].

⚠ **Record·Decision 에 연결된 Concept 의 one_liner 를 조인하는 것은 이미 재 봤고 졌다**
(Vinc#338, 2026-09-15 적재 → 2026-09-16 되돌림). 개인 골드 81행 페어 A/B 에서 **좋아진
항목이 0건**이고 R@1 은 0.4833 → 0.4167, 맞던 확정 3건이 tie 로 무너졌으며(record 가
concept 의 산문을 그대로 물려받아 둘이 같은 낱말로 묶인다) 인코드는 2.4배가 됐다.
다시 제안하기 전에 읽을 것: [[record:the-one-liner-join-improves-no-item-and-costs-four-2026-09-16]]

이 모듈은 **스토어 연산만** 한다. 인코딩은 부르는 쪽이 한다 — 허브는 사이드카가
원격 인코더에 묻고(`api/server.py`), 로컬은 `scripts/fill_node_mirror.py` 가 onnx 를
직접 든다. 인코딩이 여기 들어오면 두 경로가 서로의 의존성을 물게 된다.
"""
from __future__ import annotations
import hashlib
import json

from store.node_mirror_state import EXTRA_CHARS, extra_text, mirror_text as _mirror_text

#: 미러에 담을 라벨과 그 텍스트. (라벨, PK, kind, 부가 텍스트 열 또는 None)
SPEC = [
    ("Concept", "concept_id", "concept", "one_liner"),
    ("Record", "record_id", "record", "props"),
    ("Decision", "decision_id", "decision", "props"),
    ("Episode", "episode_id", "episode", "summary"),
]
#: ColBERT 토큰 텍스트에 넣을 열 (한 줄 설명, 요약, props). 없는 열은 ''.
#: 근거: [[vinc.plan.vin845-colbert-node-rescoring-implementation-2026-10-30]]
TOKEN_COLS = {
    "Concept": ("c.one_liner", "''", "c.props"),
    "Record": ("''", "''", "c.props"),
    "Decision": ("''", "''", "c.props"),
    "Episode": ("''", "c.summary", "''"),
}

#: 부가 텍스트를 이만큼만 쓴다. 문단 전체를 넣으면 제목이 묻힌다.
#: CREATE 한 문장에 실을 행 수. 파라미터 목록이 너무 길면 엔진이 준비 단계에서 샌다.
INSERT_BATCH = 256


def vec_id(owner: str, node_id: str) -> str:
    """미러 한 행의 기본키. **소유자를 함께 잡는다** — 같은 논리 id 가 스페이스마다
    살기 때문이다(새 스페이스는 전부 같은 네 역할로 시작한다). `ChunkVec` 이 같은
    자리에서 이미 이 모양이다."""
    return f"{owner}#{node_id}"


def mirror_text(title: str, extra: str, kind: str = "") -> str:
    """미러 한 행의 텍스트. 제목이 먼저다 — 그것이 이 노드의 이름이다."""
    return _mirror_text(title, extra, kind)


def collect(store, owner: str | None = None, *,
            with_tokens: bool = False) -> tuple[list[dict], list[dict]]:
    """미러에 담을 행과, **id 가 겹친 것들**. `owner=None` 이면 모든 소유자다.

    ⚠ owner 를 거는 쪽(CLI)과 안 거는 쪽(허브 배포 경계)이 같은 함수를 쓴다. 걸 때는
    반드시 걸어야 한다 — 안 그러면 남의 노드가 미러에 섞이고 의미 팔이 그것을 후보로
    올린다(SEC-1). 그래서 `owner_id` 는 **언제나 행에 실어 돌려준다**: 지우고 넣는
    쪽이 스코프를 다시 지어내지 않게.

    🔴 **겹침은 소유자까지 함께 봐야 한다.** 같은 논리 id 가 스페이스마다 사는 것은
    정상이다(새 스페이스는 전부 같은 네 역할로 시작한다) — 그것은 겹침이 아니라
    멀티테넌시이고, 미러의 키가 `vec_id`(소유자+id)인 이유다. 진짜 겹침은 **한 소유자
    안에서** 같은 id 가 두 표에 있는 경우다: 각 표는 자기 pk 로 유일하지만 표 사이에는
    아무 제약이 없어 한 논리 id 가 Concept 이면서 Decision 일 수 있다.

    ⚠ 2026-09-10 에 이 둘을 헷갈려 스페이스 간 중복을 그래프 결함으로 읽었다. 실제
    결함은 미러의 키였다. 그래도 걸러 내기 자체는 남긴다 — 한 소유자 안의 진짜 겹침은
    여전히 삽입을 통째로 죽이고, 한 노드의 결함이 팔 전체를 끄게 둘 이유가 없다.
    SPEC 순서로 먼저 온 것을 남기고, 무엇이 겹쳤는지 함께 돌려준다(조용히 버리면
    그래프의 진짜 결함이 미러 뒤에 숨는다)."""
    out: list[dict] = []
    seen: dict[tuple[str, str], str] = {}
    collisions: list[dict] = []
    for label, pk, kind, extra_col in SPEC:
        proj = f"c.{extra_col} AS extra" if extra_col else "'' AS extra"
        # 토큰 텍스트 열은 ColBERT 가 켜졌을 때만 읽는다 — 넓은 텍스트 2만 행을 들면 그것만으로
        # 수십 MB 다(이 컨테이너는 1GiB 를 셋이 나눠 쓴다).
        tok = (", {} AS t_one, {} AS t_summ, {} AS t_props".format(*TOKEN_COLS[label])
               if with_tokens else "")
        where = "WHERE c.owner_id = $o " if owner else ""
        rows = store.run(
            f"MATCH (c:{label}) {where}"
            f"RETURN c.{pk} AS id, c.title AS title, c.owner_id AS owner, {proj}{tok}",
            {"o": owner} if owner else None) or []
        for r in rows:
            title = " ".join(str(r.get("title") or "").split())
            text = mirror_text(title, extra_text(kind, r.get("extra")), kind)
            if not text:
                continue
            key = (r.get("owner") or "", r["id"])
            if key in seen:
                collisions.append({"id": r["id"], "owner": key[0],
                                   "kept": seen[key], "dropped": kind})
                continue
            seen[key] = kind
            row = {"id": r["id"], "kind": kind, "owner": key[0], "title": title, "text": text}
            if with_tokens:
                row["ctext"] = token_text(title, r.get("t_summ"), r.get("t_one"), r.get("t_props"))
            out.append(row)
    return out, collisions


def token_text(title: str, summary, one_liner, props_json) -> str:
    """ColBERT 입력(`vinc_rag.colbert.token_text`). dense 미러 텍스트보다 넓다 — 재채점 근거를
    이 텍스트로 쟀다. props 는 열에 JSON 문자열로 산다."""
    from vinc_rag.colbert import token_text as _tt
    try:
        props = json.loads(props_json) if isinstance(props_json, str) and props_json else (props_json or {})
    except ValueError:
        props = {}
    return _tt(title, str(summary or ""), str(one_liner or ""), props if isinstance(props, dict) else {})


def colbert_state(store, owner: str | None = None) -> dict[str, tuple[str, str]]:
    """미러 행마다 지금 실린 토큰의 `(colbert_hash, colbert_books)`. 코드는 안 읽는다(행당 수십 KB)."""
    where = "WHERE n.owner_id = $o " if owner else ""
    got = store.run(f"MATCH (n:NodeVec) {where}RETURN n.vec_id AS v, n.colbert_hash AS h, "
                    "n.colbert_books AS b", {"o": owner} if owner else None) or []
    return {g["v"]: (g.get("h") or "", g.get("b") or "") for g in got}


def colbert_hash(ctext: str) -> str:
    return hashlib.sha256(ctext.encode("utf-8")).hexdigest()


def colbert_todo(rows: list[dict], state: dict[str, tuple[str, str]], books: str) -> list[dict]:
    """토큰을 (다시) 만들 행 — 토큰 텍스트나 코드북이 바뀌었거나 아직 없는 것. 미러에 없는 행
    (방금 dense 단계가 실패한 행)은 건드리지 않는다: SET 할 자리가 없다."""
    out = []
    for r in rows:
        vid = vec_id(r.get("owner") or "", r["id"])
        if vid in state and state[vid] != (colbert_hash(r.get("ctext") or ""), books):
            out.append(r)
    return out


def set_colbert(store, updates: list[dict], books: str) -> int:
    """`updates` = [{vid, codes, hash}] 를 행에 싣는다. 벡터 열은 안 건드리는 값 교체라 색인을
    내리지 않는다(색인이 문제 삼는 것은 없던 벡터가 생기는 삽입뿐이다, 위 모듈 주석)."""
    for i in range(0, len(updates), INSERT_BATCH):
        store.run("UNWIND $rows AS r MATCH (n:NodeVec) WHERE n.vec_id = r.vid "
                  "SET n.colbert = r.codes, n.colbert_books = $b, n.colbert_hash = r.hash",
                  {"rows": updates[i:i + INSERT_BATCH], "b": books})
    return len(updates)


def existing(store, owner: str | None = None) -> dict[str, tuple[str, str, str]]:
    """지금 미러에 있는 행: `vec_id → (text, model, encoder_runtime)`. **벡터는 안 읽는다**
    — 행당 4KB 를 2만 행 읽으면 그것만으로 재구축이 피하려던 메모리가 된다."""
    where = "WHERE n.owner_id = $o " if owner else ""
    got = store.run(f"MATCH (n:NodeVec) {where}RETURN n.vec_id AS v, n.text AS t, "
                    "n.model AS m, n.encoder_runtime AS r", {"o": owner} if owner else None) or []
    return {g["v"]: (g.get("t") or "", g.get("m") or "", g.get("r") or "") for g in got}


def plan(rows: list[dict], have: dict, model: str,
         encoder_runtime: str) -> tuple[list[dict], list[str]]:
    """다시 인코딩할 행과 지울 vec_id. 글자·모델·런타임이 셋 다 같은 행은 **제자리에 둔다**
    — 같은 입력이면 같은 벡터다(`ChunkVec` 이 배포를 넘어 같은 런타임 지문으로 사는 것과
    같은 근거). 지울 것은 그래프에서 사라진 노드와, 다시 넣을 행의 옛 사본이다.

    인덱스는 여전히 통째다: 넣을 것이 하나라도 있으면 :func:`replace` 가 드롭 → 삽입 →
    생성을 그대로 밟는다. 아끼는 것은 인코딩과 삭제·삽입의 **행 수**뿐이다.
    근거: [[decision:the-node-mirror-keeps-unchanged-rows-and-rebuilds-its-index-whole-2026-10-02]]"""
    want: set[str] = set()
    todo: list[dict] = []
    for r in rows:
        vid = vec_id(r.get("owner") or "", r["id"])
        want.add(vid)
        if have.get(vid) != (r["text"], model, encoder_runtime):
            todo.append(r)
    redo = {vec_id(r.get("owner") or "", r["id"]) for r in todo}
    return todo, [v for v in have if v not in want or v in redo]


def index_answers(store) -> bool:
    """미러의 벡터 색인이 답하는가 — 미러의 아무 벡터로 되묻는다. 비었으면 거짓.
    소유자는 걸지 않는다: 재는 것은 색인이지 소유자 필터가 아니다.
    `use_index=True`: 작은 미러는 검색이 정확 스캔으로 가므로, 색인을 직접 물어야 한다."""
    got = store.run("MATCH (n:NodeVec) RETURN n.embedding AS e LIMIT 1") or []
    try:
        return bool(got and got[0].get("e")
                    and store.node_vector_search(got[0]["e"], 1, use_index=True))
    except RuntimeError as exc:
        # 준비 판정만 false로 답한다. 일반 검색의 실패 보고는 유지한다.
        if str(exc) in ("node_vector_extension_unavailable", "node_vector_query_failed"):
            return False
        raise


def replace(store, rows: list[dict], model: str, encoder_runtime: str = "",
            stale: list[str] | None = None) -> dict:
    """미러를 갈아 끼운다. 각 행에 `emb`(벡터)가 이미 있어야 한다.

    `stale` 이 없으면 **통째**다: 지우는 범위는 실린 행들의 소유자다. 전량 재구축을 한
    소유자에게 돌려도 다른 소유자의 미러가 살아남고, 인덱스는 그 남은 벡터들 위에서 다시
    만들어진다(빌드는 삽입이 아니라 일괄 구축이라 위의 세그폴트 자리가 아니다).
    `stale` 이 있으면 그 vec_id 만 지우고 `rows` 만 넣는다(:func:`plan`). 순서는 같다."""
    owners = sorted({str(r.get("owner") or "") for r in rows})
    # 🔴 미러에 잔해 색인이 있으면 아래 삭제·삽입이 **세그폴트**다 — 이 재구축은 잔해를
    # 고치지 못한다. 고치는 것은 기동이다(`heal_node_vector_index`). 먼저 말하고 멈춘다.
    # 근거: [[record:a-remnant-segfaults-every-write-to-its-table-not-only-inserts-2026-09-25]]
    if getattr(store, "node_vector_index_remnant", False):
        return {"kind": "node-mirror.replace", "rows": 0, "owners": owners, "model": model,
                "index_ok": False, "error": "node_vector_index_remnant"}
    store.drop_node_vector_index()
    if stale is None:
        store.run("MATCH (n:NodeVec) WHERE n.owner_id IN $o DELETE n", {"o": owners})
    gone = stale or []
    for i in range(0, len(gone), INSERT_BATCH):
        store.run("MATCH (n:NodeVec) WHERE n.vec_id IN $v DELETE n",
                  {"v": gone[i:i + INSERT_BATCH]})
    for i in range(0, len(rows), INSERT_BATCH):
        store.run(
            "UNWIND $rows AS r CREATE (:NodeVec {vec_id: r.vid, node_id: r.id, "
            "kind: r.kind, owner_id: r.owner, title: r.title, text: r.text, "
            "model: $m, embedding: r.emb, encoder_runtime: $runtime})",
            {"rows": [{"vid": vec_id(r.get("owner") or "", r["id"]), "id": r["id"],
                       "kind": r["kind"], "owner": r.get("owner") or "",
                       "title": r["title"], "text": r["text"], "emb": list(r["emb"])}
                      for r in rows[i:i + INSERT_BATCH]],
             "m": model, "runtime": encoder_runtime})
    # ⚠ 인덱스 생성은 예외를 삼킨다(멱등이라 그래야 한다). 그래서 **삼킨 사유를 받아
    # 두고 결과도 확인한다** — 행은 들어갔는데 인덱스가 없으면 의미 팔은 예외 없이 빈
    # 목록만 내고, 밖에서는 "안 켠 것"과 구분되지 않는다. 2026-09-10 라이브에서 정확히
    # 그랬고, 사유가 없어서 원인을 두 배포에 걸쳐 못 짚었다.
    # 확인은 방금 넣은 벡터로 되묻는 것이다: 무엇이든 돌아오면 인덱스가 있다. 소유자는
    # 걸지 않는다 — 재는 것은 색인이지 소유자 필터가 아니다(전역 상위 k 에 그 소유자가
    # 없어 거짓 음성이 났다).
    index_error = store.create_node_vector_index() or ""
    index_ok = index_answers(store)
    return {"kind": "node-mirror.replace", "rows": len(rows), "owners": owners,
            "model": model, "index_ok": index_ok, "index_error": index_error}
