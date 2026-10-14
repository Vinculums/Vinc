# v0.9.7 api/share.py exact import boundary.
# Source: 9b7b97962ee859351175e344eced247ee3a6a337
# Function SHA256: e3a44d068645fa56a1fc6befe2f7d36f49c25f31f08d3788990c721bc4ad8a13
def import_share_pack(pack: dict, *, owner: str, cfg=None, store=None,
                      max_domains: int | None = None) -> dict:
    """공유 팩 들여오기 — 수신자 ``owner`` 로 재스탬핑해 fragment MERGE(멱등).

    자기완결 팩(선택 ``documents``, D45)은 개념 MERGE **다음에** 원문을 임포트한다 —
    수신 owner 로 재청킹하고, MENTIONS 재링크가 방금 들어온 개념에 붙게 하기 위한
    순서다. 구버전 팩엔 ``documents`` 가 없어 이 단계가 스킵된다(하위호환).

    ``max_domains``(선택, AP-1 §3.3): 무료 등급 주제 상한. 팩은 새 주제가 한 번에
    여럿 들어오는 경로라 상한을 태운다 — 안 태우면 그대로 우회로가 된다. 상한에
    걸리면 **문서 임포트 이전에** 멈춘다(개념 없이 원문만 남는 반쪽 상태 방지)."""
    if not isinstance(pack, dict) or pack.get("vinc_pack") != PACK_VERSION:
        return {"executed": False,
                "error": f"unsupported pack version: {pack.get('vinc_pack')!r} "
                         f"(expected {PACK_VERSION})"}
    fragment = pack.get("fragment") or {}
    documents = pack.get("documents") or []
    owns = store is None
    if owns:
        from store.backend import open_store
        from store.config import load_config
        store = open_store(cfg or load_config())
    if store is None:
        return {"executed": False, "error": "no reachable store"}
    try:
        res = frag.merge_fragment({**fragment, "owner": owner}, store=store,
                                  max_domains=max_domains)
        if res.get("error") == "domain_limit_reached":
            return res            # 개념이 거부됐으면 원문도 넣지 않는다
        if documents:
            # 원문 복원 — put_doc 으로 수신 owner 에 재청킹한다. **doc_id 는 재사용하지
            # 않고 새로 파생한다**(doc_id=None): 팩은 교차-owner 공유라, 호스티드 단일
            # 스토어에서 원저작자의 doc_id 를 그대로 쓰면 그의 _DocMeta(owner 불일치)에
            # 부딪혀 거부된다. put_doc 이 (수신owner,content_hash,title)로 파생 → 충돌
            # 없음 + 내용 동일 재임포트는 멱등(content_hash 매치 시 skip). MENTIONS 는
            # 방금 merge 된 개념에 재링크(순서: fragment 먼저 → 여기). embedding 은
            # 미전송·수신 재생성(스택 없으면 FTS 지연 — 기존 규율과 동일).
            dapplied = dskipped = 0
            for d in documents:
                r = ds.put_doc(store, owner=owner, text=d.get("text") or "",
                               doc_id=None, title=d.get("title") or "",
                               origin=d.get("origin") or "",
                               # 내용 시점은 지시체의 것이라 공간을 건너도 참이다.
                               # created_at 은 넘기지 않는다 — 위 export 주석 참조.
                               content_time=d.get("content_time") or None)
                if r.get("executed") and r.get("skipped"):
                    dskipped += 1
                elif r.get("executed"):
                    dapplied += 1
            res["documents"] = {"applied": dapplied, "skipped": dskipped,
                                 "total": len(documents)}
    finally:
        if owns:
            store.close()
    res["manifest"] = pack.get("manifest")
    return res
