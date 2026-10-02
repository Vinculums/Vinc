"""골드 행마다 저자와 저작일이 있어야 한다.

이유: 규칙과 시험의 저자가 같은 게이트는 기본 off 이고, 저자 축이 둘 이상이 되는 날
재측정 티켓이 열린다 — 그날 비교할 열이 지금부터 있어야 한다.
근거: [[decision:a-gate-whose-rule-and-exam-share-an-author-ships-off]]
"""
import json
import re
from pathlib import Path

GOLD = Path(__file__).resolve().parents[2] / "rag_eval" / "fixtures" / "brief_gold.jsonl"


def test_every_gold_row_names_its_author_and_date():
    rows = [json.loads(l) for l in GOLD.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert rows
    for r in rows:
        assert r.get("author"), r.get("gold_node_id")
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", r.get("authored_on", "")), r.get("gold_node_id")
