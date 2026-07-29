# -*- coding: utf-8 -*-
"""파일 바이트 → 원문 텍스트 (D45 입력 어댑터).

`put_doc` 은 **파일 I/O 없음**이 명시 계약이라([[vinc.c.chunk-ssot]]), 파일에서
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


def _extract_pdf(data: bytes) -> dict:
    """PDF → 텍스트. 페이지는 빈 줄로 잇는다 — `plan_boundaries` 가 문단 경계를
    1순위로 삼으므로 페이지 경계가 자연스럽게 청크 경계가 된다."""
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
        pages = [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:
        return _fail("pdf", "pdf_unreadable", f"{type(exc).__name__}: {exc}")
    text = "\n\n".join(pages).strip()
    if not text:
        # 스캔 PDF(이미지만) — OCR 없이는 한 글자도 못 꺼낸다. 빈 문서를 정본으로
        # 만들어 두면 "가져왔는데 아무것도 없다"가 되므로 실패로 돌린다.
        return _fail("pdf", "pdf_no_text",
                     "이 PDF 에서 텍스트를 찾지 못했습니다(스캔 이미지일 수 있습니다).")
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
