# Vinc Notation 아이콘 세트 (v2)

레퍼런스 **Blue Note Re:imagined 2020** 의 그래픽-스코어 조형 어휘를 아이콘 시스템으로 정형화한 세트.
얇은 선(generic)이 아니라 **채운 면·굵은 대비·무게 변주** — 앨범과 동일한 노테이션 언어.

## 규격
- 24×24 viewBox · `stroke="currentColor"` · 기본 `stroke-width="2"`(획 요소), 채움은 `fill="currentColor" stroke="none"`.
- 색은 `currentColor` — 소비 측에서 `color: hsl(var(--foreground))` 등 토큰으로 지정.
- 그라데이션·순검정 그림자·다중 색 금지(디자인 게이트 D-S02 정합).

## 설계 원리 — 알파벳 → 조립
도메인 아이콘은 **원시 마크(알파벳)의 조합**으로 만든다. 새 아이콘도 이 어휘로 조립한다.

### 알파벳 (원시 마크)
`disc`(꽉 찬 원) · `dot` · `ring`(빈 원) · `half-disc`(반 채움) · `pill`/`pill-v`(캡슐) · `ellipse`(납작 타원) ·
`stack`(3겹 타원) · `dumbbell`(점–선–점) · `comet`(점+꼬리) · `pendulum`(선 끝 매단 점) ·
`hourglass`(보타이 ▶◀) · `arc`(호) · `dot-grid`(점자 격자) · `dash-ramp`(그라데이션 대시) ·
`bar-group`(수직 바 묶음) · `caret`(^) · `chevron-right`/`chevron-left`.

### 도메인 · 내비 (조립)
| 아이콘 | 조립 | 의미 |
|---|---|---|
| `ingest` | 혜성 → 캡슐 낙하 | 소스가 스토어로 적재 |
| `recall` | 도트 매트릭스 + 선택 링 | 레코드 열람 |
| `domains` | 그라데이션 스택 타원 | 도메인 분포 |
| `graph` | 디스크 노드 + 덤벨 엣지 | 그래프 마이닝 |
| `search` | 오픈 링 + 코어 점 + 손잡이 | 스캔/검색 |
| `pack-export` | 스택 + 이탈 혜성 | 팩 내보내기 |
| `login` | 토큰 온 스탠드 | 로컬 계정 |
| `onboarding` | 세그먼트 스테퍼 | 설치 단계 |
| `settings` | 방사 대시(비-기어) | 설정 |
| `pin` | 펜듈럼 | 고정 |
| `decide` | 아워글래스 | 변환/결정 |

### 상태 3단계 (색 아닌 형태로)
`disc`(꽉참=success) → `half-disc`(부분) → `ring`(빔=empty). 모노크롬 팔레트에서 상태는 색이 아니라
**표기 아이콘 + 텍스트 무게**로 변별한다(설계 규약).

### UI 필수
`check` `x` `alert` `play` `pause` `upload` `download` `lock` `edit` `sparkle` `sync` `undo` — 동일 굵기 언어.

시각 인덱스: `../../gallery/icons-v2.html` (전체 43개 시트, 그룹별).
