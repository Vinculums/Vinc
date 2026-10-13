# Laya 술어 측정 보고서

입력: `D:\Projects\AIProjects\vinc-project\_scratch\laya\out\ft-v2\eval.jsonl`  행 750 (절단으로 제외 0, 라벨 없음 0)

| 술어 | 언어 | n | Brier | ECE | AUROC | coverage@동결 문턱 (실제 오류) | 기권 문턱/비대칭 | 게이트 |
|---|---|---|---|---|---|---|---|---|
| supports | ko | 32 (+10) | 0.164 | 0.204 | 0.832 (best baseline 0.500) | 0.00 (n/a) | 0/0 | FAIL (auroc True, ece False, cov False, 동결 문턱 없음) |
| supports | en | 78 (+10) | 0.074 | 0.104 | 0.959 (best baseline 0.500) | 0.00 (n/a) | 78/0 | FAIL (auroc True, ece False, cov False) |
| supports | mixed | 40 (+10) | 0.166 | 0.192 | 0.753 (best baseline 0.500) | 0.00 (n/a) | 40/0 | FAIL (auroc True, ece False, cov False) |
| supports | all | 150 (+30) | 0.118 | 0.114 | 0.886 (best baseline 0.500) | 0.00 (n/a) | 118/0 | FAIL (auroc True, ece False, cov False, 동결 문턱 없음) |
| contradicts | ko | 32 (+10) | 0.125 | 0.125 | 0.977 (best baseline 0.500) | 0.91 (0.034) | 0/3 | FAIL (auroc True, ece False, cov True) |
| contradicts | en | 78 (+10) | 0.060 | 0.074 | 0.907 (best baseline 0.500) | 0.68 (0.019) | 23/8 | PASS (auroc True, ece True, cov True) |
| contradicts | mixed | 40 (+10) | 0.158 | 0.195 | 0.803 (best baseline 0.583) | 0.62 (0.080) | 13/7 | FAIL (auroc True, ece False, cov True) |
| contradicts | all | 150 (+30) | 0.100 | 0.087 | 0.814 (best baseline 0.500) | 0.71 (0.037) | 36/18 | PASS (auroc True, ece True, cov True) |
| same_as | ko | 32 (+10) | 0.103 | 0.216 | 0.927 (best baseline 0.500) | 0.00 (n/a) | 32/6 | FAIL (auroc True, ece False, cov False) |
| same_as | en | 78 (+10) | 0.096 | 0.113 | 0.947 (best baseline 0.541) | 0.71 (0.036) | 17/15 | FAIL (auroc True, ece False, cov True) |
| same_as | mixed | 40 (+10) | 0.220 | 0.227 | 0.570 (best baseline 0.500) | 0.57 (0.217) | 13/11 | FAIL (auroc True, ece False, cov True) |
| same_as | all | 150 (+30) | 0.131 | 0.077 | 0.809 (best baseline 0.500) | 0.52 (0.090) | 62/32 | PASS (auroc True, ece True, cov True) |

게이트 수치는 사전 등록(§7)이고 측정 뒤 바꾸지 않는다. 코사인 기준선이 없으면 생략된 것이다.
coverage 는 보정에서 동결한 기권 문턱(abstain_below)으로 세고 분모는 적격 쌍 전부다. 비대칭도 0.2 초과와 문턱 미달은 기권이다. AUROC·Brier·ECE 는 기권과 무관하게 적격 쌍 전부에서 냈다. 평가 라벨로 문턱을 고르는 사후 곡선은 JSON 의 posthoc_coverage_at_error 에 진단으로만 있다.
