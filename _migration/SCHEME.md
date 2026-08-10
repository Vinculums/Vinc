# 신 체계 (P3 확정)

> 이행 원장: `ledger-documents.tsv`(문서 54) · `ledger-adr.tsv`(결정 68).
> 두 파일이 P5 재적재 페이로드 생성과 P7 컷오버 삭제 목록의 단일 원천이다.

## 도메인 7종

구 8종(`architecture design governance infra mcp security timeline web`)을 갈음한다.

| 도메인 | 담는 것 | 문서 | ADR |
|---|---|---|---|
| `vinc/design` | Atlas·표면·UI 카피·모션·디자인 시스템 | 23 | 36 |
| `vinc/infra` | Azure·배포·CD·비용·사고 | 9 | 2 |
| `vinc/engineering` | 데이터플레인·MCP 계약·팩·sync | 6 | 7 |
| `vinc/product` | 계정 정책·요금·웹 시스템·릴리스 | 6 | 1 |
| `vinc/governance` | 용어·관례·ADR 대장·계층 체계 | 6 | 15 |
| `vinc/ai` | 임베딩·검색·묻기·무LLM 경계 | 2 | 1 |
| `vinc/security` | owner 격리·위협 모델·감사 | 1 | 4 |

**없앤 것**
- `vinc/timeline` — 노드 종류를 도메인으로 쓴 오류였다. Episode 는 주제 도메인 + 일급 `date` 로
- `vinc/mcp` — 인터페이스는 구현이다 → `engineering` 으로 흡수
- `vinc/web` — 해체. 제품 정책은 `product`, 웹앱 구현은 `engineering`, 사이트 운영은 `infra`
- `vinc/architecture` — `engineering` 에 흡수(둘을 가르는 실익이 없었다)

**신설**
- `vinc/product` — 계정 정책 군집이 `mcp` 로 오배정돼 있던 것의 제자리. Louvain 군집 11이 근거
- `vinc/ai` — 임베딩·검색·묻기가 `design` 에 섞여 있던 것을 뺀다

⚠ `security` 문서가 1건뿐이다(위험 감사). ADR 은 4건인데 그것을 서술하는 문서가 없다 —
P4 에서 `vinc.canon.security-model` 신설을 검토할 것.

## 문서 계층 6종

`a/b/c/d` 라는 불투명한 문자 계층을 버리고 **성격을 이름이 말하게** 한다.

| 계층 | 뜻 | 건수 |
|---|---|---|
| `vinc.canon.*` | 불변 원칙·용어·계약. 바뀌면 다른 것이 따라 바뀐다 | 8 |
| `vinc.spec.*` | as-built 사양. 지금 그렇게 돌아간다 | 19 |
| `vinc.research.*` | 연구·분석. 결론이 아니라 입력 | 13 |
| `vinc.ops.*` | 런북·사고·감사 | 5 |
| `vinc.plan.*` | 실행 계획 | 6 |
| `vinc.release.*` | 릴리스 기록 | 2 |

`vinc.next-session` 1건은 **drop**(P0~P2 시절 핸드오프, 전량 낡음).

## authority 단일 축 (status 폐기)

구 체계의 `status`(draft/final)는 아무도 갱신하지 않아 신호가 죽었다 — 54건 중 39건이 draft 였다.
**status 를 폐기하고 `authority` 하나로 간다.** 상태가 아니라 성격이라 썩지 않는다.

| authority | 뜻 | 건수 |
|---|---|---|
| `canon` | 불변 계약. 여기가 바뀌면 아래가 전부 바뀐다 | 8 |
| `spec` | as-built. 코드와 대조 가능해야 한다 | 23 |
| `research` | 입력. 결론으로 인용하면 안 된다 | 13 |
| `record` | 역사. 당시엔 참이었다 | 9 |

구 어휘의 변형(`SSoT (계획)` · `SSoT (as-built)` · `Delta` · `inventory`)은 전부 위 넷으로 흡수한다.

## 결정 — 사건과 규칙을 분리

`dec-` 를 버리고 **`adr-`** 로 간다. 이유 셋:
1. `dec` 는 영어 코퍼스에서 모호하다(December? declare?). `adr` 은 업계 표준 표기다
2. `dec-` 가 `concept:` 안에서 **두 번째 타입 노릇**을 하고 있었다. 시리즈 문자가 사실상 도메인이었다
3. 시리즈 문자를 도메인으로 승격하면 그 은닉 분류가 드러난다

**형식**: `concept:adr-{domain}-{NNN}` (제로패딩 3자리)
**번호는 보존한다** — `D45` → `adr-engineering-045`. 사람용 라벨 `D45` 는 `props.label` 에 남긴다.
시리즈가 달라 도메인이 갈리므로 번호를 보존해도 충돌이 없다(기계 검증 완료).

**사건과 규칙 분리**: 결정한 사건 = Episode(`date` 보유) · 지금 유효한 규칙 = Concept. `about` 으로 잇는다.
**인용이 가리키는 곳은 규칙**(Concept)이다. Episode 는 출처다.

⚠ **제약**: DEC 68 중 날짜를 아는 것이 약 15개뿐이다. **날짜를 지어내지 않는다** —
출처를 아는 결정에만 Episode 를 만들고 나머지는 규칙만 둔다. 결손은 눈에 보이게 남긴다.

### 해소하는 이상 4건

| 이상 | 처리 |
|---|---|
| 이중 등록 (`d75`↔`d-vis-a`, `d76`↔`d-vis-b`) | 병합. `d-vis-*` id 소멸 |
| 정수 번호 없음 (`d-vis-0·c·d`) | design 최고번호(080) 다음부터 081·082·083 |
| 번호 이중 사용 (`d43`·`d63`) | 분할. d43 은 도메인이 갈려(engineering/design) 번호 보존 가능 |
| `dec-registry` | 결정이 아니라 대장 → `concept:decision-registry` 로 네임스페이스 밖 |
| `dec-d48-t` | 1회성 접미사 `-t` 폐기 → `adr-design-048` |
| 결번 (`gm-6`·`sec-2`) | **비워 둔다.** 번호를 당기면 역사가 어긋난다 |

## 엣지 어휘 7종 (ATTACHES_TO 정식화)

| 타입 | 방향·용도 |
|---|---|
| `relates_to` | 느슨한 연관. 무방향(역방향은 같은 간선으로 접힘) |
| `prerequisite_of` | A 가 B 보다 먼저 |
| `derives_from` | 개정·대체·계보 |
| `inherits_from` | 문서 frontmatter 상속의 미러 |
| `connects_via` | Bridge → Concept (role 필수) |
| `about` | Episode → Concept |
| **`attaches_to`** | **Record → Concept. 신규 정식화** |

`attaches_to` 는 이미 88건이 돌고 있었는데 `vinc_ingest` 공개 계약(6종)에 없었다.
부착은 연관과 다른 관계다 — Record 는 개념에 매달린 사실이지 동등한 이웃이 아니다.
**GRAPH.md 어휘와 MCP 스키마 양쪽에 드러낸다.** 데이터 이행은 없다.

## P4 가 판정할 미결 (원장 note 에 표시됨)

1. `vinc.ops.deployment` — 리포 파일이 정본인 예외를 신 체계에서도 유지할지
2. `vinc.changelog` — 같은 예외 후보(리포 CHANGELOG 미러)
3. `vinc.research.motion` — v0.7.4 에서 실제 구현됐으므로 spec 승격 후보
4. `dec-d63` 분할 시 두 결정의 도메인 배정(둘 다 design 이면 뒤엣것에 084)
5. `vinc.canon.security-model` 신설 여부(security 문서가 1건뿐)
6. `vinc.canon.principles` 의 `inherits_from: weg.c.memory-standalone` — 외부 프로젝트 참조를
   끊을지 허용목록에 넣을지
7. `vinc.next-session` drop 전 살릴 사실이 있는지 1회 확인
