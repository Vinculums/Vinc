"""`.vincpack` 컨테이너 입출력 (D55).

**왜 컨테이너인가.** `.vincpack` 은 여태 확장자만 있고 컨테이너가 없었다 — 그냥
pretty-print JSON 이었다(매직넘버·압축·무결성 전부 없음). 개념만 담을 때는 그래도
됐지만 D45 가 팩에 **원문(`documents`)** 을 싣기 시작하면서 셋이 한꺼번에 아프다:

  · 크기 — 실측 개념 74개짜리가 이미 1.6MB, gzip 하면 461KB(28%). 원문을 실으면 배가 된다.
  · 통짜 파싱 — 문서 하나만 보고 싶어도 전체를 읽어야 한다.
  · 바이너리 — 이미지·PDF 를 넣으려면 base64 로 33% 더 부푼다.

zip 은 이 셋을 한 번에 풀고, **표준이라 사람이 열어볼 수 있다**는 기존 장점(텍스트
JSON 이라 눈으로 확인 가능)도 잃지 않는다. 스펙이 유보했던 무결성도 zip CRC 로
공짜로 따라온다(서명은 여전히 별건 — vinc.c.sharepack §미도입).

**레이아웃**::

    manifest.json      {vinc_pack, kind, manifest{...}}   ← 버전·범위·집계
    fragment.json      {nodes, edges}                     ← 그래프 조각
    documents/0001.json …                                 ← 원문 한 건당 한 파일

**하위호환이 이 모듈의 핵심 계약이다.** 커뮤니티 갤러리에 이미 구 JSON 팩이 올라가
있으므로 **읽기는 두 형식 모두** 받고 **쓰기만** zip 으로 간다. 판별은 첫 바이트로
한다: ``PK`` = zip, ``{`` = 구 JSON. `vinc_pack` 정수 검증은 그대로 살아 있다
(컨테이너가 바뀐 것이지 내용 계약이 바뀐 것이 아니다 — 버전을 올리지 않는 이유).
"""
from __future__ import annotations

import io
import json
import zipfile

MANIFEST_NAME = "manifest.json"
FRAGMENT_NAME = "fragment.json"
DOC_DIR = "documents/"


def is_zip(data: bytes) -> bool:
    """zip 매직(`PK\\x03\\x04`). 빈 zip(`PK\\x05\\x06`)도 형식상 zip 이라 함께 본다."""
    return data[:2] == b"PK"


def to_bytes(pack: dict) -> bytes:
    """팩 dict → zip 바이트. 문서는 건별 파일로 흩고 나머지는 두 조각으로 나눈다.

    `documents` 는 팩에 없을 수도 있다(구 팩·문서 없는 슬라이스) — 그때는
    `documents/` 자체를 만들지 않는다. 압축은 DEFLATE(zip 기본 호환).
    파일 순서는 **입력 순서 그대로**라 같은 팩이면 같은 바이트가 나온다(결정론 —
    D17 의 태도를 파일에도 적용. 타임스탬프는 zipfile 기본을 그대로 두면 흔들리므로
    고정값을 준다)."""
    body = dict(pack)
    documents = body.pop("documents", None) or []
    fragment = body.pop("fragment", None) or {"nodes": [], "edges": []}
    body.pop("executed", None)          # 전송 상태값 — 파일에 담지 않는다
    buf = io.BytesIO()
    # 고정 타임스탬프(1980-01-01) — zip 헤더의 mtime 이 매 실행 달라지면 같은 팩이
    # 다른 바이트가 되어 해시 비교·중복 판정이 무의미해진다.
    fixed = (1980, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        def put(name: str, obj) -> None:
            zi = zipfile.ZipInfo(name, date_time=fixed)
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, json.dumps(obj, ensure_ascii=False, indent=2))
        put(MANIFEST_NAME, body)
        put(FRAGMENT_NAME, fragment)
        for i, doc in enumerate(documents, 1):
            put(f"{DOC_DIR}{i:04d}.json", doc)
    return buf.getvalue()


def from_bytes(data: bytes) -> dict:
    """zip 또는 구 JSON 바이트 → 팩 dict. 형식은 첫 바이트로 가른다.

    zip 안에 `manifest.json` 이 없으면 팩이 아니다 — 아무 zip 이나 삼키지 않는다.
    반환 모양은 두 경로가 **완전히 같다**(호출부가 형식을 몰라도 되게)."""
    if not is_zip(data):
        return json.loads(data.decode("utf-8"))
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        if MANIFEST_NAME not in names:
            raise ValueError("not a vinc pack: manifest.json missing")
        pack = json.loads(z.read(MANIFEST_NAME).decode("utf-8"))
        pack["fragment"] = (json.loads(z.read(FRAGMENT_NAME).decode("utf-8"))
                            if FRAGMENT_NAME in names else {"nodes": [], "edges": []})
        docs = sorted(n for n in names
                      if n.startswith(DOC_DIR) and n.endswith(".json"))
        if docs:
            pack["documents"] = [json.loads(z.read(n).decode("utf-8")) for n in docs]
    return pack


def read_file(path) -> dict:
    """경로에서 팩을 읽는다(형식 무관). 텍스트가 아니라 **바이트**로 연다 —
    zip 을 텍스트로 열면 깨진다."""
    with open(path, "rb") as f:
        return from_bytes(f.read())


def write_file(path, pack: dict) -> int:
    """팩을 zip 컨테이너로 쓴다. 반환 = 쓴 바이트 수."""
    data = to_bytes(pack)
    with open(path, "wb") as f:
        f.write(data)
    return len(data)
