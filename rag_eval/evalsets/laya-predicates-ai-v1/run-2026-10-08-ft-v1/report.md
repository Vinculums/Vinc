# Laya 술어 측정 보고서

입력: `D:\Projects\AIProjects\vinc-project\_scratch\laya\out\ai-v1-ft\eval.jsonl`  행 750 (절단으로 제외 0, 라벨 없음 0)

| 술어 | 언어 | n | Brier | ECE | AUROC | coverage@동결 문턱 (실제 오류) | 기권 문턱/비대칭 | 게이트 |
|---|---|---|---|---|---|---|---|---|
| supports | ko | 32 (+10) | 0.179 | 0.202 | 0.868 (best baseline 0.500) | 0.81 (0.115) | 6/0 | FAIL (auroc True, ece False, cov True) |
| supports | en | 78 (+10) | 0.027 | 0.039 | 0.999 (best baseline 0.500) | 0.94 (0.014) | 5/0 | PASS (auroc True, ece True, cov True) |
| supports | mixed | 40 (+10) | 0.206 | 0.242 | 0.763 (best baseline 0.500) | 0.75 (0.200) | 10/0 | FAIL (auroc True, ece False, cov True) |
| supports | all | 150 (+30) | 0.107 | 0.111 | 0.924 (best baseline 0.500) | 0.86 (0.078) | 21/0 | FAIL (auroc True, ece False, cov True) |
| contradicts | ko | 32 (+10) | 0.094 | 0.094 | 0.982 (best baseline 0.500) | 0.94 (0.033) | 0/2 | PASS (auroc True, ece True, cov True) |
| contradicts | en | 78 (+10) | 0.059 | 0.066 | 0.960 (best baseline 0.500) | 0.94 (0.027) | 2/5 | PASS (auroc True, ece True, cov True) |
| contradicts | mixed | 40 (+10) | 0.177 | 0.201 | 0.853 (best baseline 0.583) | 0.85 (0.147) | 3/6 | FAIL (auroc True, ece False, cov True) |
| contradicts | all | 150 (+30) | 0.098 | 0.108 | 0.871 (best baseline 0.500) | 0.91 (0.058) | 5/13 | FAIL (auroc True, ece False, cov True) |
| same_as | ko | 32 (+10) | 0.121 | 0.124 | 0.932 (best baseline 0.500) | 0.91 (0.034) | 0/3 | FAIL (auroc True, ece False, cov True) |
| same_as | en | 78 (+10) | 0.092 | 0.099 | 0.935 (best baseline 0.541) | 0.85 (0.015) | 10/6 | PASS (auroc True, ece True, cov True) |
| same_as | mixed | 40 (+10) | 0.300 | 0.312 | 0.600 (best baseline 0.500) | 0.80 (0.188) | 5/6 | FAIL (auroc True, ece False, cov True) |
| same_as | all | 150 (+30) | 0.154 | 0.153 | 0.826 (best baseline 0.500) | 0.85 (0.063) | 15/15 | FAIL (auroc True, ece False, cov True) |

게이트 수치는 사전 등록(§7)이고 측정 뒤 바꾸지 않는다. 코사인 기준선이 없으면 생략된 것이다.
coverage 는 보정에서 동결한 기권 문턱(abstain_below)으로 세고 분모는 적격 쌍 전부다. 비대칭도 0.2 초과와 문턱 미달은 기권이다. AUROC·Brier·ECE 는 기권과 무관하게 적격 쌍 전부에서 냈다. 평가 라벨로 문턱을 고르는 사후 곡선은 JSON 의 posthoc_coverage_at_error 에 진단으로만 있다.
