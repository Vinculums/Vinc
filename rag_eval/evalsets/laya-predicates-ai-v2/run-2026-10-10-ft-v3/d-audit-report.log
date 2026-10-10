# Laya 술어 측정 보고서

입력: `D:\Projects\AIProjects\vinc-project\_scratch\laya\out\ft-v3\d-audit.jsonl`  행 225 (절단으로 제외 0, 라벨 없음 0)

| 술어 | 언어 | n | Brier | ECE | AUROC | coverage@동결 문턱 (실제 오류) | 기권 문턱/비대칭 | 게이트 |
|---|---|---|---|---|---|---|---|---|
| supports | en | 44 (+0) | 0.015 | 0.107 | nan (best baseline 0.500) | 0.00 (n/a) | 44/0 | FAIL (auroc False, ece False, cov False) |
| supports | mixed | 1 (+0) | 0.019 | 0.138 | nan (best baseline 0.500) | 0.00 (n/a) | 1/0 | FAIL (auroc False, ece False, cov False) |
| supports | all | 45 (+0) | 0.015 | 0.108 | nan (best baseline 0.500) | 0.00 (n/a) | 45/0 | FAIL (auroc False, ece False, cov False) |
| contradicts | en | 44 (+0) | 0.003 | 0.051 | nan (best baseline 0.500) | 0.98 (0.000) | 1/0 | FAIL (auroc False, ece True, cov True) |
| contradicts | mixed | 1 (+0) | 0.001 | 0.027 | nan (best baseline 0.500) | 1.00 (0.000) | 0/0 | FAIL (auroc False, ece True, cov True) |
| contradicts | all | 45 (+0) | 0.003 | 0.051 | nan (best baseline 0.500) | 0.98 (0.000) | 1/0 | FAIL (auroc False, ece True, cov True) |
| same_as | en | 44 (+2) | 0.070 | 0.158 | 0.929 (best baseline 1.000) | 0.05 (0.000) | 42/4 | FAIL (auroc False, ece False, cov False) |
| same_as | mixed | 1 (+0) | 0.006 | 0.078 | nan (best baseline 0.500) | 0.00 (n/a) | 1/0 | FAIL (auroc False, ece True, cov False) |
| same_as | all | 45 (+2) | 0.069 | 0.153 | 0.930 (best baseline 1.000) | 0.04 (0.000) | 43/4 | FAIL (auroc False, ece False, cov False) |

게이트 수치는 사전 등록(§7)이고 측정 뒤 바꾸지 않는다. 코사인 기준선이 없으면 생략된 것이다.
coverage 는 보정에서 동결한 기권 문턱(abstain_below)으로 세고 분모는 적격 쌍 전부다. 비대칭도 0.2 초과와 문턱 미달은 기권이다. AUROC·Brier·ECE 는 기권과 무관하게 적격 쌍 전부에서 냈다. 평가 라벨로 문턱을 고르는 사후 곡선은 JSON 의 posthoc_coverage_at_error 에 진단으로만 있다.
