# Laya 술어 측정 보고서

입력: `D:\Projects\AIProjects\vinc-project\_scratch\laya\out\ft-v3\b-continuity.jsonl`  행 750 (절단으로 제외 0, 라벨 없음 0)

| 술어 | 언어 | n | Brier | ECE | AUROC | coverage@동결 문턱 (실제 오류) | 기권 문턱/비대칭 | 게이트 |
|---|---|---|---|---|---|---|---|---|
| supports | ko | 32 (+10) | 0.101 | 0.155 | 0.950 (best baseline 0.500) | 0.00 (n/a) | 32/0 | FAIL (auroc True, ece False, cov False) |
| supports | en | 78 (+10) | 0.041 | 0.141 | 0.997 (best baseline 0.500) | 0.00 (n/a) | 78/0 | FAIL (auroc True, ece False, cov False) |
| supports | mixed | 40 (+10) | 0.168 | 0.135 | 0.760 (best baseline 0.500) | 0.00 (n/a) | 40/0 | FAIL (auroc True, ece False, cov False) |
| supports | all | 150 (+30) | 0.088 | 0.115 | 0.943 (best baseline 0.500) | 0.00 (n/a) | 150/0 | FAIL (auroc True, ece False, cov False) |
| contradicts | ko | 32 (+10) | 0.055 | 0.126 | 0.995 (best baseline 0.500) | 0.00 (n/a) | 32/5 | FAIL (auroc True, ece False, cov False) |
| contradicts | en | 78 (+10) | 0.058 | 0.075 | 0.929 (best baseline 0.500) | 0.56 (0.000) | 32/7 | PASS (auroc True, ece True, cov True) |
| contradicts | mixed | 40 (+10) | 0.148 | 0.172 | 0.803 (best baseline 0.583) | 0.53 (0.000) | 17/8 | FAIL (auroc True, ece False, cov True) |
| contradicts | all | 150 (+30) | 0.081 | 0.070 | 0.906 (best baseline 0.500) | 0.43 (0.000) | 81/20 | FAIL (auroc True, ece True, cov False) |
| same_as | ko | 32 (+10) | 0.088 | 0.108 | 0.936 (best baseline 0.500) | 0.62 (0.000) | 10/6 | FAIL (auroc True, ece False, cov True) |
| same_as | en | 78 (+10) | 0.094 | 0.163 | 0.937 (best baseline 0.541) | 0.14 (0.000) | 67/17 | FAIL (auroc True, ece False, cov False) |
| same_as | mixed | 40 (+10) | 0.166 | 0.252 | 0.657 (best baseline 0.500) | 0.12 (0.400) | 35/7 | FAIL (auroc True, ece False, cov False) |
| same_as | all | 150 (+30) | 0.112 | 0.113 | 0.852 (best baseline 0.500) | 0.24 (0.056) | 112/30 | FAIL (auroc True, ece False, cov False) |

게이트 수치는 사전 등록(§7)이고 측정 뒤 바꾸지 않는다. 코사인 기준선이 없으면 생략된 것이다.
coverage 는 보정에서 동결한 기권 문턱(abstain_below)으로 세고 분모는 적격 쌍 전부다. 비대칭도 0.2 초과와 문턱 미달은 기권이다. AUROC·Brier·ECE 는 기권과 무관하게 적격 쌍 전부에서 냈다. 평가 라벨로 문턱을 고르는 사후 곡선은 JSON 의 posthoc_coverage_at_error 에 진단으로만 있다.
