# -*- coding: utf-8 -*-
"""파일 바이트 → 원문 텍스트 (D45 입력 어댑터).

`put_doc` 은 **파일 I/O 없음**이 명시 계약이라([[vinc.canon.chunk-ssot]]), 파일에서
텍스트를 꺼내는 일은 그 바깥에 둔다. 이 모듈이 그 유일한 지점이다: 확장자와
매직바이트로 형식을 판별하고, 형식별 추출기를 태워 raw 텍스트를 돌려준다.
스토어·네트워크·LLM 을 모르는 **순수 모듈**이라 드라이버 없이 테스트된다.

왜 모아야 했나 — 파일 읽기가 `open(..., encoding="utf-8")` 으로 엔진마다 흩어져
있었고 `errors=` 도 없어서, cp949 로 저장된 한국어 메모 하나가 UnicodeDecodeError
로 적재를 통째로 죽였다. 디코드는 여기 사다리 하나로 수렴시킨다.

바이너리(PDF)는 **선택 의존**(`pypdf`)이며 지연 임포트 + 강등이다: 없으면
executed=False 와 사유 코드를 돌려주고 호출부가 읽히는 문구를 준다(임베딩 스택
부재를 다루는 `/doc/reembed` 와 같은 규율 — 조용한 실패 금지).

반환 계약은 사이드카 응답과 동형이다:
    {"kind": "text.extract", "executed": bool, "format": str,
     "text": str,            # executed=True 일 때만
     "error": str, "note": str}   # executed=False 일 때만
`error` 는 **안정 코드**(호출부가 카피로 매핑), `note` 는 사람용 진단이다.
"""
from __future__ import annotations

import io
import os

# 원문 그대로 텍스트로 받는 확장자. 프론트 accept 목록(app/src/doc-import.ts)과
# 같은 집합을 유지해야 한다 — 갈라지면 UI 가 받아 놓고 서버가 거절한다.
TEXT_EXTS = (".md", ".markdown", ".txt", ".text", ".rst")
PDF_EXTS = (".pdf",)

_PDF_MAGIC = b"%PDF-"
_UTF8_BOM = b"\xef\xbb\xbf"
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")


def ext_of(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower()


def sniff(filename: str, data: bytes = b"") -> str:
    """``"text"`` | ``"pdf"`` | ``""``(미지원).

    확장자를 우선하되 매직바이트로 보정한다: 확장자는 사용자가 붙이는 라벨이고
    바이트는 사실이라, `.txt` 로 저장된 PDF 를 텍스트로 디코드해 깨진 정본을
    만드는 것보다 바이트를 믿는 편이 안전하다. 반대 방향(확장자 pdf · 내용 텍스트)
    은 정본 손상이 아니므로 확장자를 존중하지 않고 텍스트로 흘린다."""
    if data[:5] == _PDF_MAGIC:
        return "pdf"
    ext = ext_of(filename)
    if ext in PDF_EXTS:
        # 확장자만 pdf 이고 매직이 없다 — 텍스트로 읽히면 텍스트로 받는다.
        return "pdf"
    if ext in TEXT_EXTS:
        return "text"
    return ""


def decode_text(data: bytes) -> str:
    """결정론 디코드 사다리 — BOM → UTF-8 → CP949 → UTF-8(replace).

    마지막 칸이 latin-1 이 아닌 이유: latin-1 은 모든 바이트를 받아내므로 절대
    실패하지 않지만, 한국어 문서를 **그럴듯한 오역**(모지바케)으로 바꿔 놓는다.
    UTF-8 replace 는 손상 자리에 U+FFFD 를 남겨 눈에 보이게 하고, 브라우저
    `File.text()` 의 동작과도 같아 데스크톱·웹 결과가 갈리지 않는다.

    CP949 를 UTF-8 다음에 두는 것은 사용자층(한국어 Windows) 기준의 선택이다 —
    다른 레거시 인코딩은 UTF-8 replace 로 떨어진다."""
    if data[:3] == _UTF8_BOM:
        return data[3:].decode("utf-8", errors="replace")
    if data[:2] in _UTF16_BOMS:
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8", "cp949"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


# 미신뢰 PDF DoS 상한(감사 2026-08-18 M6). PdfReader.pages 를 무제한 순회하며
# extract_text 하면 악의적·기형 PDF(깊은 중첩·순환 페이지트리)가 CPU·메모리를 태운다.
# 허브 경로는 본문 4MB 로 압축본이 묶이지만 작은 PDF 도 병적일 수 있고, CLI 경로는
# 상한이 아예 없다. 페이지 수와 누적 텍스트 길이 둘 다 건다 — 넘으면 자르고 알린다.
MAX_PDF_PAGES = 2000
MAX_PDF_CHARS = 8 * 1024 * 1024

# 목차(북마크) 상한. 항목 수와 제목 길이를 따로 묶어 기형 목차가 위 상한을 돌아
# CPU·메모리를 태우지 못하게 한다. 넘으면 목차를 통째로 버린다.
MAX_OUTLINE_ENTRIES = 10_000
MAX_OUTLINE_TITLE = 300
# 목차 깊이 → 헤딩 수준. 맨 위 항목이 `##` 인 것은 `#` 이 문서 제목 층이라 개념
# 추출(`docstore.extract_concepts`, `##`·`###` 만 읽는다)이 건너뛰기 때문이다.
# 마크다운 헤딩은 여섯 단계라 그 아래는 `######` 로 묶는다.
_OUTLINE_TOP_LEVEL = 2
_MAX_HEADING_LEVEL = 6


class _BrokenOutline(ValueError):
    """목차를 믿을 수 없다. 헤딩 없이 페이지만 이은 출력으로 돌아간다."""


def _resolve(raw):
    """간접 참조를 풀고, 없는 객체(None·PDF null)는 None 으로 돌린다."""
    from pypdf.generic import NullObject
    obj = raw.get_object() if raw is not None else None
    return None if obj is None or isinstance(obj, NullObject) else obj


def _outline_page_ref(reader, node, named: dict):
    """목차 항목이 가리키는 페이지 참조. 문서 안을 가리키지 않으면(목적지 없음·
    GoTo 가 아닌 동작) None, 가리키는데 못 풀면 `_BrokenOutline`."""
    from pypdf.generic import ArrayObject, DictionaryObject
    dest = node.get("/Dest")
    if dest is None:
        action = _resolve(node.get("/A"))
        if action is None:
            return None
        if not isinstance(action, DictionaryObject):
            raise _BrokenOutline("action is not a dictionary")
        if _resolve(action.get("/S")) != "/GoTo":
            return None
        dest = action.get("/D")
    dest = _resolve(dest)
    if isinstance(dest, DictionaryObject):      # 참조된 목적지는 /D 로 감싸여 온다
        dest = _resolve(dest.get("/D"))
    if isinstance(dest, str):                   # 이름 목적지(NameObject·문자열 둘 다 str)
        if "map" not in named:                  # 이름 표는 처음 필요할 때 한 번만 만든다
            named["map"] = reader.named_destinations
        found = named["map"].get(dest)
        if found is None:
            raise _BrokenOutline("unknown named destination")
        return found.page
    if isinstance(dest, ArrayObject) and len(dest) > 0:
        return dest[0]
    raise _BrokenOutline("unreadable destination")


def _outline_headings(reader, page_ids: dict, truncated: bool) -> dict:
    """목차 → ``{페이지 번호: [헤딩 줄, ...]}``. 목차 순서(부모 먼저, 그다음 자식,
    그다음 형제)를 지키므로 한 페이지의 여러 항목도 순서대로 쌓인다.

    한 항목이라도 못 읽으면(사전이 아님·제목 없음·상한 초과·같은 항목 재방문·
    가리키는 페이지를 못 찾음) `_BrokenOutline`. 페이지 상한으로 잘린 뒤쪽 페이지를
    가리키는 항목만은 오류가 아니라 버린다(그 페이지가 출력에 없다)."""
    from pypdf.generic import DictionaryObject, IndirectObject
    outlines = _resolve(reader.root_object.get("/Outlines"))
    if outlines is None:
        return {}
    if not isinstance(outlines, DictionaryObject):
        raise _BrokenOutline("outlines is not a dictionary")
    heads: dict = {}
    named: dict = {}
    seen: set = set()
    stack = [(outlines.get("/First"), 0)]
    while stack:
        raw, depth = stack.pop()
        if raw is None:
            continue
        node = _resolve(raw)
        # 한 번 본 항목을 다시 만나면 순환이거나 자식 공유다. 둘 다 끝없이 또는
        # 지수적으로 불어나므로 기형으로 본다.
        if not isinstance(node, DictionaryObject) or id(node) in seen:
            raise _BrokenOutline("cycle or non-dictionary entry")
        seen.add(id(node))
        if len(seen) > MAX_OUTLINE_ENTRIES:
            raise _BrokenOutline("too many outline entries")
        # 스택이라 형제를 먼저 넣어야 자식이 먼저 나온다.
        stack.append((node.get("/Next"), depth))
        stack.append((node.get("/First"), depth + 1))
        title = _resolve(node.get("/Title"))
        # 길이를 먼저 본다: 한 제목 객체를 여러 항목이 공유할 수 있어서, 거대한 제목을
        # 항목마다 다듬으면 상한이 무력해진다.
        if not isinstance(title, str) or len(title) > MAX_OUTLINE_TITLE:
            raise _BrokenOutline("missing or oversized title")
        title = " ".join(title.split())          # 헤딩은 한 줄이어야 한다
        if not title:
            raise _BrokenOutline("empty title")
        ref = _outline_page_ref(reader, node, named)
        if ref is None:
            continue                             # 묶음 항목: 헤딩 없이 자식만 읽는다
        if not isinstance(ref, IndirectObject):
            ref = getattr(ref, "indirect_reference", None)
        idx = page_ids.get(ref.idnum) if isinstance(ref, IndirectObject) else None
        if idx is None:
            if truncated:
                continue
            raise _BrokenOutline("destination is not a page of this document")
        level = min(_OUTLINE_TOP_LEVEL + depth, _MAX_HEADING_LEVEL)
        heads.setdefault(idx, []).append("#" * level + " " + title)
    return heads


def _extract_pdf(data: bytes) -> dict:
    """PDF → 텍스트. 페이지는 빈 줄로 잇는다 — `plan_boundaries` 가 문단 경계를
    1순위로 삼으므로 페이지 경계가 자연스럽게 청크 경계가 된다.

    목차(북마크)가 있으면 항목 제목을 그것이 가리키는 페이지 앞에 마크다운 헤딩으로
    넣는다. 적재 뒤의 결정론 개념 추출이 헤딩을 읽으므로, 이것이 없으면 PDF 는 후보가
    0개다. 목차가 없거나 못 읽으면 출력은 헤딩 없이 페이지만 이은 것과 같다."""
    try:
        from pypdf import PdfReader          # 선택 의존 — 지연 임포트
    except Exception:
        return _fail("pdf", "pdf_support_missing",
                     "이 빌드에는 PDF 추출기가 포함돼 있지 않습니다.")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            # 빈 암호로 열리는 것(소유자 암호만 걸린 PDF)까지는 받아준다. 실제
            # 복호화가 필요한 문서는 강등한다 — 동결 빌드는 `cryptography` 를
            # 의도적으로 제외해(packaging/sidecar.spec) AES 경로가 아예 없다.
            try:
                if not reader.decrypt(""):
                    raise ValueError("empty password rejected")
            except Exception:
                return _fail("pdf", "pdf_encrypted",
                             "암호가 걸린 PDF 는 가져올 수 없습니다.")
        # len(reader.pages) 를 먼저 부르지 않는다 — 그 자체가 기형 페이지트리에서
        # 비쌀 수 있다. 순회 카운터로 상한을 걸어 순환·거대 트리를 유한하게 만든다.
        pages = []
        page_ids: dict = {}                      # 페이지 객체 번호 → 페이지 번호(목차용)
        total = 0
        truncated = False
        for i, page in enumerate(reader.pages):
            if i >= MAX_PDF_PAGES or total > MAX_PDF_CHARS:
                truncated = True
                break
            t = page.extract_text() or ""
            pages.append(t)
            total += len(t)
            ref = getattr(page, "indirect_reference", None)
            if ref is not None:
                page_ids.setdefault(ref.idnum, i)
    except Exception as exc:
        return _fail("pdf", "pdf_unreadable", f"{type(exc).__name__}: {exc}")
    if truncated:
        # 조용히 자르지 않는다(RULES: no silent caps) — 잘렸음을 정본 텍스트에 남긴다.
        pages.append(f"\n\n[truncated: PDF exceeded {MAX_PDF_PAGES} pages / "
                     f"{MAX_PDF_CHARS} chars]")
    text = "\n\n".join(pages).strip()
    if not text:
        # 스캔 PDF(이미지만) — OCR 없이는 한 글자도 못 꺼낸다. 빈 문서를 정본으로
        # 만들어 두면 "가져왔는데 아무것도 없다"가 되므로 실패로 돌린다.
        # 헤딩을 넣기 전에 판단한다: 목차만 있는 스캔본도 여기서 실패해야 한다.
        return _fail("pdf", "pdf_no_text",
                     "이 PDF 에서 텍스트를 찾지 못했습니다(스캔 이미지일 수 있습니다).")
    try:
        heads = _outline_headings(reader, page_ids, truncated)
    except Exception:
        heads = {}                               # 목차가 깨져도 적재는 막지 않는다
    if heads:
        for i, lines in heads.items():
            body = pages[i]
            pages[i] = "\n\n".join(lines + ([body] if body.strip() else []))
        text = "\n\n".join(pages).strip()
    return {"kind": "text.extract", "executed": True, "format": "pdf", "text": text}


def _fail(fmt: str, code: str, note: str) -> dict:
    return {"kind": "text.extract", "executed": False, "format": fmt,
            "error": code, "note": note}


def extract_text(filename: str, data: bytes) -> dict:
    """파일 바이트에서 원문 텍스트를 꺼낸다. 형식 판별 → 형식별 추출기."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("data must be bytes")
    data = bytes(data)
    fmt = sniff(filename, data)
    if fmt == "text":
        return {"kind": "text.extract", "executed": True, "format": "text",
                "text": decode_text(data)}
    if fmt == "pdf":
        return _extract_pdf(data)
    return _fail("", "unsupported_format",
                 f"가져올 수 없는 형식입니다: {ext_of(filename) or filename!r}")


def read_file(path: str) -> dict:
    """경로에서 읽어 추출까지 — CLI·배치용 편의(서버 경로는 바이트를 직접 받는다)."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        return _fail("", "unreadable_file", f"{type(exc).__name__}: {exc}")
    return extract_text(os.path.basename(path), data)
