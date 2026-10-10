# Laya 술어 측정 보고서

입력: `D:\Projects\AIProjects\vinc-project\_scratch\laya\out\ft-v3\c-community.jsonl`  행 700 (절단으로 제외 0, 라벨 없음 0)

| 술어 | 언어 | n | Brier | ECE | AUROC | coverage@동결 문턱 (실제 오류) | 기권 문턱/비대칭 | 게이트 |
|---|---|---|---|---|---|---|---|---|
| supports | ko | 40 (+8) | 0.138 | 0.165 | 0.824 (best baseline 0.500) | 0.00 (n/a) | 40/0 | FAIL (auroc True, ece False, cov False) |
| supports | en | 60 (+12) | 0.141 | 0.133 | 0.823 (best baseline 0.500) | 0.00 (n/a) | 60/0 | FAIL (auroc True, ece False, cov False) |
| supports | mixed | 40 (+8) | 0.155 | 0.170 | 0.711 (best baseline 0.500) | 0.00 (n/a) | 40/0 | FAIL (auroc True, ece False, cov False) |
| supports | all | 140 (+28) | 0.144 | 0.103 | 0.791 (best baseline 0.500) | 0.00 (n/a) | 140/0 | FAIL (auroc True, ece False, cov False) |
| contradicts | ko | 40 (+8) | 0.108 | 0.150 | 0.875 (best baseline 0.516) | 0.00 (n/a) | 40/6 | FAIL (auroc True, ece False, cov False) |
| contradicts | en | 60 (+12) | 0.117 | 0.120 | 0.847 (best baseline 0.573) | 0.57 (0.059) | 25/12 | FAIL (auroc True, ece False, cov True) |
| contradicts | mixed | 40 (+8) | 0.155 | 0.153 | 0.793 (best baseline 0.500) | 0.78 (0.097) | 9/2 | FAIL (auroc True, ece False, cov True) |
| contradicts | all | 140 (+28) | 0.125 | 0.131 | 0.839 (best baseline 0.522) | 0.46 (0.077) | 74/20 | FAIL (auroc True, ece False, cov False) |
| same_as | ko | 40 (+8) | 0.061 | 0.102 | 0.887 (best baseline 0.500) | 0.85 (0.000) | 5/4 | FAIL (auroc True, ece False, cov True) |
| same_as | en | 60 (+12) | 0.065 | 0.126 | 1.000 (best baseline 0.625) | 0.12 (0.000) | 53/9 | FAIL (auroc True, ece False, cov False) |
| same_as | mixed | 40 (+8) | 0.162 | 0.191 | 0.492 (best baseline 0.500) | 0.07 (0.000) | 37/3 | FAIL (auroc False, ece False, cov False) |
| same_as | all | 140 (+28) | 0.091 | 0.102 | 0.853 (best baseline 0.554) | 0.31 (0.000) | 95/16 | FAIL (auroc True, ece False, cov False) |

게이트 수치는 사전 등록(§7)이고 측정 뒤 바꾸지 않는다. 코사인 기준선이 없으면 생략된 것이다.
coverage 는 보정에서 동결한 기권 문턱(abstain_below)으로 세고 분모는 적격 쌍 전부다. 비대칭도 0.2 초과와 문턱 미달은 기권이다. AUROC·Brier·ECE 는 기권과 무관하게 적격 쌍 전부에서 냈다. 평가 라벨로 문턱을 고르는 사후 곡선은 JSON 의 posthoc_coverage_at_error 에 진단으로만 있다.
