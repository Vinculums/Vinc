# -*- coding: utf-8 -*-
"""공유 시트의 **문서 수**는 장식이 아니라 계측기다.

팩의 `documents` 는 고르는 것이 아니라 파생된다 — `api/share.py` `_docs_for_concepts`
가 담긴 개념을 언급하는 문서만 역추적해 싣는다. 그래서 스코프가 어긋나면 개념도
문서도 0인 **빈 팩**이 아무 오류 없이 저장되고, 여태 그 사실이 처음 보이는 자리는
**받는 쪽**의 가져오기 시트였다. 보내는 사람이 저장 전에 알아챌 수 있는 자리는
공유 시트의 이 한 줄뿐이다.

⚠ 이 줄은 **두 층에 걸쳐 있다** — 서버가 `manifest.counts.documents` 를 세고 앱이
그 키를 읽는다. 한쪽만 이름이 바뀌면 화면은 오류 없이 `0` 을 찍는다(= 빈 팩과
구분 불가). 그래서 양쪽을 여기서 대조한다. 앱에는 JS 테스트 러너가 없다
(`app/package.json` 에 `test` 없음) — `tests/mcp/test_view_url.py` 와 같은 자리다.

⚠ `unittest.TestCase` 로 둘 것. CI 의 무조건 잡은 `unittest.discover` 라 모듈 수준
`def test_*` 를 아예 수집하지 못한다.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _src() -> str:
    return (ROOT / "app" / "src" / "sheets.ts").read_text(encoding="utf-8")


def _docs_src() -> str:
    return (ROOT / "app" / "src" / "share-docs.ts").read_text(encoding="utf-8")


def _body(src: str, head: str) -> str:
    """`head` 로 시작하는 최상위 함수의 본문만 잘라 낸다 — 열 0 의 `}` 가 끝이다."""
    body = src[src.index(head):]
    return body[:body.index("\n}")]


class ShareSheetSpecTests(unittest.TestCase):
    def test_공유_시트가_문서_수를_보여준다(self):
        src = _src()
        spec = _body(src, "function specRows")
        self.assertIn("specDocs", spec, "공유 시트 사양 표에 문서 줄이 없다")
        self.assertIn("c.documents", spec, "문서 줄이 매니페스트 카운트를 안 읽는다")
        # 헬퍼가 **있는 것**과 **불리는 것**은 다르다 — 위 둘은 헬퍼 본문만 보므로
        # 호출부를 지워도 그대로 통과한다(변이로 실측). 그러면 문서 줄을 포함한 사양 표
        # 전체가 공유 시트에서 사라져도 가드는 초록이다 — 막으려던 바로 그것이다.
        share = _body(src, "export async function openShareSheet")
        self.assertIn("specRows(c, scopeLabel)", share, "공유 시트가 사양 표를 안 그린다")

    def test_제외한_미러의_원문은_팩에_안_실린다(self):
        """① blocker — 저장 핸들러가 노드·엣지만 걸렀고 `documents` 는 안 걸렀다.

        P11b 이전에는 덜 나빴다: 문서는 본문 MENTIONS 로만 실렸으니 미러 행을 끄는
        것과 문서는 별개였다. **P11b 이후로는 그 행이 곧 그 문서다.** 미러를 끄고
        저장해도 원문이 그대로 나갔고, 오류도 경고도 없었다.

        실측(node, `keptDocs` 를 이 소스에서 떼어 돌림 · 고치기 전 저장 핸들러 식):
          fragment.nodes 0 · counts {"concepts":0,...,"documents":1} · documents 에 원문 그대로

        JS 러너가 없으므로(`app/package.json` 에 `test` 없음) 소스에서 잠근다. 여기
        잠그는 것은 **나가는 객체 그 자체**다 — 렌더 간접이 없어 `out` 이 곧 팩이고,
        세 줄 중 어느 하나를 지워도 이 테스트가 빨개진다."""
        share = _body(_src(), "export async function openShareSheet")
        self.assertIn("const docs = keptDocs(pack.documents, excluded, pack.doc_refs, keepIds, standalone)",
                      share, "제외 집합과 남은 노드를 원문에 적용하지 않는다")
        self.assertIn("{ documents: docs }", share,
                      "거른 목록이 나가는 팩에 안 실린다 — 끈 원문이 그대로 나간다")
        self.assertIn("recount(keep, edges, docs, c)", share,
                      "매니페스트 수가 실제 나가는 수와 갈린다(시트가 저장 전에 그 수를 보여준다)")
        self.assertIn("documents: docs.length", _body(_src(), "function recount"),
                      "다시 센 문서 수가 거른 목록 길이가 아니다")

    def test_미러가_아닌_개념을_끄는_것은_원문을_안_뺀다(self):
        """판정을 고정한다 — 두 경우는 다르게 다룬다.

        미러 행은 화면에서 그 문서 자체라 끄면 원문이 빠진다. 미러가 **아닌** 개념은
        다르다: 그 문서는 본문 MENTIONS 로 실린 것이라 다른 개념도 언급하므로, 개념
        하나를 뺀 것이 "원문을 빼라"는 뜻이 아니다. 그래서 `keptDocs` 는 제외 집합에서
        **미러 id 만** doc_id 로 푼다. 이 분기를 없애고 전부 빼거나 전부 남기면 여기가
        빨개진다.

        2026-09-26 정련: 미러가 아닌 개념이 빠진 원문은 **남은 노드가 하나도 그 문서를
        붙잡지 않을 때** 빠진다(아래 `test_원문은_남은_노드가_붙잡을_때만_나간다`). 개념
        하나를 끈 것만으로는 여전히 안 빠진다 — 실제 동작은 `app/dev/share-docs-check.ts`
        가 돌려 본다."""
        helper = _body(_docs_src(), "export function keptDocs")
        self.assertIn("indexOf(DOC_MIRROR)", helper, "미러인지 아닌지를 안 가린다")
        self.assertIn("at >= 0", helper, "미러가 아닌 id 도 doc_id 로 푼다")

    def test_미러_접두가_서버와_같은_값이다(self):
        """접두가 한쪽만 바뀌면 앱은 아무 문서도 못 빼고 조용히 넘긴다 — 두 층 대조."""
        self.assertIn('export const DOC_MIRROR = "concept:doc."', _docs_src())
        self.assertIn('_DOC_MIRROR_PREFIX = "concept:doc."',
                      (ROOT / "api" / "docstore.py").read_text(encoding="utf-8"))

    def test_원문은_남은_노드가_붙잡을_때만_나간다(self):
        """미러 행이 없는 스토어(원문이 전부 본문 언급으로 실림)에서 행을 전부 끄고
        저장하면 노드 0 · 원문 123 인 팩이 나갔다(실측 2026-09-26, 개인 dev 스토어).
        원문을 지목할 행이 없으니 끌 방법도 없었다. 서버가 원문을 고른 기준(`doc_refs`
        = 그 원문을 끌어온 노드)을 시트가 남은 노드에 다시 적용한다.
        근거: [[decision:share-documents-follow-the-nodes-that-carry-them]]"""
        helper = _body(_docs_src(), "export function keptDocs")
        self.assertIn("refs[d?.doc_id]", helper, "원문을 끌어온 노드를 안 본다")
        self.assertIn("kept.has(id)", helper, "남은 노드와 대조하지 않는다")
        share = _body(_src(), "export async function openShareSheet")
        self.assertIn("pack.doc_refs, keepIds", share)
        self.assertIn("delete (out as any).doc_refs", share,
                      "시트용 대응표가 팩 파일·전송에 그대로 실린다")

    def test_원문_규칙_모듈은_import_가_없다(self):
        """`app/dev/share-docs-check.ts` 가 plain node 로 이 규칙을 돌린다. import 가 하나라도
        생기면 그 검사는 모듈을 못 찾고 CI 의 Dev checks 가 죽는다."""
        self.assertNotRegex(_docs_src(), r"(?m)^import ")
        self.assertTrue((ROOT / "app" / "dev" / "share-docs-check.ts").exists())

    def test_서버가_시트에만_대응표를_싣는다(self):
        """`/share` 응답에는 싣고(시트가 쓴다), 팩을 그대로 파일로 쓰는 CLI 는 안 켠다."""
        server = (ROOT / "api" / "server.py").read_text(encoding="utf-8")
        read_share = server[server.index("def read_share"):server.index("def read_search")]
        self.assertIn("doc_refs=True", read_share)
        share_py = (ROOT / "api" / "share.py").read_text(encoding="utf-8")
        self.assertIn('pack["doc_refs"] = refs', share_py)
        cli = (ROOT / "api" / "cli.py").read_text(encoding="utf-8")
        self.assertNotIn("doc_refs", cli, "CLI 가 저장하는 팩 파일에 시트용 대응표가 실린다")

    def test_서버가_같은_이름으로_문서를_센다(self):
        """앱이 읽는 키를 빌더가 실제로 넣는가 — 이름이 갈리면 화면은 조용히 0 이다."""
        src = (ROOT / "api" / "share.py").read_text(encoding="utf-8")
        self.assertIn('"documents": len(documents)', src)

    def test_공유_시트가_기록과_결정_수를_보여준다(self):
        """VIN-105 가 counts.records·decisions 를 넣는데 사양 표가 그 키를 안 읽으면
        화면은 오류 없이 개념·연결·문서만 보여 준다. 구버전 팩은 키가 없어도
        `?? 0` 으로 0 을 찍는다."""
        src = _src()
        spec = _body(src, "function specRows")
        self.assertIn("specRecords", spec)
        self.assertIn("specDecisions", spec)
        self.assertIn("c.records", spec)
        self.assertIn("c.decisions", spec)
        self.assertIn("?? 0", spec, "구버전 매니페스트에 decisions 가 없으면 조용히 터진다")
        share = _body(src, "export async function openShareSheet")
        self.assertIn("specRows(c, scopeLabel)", share)

    def test_제외_후_카운트가_모든_종을_다시_센다(self):
        """개별 제외가 concepts·edges·documents 만 다시 쓰면 Record/Decision 은
        끈 뒤에도 원래 수가 남는다. `recount` 가 VIN-105 술어(Episode+Record,
        Decision)로 다시 채운다."""
        src = _src()
        helper = _body(src, "function recount")
        self.assertIn('n.type === "record" || n.type === "episode"', helper)
        self.assertIn('n.type === "decision"', helper)
        self.assertIn("documents: docs.length", helper)
        share = _body(src, "export async function openShareSheet")
        self.assertIn("recount(keep, edges, docs, c)", share)

    def test_가져오기_요약이_기록과_결정을_문서와_섞지_않는다(self):
        """updateNote 세 번째 자리에 records 를 넣고 라벨은 문서였다. 원문 수와
        기록 수가 한 줄에서 갈린다."""
        src = _src()
        imp = _body(src, "export function openImportSheet")
        self.assertIn("specRecords", imp)
        self.assertIn("specDecisions", imp)
        self.assertIn("c.decisions ?? 0", imp)
        self.assertIn("updateNote(c.concepts ?? n, c.edges ?? 0, c.records ?? 0, c.decisions ?? 0", imp)

    def test_서버가_같은_이름으로_결정과_기록을_센다(self):
        src = (ROOT / "api" / "share.py").read_text(encoding="utf-8")
        self.assertIn('"decisions": len(selected["decision"])', src)
        self.assertIn('"records": len(selected["episode"]) + len(selected["record"])', src)


if __name__ == "__main__":
    unittest.main()
