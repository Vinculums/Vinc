"""에이전트 작업 평가 하네스 — 같은 과제를 비교군 넷 × 호스트 × 모델로 돌려 산출물을 채점한다.

    python -X utf8 -m rag_eval.agent_tasks run --store-path <hub.db> --store-sha256 <sha> \\
        --onnx <encoder.onnx> --tasks <과제.json> --split smoke --out <dir>
    python -X utf8 -m rag_eval.agent_tasks compare <a/result.json> <b/result.json>
    python -X utf8 -m rag_eval.agent_tasks check <과제.json>

비교군(arm)은 넷이다. A0 지식 없음 · A1 같은 문서의 단순 벡터 RAG(운영 인코더) · A2 배포된
Vinc MCP 도구 표면 그대로 · A3 vinc-client 맥락 모드(어댑터가 붙이는 블록). 모델과 호스트와
도구 호출 상한은 네 비교군에 같다. 고정 스토어 하나를 로컬 스택(`evalkit.stack`)으로 띄우고,
실행 헤더의 지문이 다른 결과끼리는 비교하지 않는다.

모듈:
- ``tasks``    과제 파일 읽기·검사·분할(smoke·search·test) 강제
- ``checkers`` 기계 채점기(정규식·Sources 실재·기권·집합 F1·경로 엣지·채점 모델)
- ``models``   모델 실행 인터페이스와 결정론적 가짜 모델
- ``arms``     네 비교군의 표면(A1 검색기, A2 MCP 도구, A3 맥락)
- ``hosts``    호스트 셋: 맨 MCP 루프, LangGraph, Microsoft Agent Framework
- ``record``   원본 기록(해시로 주소) · 도구 표면 고장 분류 · 실행 기록 조각
- ``metrics``  Wilson 구간 · 과제 단위 짝 부트스트랩 · 정확 McNemar · 안정성 · 성공당 비용
- ``runner``   스택 기동부터 후보 디렉터리 쓰기까지

근거: [[vinc.plan.agent-framework-task-eval-2026-10-18]] · [[vinc.plan.agent-harness-work-order-2026-10-25]]
"""
