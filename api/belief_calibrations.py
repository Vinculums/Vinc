"""grade model belief 가 기댈 수 있는 승인된 보정 목록. 비어 있으면 모델 belief 는 하나도 서지 않는다.

항목 하나는 calibration_id · judged_by · predicate · language · decision 다섯 칸이다.
judged_by 는 belief 의 judged_by 와 글자 그대로 같아야 하는 모델 판이고, decision 은 이 보정을
승인한 결정 노드 id 다. 항목을 더하는 것은 코드 수정이 아니라 결정이므로 결정 노드 없이 넣지 않는다.
language 는 생산자가 쌍의 언어로 보정을 고르는 기준이다. belief 에는 언어 칸이 없어 계약이
대조하지 않는다.

지금 비어 있는 이유: 원본 Laya 는 어느 술어에서도 등록 게이트를 넘지 못했다.
근거: [[vinc.research.laya-graph-predicate-qualification-2026-10-16 §4]]
"""
ADMITTED: tuple[dict, ...] = ()
