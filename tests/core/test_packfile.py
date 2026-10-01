# -*- coding: utf-8 -*-
"""packfile 컨테이너 — 라운드트립 + 압축폭탄 방어(보안 감사 2026-08-18 H2)."""
import io
import json
import zipfile

import pytest

from api import packfile


def _zip(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in members:
            z.writestr(name, content)
    return buf.getvalue()


def test_roundtrip_still_works():
    pack = {"vinc_pack": 1, "kind": "test",
            "fragment": {"nodes": [{"id": "concept:x", "type": "concept"}], "edges": []},
            "documents": [{"doc_id": "d1", "text": "hi"}]}
    got = packfile.from_bytes(packfile.to_bytes(pack))
    assert got["vinc_pack"] == 1
    assert got["fragment"]["nodes"][0]["id"] == "concept:x"
    assert got["documents"] == [{"doc_id": "d1", "text": "hi"}]


def test_rejects_too_many_entries():
    members = [(packfile.MANIFEST_NAME, json.dumps({"vinc_pack": 1}))]
    members += [(f"{packfile.DOC_DIR}{i:05d}.json", "{}")
                for i in range(packfile.MAX_ENTRIES + 1)]
    with pytest.raises(ValueError, match="too many entries"):
        packfile.from_bytes(_zip(members))


def test_rejects_decompression_bomb():
    # 한 멤버가 인플레이트 상한을 넘게 **선언**하도록 크게 만든다 — zeros 라 압축은
    # 잘 되지만(디스크 수십 KB) 중앙 디렉터리의 file_size 는 64MB+1 이다. 방어는
    # 아무 멤버도 read 하기 전에 그 선언 합만 보고 거부한다(메모리에 안 푼다).
    data = _zip([(packfile.MANIFEST_NAME, b"\0" * (packfile.MAX_UNCOMPRESSED + 1))])
    with pytest.raises(ValueError, match="inflates past"):
        packfile.from_bytes(data)
