# Vinc 워크스페이스 — 세션 규율

이 파일은 **지시**다. 여기 적힌 것은 기본 동작을 덮어쓴다.

## 1. 지식의 정본은 Vinc 그래프다 (파일이 아니다)

Vinc 는 자기 지식을 자기 제품에 담는다(도그푸딩). 개념·결정·사실·사고 기록·연구
산출물은 **그래프에 쓴다**:

```
vinc_doc_put   문서 정본(Chunk=SSoT) — 산문·연구·사고 기록·계획
vinc_ingest    노드와 엣지 — 개념 · 기록(Record) · 사건(Episode) · 관계
```

`target` 은 **`remote`**(계정 그래프)가 기본이다. `local` 은 이 기기의 개발용 스토어라
다음 세션이나 다른 기기에서 안 보인다.

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
- **그 후속: Entity 는 `tags`·`theme_id` 가 여전히 무가드다.** 개방 타입 노드의
  Entity 분기는 두 필드를 **무조건 SET** 한다 — id 와 title 만 실은 부분 재전송이
  `tags` 를 빈 배열로 지운다. 그리고 `themes=None` 으로 부르는 경로(sync·share·
  테스트)는 `theme_id` 를 `""` 로 덮는다. 개방 타입 노드를 다시 보낼 때는 tags 를
  함께 실을 것.
- 화면에 노출되는 텍스트에 **em-dash(`—`) 금지**. 대체는 `·`·괄호·콜론.
  (이 규칙은 UI 텍스트에만 해당한다. 커밋 메시지·이 문서는 무관하다.)
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
| ~~`Vinc-design`~~ | **폐기(2026-08-08)** — `Vinc/design-hub/` 로 이전 | §3 참조 |
| ~~`Vinc-docs`~~ | **폐기(2026-08-11)** — 아카이브. 지식은 그래프, 이력은 `Vinc/_migration/` | §4 참조 |

⚠ GitHub 상태와 이 표가 한 곳에서 어긋난다: `Vinc-design` 은 여기 폐기로 적혀 있지만
리포는 아직 **아카이브되지 않았다**(2026-08-11 확인). 내용은 `design-hub/` 로 옮겨졌으니
읽을 이유는 없다. 조직에 `Vincs-app` 이라는 리포도 있는데 이 표에 없다 — 둘 다 미판정.

## 3. design-hub — 파일은 빌드 입력, 정본은 그래프

`Vinc/design-hub/` 는 `lime-design` 플러그인이 규정한 Design Hub 구조다
(2026-08-08 에 별도 리포 `Vinc-design` 에서 이 리포로 옮겼다 — 실질 내용이 50KB·커밋
21개뿐이라 리포 하나를 유지할 무게가 아니었고, 산문은 이미 전부 그래프에 있다)
(`design.profile.yml` · `DESIGN/design-tokens.json` · `tokens.css` · `imports/` ·
`tenants/` · `dist/` · `reports/`).

lime-design 엔진은 **네트워크 I/O 를 하지 않는 파일 기반**이고 허브 디렉터리 기준
상대경로로 푼다. 그러니 파일이 사라지면 `/ds-gen`·`/ds-gate` 가 못 돈다.

### 값의 정본과 이유의 정본은 다르다

> ⚠ 이 절은 2026-08-08 에 고쳐 썼다. 전에는 "정본 = 그래프, `DESIGN/*` 는 그래프에서
> **재생성**하는 산출물"이라고 적혀 있었다. 그런 재생성 경로는 **없다** — 그래프를
> 읽어 파일을 쓰는 스크립트가 허브에도 플러그인에도 0건이고, 엔진은 네트워크를 아예
> 안 쓴다. 즉 규율이 물리적으로 불가능한 것을 요구하고 있었고, 실제 관행은 파일을
> 고치고 그래프에 복사하는 **반대 방향**이었다. 안 지켜지는 규칙은 규칙이 아니다.

| 층 | 무엇의 정본인가 | 방향 |
|---|---|---|
| Figma | **시각적 저작** — 사람이 그리는 곳 | 상류 |
| `DESIGN/design-tokens.json` | **값** — 빌드가 읽는 유일한 것 | 중심 |
| `tokens.css` · `dist/` · 소비 리포 사본 | 파생물, 수기 편집 금지 | 하류 |
| Vinc 그래프 | **이유** — 결정·근거·이력 | 옆 |
| Claude Design | 배포본 | 하류 |

- 토큰 **값**을 바꾸려면 `DESIGN/design-tokens.json` 을 고친다. 그리고 `/ds-gate` →
  `scripts/sync_out.py --apply` 까지 한 세션 안에 끝낸다
- 그래프에 두는 토큰 사본(`vinc.design.tokens`)은 **검색용 스냅샷**이지 정본이 아니다.
  배포 런북과 같은 예외이며 본문 첫 줄에 배너를 단다(GRAPH.md 관례)
- 그래프가 정본인 것은 **왜**다: 시안 결정, 게이트 판정, 그 값을 고른 이유.
  `concepts/*.md` 는 `vinc_doc_put` + `episode`, `reports/eval.design.*` 는
  `record` 노드로("그때는 참이었다")

이 형태의 이점은 디자인 결정과 제품 결정이 **한 그래프에서 이어진다**는 것이다 —
"이 토큰이 왜 이 값인가" 에서 시안 문서 · DEC · 화면설계까지 `vinc_path` 로 따라간다.
파일은 그 일을 못 하고, 그래프는 빌드에 값을 못 준다. 둘 다 필요한 이유다.

### 디자인 게이트 (실행체)

```bash
cd design-hub
python -X utf8 scripts/gate_surface.py ../app/src              # 하드코딩 색 (생성물 제외)
python -X utf8 scripts/gate_surface.py ../../Vinc-homepage/site
python -X utf8 scripts/sync_out.py --check                     # 소비 사본이 허브와 같은가
python -X utf8 scripts/push_ssot.py                            # Claude Design 으로 내보낼 것 빌드
```

소비 리포 CI 에는 `scripts/gate_design_sync.py` 가 있다 — 허브를 체크아웃하지 않고
각인 해시로 **사본이 손으로 고쳐졌는지**만 본다. 허브가 앞서간 경우(동기화 누락)는
거기서 안 보이고 위의 `--check` 가 잡는다. 둘은 다른 고장을 잡는다.

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

신 도메인은 7종(design·engineering·infra·governance·security·product·ai). 원장 밖
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

배포 정책 정본은 그래프의 `vinc.ops.deployment`(30청크) — `vinc_doc_get` 으로 읽는다.
짚어 둘 것:

- `main` push 가 곧 배포다(`hub-deploy`·`site-deploy`·`release`). 브랜치에서 검증하고 머지
- **허브 env 를 `az` 로 바꿀 때는 PowerShell 을 쓴다.** Git Bash 는 MSYS 경로 변환이
  값을 바꿔치기한다(`/data/db` → `D:\git\data\db`, 실제 사고 이력)
- 허브 DB 는 Azure Files 위 단일 writer 다. 파일이 SMB 로 잠겨 있어 **서버측 복사가
  거절된다**(`FileLockConflict`). 사본이 필요하면 공유 스냅샷을 뜨고 거기서 받는다:
  `az storage share snapshot` → `az storage file download --snapshot <ts>`
- ⚠ **FTS 색인은 시간이 지나며 존재하는 행의 문서를 잃는다**(ladybug 0.18.0). 그 행을
  지우면 `FTS index ... is inconsistent: document for node offset N is missing during
  delete` 가 난다. 2026-08-09 에 세 번째로 났고, 그날 쓰기 경로에 자기복구를 달았다
  (`api.docstore._with_fts_repair` → `rebuild_fts_index`, 5.8초). **그러니 이 오류를
  보면 이제 저절로 낫는다** — 파일 수술이 아니라 로그를 확인할 것.
  기각된 가설들(전부 재현으로): "색인이 정적 스냅샷이라 새 행이 안 들어간다"(들어간다)
  · "재빌드를 안 해서 썩는다"(단순 사이클은 정상) · "WAL 을 버리는 복구가 원인"(무죄).
  재현 절차는 그래프의 `vinc.ops.fts-index-drift` 에 있다
- FTS 손상이 **기동을 막는** 경우의 복구(08-04·08-07 에 쓴 길): `db` 를 다른 이름으로
  복사(그 이름엔 `.wal` 이 없어 재생을 건너뛴다) 후 `VINC_DATA_DIR` 전환
- ⚠ **하드다운 3회의 공통 원인은 SMB 위에서 임베디드 DB 를 직접 read-write 하는 것**
  이다(FTS 손상 2 + 재생 불가 WAL 1). 이 리포는 같은 교훈을 OAuth SQLite 에서 이미
  배워 "읽고 쓰는 곳과 남기는 곳을 가른다"를 `vinc_mcp/store.py` 에 적어 놓고, 정작
  그래프 DB 에는 안 썼다. 2026-08-09 에 `store/persistence.py` 로 같은 처방을 붙였다
  (`VINC_STORE_LOCAL=on` 으로 켠다 — 데이터 이관 선행 필요)
- ⚠ **"엔진은 close 때만 체크포인트한다"는 틀린 말이다.** ladybug 0.18.0 은
  `autoCheckpoint=true, checkpointThreshold=16MB` 가 기본이라 close 없이도 스스로
  한다. 2026-08-08 에 코드 주석 한 줄을 믿고 이걸 근본원인으로 오진했다. 임계값은
  `VINC_CHECKPOINT_THRESHOLD_MB`(기본 4MB)로 조절하며 **그 값이 곧 최대 손실 창**이다
- SMB 고아 핸들이 파일을 물고 있으면 **레플리카를 0으로 내려도 안 풀리고 만료 시간도
  없다.** `az storage share close-handle --handle-id "*" [--recursive]` 로 강제 종료할 것
