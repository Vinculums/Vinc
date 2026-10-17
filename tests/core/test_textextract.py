# -*- coding: utf-8 -*-
"""파일 바이트 → 원문 텍스트 어댑터(api/textextract) — 순수 계약.

드라이버·스토어 없이 도는 계층이라 임베디드 드라이버 유무와 무관하게 항상 실행된다.
PDF 는 선택 의존(`pypdf`)이므로 **설치 유무 양쪽에서 통과**해야 한다: 없으면
executed=False + pdf_support_missing 로 강등되는 것이 계약이다.
"""
from __future__ import annotations

import io
import sys
import unittest
import unittest.mock
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


# 목차 픽스처의 페이지 본문. 문장으로 끝나야 개념 추출이 one_liner 를 얻는다.
_PAGES = [
    "The graph store keeps every node in one table.",
    "Chunk boundaries follow paragraphs so a page stays whole.",
    "Owner scope hides every row that belongs to someone else.",
]
# (제목, 페이지, 부모 항목 번호). 한 페이지에 두 항목(1·2, 3·4)이 걸린다.
_OUTLINE = [
    ("Graph Store", 0, None),
    ("Chunk Boundaries", 1, None),
    ("Page Joins", 1, 1),
    ("Blank Lines", 2, 2),
    ("Owner Scope", 2, None),
]


@unittest.skipUnless(_HAS_PYPDF, "pypdf 미설치: PDF 목차 경로 skip")
class PdfOutlineTests(unittest.TestCase):
    """목차(북마크) 제목이 그 페이지 앞의 마크다운 헤딩이 되는가. 목차가 없거나
    깨졌으면 출력은 페이지만 이은 것과 글자 하나 다르지 않아야 한다."""

    def test_no_outline_output_is_the_plain_page_join(self):
        data = _pdf(_PAGES)
        res = tx.extract_text("plain.pdf", data)
        self.assertTrue(res["executed"], res)
        self.assertEqual(res["text"], "\n\n".join(_page_texts(data)).strip())
        self.assertNotIn("#", res["text"])

    def test_outline_titles_become_headings_in_front_of_their_pages(self):
        data = _outlined(_pdf(_PAGES), _OUTLINE)
        p = _page_texts(data)
        res = tx.extract_text("outlined.pdf", data)
        self.assertTrue(res["executed"], res)
        # 깊이 0 → `##`, 1 → `###`, 2 → `####`. 한 페이지의 항목은 목차 순서를 지킨다.
        self.assertEqual(res["text"], "\n\n".join([
            "## Graph Store", p[0],
            "## Chunk Boundaries", "### Page Joins", p[1],
            "#### Blank Lines", "## Owner Scope", p[2],
        ]))

    def test_heading_level_stops_at_six(self):
        chain = [(f"Level {k}", 0, k - 1 if k else None) for k in range(7)]
        res = tx.extract_text("deep.pdf", _outlined(_pdf(_PAGES[:1]), chain))
        lines = res["text"].split("\n\n")
        self.assertEqual(lines[0], "## Level 0")
        self.assertEqual(lines[4], "###### Level 4")
        self.assertEqual(lines[6], "###### Level 6")

    def test_dest_array_named_destination_and_group_entries(self):
        """writer 가 쓰는 GoTo 동작 말고도 `/Dest` 배열과 이름 목적지를 읽는다.
        페이지를 가리키지 않는 묶음 항목은 헤딩 없이 자식만 남는다."""
        from pypdf.generic import (ArrayObject, DictionaryObject, NameObject,
                                   TextStringObject)

        def mutate(w, refs):
            w.add_named_destination("owner-scope", 2)
            first = refs[0].get_object()
            del first["/A"]
            first[NameObject("/Dest")] = ArrayObject(
                [w.pages[0].indirect_reference, NameObject("/Fit")])
            del refs[1].get_object()["/A"]                  # 묶음 항목
            refs[4].get_object()[NameObject("/A")] = DictionaryObject({
                NameObject("/S"): NameObject("/GoTo"),
                NameObject("/D"): TextStringObject("owner-scope")})

        data = _outlined(_pdf(_PAGES), _OUTLINE, mutate)
        p = _page_texts(data)
        res = tx.extract_text("forms.pdf", data)
        self.assertEqual(res["text"], "\n\n".join([
            "## Graph Store", p[0], "### Page Joins", p[1],
            "#### Blank Lines", "## Owner Scope", p[2],
        ]))

    def test_broken_outline_falls_back_to_the_plain_page_join(self):
        from pypdf.generic import ArrayObject, NameObject, TextStringObject

        def cycle(w, refs):
            refs[4].get_object()[NameObject("/Next")] = refs[0]

        def no_title(w, refs):
            del refs[2].get_object()["/Title"]

        def not_a_page(w, refs):
            item = refs[3].get_object()
            del item["/A"]
            item[NameObject("/Dest")] = ArrayObject([refs[0], NameObject("/Fit")])

        def unknown_name(w, refs):
            item = refs[3].get_object()
            del item["/A"]
            item[NameObject("/Dest")] = TextStringObject("nowhere")

        def oversized_title(w, refs):
            refs[1].get_object()[NameObject("/Title")] = TextStringObject(
                "x" * (tx.MAX_OUTLINE_TITLE + 1))

        for mutate in (cycle, no_title, not_a_page, unknown_name, oversized_title):
            with self.subTest(case=mutate.__name__):
                data = _outlined(_pdf(_PAGES), _OUTLINE, mutate)
                res = tx.extract_text("broken.pdf", data)
                self.assertTrue(res["executed"], res)
                self.assertEqual(res["text"], "\n\n".join(_page_texts(data)).strip())

    def test_too_many_entries_falls_back(self):
        data = _outlined(_pdf(_PAGES), _OUTLINE)
        with unittest.mock.patch.object(tx, "MAX_OUTLINE_ENTRIES", len(_OUTLINE) - 1):
            res = tx.extract_text("many.pdf", data)
        self.assertEqual(res["text"], "\n\n".join(_page_texts(data)).strip())

    def test_page_limit_drops_entries_past_the_cut(self):
        """페이지 상한은 그대로다. 잘린 페이지를 가리키는 항목만 빠지고 나머지는 남는다."""
        data = _outlined(_pdf(_PAGES), _OUTLINE)
        with unittest.mock.patch.object(tx, "MAX_PDF_PAGES", 1):
            res = tx.extract_text("cut.pdf", data)
        self.assertTrue(res["executed"], res)
        self.assertTrue(res["text"].startswith("## Graph Store\n\n" + _page_texts(data)[0]))
        self.assertIn("[truncated: PDF exceeded 1 pages", res["text"])
        self.assertNotIn("Chunk Boundaries", res["text"])

    def test_outline_does_not_hide_a_scan(self):
        """목차 제목만 있는 스캔본은 여전히 pdf_no_text 다."""
        res = tx.extract_text("scan.pdf", _outlined(_pdf(["", "", ""]), _OUTLINE))
        self.assertFalse(res["executed"])
        self.assertEqual(res["error"], "pdf_no_text")

    def test_outlined_pdf_yields_concept_candidates(self):
        """앱 가져오기 뒤의 결정론 추출(`/doc/extract` → `extract_concepts`)이
        목차 없는 PDF 에서는 후보 0개, 목차 있는 PDF 에서는 헤딩마다 후보를 낸다."""
        from api import docstore
        plain = tx.extract_text("plain.pdf", _pdf(_PAGES))["text"]
        self.assertEqual(docstore.extract_concepts(plain), [])
        outlined = tx.extract_text("outlined.pdf", _outlined(_pdf(_PAGES), _OUTLINE))["text"]
        cands = docstore.extract_concepts(outlined)
        # 자기 본문 없이 자식 헤딩이 바로 붙은 항목(Chunk Boundaries)과 `####` 는
        # 후보가 아니다. 나머지는 그 페이지 첫 문장을 one_liner 로 얻는다.
        self.assertEqual([c["title"] for c in cands],
                         ["Graph Store", "Page Joins", "Owner Scope"])
        self.assertEqual(cands[0]["one_liner"], _PAGES[0])
        self.assertEqual(cands, docstore.extract_concepts(outlined))   # 결정론


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
    return _pdf([text])


def _pdf(texts: list[str]) -> bytes:
    """페이지마다 텍스트 한 줄(빈 문자열이면 텍스트 없는 페이지)이 들어간 최소 PDF."""
    kids = " ".join(f"{4 + 2 * k} 0 R" for k in range(len(texts)))
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(texts)} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for k, text in enumerate(texts):
        line = text.encode("ascii", "replace").decode()
        body = f"BT /F1 10 Tf 10 100 Td ({line}) Tj ET".encode("ascii") if text else b""
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 200] "
                    f"/Resources << /Font << /F1 3 0 R >> >> "
                    f"/Contents {5 + 2 * k} 0 R >>".encode())
        objs.append(b"<< /Length " + str(len(body)).encode() + b" >>\nstream\n"
                    + body + b"\nendstream")
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


def _outlined(data: bytes, entries, mutate=None) -> bytes:
    """``entries = [(제목, 페이지, 부모 번호|None)]`` 목차를 pypdf writer 로 단다.
    ``mutate(writer, refs)`` 는 쓰기 직전에 목차 항목을 고칠 자리다(깨진 목차 픽스처)."""
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    refs = []
    for title, page, parent in entries:
        refs.append(w.add_outline_item(
            title, page, parent=refs[parent] if parent is not None else None))
    if mutate:
        mutate(w, refs)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _page_texts(data: bytes) -> list[str]:
    """pypdf 가 페이지마다 꺼내는 텍스트(기대값을 손으로 옮겨 적지 않으려고)."""
    from pypdf import PdfReader
    return [p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages]


if __name__ == "__main__":
    unittest.main()
