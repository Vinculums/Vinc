# Laya 술어 측정 보고서

입력: `D:\Projects\AIProjects\vinc-project\_scratch\laya\out\ft-v3\a-primary.jsonl`  행 865 (절단으로 제외 0, 라벨 없음 0)

| 술어 | 언어 | n | Brier | ECE | AUROC | coverage@동결 문턱 (실제 오류) | 기권 문턱/비대칭 | 게이트 |
|---|---|---|---|---|---|---|---|---|
| supports | ko | 59 (+12) | 0.139 | 0.154 | 0.793 (best baseline 0.500) | 0.00 (n/a) | 59/0 | FAIL (auroc True, ece False, cov False) |
| supports | en | 56 (+12) | 0.166 | 0.118 | 0.701 (best baseline 0.500) | 0.00 (n/a) | 56/0 | FAIL (auroc True, ece False, cov False) |
| supports | mixed | 58 (+11) | 0.120 | 0.137 | 0.789 (best baseline 0.500) | 0.00 (n/a) | 58/0 | FAIL (auroc True, ece False, cov False) |
| supports | all | 173 (+35) | 0.142 | 0.080 | 0.775 (best baseline 0.500) | 0.00 (n/a) | 173/0 | FAIL (auroc True, ece True, cov False) |
| contradicts | ko | 59 (+12) | 0.107 | 0.147 | 0.897 (best baseline 0.613) | 0.00 (n/a) | 59/14 | FAIL (auroc True, ece False, cov False) |
| contradicts | en | 56 (+11) | 0.119 | 0.111 | 0.788 (best baseline 0.560) | 0.61 (0.118) | 19/8 | FAIL (auroc True, ece False, cov True) |
| contradicts | mixed | 58 (+12) | 0.169 | 0.146 | 0.743 (best baseline 0.500) | 0.76 (0.136) | 14/1 | FAIL (auroc True, ece False, cov True) |
| contradicts | all | 173 (+35) | 0.132 | 0.094 | 0.788 (best baseline 0.534) | 0.45 (0.128) | 92/23 | FAIL (auroc True, ece True, cov False) |
| same_as | ko | 59 (+12) | 0.053 | 0.095 | 0.881 (best baseline 0.500) | 0.83 (0.000) | 7/6 | PASS (auroc True, ece True, cov True) |
| same_as | en | 56 (+12) | 0.071 | 0.155 | 0.973 (best baseline 0.519) | 0.09 (0.000) | 51/13 | FAIL (auroc True, ece False, cov False) |
| same_as | mixed | 58 (+12) | 0.092 | 0.108 | 0.828 (best baseline 0.500) | 0.17 (0.000) | 47/9 | FAIL (auroc True, ece False, cov False) |
| same_as | all | 173 (+36) | 0.072 | 0.090 | 0.887 (best baseline 0.507) | 0.37 (0.000) | 105/28 | FAIL (auroc True, ece True, cov False) |

게이트 수치는 사전 등록(§7)이고 측정 뒤 바꾸지 않는다. 코사인 기준선이 없으면 생략된 것이다.
coverage 는 보정에서 동결한 기권 문턱(abstain_below)으로 세고 분모는 적격 쌍 전부다. 비대칭도 0.2 초과와 문턱 미달은 기권이다. AUROC·Brier·ECE 는 기권과 무관하게 적격 쌍 전부에서 냈다. 평가 라벨로 문턱을 고르는 사후 곡선은 JSON 의 posthoc_coverage_at_error 에 진단으로만 있다.
