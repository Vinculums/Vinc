# Vinc 워크스페이스 — 세션 규율

이 파일은 **지시**다. 여기 적힌 것은 기본 동작을 덮어쓴다.

## 1. 지식의 정본은 Vinc 그래프다 (파일이 아니다)

Vinc 는 자기 지식을 자기 제품에 담는다(도그푸딩). 개념·결정·사실·사고 기록·연구
산출물은 **그래프에 쓴다**:

```
vinc_doc_put   문서 정본(Chunk=SSoT) — 산문·연구·사고 기록·계획
vinc_ingest    노드와 엣지 — 개념 · 기록(Record) · 사건(Episode) · 관계
```

쓰기는 **호스티드 `vinc` MCP**(계정 그래프)로 한다. 로컬 stdio(`vinc-local`)는 이 기기의
개발용 스토어라 다음 세션이나 다른 기기에서 안 보인다. 로컬 stdio 에는 `target` 인자가
없다: 등록된 `vinc mcp serve` 는 앱 모드를 따른다(VIN-132, 계정 모드면 호스티드로 통째로
넘기고 로컬 모드면 이 기기 스토어). 예전의 `target:"remote"` 는 이제 거절된다.

**파일로 남기는 것은 실행 시점에 도구가 읽는 것뿐이다** — 코드, 워크플로, 스크립트,
설정(`*.yml`·`*.json`), 그리고 이 파일. 사람이 읽으라고 쓰는 산문은 파일이 아니라
그래프로 간다.

> ⚠ 이 규율이 지켜지지 않은 이유가 있다. 여태 이 내용은 **메모리에만** 있었는데,
> 메모리는 `<system-reminder>` 로 전달되는 **배경 정보**이지 지시가 아니다. 그래서
> 세션마다 문서를 파일에 쓰고 그래프에는 안 쓰는 드리프트가 쌓였다. 그래서 여기 옮겼다.

### 코드 주석: 주장은 코드에, 논증은 그래프에

계약 정본은 [[vinc.canon.code-annotation]]. 요지만:

- 그 줄을 고치는 사람이 알아야 하는 것(무엇을 하는가·무엇을 깨면 안 되는가)은 코드에
  한국어로 남긴다. **왜 이렇게 됐나·언제 뭘 재봤나·뭘 기각했나**는 그래프로 옮기고
  주석은 그 자리를 가리킨다 — `근거: [[vinc.spec.dataplane §7]]` 처럼 문서·절이나
  노드 id 를 이중대괄호로 적는다.
- 인용 문법은 `[[...]]` **하나**다. `scripts/gate_graph_refs.py` 가 CI 에서 실재를
  대조한다(오프라인 — 대조표는 `scripts/graph-refs.json`, 갱신은
  `scripts/refresh_graph_refs.py` 로 로컬에서 돌려 **같은 커밋에** 넣는다).
- **금지 어휘를 담은 id 는 인용 불가**다(인용은 소스 파일에 남고 `gate_forbidden` 이
  소스를 읽는다). 레거시 구명 문서 대신 후속 문서를 가리킬 것.
- 순서: 검색 → (없으면) 쓰기 → **읽어서 확인** → 주석 축약. 3번을 건너뛰면
  `partial` 로 반쯤 들어간 쓰기를 성공으로 읽고 주석만 잃는다.

### 쓰기 전에

`vinc_conventions` 를 세션의 첫 쓰기 **전에** 한 번 부른다 — 이 소유자의 노드·관계
관례와 이미 있는 도메인을 실어 오므로, 새로 만드는 것이 기존과 어긋나지 않는다.
그리고 **read-before-write**: `vinc_search` 로 기존 id 를 찾아 재사용한다. 새 id 를
지어내면 같은 것이 두 벌 생긴다.

### 급소 (실측으로 물린 것들)

- **오픈 타입 노드는 `props`·`domain` 이 저장되지 않는다.** `record|concept|domain|
  bridge|episode|decision` 여섯만 전용 테이블로 가고 `props{}` 가 JSON 열로 남는다.
  (`decision` 은 v12 에서 늘었다. 이 목록이 다섯으로 적혀 있어서 "결정에 props 를
  실으면 사라진다"는 오해가 세션마다 재생산됐다.) 그 밖의
  타입은 Entity 캐리어로 가는데 열이 `type/title/tags` 뿐이라 **값이 조용히 사라진다**
  (경고는 뜬다). 값을 실어야 하면 `concept` 이나 `record` 를 쓸 것.
- **엣지는 같은 페이로드에서 실제로 MERGE 된 노드 사이에서만 해석된다.** 배치를
  나누면 배치 간 엣지가 조용히 skip 되고, **같은 배치라도 노드가 스킵되면 그 노드로
  향하는 엣지가 함께 떨어진다**(제목 없는 Entity · 남이 소유한 id 등). 양 끝 노드를
  엣지와 같은 호출에 동봉하고, `warnings` 를 읽을 것 — `merged` 는 실제로 들어간
  것만 센다.
- ~~**`title` 에는 가드가 없다.**~~ **해소됨**(`api/fragment.py` `_merge_node`,
  커밋 85c75f2). 이제 `title` 도 `domain`·`tags`·`props` 와 같이 미지정 시 보존된다.
  지우지 않고 남기는 이유는 이 함정을 기억하는 세션이 아직 있어서다.
- **"미지정 시 보존"은 "부분 전송이 안전하다"가 아니다 — `props` 는 통째로 교체된다.**
  위 줄을 그렇게 읽고 물렸다(2026-09-01). `props` 를 **실으면** 기존 dict 이 그 값으로
  갈아치워지고, 안 실은 키는 **경고 없이 사라진다**. 즉석 실측: `{alpha, beta}` 를 가진
  레코드에 `{gamma}` 만 재전송했더니 남은 것은 `gamma` 하나였고 `warnings` 는 비었다.
  필드 이력이 없으므로 **복구는 없다.** 키 하나를 더하려면 **기존 props 를 먼저 읽어
  합쳐서** 통째로 보낼 것(`vinc_ingest` 의 `stored` 가 현재 값을 그대로 준다).
  아래 Entity `tags` 함정과 같은 종인데 그쪽만 명시돼 있어서 이 줄을 더한다.
- **그 후속: Entity 는 `tags`·`theme_id` 가 여전히 무가드다.** 개방 타입 노드의
  Entity 분기는 두 필드를 **무조건 SET** 한다 — id 와 title 만 실은 부분 재전송이
  `tags` 를 빈 배열로 지운다. 그리고 `themes=None` 으로 부르는 경로(sync·share·
  테스트)는 `theme_id` 를 `""` 로 덮는다. 개방 타입 노드를 다시 보낼 때는 tags 를
  함께 실을 것.
- **도메인은 `vinc/` 아래에 둔다 — 접두를 빼면 지도가 통째로 다시 뿌리내린다.**
  배치는 모든 노드 도메인의 **공통 접두**를 뿌리로 벗긴다(`api/query.py` `_build_tree`).
  2026-09-18 에 노드 **셋**이 `product`·`engineering` 으로 들어오자 공통 접두가
  `vinc` 에서 빈 것으로 떨어졌고, 나머지 2,276개 전부가 `vinc` 라는 자식 **하나의
  원뿔 안**으로 들어갔다 — 원점에서 사방으로 터지던 지도가 한쪽 부채꼴이 됐다.
  아래 §4 의 "신 도메인 7종"은 **`vinc/` 다음 칸**을 말한다: `vinc/engineering/...`
  이지 `engineering` 이 아니다. 고치는 법은 도메인만 실은 부분 재전송이다(제목·
  태그·props·유효구간은 키가 없으면 보존된다).
  정본: [[record:three-nodes-without-the-vinc-prefix-collapsed-the-whole-map-into-one-cone-2026-09-19]]
- 화면 텍스트 규칙(em-dash 금지 등)은 [[vinc.manual.app]] §3 에 있다. 앱·사이트 문구를
  만지기 전에 읽는다(§3).
- **"atlas" 절대 금지어 — 후속 명칭은 "view"다(F8, 2026-08-15 발주자 지시).** 그래프 맵
  서피스의 구명(Atlas)을 코드·식별자·라우트·UI 카피·홈페이지 문구 전체에서 걷어냈다
  (`Vinc/app`·`Vinc-homepage` 전체 리네임 완료). `scripts/gate_forbidden.py` 가 F8 로
  강제하고(대소문자 무관 부분일치라 `AtlasView`·`atlasScene` 같은 파생어도 잡는다),
  CHANGELOG·README 의 과거 릴리스 기록만 예외다. 정본과 이유는 그래프
  `vinc.canon.terminology` §7. ⚠ 서피스 **안**에 이미 있던 "view/viewpoint" 어휘(3D·2D·
  타임라인·번들 전환)와 이름이 겹쳐서 — `AtlasView`를 그대로 바꾸면 `ViewView`가 된다 —
  그 어휘는 **"viewpoint"로 개명**해 분리했다(`AtlasView`→`Viewpoint`, `VIEWS`→
  `VIEWPOINTS` 등). 새 코드에서 "atlas"를 다시 쓰지 말 것 — 게이트가 막는다.

## 2. 리포별 지위

| 리포 | 지위 | 세션에서 할 것 |
|---|---|---|
| `Vinc` | 제품 코드 (앱·허브·MCP·사이드카) | 코드는 여기. 설계 산문은 그래프 |
| `Vinc-homepage` | 사이트 + 계정 백엔드 | 코드는 여기 |
| ~~`Vinc-design`~~ | **폐기(2026-08-08)** — `Vinc/design-hub/` 로 이전 | [[vinc.manual.design]] 참조 |
| ~~`Vinc-docs`~~ | **폐기(2026-08-11)** — 아카이브. 지식은 그래프, 이력은 `Vinc/_migration/` | §4 참조 |

⚠ GitHub 상태와 이 표가 한 곳에서 어긋난다: `Vinc-design` 은 여기 폐기로 적혀 있지만
리포는 아직 **아카이브되지 않았다**(2026-08-11 확인). 내용은 `design-hub/` 로 옮겨졌으니
읽을 이유는 없다. 조직에 `Vincs-app` 이라는 리포도 있는데 이 표에 없다 — 둘 다 미판정.

## 3. 팀별 매뉴얼 — 그 경로를 만지기 전에 읽는다

**지시다.** 아래 경로를 고치기 전에 그 팀의 매뉴얼을 `vinc_doc_get` 으로 읽는다. 팀별 함정
(엔진·배포·평가·디자인 허브·화면 문구)은 이 파일이 아니라 거기 있다. 이 파일에는 모든 세션에
공통인 규율만 남긴다. 경로의 정본은 `agents/teams.json` 이고(`scripts/gate_ownership.py` 가
강제한다), 아래 열은 요약이다. 근거: [[vinc.plan.team-split]]

| 팀 | 주로 만지는 곳 | 먼저 읽을 것 |
|---|---|---|
| core | `store/` · `api/` · `vinc_mcp/` · `engines/` · 허브 스토어 | [[vinc.manual.core]] |
| rag | `vinc_rag/`(검색 코어·리랭커·임베딩 워커) · `rag_eval/`(평가 하네스·평가셋·고정 스토어 락) | [[vinc.manual.rag]] |
| app | `app/` (데스크톱·웹 프론트엔드), 사이드카 라우트 | [[vinc.manual.app]] |
| design-content | `design-hub/` · `starter-packs/` · `emails/` · `Vinc-homepage/site/` · `app/src/copy.ts`(app 과 공동) | [[vinc.manual.design]] |
| platform | `infra/` · `packaging/` · `.github/` · `scripts/gate_*` · `agents/` · 허브 운영(배포·`az`·파일 공유) | [[vinc.manual.platform]] |

- 두 팀에 걸친 변경이면 둘 다 읽는다(예: 새 사이드카 라우트는 core 와 app, 토큰 값은
  design 과 소비 리포).
- 매뉴얼에 없는 함정을 새로 물렸으면 **그 매뉴얼에 쓴다**(읽기 → 합쳐서 `vinc_doc_put` →
  읽어서 확인). 이 파일로 되돌려 쓰지 않는다. 모든 세션에 해당하는 것만 여기로 온다.
- design-hub 절(값의 정본과 이유의 정본, 디자인 게이트)은 2026-10-01 에 여기서
  [[vinc.manual.design]] §1 로 옮겼다.

## 4. Vinc-docs — 폐기 완료 (2026-08-11)

리포는 아카이브됐다. **다시 열지 마라. 지식은 그래프에 있고 이력은 `Vinc/_migration/`
에 있다.**

폐기 판단의 근거는 대조였다: 재작성본 54건의 id 를 라이브 `vinc_doc_list` 와 기계
대조해 **누락 0** 을 확인했다. 그리고 구 코퍼스(`vinc/`, 802K)는 그 대조에 덮이므로
가져오지 않았고, **덮이지 않은 605K 는 전부 가져왔다**.

> ⚠ 여기서 하나 배웠다. **원장은 "그래프에 이미 적재된 문서"를 훑어 만들어졌다.**
> 그래서 파일로만 존재하고 적재된 적 없는 것은 원장의 시야 밖이었다 — `plans/` 11건
> 중 7건, `DESIGN/` 아이콘·Figma 내보내기, 그리고 이 파일이 "마일스톤 정본"으로
> 지목하던 `PLAN.md` 자신이 거기 있었다. 원장을 유실 검사로 쓰면 안 된다. 파일
> 트리를 직접 세야 한다.

| 어디 | 무엇 |
|---|---|
| `Vinc/_migration/` | 이행 원장·재작성본·실행 스크립트(139파일). P5 를 결정론적으로 재현하는 장치 |
| `Vinc/_migration/legacy/` | 원장이 못 본 잔여 605K. `plans/`·`DESIGN/`·`PLAN.md`·`master-id-map.yml` |

남은 이행 작업은 리포와 무관하게 그래프 위에서 계속된다:

- **엣지 재배선 판정 263건**(2026-08-09 센서스, `_migration/edge-census.json`).
  ⚠ 이 수는 223 → 160 → 263 으로 두 번 바뀌었고 그 계보는 `_migration/STATE.md` 에
  표로 있다. **어느 하나를 인용하기 전에 `edge_census.py` 를 다시 돌릴 것**
- **제목이 한국어인 노드** — 신 체계는 영어다. 규모 미측정
- **doc 미러 제목이 주제 개념과 충돌** — `concept:doc.*` 가 문서 제목을 글자 그대로
  물려받아 Ask 가 구분 불가능한 후보 둘을 내민다. 정본은 `record:risk-doc-title-collides-with-topic`

### 그때 무슨 일이 있었나 (P5, 보존용)

**P5 는 2026-08-09 에 재실행됐다**(`_migration/STATE.md` 가 정본).

> ⚠ 여기 "2026-08-08 에 끝났다"고 적혀 있었는데 **거짓이 됐다.** 그날 밤 재생 불가
> WAL 로 그래프가 통째로 되감겨 P5 결과가 전부 사라졌다. 원장과 재작성본과 스크립트가
> 파일로 남아 있어 결정론적으로 재현된다는 것이 이 이행의 유일한 구원이었다.
> **"끝났다"를 쓰기 전에 라이브를 조회할 것** — `vinc_domains` 한 번이면 갈린다.

재실행 진행분: 노드 192건 제자리 재작성(created 0 · matched 192) · 영어 문서 54건
적재 · 구 문서 49건 삭제. 남은 것은 학습자료 퍼지 · 도메인 재매핑 · doc-concept 정리
· 상속 엣지 96건이다.

신 도메인은 7종(design·engineering·infra·governance·security·product·ai)이고 **전부 `vinc/` 아래 둘째 칸**이다(`vinc/design`·`vinc/engineering`…). 원장 밖
노드 30건이 아직 구 도메인(`vinc/mcp`·`vinc/architecture`·`vinc/web`)을 달고 있고
`remap_domains.py` 가 그걸 옮긴다(`vinc/web` 4건은 원장에서 갈려 사람 판정 대기).

급소 하나: **원장의 신·구 문서 id 가 4건에서 같다**(`vinc.ops.deployment` ·
`vinc.ops.incident-2026-08-04-easyauth` · `vinc.ops.risk-audit-2026-08-05` ·
`vinc.plan.code-signing`). 그것들은 제자리 재작성이므로 `olddocs` 단계가 지우면
방금 올린 재작성본이 오류 없이 사라진다. `load_p5.py` 가 이제 걸러 낸다.

이 절의 규율은 폐기 후에도 그대로다: **새 지식은 그래프로 간다.** `_migration/` 은
이력이지 조회처가 아니다.

## 5. 테스트와 배포

**Vinc 테스트는 이 한 줄로만 제대로 돈다:**

```bash
env -u PYTHONHOME -u PYTHONPATH ./.venv/Scripts/python.exe -m pytest tests
```

전역 `PYTHONHOME`/`PYTHONPATH` 가 시스템 파이썬을 가리켜서 그냥 부르면 venv 조차 깨진다.
**정상은 938 passed · 4 skipped**(2026-08-09 기준. 늘어나는 값이니 중요한 건 skip=4).
skip 이 135 면 인터프리터를 잘못 잡은 것이고, 그
상태에서 "main 과 똑같이 실패한다"는 대조는 **무효다**(실제로 그걸 믿고 머지했다가
CI 에서 걸렸다).

**확장 경로는 기계마다 다르다.** 리눅스(CI·클라우드 세션)에서 스토어 테스트를 돌릴 때는
`VINC_LBUG_EXT_DIR=packaging/lbug_extensions` 를 같이 준다(`ci.yml` 과 같다). 없으면 재구축
테스트가 "fts 확장 미설치"로 떨어지는데, 그 실패가 진짜 고장을 가린다. 윈도우 로컬에서는
**주지 않는다**: 번들은 리눅스 ELF 이고, 로컬 venv 는 CI·허브(0.20.4)와 다른 엔진 판이다.
자세한 것은 [[vinc.manual.core]] §1.

배포 정책 정본은 그래프의 `vinc.ops.deployment` — `vinc_doc_get` 으로 읽는다. 모든 세션이
알아야 하는 것은 하나다: **`main` push 가 곧 배포다**(`hub-deploy`·`site-deploy`·`release`).
브랜치에서 검증하고, 한 번에 하나씩 머지하고, 배포가 끝나는 것을 보고 다음을 넣는다.

허브 운영의 함정(`az` 는 PowerShell 로, 윈도우 jq 의 CRLF, SMB 파일 잠금과 공유 스냅샷,
고아 핸들 강제 종료)은 [[vinc.manual.platform]] 에, 스토어 엔진의 함정(SMB 위 임베디드 DB,
FTS 색인 표류와 자기복구, 기동을 막는 FTS 손상의 복구, 체크포인트 임계값, 벡터 인덱스
드롭이 남는 이유)은 [[vinc.manual.core]] §3 에 있다. 2026-10-01 에 이 절에서 옮겼다.

## 6. 세션 시작 — 로스터가 보이는지 먼저 답할 것

하위 에이전트 정의는 **cwd 에서 위로 걸어 올라가며** 찾는다. 그래서 로스터는 리포 안이
아니라 **워크스페이스 루트**(`Vinc` 의 부모)의 `.claude/agents` 에 한 벌 둔다 — 루트와
그 아래 어느 체크아웃·worktree 에서 열어도 닿는 자리다. 리포는 추적 사본
`agents/shells/` 만 갖고(CI 가 `agents_sync.py --check` 로 대조), 설치는 사람이 머지 뒤에
한다. 같은 명령이 격리 역할(dev-core·dev-app)용 WorktreeCreate/WorktreeRemove/SubagentStop
훅도 건다(루트는 git 리포가 아니라서 훅 없이는 하네스가 그 역할을 거절하고, 훅이 만든 트리는
하네스가 치우지 않는다):

```bash
python -X utf8 scripts/agents_sync.py --install   # 반복해도 같다. 쓰는 것은 agents/·hooks/ 와 settings.json 의 훅 세 키뿐
python -X utf8 scripts/roster_probe.py
```

빈손일 때 "로스터가 없다"와 "여기서는 안 보인다"는 다른 말이다. 실제로 잘못된
디렉터리에서 `ls` 한 결과를 보고 앞의 것으로 단정한 적이 있다. 이 도구는 개수만
세지 않고 **어디서 시작해 어디까지 걸었는지**를 함께 말한다.

## 7. PR 제목은 영어 · 타입 접두 · 대시 없음 (2026-09-11 지시)

**스쿼시 머지에서 PR 제목이 곧 `main` 의 커밋 제목이다.** 그래서 이것은 PR 화면의
취향이 아니라 `git log` 의 계약이고, 히스토리는 되돌릴 수 없다.

```
type(scope): subject          # scope 는 선택, `!` 는 파괴적 변경
fix(share): unchecked documents stay behind
feat(search): say when the meaning arm is missing
ci(release): pin the interpreter the container can run
```

- **타입은 소문자** 고정: `feat` `fix` `ci` `chore` `design` `docs` `release` `test`
  `infra` `agents` `refactor` `security` `perf` `build` `style` `revert`.
  대문자(`Fix:`)로 바꾸지 않는 이유는 이미 소문자가 162건이고 표기를 섞으면 얻는 것이
  없어서다(실측 2026-09-11, 최근 200 커밋 중 접두 없는 것이 38건이었다).
- **제목은 영어(ASCII)**. 한국어와 영어가 섞여 `--oneline` 이 한 화면에서 두 언어로
  읽히던 것을 닫는다. 본문·코드 주석·이 문서는 **그대로 한국어다** — 제목 한 줄만이다.
- **em-dash(`—`)·en-dash(`–`) 금지.** 화면 텍스트 규칙([[vinc.manual.app]] §3)을 제목까지 넓힌 것이다.
  좁은 목록에서 폭이 넓어 줄을 밀어낸다. 대체는 콜론·괄호·새 문장.
- subject 는 소문자로 시작하고 마침표로 끝내지 않는다(약어는 예외 — `MCP`·`CI`).
  제목은 100자 안. 자세한 것은 본문에 쓴다.

강제는 `scripts/gate_pr_title.py` 이고 `ci.yml` 의 `changes` 잡에서 PR 마다 돈다
(잡을 새로 세우지 않은 이유는 청구가 1분부터 시작해서다). 손으로 보려면:

```bash
python -X utf8 scripts/gate_pr_title.py "fix(share): unchecked documents stay behind"
```

⚠ 이 규칙은 **제목만** 본다. 게이트가 본문을 읽지 않으므로 본문의 한국어는 위반이
아니고, 반대로 게이트가 초록이라고 본문이 검토된 것도 아니다.

## 8. Linear 에 이슈·프로젝트를 등록할 때 (2026-10-03 지시)

**지시다.** Linear 이슈나 프로젝트를 만들거나 옮기거나 닫기 전에 [[vinc.canon.linear-workflow]]
를 `vinc_doc_get` 으로 읽는다. 그래프에 닿지 않는 도구라면 Linear 팀 문서 "How Vinc uses
Linear" 가 같은 내용의 요약이다(둘이 다르면 정본을 따른다). 세션마다 물리는 것만 여기 둔다:

- **브랜치보다 이슈가 먼저다.** 먼저 검색해서 열린 이슈를 재사용하고, 키는 브랜치 이름이나
  PR 본문(`Closes VIN-123`)에 넣는다. PR 제목은 §7 그대로라 키를 넣지 않는다.
- **이슈마다 Area 라벨 하나**: `core` `rag` `app` `design-content` `platform` `qa`.
  앞의 다섯은 `agents/teams.json` 의 팀이다.
- **이슈마다 Agent 라벨 하나**(2026-10-04): `agents/roles.json` 의 역할 id(`agent-dev-core`
  등). 담당자 칸은 사람으로 둔다. 사람만 할 수 있는 단계는 Agent 라벨 없이 설명 첫 줄에 적는다.
- **설명에 실행 로그를 넣지 않는다**(2026-10-04). 대화 기록·명령 로그·테스트 출력·로컬 경로는
  그래프 에피소드로 보내고 설명이나 댓글에는 그 id 만 적는다.
- **배포 확인은 사람이 하지 않고 에이전트가 훑는다**(2026-10-04). Done 은 머지 **그리고** 그
  배포 확인이다. `agent-qa`·`agent-steward` 세션이 Done 이슈마다 그 머지를 담은 성공한 배포 실행을
  대조하고, 없는 것만 In Review 로 되돌린다. 실패했거나 빌드를 건너뛴 실행은 확인이 아니다.
  **릴리스로만 나가는 코드(CLI·데스크톱·설치본)는 머지되면 Done** 이고, 릴리스는 프로젝트 단위로
  확인한다: 태그가 있고 release 가 성공하고 `latest.json` 이 그 버전일 때 프로젝트를 닫으며, 그때
  태그에 안 담긴 이슈는 다음 릴리스 프로젝트로 옮긴다.
- **프로젝트는 끝이 있는 결과물에만** 붙인다. 영역 버킷(Site & Docs · Infra & CI ·
  Research 같은 것)은 2026-10-03 에 닫았고 다시 만들지 않는다. 프로젝트가 없는 이슈는 정상이다.
- **Linear 화면과 커넥터에는 보관 기능이 없다.** 닫힌 이슈도 무료 한도(250)를 먹는다.
  보관은 팀 자동 보관이나 사람의 키로 부르는 `issueArchive` 뿐이고, 키는 사람의 환경변수
  `LINEAR_API_KEY` 에만 있다. 채팅으로 키를 받거나 입력하지 않는다.
