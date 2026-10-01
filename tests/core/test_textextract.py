# -*- coding: utf-8 -*-
"""파일 바이트 → 원문 텍스트 어댑터(api/textextract) — 순수 계약.

드라이버·스토어 없이 도는 계층이라 임베디드 드라이버 유무와 무관하게 항상 실행된다.
PDF 는 선택 의존(`pypdf`)이므로 **설치 유무 양쪽에서 통과**해야 한다: 없으면
executed=False + pdf_support_missing 로 강등되는 것이 계약이다.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import textextract as tx  # noqa: E402

try:                                     # PDF 경로는 있을 때만 깊게 검증
    import pypdf                         # noqa: F401
    _HAS_PYPDF = True
except Exception:
    _HAS_PYPDF = False


class SniffTests(unittest.TestCase):
    def test_extension_routing(self):
        for name in ("a.md", "a.markdown", "a.txt", "a.text", "a.rst", "A.MD"):
            self.assertEqual(tx.sniff(name, b"hello"), "text", name)
        self.assertEqual(tx.sniff("a.pdf", b""), "pdf")
        self.assertEqual(tx.sniff("a.docx", b"PK\x03\x04"), "")
        self.assertEqual(tx.sniff("noext", b"plain"), "")

    def test_magic_beats_extension(self):
        """`.txt` 로 저장된 PDF 를 텍스트로 디코드하면 깨진 정본이 남는다 —
        바이트가 확장자를 이긴다."""
        self.assertEqual(tx.sniff("report.txt", b"%PDF-1.7\n%..."), "pdf")


class DecodeLadderTests(unittest.TestCase):
    def test_plain_utf8(self):
        self.assertEqual(tx.decode_text("한글 본문\n".encode("utf-8")), "한글 본문\n")

    def test_utf8_bom_stripped(self):
        self.assertEqual(tx.decode_text("﻿제목".encode("utf-8")), "제목")

    def test_utf16_bom(self):
        self.assertEqual(tx.decode_text("한글".encode("utf-16")), "한글")

    def test_cp949_fallback(self):
        """UTF-8 로 못 읽히는 한국어 Windows 저장본이 살아 돌아온다."""
        self.assertEqual(tx.decode_text("메모 한 줄".encode("cp949")), "메모 한 줄")

    def test_undecodable_is_visible_not_plausible(self):
        """마지막 칸은 latin-1(그럴듯한 오역)이 아니라 U+FFFD(눈에 보이는 손상)."""
        out = tx.decode_text(b"ok \xff\xfe\xfd tail")
        self.assertIn("�", out)
        self.assertTrue(out.startswith("ok "))

    def test_crlf_and_bytes_preserved(self):
        """무손실 계약(put_doc)이 아래에 있으므로 개행 정규화를 하지 않는다."""
        self.assertEqual(tx.decode_text(b"a\r\nb\r\n"), "a\r\nb\r\n")

    def test_empty(self):
        self.assertEqual(tx.decode_text(b""), "")


class ExtractTests(unittest.TestCase):
    def test_text_roundtrip(self):
        res = tx.extract_text("note.md", "# 제목\n\n본문.\n".encode("utf-8"))
        self.assertTrue(res["executed"])
        self.assertEqual(res["format"], "text")
        self.assertEqual(res["text"], "# 제목\n\n본문.\n")

    def test_unsupported_format_degrades(self):
        res = tx.extract_text("sheet.xlsx", b"PK\x03\x04rest")
        self.assertFalse(res["executed"])
        self.assertEqual(res["error"], "unsupported_format")
        self.assertTrue(res["note"])

    def test_rejects_str_input(self):
        with self.assertRaises(TypeError):
            tx.extract_text("a.md", "이미 문자열")   # type: ignore[arg-type]

    def test_pdf_degrades_or_extracts(self):
        """pypdf 유무 양쪽에서 **계약이 지켜지는지**만 본다(설치를 강제하지 않는다)."""
        res = tx.extract_text("broken.pdf", b"%PDF-1.4\nnot really a pdf")
        self.assertFalse(res["executed"])
        self.assertEqual(res["format"], "pdf")
        self.assertIn(res["error"], ("pdf_support_missing", "pdf_unreadable"))
        if not _HAS_PYPDF:
            self.assertEqual(res["error"], "pdf_support_missing")

    @unittest.skipUnless(_HAS_PYPDF, "pypdf 미설치 — PDF 추출 경로 skip")
    def test_pdf_text_extraction(self):
        from pypdf import PdfWriter
        buf = _one_page_pdf("Vinc 문서 정본")
        res = tx.extract_text("doc.pdf", buf)
        self.assertTrue(PdfWriter is not None)
        self.assertTrue(res["executed"], res)
        self.assertIn("Vinc", res["text"])

    @unittest.skipUnless(_HAS_PYPDF, "pypdf 미설치 — PDF 추출 경로 skip")
    def test_pdf_without_text_is_a_failure_not_an_empty_doc(self):
        """스캔 PDF 를 빈 정본으로 만들면 '가져왔는데 아무것도 없다'가 된다."""
        from pypdf import PdfWriter
        w = PdfWriter()
        w.add_blank_page(width=200, height=200)
        import io as _io
        b = _io.BytesIO()
        w.write(b)
        res = tx.extract_text("scan.pdf", b.getvalue())
        self.assertFalse(res["executed"])
        self.assertEqual(res["error"], "pdf_no_text")


class ReadFileTests(unittest.TestCase):
    def test_missing_path_degrades(self):
        res = tx.read_file(str(Path(__file__).parent / "no-such-file.md"))
        self.assertFalse(res["executed"])
        self.assertEqual(res["error"], "unreadable_file")

    def test_reads_this_repo_file(self):
        res = tx.read_file(str(Path(__file__).resolve().parents[2] / "README.md"))
        self.assertTrue(res["executed"])
        self.assertGreater(len(res["text"]), 0)


def _one_page_pdf(text: str) -> bytes:
    """텍스트 한 줄이 들어간 최소 PDF(외부 픽스처 파일 없이 인라인 생성)."""
    body = (
        f"BT /F1 24 Tf 40 120 Td ({text.encode('ascii', 'replace').decode()}) Tj ET"
    ).encode("ascii")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(body)).encode() + b" >>\nstream\n" + body + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)


if __name__ == "__main__":
    unittest.main()
