<!-- vinc-export
doc_id: vinc.d.alluvial-research
title: Vinc 합계(Sankey) 뷰 × Alluvial 다이어그램 적용 연구
origin: Vinc-docs/vinc/D/vinc.d.alluvial-research.md@5090690
chunks: 11
verified: true
content_hash: 092f8c4e58677b0d9ff4961b5c3f37f6841302577d12539378c9928fb63e653b
updated_at: 2026-08-01T16:20:29.411Z
-->

---
doc_id: vinc.d.alluvial-research
layer: D
domain: graph
title: Vinc 합계(Sankey) 뷰 × Alluvial 다이어그램 — 레이아웃·정렬·모노크롬 설계 연구
status: draft
authority: research-input
version: v0.1
owner: vinc
last_updated: 2026-07-28
inherits_from: [vinc.d.graph-views, vinc.d.atlas-visual-language, vinc.d.view-redesign-research]
tags: [vinc, D, atlas, sankey, alluvial, layout, research, dec-candidate]
---

# Vinc 합계(Sankey) 뷰 × Alluvial 다이어그램 적용 연구

> **지위: 연구 입력(research-input).** 발주자 업로드 원문(2026-07-28, compass 리서치)을
> §1 에 그대로 보존한다. SSoT 아님 — 채택분은 DEC **D68** 로 정본화됐다
> ([[vinc.d.atlas-visual-language]] §6, 규정 서술은 [[vinc.d.graph-views]] §2.3.2).
> **실행 전 부록(as-built 대조)을 먼저 읽는다** — 원문 권고 중 절반은 파일링 시점에
> 이미 구현돼 있었고, 핵심 전제 하나("재정렬로 교차 대부분이 사라진다")는 구현
> 검증에서 **부분 반박**됐다(부록 §A2 교차 불변 정리). 원문의 em-dash 는 문서
> 텍스트라 화면 카피 규율 대상이 아니다.

## 1. 원문 (compass_artifact_wf-c4f473f4, 2026-07-28 업로드)

### Vinc "합계/Totals" 뷰를 진짜 Alluvial 다이어그램으로 만들기: 설계·구현 가이드

#### TL;DR
- 지금 화면이 "Alluvial 같지 않은" 핵심 원인은 튜플 단위(source×topic×status) 스트랜드를 전부 개별 리본으로 그리면서 양 끝 정렬 키를 전역 고정(왼쪽 (source,topic,status) / 오른쪽 (topic,source,status))했기 때문이다. Alluvial의 정의적 특징은 "축(axis)마다 strata를 갭 없이 쌓고, 각 갭에서 lode 순서를 교차 최소화 기준으로 로컬 재정렬한다"는 것인데, 전역 고정 키는 한쪽 갭을 정리하는 대신 반대쪽 갭에서 교차를 강제한다.
- 권고안: (1) 튜플별 연속 스트랜드 개념은 유지하되 렌더는 갭별(2-layer) barycenter/중앙값 스윕으로 lode 순서를 재정렬하고, (2) 색상 대신 luminance(명도) 램프 + hover 기반 focus+context(선택 요소는 유지, 나머지는 옅게 후퇴)로 모노크롬 제약을 보완하며, (3) 작은 튜플은 "기타(Others)" 버킷으로 병합하고 최소 밴드 두께를 강제한다.
- 3단계·13튜플 현재 규모라면 per-tuple alluvial이 정당하며 위 개선만으로 "진짜 alluvial" 인상을 얻을 수 있다. 그러나 토픽이 8개 이상으로 늘면 per-tuple 밴드 수가 선형 폭증해 무너진다. 그 시점에는 per-gap(인접쌍) Sankey + end-to-end hover 하이라이트(Vinc가 이미 만든 인프라 재활용)로 전환하는 것이 1순위 권고다.

#### Key Findings

##### 1) Alluvial과 Sankey는 무엇이 다른가 (구조적 정의)
- **Sankey**: Alex B. W. Kennedy와 H. Riall Sankey가 1898년 "The Thermal Efficiency of Steam Engines"(Minutes of the Proceedings of the Institution of Civil Engineers, vol. 134: 278-312)에서 증기기관의 에너지 효율을 나타내며 사용한 것이 기원이다. Wikipedia의 표현대로 원본 차트는 "흑백으로 단 한 종류의 흐름(예: 증기)만" 표시했다. 구조적으로 Sankey는 "노드"와 "링크"의 유량 네트워크로, 흐름이 임의 단계에서 합쳐지거나 갈라질 수 있고 사이클(loop)도 허용하며, 노드 위치가 상대적으로 자유롭다. 방향/크기(양)의 흐름을 강조한다.
- **Alluvial**: Rosvall & Bergstrom의 "Mapping Change in Large Networks"(PLoS ONE 5(1):e8694, 2010년 1월 27일; doi:10.1371/journal.pone.0008694)에서 네트워크 커뮤니티 구조의 시간적 변화를 나타내며 대중화됐다. 이 논문은 "약 7000개 과학저널 간 인용 패턴을 지난 10년에 걸쳐 연구해, 신경과학이 학제간 전문분야에서 성숙한 독립 학문으로 변모했음을 발견"했다. 구조적으로 수직 축(axis/stage)이 고정되고, 각 축에서 값(범주)이 블록(strata)으로 표현되며 블록 높이가 크기에 비례한다. 흐름은 인접 축 사이에서만 한 방향으로 흐르고 사이클이 없다.
- **핵심 어휘 (ggalluvial, Brunson 2020, JOSS 5(49):2017)**: ggalluvial은 "alluvial plot과 그 그래픽 요소를 Sankey·parallel sets와 구별하기 위해 독특한 지질학적 명명법을 채택"한다. axis(축=범주형 변수 열), stratum(축 위의 한 범주를 나타내는 사각형), alluvium(모든 축을 가로질러 정체성을 보존하는 하나의 흐름), lode(alluvium과 stratum의 교차로 stratum이 세분되는 구간), flow(인접 strata 사이의 alluvium 세그먼트). **Vinc가 만든 "path-preserving ribbon"은 정확히 alluvium 개념이다.**
- **Alluvial vs Parallel Sets**: Parallel Sets(Kosara, Bendix, Hauser, IEEE TVCG 12(4):558-568, 2006; doi:10.1109/TVCG.2006.76)는 "개별 데이터 포인트가 아니라 데이터 빈도를 보여주는" 방법으로, 각 축이 전체(100%)를 나타내도록 균일 길이로 stack하는 part-to-whole 표현이다. Alluvial은 축마다 높이 총합이 달라질 수 있고(양 자체 표현), 범주 순서를 축 간에 일관되게 유지하며 갭 없이 stack한다. Cory Brunson의 분류상 "alluvial plot = 범주 순서가 축 간 일관되고 갭 없이 stack된 parallel sets plot"이다.
- **함의**: Vinc의 뷰는 3개 고정 축(source→topic→status), 범주형 재그룹핑, 사이클 없음, 정체성 보존 흐름 → 정의상 alluvial이 맞다. 문제는 정의가 아니라 레이아웃·정렬·시각 설계에 있다.

##### 2) "Alluvial로 안 보이는" 원인 진단 프레임
스크린샷의 "13튜플 → 26스트랜드, 교차 카오스"는 다음 중 하나 이상 때문이다. 진단 체크리스트:
1. **Lode 순서가 로컬 최적화되지 않음**: 양 갭 정렬 키를 전역 고정했다. Alluvial 정석은 각 축에서 인접 축 기준으로 lode 순서를 로컬 재정렬하는 것(ggalluvial의 lode.guidance). 전역 고정 키는 한 갭이 깔끔해도 다른 갭에서 교차를 강제한다.
2. **교차 최소화 부재**: barycenter/중앙값 스윕 같은 2-layer crossing minimization을 돌리지 않았다.
3. **모든 튜플을 개별 리본으로**: 같은 (source,topic) 또는 (topic,status)를 공유하는 흐름을 갭 안에서 묶지(merge) 않아 밴드 수가 폭발.
4. **작은 흐름 미처리**: 얇은 슬리버 리본 다수 → "spaghetti effect".
5. **strata(블록) 시각적 약화**: alluvial은 축마다 뚜렷한 블록(stratum)이 있어야 "강" 같은 인상을 준다. 블록이 약하면 엉킨 곡선 다발로 보인다(Rosvall 원본도 블록·흐름 구분이 약해 읽기 어렵다는 비판을 받았다).
6. **곡선 형태**: 급격한 기울기(slope)의 리본이 많으면 zigzag 노이즈. 완만한 sigmoid/cubic이 흐름 연속성을 높인다.

##### 3) 레이아웃/정렬 알고리즘 권고
- **2-layer barycenter(중력 중심) 휴리스틱**: 각 노드를 인접 층에서 연결된 이웃들의 평균 위치에 놓는다(v.ord = Σ이웃위치 / 이웃수). 위·아래 스윕을 수렴 또는 컷오프까지 반복. 교차 최소화는 NP-hard이며 barycenter는 그 표준 근사로, sparse 그래프에서 특히 우수하다. Sankey 크로싱 축소에서도 barycentre ordering이 표준으로 쓰인다.
- **중앙값(median) 휴리스틱**: barycenter의 대안으로 이웃 위치의 중앙값 사용. 두 층 모두 자유로운 경우 barycenter와 함께 최상위권 성능.
- **Alluvial 특유의 lode 정렬 (ggalluvial lode.guidance)**: 각 index 축에서 나머지 축들이 lode 정렬에 기여하는 우선순위 함수. `zigzag`(기본; index에서 바깥으로 지그재그), `forward`/`backward`(증가/감소 순), `frontback`/`backfront`(한 방향 끝까지 갔다 반대로). 3축뿐이므로 중간축(topic)을 index로 두고 `forward`(source 우선) 또는 `backfront`를 실험해 교차 최소인 것을 채택하면 된다.
- **Stratum 순서**: Rosvall & Bergstrom 원본은 크기순 정렬을 썼다(Figure 2 캡션 verbatim: "clusters ordered by size... The clusters are ordered from bottom to top by their size"). 또는 의미순(고정 도메인 순서)도 유효하다. 어느 쪽이든 하나를 골라 세 축 전체에 일관 적용해야 한다.
- **곡선 타입**: ggalluvial은 `xspline`(기본, 4점 근사 스플라인)과 `linear`,`cubic`(f=3x²−2x³),`quintic`(f=10x³−15x⁴+6x⁵),`sine`,`arctangent`,`sigmoid`를 제공한다. cubic/quintic/sigmoid는 양 끝에서 기울기 0(flat)이라 stratum에 수직으로 진입 → 흐름이 블록에서 "자연스럽게 흘러나오는" 인상을 준다. d3-sankey의 `sankeyLinkHorizontal()`도 동일 원리의 cubic bezier다. **band straightening**: 기울기 큰 리본일수록 얇고 길어져 overlap에 기여하므로, 평균 slope를 낮추는 정렬이 clutter를 직접 줄인다.
- **ParSetgnostics 품질 지표 (Dennig, Fischer, Blumenschein, Fuchs, Keim, Dimara 2021, CGF 40(3):375-386; doi:10.1111/cgf.14314)**: parallel sets/alluvial의 clutter를 정량화하는 8개 지표. **범주 순서 의존 5개**: ① Overlap(리본 겹침 면적; 낮을수록 좋음), ② Slope(평균 기울기; 낮을수록 좋음 — "highly sloped ribbons get thinner and longer"), ③ Orthogonality(임계각 τ보다 기울기 작은 리본 비율; τ 기본 0), ④ Number of Crossings(교차 수), ⑤ Crossing Angle(교차각; 클수록 읽기 쉬움 — "ribbons crossing at a flat angle are hard to follow"). **차원 순서 의존 3개**: ⑥ Number of Ribbons(리본 수; 낮을수록), ⑦ Ribbon Width Variance(폭 분산; 낮을수록 — "very broad ribbons hide smaller ones"), ⑧ Mutual Information(인접 축 상호정보; 높을수록 — 관련 차원을 인접 배치). 논문은 6개 데이터셋 재구성 실험에서 원본 대비 "clutter reduction of up to 81%"를 보고했다(단, 아래 Caveats 참조). 실무 가이드(논문 §6.1): 리본 수가 적으면 **Overlap·Slope** 최적화, 많으면 **Orthogonality·Number of Crossings·Crossing Angle** 최적화가 유리. 3축·소수 튜플이면 전수 계산(brute force)으로 최적 순서 탐색 가능(탐색 공간 |D|!·Π|Cd|!). 단, 논문은 "지표는 재정렬 전략을 제공하지 않으며"(descriptive only) 최적은 전수 계산으로 찾았다고 명시한다.

##### 4) 모노크롬 제약 하의 시각 설계 (색상당-범주 인코딩 금지)
- **Luminance(명도) 램프**: 범주형에 색상 대신 회색 명암 팔레트. ggplot2의 `scale_*_grey()`, base R 막대의 기본 회색 팔레트가 예. 명도는 암묵적 크기/순서 인상을 줘서 순서형(status: 연결됨→이어짐→미연결)에 특히 적합. 단, 명도가 유사한 범주는 인접 시 구별이 어렵다는 알려진 한계가 있다.
- **Hover 기반 focus+context**: 선택 요소는 진하게 유지, 나머지는 옅게(desaturate/저-opacity) 후퇴시키는 것이 표준. 반투명(semi-transparency) 접근으로 전역 뷰와 상세 뷰를 같은 공간에 겹치되 항상 하나는 강조, 하나는 약화한다. **Vinc가 이미 구현한 end-to-end path highlighting이 바로 이 focus+context이며, 정적 clutter의 표준 보완책이다.**
- **Others 임계치**: 실무 원칙은 "50개 밴드 중 40개가 머리카락 슬리버면 시각 노이즈이므로, 의미 있는 임계치 미만은 필터링하거나 Others로 병합"한다는 것이다. 관련 경험칙으로 Sankey는 노드 15개 이하, 4-5 레벨 이하가 가독 최대치로 언급된다. Vinc의 경우 (a) 절대 최소 밴드 픽셀 두께(예: 2-3px)를 강제하고, 그 미만 튜플은 (b) 같은 (source,status)를 공유하는 "기타 토픽" lode로 병합해 중간축에서만 분리하지 않는 방식을 권장한다.
- **Strata 강조**: 모노크롬에서는 블록(stratum) 외곽선/채움을 뚜렷이 해 흐름과 분리해야 한다.
- **패딩**: strata 간 수직 갭(node padding)을 둬 블록을 구분한다. 단 alluvial 불변식(stratum 높이 = 통과 flow 합)은 유지. 노드가 너무 많아 패딩이 안 들어가면 자동 축소되지만, 그 전에 Others 병합이 우선이다.
- **라벨**: 모든 stratum에 직접 라벨. 값/비율을 라벨에 포함하면 판독 부담이 줄어든다.

##### 5) 구현 패턴 / 참고 소스
- **d3-sankey 알고리즘 차용(라이브러리 번들 없이)**: d3-sankey는 (1) node links, (2) node values, (3) node depths/heights(수평 층), (4) node breadths(수직 위치) 계산 후 `sankeyLinkHorizontal()`로 cubic bezier 링크를 생성한다. Alluvial화하려면 노드 열(depth)을 데이터가 아니라 3개 stage로 **고정**하고, `sankey.sort(null)`로 입력 순서를 고정하거나 커스텀 정렬로 lode 순서를 직접 제어한다. 링크 생성 함수(source accessor `[d.source.x1, d.y0]` 기반 cubic bezier, 수십 줄)만 vanilla TS로 이식하는 것이 strict CSP/no-CDN 환경에서 d3 전체 번들보다 낫다.
- **d3-parsets (Jason Davies)**: parallel sets의 D3 구현. `tension`(리본 곡률), `spacing`(범주 막대 간 픽셀 간격, 기본 20) 파라미터가 참고점. Kosara/Ziemkiewicz 기반.
- **RAWGraphs alluvial**: 오픈소스(CC BY-NC-SA), SVG export. 데이터는 각 행=아이템, 열=단계, 추가 열=선 가중치. layout 옵션: height, margin, node padding, node width.
- **Plotly parcats(parallel categories)**: `hoveron` = `color`/`category`/`dimension`(색상별/범주별/차원별 hover 상호작용), `arrangement`(perpendicular/freeform), `bundlecolors`, `sortpaths`(forward/backward), `counts`. hover 상호작용 모델과 `sortpaths` 개념이 특히 참고할 만하다.
- **크로싱 축소 코드 패턴(직접 이식용)**: 각 stage를 노드 배열로, 각 flow를 (leftNode, rightNode, value)로 표현. barycenter 스윕 = 오른쪽 고정하고 왼쪽 노드를 연결된 오른쪽 lode들의 가중평균 y로 재정렬 → 방향 반전 → 수렴까지 반복. 3 stage면 gap0(source-topic)·gap1(topic-status) 두 bipartite에서 topic 축을 공유 노드로 교대 스윕한다.

##### 6) 최종 권고안과 트레이드오프
아래 Recommendations에서 Vinc 제약(오프라인, strict CSP, no-CDN, 커스텀 SVG, 모노크롬+단일 accent, 3 stage)에 맞춰 단계적으로 통합한다.

#### Details
Vinc의 현재 렌더 파이프라인은 "튜플=alluvium=하나의 리본"이라는 올바른 데이터 모델을 이미 갖고 있다(정체성 보존, end-to-end hover). 부족한 것은 **렌더 시점의 순서 최적화 계층**과 **작은 흐름 처리 정책**이다. 즉 데이터 모델은 그대로 두고, "그리기 직전에 각 축의 lode를 재정렬"하는 얇은 레이어 하나를 추가하는 것이 최소 변경으로 최대 효과를 내는 지점이다. 세 축뿐이므로 barycenter 2-3회 스윕 또는 소규모 전수 탐색으로 거의 전역 최적에 도달할 수 있고, 이는 무거운 라이브러리 없이 순수 TS로 구현 가능하다. 곡선을 cubic/sigmoid로 바꾸고 strata 블록을 강화하면 "곡선 다발"이 "강(river)"으로 보이는 지각적 전환이 일어난다. 규모별 판단은 아래와 같다.

#### Recommendations

**Stage 0 — 즉시(현 13튜플 유지):**
1. 전역 고정 정렬 키를 폐기하고 **갭별 lode 재정렬**로 전환. topic을 중간 index 축으로 두고 gap0(source↔topic)·gap1(topic↔status) 각각에 barycenter 스윕 2-3회. 이것만으로 교차 대부분이 사라진다.
2. strata 블록 시각 강화(외곽선/채움 대비, 축 간 node padding 6-10px).
3. 곡선을 cubic/sigmoid(양 끝 flat)로 바꿔 블록 진입을 수직화.
4. 최소 밴드 두께(2-3px) 강제. hover focus+context는 이미 있으니 "나머지 opacity 0.1-0.2로 후퇴" 규칙만 확정.

**Stage 1 — 데이터가 커지면(토픽 6-8개, 튜플 20+):**
5. Others 버킷 도입: 임계치(예: 전체의 <2%, 또는 밴드 <3px) 미만 튜플을 중간축에서 "기타 토픽" stratum으로 병합. 좌·우 축은 유지하되 중간에서만 묶어 정체성 일부 보존.
6. lode.guidance를 forward/backfront로 A/B 테스트해 교차 최소 순서 채택. 소규모면 전수 계산으로 최적화.

**Stage 2 — 토픽 8개+, per-tuple이 무너지면:**
7. **결정 규칙**: 렌더된 밴드가 40개를 넘거나 평균 밴드<2px가 되면 per-tuple alluvial을 포기하고 다음 중 하나로 전환:
   - **(A) per-gap Sankey + end-to-end hover [1순위]**: 각 갭에서 (source,topic)·(topic,status)로 flow를 병합(개별 튜플 리본 대신 집계 리본). 정체성은 hover 시 경로 하이라이트로 복원. 정적 clutter 최소, 상호작용으로 보완.
   - **(B) parallel-sets/parcats 스타일**: 중간축 strata에서 리본을 재-스택(part-to-whole). 범주 조합 빈도 비교가 주 목표일 때 강함.
8. Others 병합 + luminance 램프(순서형 status)로 모노크롬 판독성 확보.

**판단 결론**: 현재 3 stage·13튜플에서는 per-tuple alluvial이 정당하며, Stage 0 개선만으로 "진짜 alluvial" 인상을 얻을 수 있다. 그러나 per-tuple 밴드 수는 튜플 수에 선형 비례하므로 토픽 8개+에서는 지속 불가능하다. 그 시점의 1순위는 **(A) per-gap Sankey + end-to-end hover**다. 이유: (1) Vinc가 이미 만든 hover 인프라를 재활용, (2) 정적 밴드 수를 |source×topic|+|topic×status|로 억제(곱이 아니라 합), (3) alluvial의 "연속 정체성"은 필요할 때만 hover로 복원하면 되기 때문. parcats 스타일(B)은 "범주 조합 분포"가 주 분석 목표일 때만 우선한다.

#### Caveats
- ParSetgnostics의 "clutter 최대 81% 감소"는 특정 데이터셋(Hassan et al.)의 Overlap 지표 최댓값(80.7% 반올림)이며 평균이 아니다. 이미 최적화된 원본에서는 0% 개선인 지표도 있었고, "clutter"는 overlap 면적(Zarate et al. 2018 기준)으로 조작적으로 정의됐다. 지표는 진단만 제공하고 재정렬 알고리즘 자체는 제공하지 않으며, 최적은 전수 계산으로 찾았다(비확장적). 평가 데이터셋도 Titanic을 제외하면 논문 그림에서 수동 재구성한 근사값이다.
- barycenter는 NP-hard 문제의 휴리스틱이므로 전역 최적을 보장하지 않는다. 소규모(3축)에서는 전수 탐색이 더 안전할 수 있다.
- Alluvial/Sankey/Parallel Sets의 명칭은 문헌에서 오랫동안 혼용돼 왔다(datavizcatalogue, Elijah Meeks, Cory Brunson 모두 지적). 이 보고서는 "고정 축·정체성 보존·사이클 없음"을 alluvial의 작동 정의로 채택했다.
- 모노크롬 명도 인코딩은 명도가 유사한 범주 구별이 어렵다는 한계가 있으므로, 축당 범주를 소수(권장 3개 이하)로 유지하거나 hover 보완이 필수다.
- "Sankey는 노드 15개 이하, 4-5 레벨" 같은 수치는 BI 벤더/실무 블로그 기준의 경험칙이며 엄밀한 사용자 연구 결과가 아니다.

## 부록 — as-built 대조 (2026-07-28, D68 구현과 동시 작성)

### A1. 갭 분석 — 원문 권고 중 파일링 시점에 이미 구현돼 있던 것

| 원문 권고 | as-built (파일링 시점) | 판정 |
|---|---|---|
| Stage 0-3 곡선 cubic 양끝 flat | `band()` 이 이미 cubic bezier, 제어점 x = 중점(양끝 수평 접선) — `atlas-planar.ts` | 이미 충족 |
| Stage 0-2 node padding 6-10px | NODE_PAD 24(bklit/D47 정본) | 초과 충족 |
| Stage 0-4 hover 후퇴 0.1-0.2 | `--recede-line: .12`(D32-a, `app.css` 유일 정의처) | 이미 충족 |
| §4 luminance 램프 | 값 비례 농도 `opOf(v)=0.16+0.5·(v/ribMax)` | 이미 충족(값-비례 해석) |
| §4 focus+context e2e | `data-path` + focusNode/focusRibbon(D63) | 이미 충족 |
| §4 stratum 직접 라벨+값 | sideNode 라벨+수치, 주제 판+수치 tspan | 이미 충족 |
| Stage 0-1 갭별 lode 재정렬 | 없음(전역 고정 정렬) | **D68 ①로 구현** |
| Stage 0-4 최소 밴드 2-3px | 1px clamp 만 | **D68 ②로 구현(2px, H 접기)** |
| Stage 1-5 Others 버킷 | 없음 | **D68 ③으로 구현(주제 통병합 변형)** |
| Stage 2-7(A) per-gap + e2e hover | 집계 폴백은 있었으나 자동 전환·끝-끝 hover 없음 | **D68 ④로 구현** |

### A2. 교차 불변 정리 — 원문 전제의 부분 반박 (구현 검증에서 발견)

원문 Stage 0-1 은 "barycenter 재정렬만으로 교차 대부분이 사라진다"고 기대하나,
Vinc 의 제약(stratum 순서 고정 + 주제 노드 안 들어오는 순서 == 나가는 순서) 하에서는
**성립하지 않는다**:

1. 다른 stratum 에 속한 두 가닥의 교차는 stratum 순서가 전부 결정한다 — lode
   재정렬로 제거 불가.
2. 같은 stratum 을 공유하는 쌍 중, 종전 (출처,상태) 사전순 배치는 왼쪽 갭 교차를
   이미 0 으로 만들고 있었다(정렬 키가 상대편 stratum 순서와 일치).
3. 남는 것은 같은 주제 안 **충돌쌍**(출처 순서와 상태 순서가 서로 반대인 튜플쌍)
   뿐인데, 공통 밴드 순서(띠 정합의 요구)를 어떻게 잡아도 이 쌍은 왼쪽 아니면
   오른쪽 갭에서 정확히 1회 교차한다. 즉 **교차 총수는 배치의 불변량**이다.

종전 배치는 이 최소치를 이미 달성하되 충돌 교차를 전부 오른쪽 갭에 몰아넣고
있었다 — 화면 오른쪽 절반만 엉켜 보이던 실측 인상의 원인. 따라서 재정렬의 실효는
① 교차의 양 갭 분산 ② 평균 기울기·방랑 축소(원문 §3 band straightening,
ParSetgnostics Slope 지표와 같은 방향)이고, D68 은 목표 메트릭을 교차 수가 아니라
**방랑 J** 로 정의한다. 교차 수 자체를 줄이는 유일한 지렛대는 주제 stratum 순서의
barycenter 재정렬인데, 이는 "크기순"(원문 §3 도 Rosvall 관례로 인정) 관례를 깨므로
발주자 부결(2026-07-28, 시각 검증 후 재론).

### A3. 방랑 메트릭은 L1 이 아니라 L2 (구현 중 실측 교정)

초기 구현은 J = Σ n·(|yL−yB| + |yB−yR|)(L1)이었다. 실측에서 barycenter 가 밴드
순서를 실제로 바꿔도 J 가 전혀 움직이지 않았다 — 밴드 중심이 양끝 사이에 있으면
|yL−yB| + |yB−yR| = |yR−yL| 로 **밴드 위치와 무관**해지고, 전형적 배치가 전부 그
평탄 구간에 들어가기 때문이다(keep-best 가 개선을 못 봐 초기 순서를 돌려줬다).
L2(제곱 합)로 바꾸면 중점(= barycenter 자리)이 유일 최소가 되어 개선을 추적하고,
기울기 분산을 벌하는 성질도 지각과 맞다(한쪽 0°·한쪽 급경사보다 양쪽 완만).
수기 검산: 같은 스왑이 L1 로는 0, L2 로는 −168.

### A4. 계측 (2026-07-28, 합성 데이터 node 실행 + 브라우저 하네스)

- 13튜플(3×4×3): 교차 총수 **41 = 41**(before g0 16/g1 25 → after g0 18/g1 23 —
  총수 불변 + 갭 분산 이동, A2 정리의 실증), 방랑 J(L2) 3814 → 3486(−8.6%).
- 합성 68튜플(3×8×3): 교차 총수 **124 = 124**(58/66 → 59/65), J −60.
- 브라우저(실 렌더러, 27항목 전부 통과): 열 합 보존 스프레드 ≤0.2px · 튜플 13쌍
  띠 정합 오차 **0.000px**(배치표 구조 보증의 실증) · 같은 데이터 2회 mount
  innerHTML **완전 일치**(결정론) · 최소 두께 9.2px(≥2) · hover 끝-끝(경로 모드
  가닥 2+노드 3, 출처 노드 8가닥) · 개념 알갱이 흐름 정렬 비내림 · 집계 모드
  전 리본 data-gk + 조인 hover + 강등 캡션 + 기타 stratum 비클릭.

### A5. 원문에서 변형·기각한 것

| 원문 | D68 채택형 | 이유 |
|---|---|---|
| Others = 튜플 부분 병합(중간축에서만 묶기) | **주제 단위 통병합** | 부분 병합은 화면에 남은 주제의 노드 값이 원장 수치와 어긋난다(주제 무결성) |
| 강등 판정에 "평균 밴드 <2px" | 가닥 수 >40 **또는** needRib > 절대 캡 2200px | 최소 두께를 H 로 접으면 평균은 구조적으로 2px 이상(자기모순), 픽셀 판정은 리사이즈에 모드가 튄다 — 데이터 순수 판정으로 교체 |
| lode.guidance forward/backfront A/B | barycenter 단일안 | 3축·소규모에서 barycenter 가 같은 효과를 결정론으로 얻는다. 교차 총수가 불변이라 A/B 의 판별력도 없다(A2) |
| 전수 탐색 옵션 | 기각 | 동상 — 목표가 방랑 최소화로 바뀌어 barycenter+keep-best 로 충분 |
| status luminance 순서 램프 | 기각(현행 유지) | 값 비례 농도가 이미 농도 채널을 점유 — 범주 램프를 겹치면 두 의미가 한 채널에서 충돌. D31 닫힌 층 |

### A6. 열린 질문

| # | 질문 | 상태 |
|---|---|---|
| Q1 | 주제 stratum 순서의 barycenter 재정렬 허용(교차 총수를 실제로 줄이는 유일 수단, 크기순 관례 파기) | **부결**(발주자 2026-07-28) — 라이브 시각 검증 후 재론 가능 |
