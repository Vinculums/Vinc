# -*- coding: utf-8 -*-
"""합성 문서의 교체 실패를 실제 임베디드 엔진에서 재현한다.

`put_doc` 은 문서 하나를 트랜잭션 하나로 쓴다(`store.write_batch`). 그래서 교체가 도중에 실패하거나
프로세스가 죽으면 이전 문서가 그대로 남는다. 한 문장씩 쓰는 경로(`VINC_FRAGMENT_BATCH=off`)에서는
이전 청크 보존과 불완전 본문 읽기 거절이 다른 보장이고, 전체 원문 재전송이 복구 전제다. 두 경로 다
테스트 결과에 남긴다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from api import docstore as ds
from store.backend import open_store
from store.config import StoreConfig
from store.embedded_backend import import_driver

DRIVER, DRIVER_NAME = import_driver()
pytestmark = pytest.mark.skipif(DRIVER is None, reason="임베디드 드라이버 미설치")
OWNER, DOC = "failure-test-owner", "failure-test-doc"
OLD = "이전 합성 본문입니다.\n" * 5
NEW = "새로운 합성 문서 내용을 기록합니다.\n" * 40
STAGES = ["delete_before", "delete_after", "insert_before", "insert_after", "insert_middle", "meta_before", "meta_after"]


def make_store(path):
    # 사용자 설정과 데이터 경로를 읽지 않는다. 모델·검색 확장 다운로드도 하지 않는다.
    store = open_store(StoreConfig(data_dir=str(path), max_db_size=1024**3, buffer_pool_mb=256))
    assert store is not None, "합성 임시 스토어를 열지 못함"
    def no_extensions(*args, **kwargs):
        raise RuntimeError("failure test: extensions disabled, no download")
    store._load_ext = no_extensions
    return store


def chunks(store):
    return store.run("MATCH (c:Chunk) WHERE c.doc_id = $d "
                     "RETURN c.text AS text ORDER BY c.chunk_index", {"d": DOC})


class InjectedFailure(RuntimeError):
    pass


class FailingStore:
    def __init__(self, store, stage, *, abrupt=False):
        self.store, self.stage, self.abrupt = store, stage, abrupt
        self.hit = False
        self.inserts = 0

    def __getattr__(self, name):
        return getattr(self.store, name)

    def fail(self):
        self.hit = True
        if self.abrupt:
            # 합성 자식 프로세스만 종료한다. 운영 프로세스나 원본 CLI에는 접근하지 않는다.
            os._exit(23)
        raise InjectedFailure(self.stage)

    def run(self, query, params=None, **kwargs):
        delete = query.startswith("MATCH (c:Chunk)") and "DETACH DELETE c" in query
        insert = query.startswith("MERGE (c:Chunk")
        meta = query.startswith("MERGE (d:_DocMeta")
        if insert:
            self.inserts += 1
        before = ((delete and self.stage == "delete_before") or
                  (insert and self.stage == "insert_before" and self.inserts == 1) or
                  (insert and self.stage == "insert_middle" and self.inserts == 2) or
                  (meta and self.stage == "meta_before"))
        after = ((delete and self.stage == "delete_after") or
                 (insert and self.stage == "insert_after" and self.inserts == 1) or
                 (meta and self.stage == "meta_after"))
        if before:
            self.fail()
        result = self.store.run(query, params, **kwargs)
        if after:
            self.fail()
        return result


def write(store, text):
    return ds.put_doc(store, owner=OWNER, doc_id=DOC, text=text, title="합성 문서", max_chars=120, link=False)


def check_interrupted(store, stage, old_chunks, *, batched=True):
    current = chunks(store)
    result = ds.get_doc(store, DOC, owner=OWNER)
    if batched:
        # 트랜잭션 하나였으므로 어느 단계에서 끊겨도 이전 문서가 온전히 남는다.
        assert current == old_chunks
        assert result["executed"] and result["verified"] and result["text"] == OLD
        return {"stage": stage, "old_chunks_preserved": True,
                "read_executed": True, "live_chunks": len(current)}
    if stage == "delete_before":
        assert current == old_chunks  # 삭제 전에는 이전 청크가 물리적으로 남는다.
    else:
        assert current != old_chunks  # 삭제 후에는 이전 버전 자동 보존을 보장하지 않는다.
    if stage == "meta_after":
        assert result["executed"] and result["verified"] and result["text"] == NEW
    else:
        assert result["executed"] is False and "text" not in result
        assert result["chunk_count"] == -1  # 잘린 새 본문을 정상 문서로 답하지 않는다.
    return {"stage": stage, "old_chunks_preserved": current == old_chunks,
            "read_executed": result["executed"], "live_chunks": len(current)}


@pytest.mark.parametrize("batch", ["on", "off"])
@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("retry_text", [OLD, NEW], ids=["retry_original", "retry_replacement"])
def test_exception_reopen_and_full_text_retry(tmp_path, stage, retry_text, batch, monkeypatch):
    monkeypatch.setenv("VINC_FRAGMENT_BATCH", batch)
    batched = batch == "on"
    path = tmp_path / "synthetic-store"
    store = make_store(path)
    try:
        store.provision(dim=4)
        assert write(store, OLD)["executed"]
        old_chunks = chunks(store)
        injected = FailingStore(store, stage)
        with pytest.raises(InjectedFailure):
            write(injected, NEW)
        assert injected.hit
        before = check_interrupted(store, stage, old_chunks, batched=batched)
    finally:
        store.close()
    store = make_store(path)
    try:
        assert check_interrupted(store, stage, old_chunks, batched=batched) == before
        assert write(store, retry_text)["executed"]
        result = ds.get_doc(store, DOC, owner=OWNER)
        assert result["executed"] and result["verified"] and result["text"] == retry_text
        assert ds.chunks_intact(store, DOC)
    finally:
        store.close()


@pytest.mark.parametrize("stage", STAGES)
def test_abrupt_process_restart_and_full_text_retry(tmp_path, stage):
    path = tmp_path / "synthetic-store"
    store = make_store(path)
    try:
        store.provision(dim=4)
        assert write(store, OLD)["executed"]
        old_chunks = chunks(store)
    finally:
        store.close()
    child = subprocess.run([sys.executable, "-E", "-X", "utf8", str(Path(__file__)),
                            "--child-fail", str(path), stage], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert child.returncode == 23, child.stderr
    store = make_store(path)
    try:
        observation = check_interrupted(store, stage, old_chunks)
        assert write(store, NEW)["executed"]
        result = ds.get_doc(store, DOC, owner=OWNER)
        assert result["executed"] and result["verified"] and result["text"] == NEW
        # pytest -s 출력은 합성 데이터의 상태값뿐이다. 문서 본문·키·운영 로그는 없다.
        print(json.dumps({**observation, "restart": "abrupt_child_exit", "full_text_retry_verified": True}))
    finally:
        store.close()


if __name__ == "__main__":
    assert len(sys.argv) == 4 and sys.argv[1] == "--child-fail"
    child_store = make_store(sys.argv[2])
    write(FailingStore(child_store, sys.argv[3], abrupt=True), NEW)
    raise RuntimeError("failure injection did not fire")
