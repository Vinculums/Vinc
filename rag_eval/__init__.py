"""RAG 평가 하네스 — rag 팀의 지표 원천(평가셋·고정 스토어 락·채점기·전송).

제품 표면 전체(api·vinc_mcp·CLI·REST v1)를 끝에서 끝까지 두드리므로 `vinc_rag` 안이 아니라
그 옆에 둔다 — `vinc_rag` 는 api 를 import 하지 않는 층이다. 진입점은 `python -m rag_eval.<이름>`.
근거: [[decision:the-eval-harness-lives-in-top-level-rag-eval-not-inside-vinc-rag-2026-10-01]]
"""
