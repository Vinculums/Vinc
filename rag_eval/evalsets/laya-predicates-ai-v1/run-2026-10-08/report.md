# Laya 술어 측정 보고서

입력: `D:\Projects\AIProjects\vinc-project\_scratch\laya\out\ai-v1\eval.jsonl`  행 750 (절단으로 제외 0, 라벨 없음 0)

| 술어 | 언어 | n | Brier | ECE | AUROC | coverage@동결 문턱 (실제 오류) | 기권 문턱/비대칭 | 게이트 |
|---|---|---|---|---|---|---|---|---|
| supports | ko | 32 (+10) | 0.263 | 0.210 | 0.527 (best baseline 0.500) | 0.00 (n/a) | 32/0 | FAIL (auroc False, ece False, cov False) |
| supports | en | 78 (+10) | 0.297 | 0.345 | 0.388 (best baseline 0.500) | 0.00 (n/a) | 78/0 | FAIL (auroc False, ece False, cov False) |
| supports | mixed | 40 (+10) | 0.295 | 0.320 | 0.257 (best baseline 0.500) | 0.00 (n/a) | 40/0 | FAIL (auroc False, ece False, cov False) |
| supports | all | 150 (+30) | 0.289 | 0.249 | 0.378 (best baseline 0.500) | 0.00 (n/a) | 150/0 | FAIL (auroc False, ece False, cov False) |
| contradicts | ko | 32 (+10) | 0.232 | 0.156 | 0.600 (best baseline 0.500) | 0.00 (n/a) | 32/2 | FAIL (auroc True, ece False, cov False) |
| contradicts | en | 78 (+10) | 0.235 | 0.114 | 0.503 (best baseline 0.500) | 0.00 (n/a) | 78/3 | FAIL (auroc False, ece False, cov False) |
| contradicts | mixed | 40 (+10) | 0.272 | 0.140 | 0.433 (best baseline 0.583) | 0.00 (n/a) | 40/1 | FAIL (auroc False, ece False, cov False) |
| contradicts | all | 150 (+30) | 0.245 | 0.086 | 0.492 (best baseline 0.500) | 0.00 (n/a) | 150/6 | FAIL (auroc False, ece True, cov False) |
| same_as | ko | 32 (+10) | 0.208 | 0.229 | 0.927 (best baseline 0.500) | 0.00 (n/a) | 32/6 | FAIL (auroc True, ece False, cov False) |
| same_as | en | 78 (+10) | 0.275 | 0.325 | 0.825 (best baseline 0.541) | 0.00 (n/a) | 78/5 | FAIL (auroc True, ece False, cov False) |
| same_as | mixed | 40 (+10) | 0.278 | 0.217 | 0.497 (best baseline 0.500) | 0.00 (n/a) | 40/0 | FAIL (auroc False, ece False, cov False) |
| same_as | all | 150 (+30) | 0.262 | 0.262 | 0.741 (best baseline 0.500) | 0.00 (n/a) | 150/11 | FAIL (auroc True, ece False, cov False) |

게이트 수치는 사전 등록(§7)이고 측정 뒤 바꾸지 않는다. 코사인 기준선이 없으면 생략된 것이다.
coverage 는 보정에서 동결한 기권 문턱(abstain_below)으로 세고 분모는 적격 쌍 전부다. 비대칭도 0.2 초과와 문턱 미달은 기권이다. AUROC·Brier·ECE 는 기권과 무관하게 적격 쌍 전부에서 냈다. 평가 라벨로 문턱을 고르는 사후 곡선은 JSON 의 posthoc_coverage_at_error 에 진단으로만 있다.
