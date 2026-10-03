"""RAG 팀 패키지 — 검색·순위·임베딩·평가. 위 층(`api`·`vinc_mcp`)을 import 하지 않는다.

`retrieval`(검색 코어), `rerank`(리랭커 계기), `embed`(임베딩 워커·평가 인코더)가 있다.
평가 하네스는 위 층을 끝까지 몰아야 해서 최상위 `rag_eval/` 에 있다.
근거: [[vinc.plan.team-split]]
"""
