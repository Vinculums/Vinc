"""급사 후 살아 있는 정본이 FTS에서도 다시 검색돼야 한다."""
import os
from pathlib import Path
import signal
import subprocess
import sys
from unittest import mock

import pytest

from api.docstore import get_doc
from store.backend import open_store
from store.config import load_config
from store.embedded_backend import _import_driver

pytestmark = pytest.mark.skipif(sys.platform != "linux" or _import_driver()[0] is None,
                               reason="Linux SIGKILL and the embedded driver are required")
ROOT = Path(__file__).resolve().parents[2]
TEXT = "durability sentinel checkpoint recovery evidence"
OWNER = "user:recovery-probe"
WRITER = r'''
import os,signal,sys
sys.path.insert(0,sys.argv[1])
from api.docstore import put_doc
from store.backend import open_store
from store.config import load_config
s=open_store(load_config({'data_dir':sys.argv[2]}));assert s is not None
s.provision(dim=768);s.heal_fts_index(dim=768)
r=put_doc(s,owner='user:recovery-probe',doc_id='sentinel',text=sys.argv[3],link=False)
assert r['executed'] and not r['warnings'],r
assert s.fulltext_search('sentinel',10,{'owner_id':'user:recovery-probe'})
os.kill(os.getpid(),signal.SIGKILL)
'''


@pytest.fixture
def crashed(tmp_path, monkeypatch):
    monkeypatch.setenv("VINC_BUFFER_POOL_MB", "128")
    monkeypatch.setenv("VINC_MAX_DB_SIZE", "536870912")
    monkeypatch.setenv("VINC_WAL_REPLAY_TOLERANT", "1")
    path = tmp_path / "db"
    child = subprocess.run([sys.executable, "-c", WRITER, str(ROOT), str(path), TEXT],
                           capture_output=True, text=True, timeout=60)
    assert child.returncode == -signal.SIGKILL, child.stderr
    assert Path(str(path) + ".wal").stat().st_size > 0
    return path


def opened(path):
    store = open_store(load_config({"data_dir": str(path)}))
    assert store is not None
    store.provision(dim=768)
    return store


def assert_recovered(store):
    store.heal_fts_index(dim=768)
    document = get_doc(store, "sentinel", owner=OWNER)
    assert document["verified"] and document["text"] == TEXT
    assert store.fulltext_search("sentinel", 10, {"owner_id": OWNER})


def test_replayed_document_is_searchable_after_boot_heal(crashed):
    store = opened(crashed)
    try:
        with pytest.raises(RuntimeError, match="FTS recovery"):
            store.snapshot(str(crashed.parent / "backup"))
        assert_recovered(store)
    finally:
        store.close()


def test_recovery_obligation_survives_close_before_heal(crashed):
    store = opened(crashed)
    store.close()  # WAL 재생 뒤 체크포인트돼도 미완료 수리는 사라지면 안 된다.
    store = opened(crashed)
    try:
        assert_recovered(store)
    finally:
        store.close()


def test_failed_rebuild_retains_the_obligation_and_refuses_stale_search(crashed):
    store = opened(crashed)
    try:
        with mock.patch.object(store, "rebuild_fts_index", side_effect=RuntimeError("copy unavailable")):
            with pytest.raises(RuntimeError, match="copy unavailable"):
                store.heal_fts_index(dim=768)
        with pytest.raises(RuntimeError, match="FTS recovery"):
            store.fulltext_search("sentinel", 10, {"owner_id": OWNER})
        assert_recovered(store)
        with mock.patch.object(store, "rebuild_fts_index", side_effect=AssertionError("repeat rebuild")):
            assert store.heal_fts_index(dim=768)["state"] == "present"
    finally:
        store.close()
