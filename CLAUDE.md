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

### 쓰기 전에

`vinc_conventions` 를 세션의 첫 쓰기 **전에** 한 번 부른다 — 이 소유자의 노드·관계
관례와 이미 있는 도메인을 실어 오므로, 새로 만드는 것이 기존과 어긋나지 않는다.
그리고 **read-before-write**: `vinc_search` 로 기존 id 를 찾아 재사용한다. 새 id 를
지어내면 같은 것이 두 벌 생긴다.

### 급소 (실측으로 물린 것들)

- **오픈 타입 노드는 `props`·`domain` 이 저장되지 않는다.** `record|concept|domain|
  bridge|episode` 다섯만 전용 테이블로 가고 `props{}` 가 JSON 열로 남는다. 그 밖의
  타입은 Entity 캐리어로 가는데 열이 `type/title/tags` 뿐이라 **값이 조용히 사라진다**
  (경고는 뜬다). 값을 실어야 하면 `concept` 이나 `record` 를 쓸 것.
- **엣지는 같은 페이로드의 `nodes` 안에서만 해석된다.** 배치를 나누면 배치 간 엣지가
  조용히 skip 된다. 양 끝 노드를 엣지와 같은 호출에 동봉할 것.
- **`title` 에는 가드가 없다.** 부분 재전송이 제목을 덮어쓴다(`domain`·`tags`·`props`
  는 미지정 시 보존). 스텁으로 덮어쓰지 말 것.
- 화면에 노출되는 텍스트에 **em-dash(`—`) 금지**. 대체는 `·`·괄호·콜론.
  (이 규칙은 UI 텍스트에만 해당한다. 커밋 메시지·이 문서는 무관하다.)

## 2. 리포별 지위

| 리포 | 지위 | 세션에서 할 것 |
|---|---|---|
| `Vinc` | 제품 코드 (앱·허브·MCP·사이드카) | 코드는 여기. 설계 산문은 그래프 |
| `Vinc-homepage` | 사이트 + 계정 백엔드 | 코드는 여기 |
| ~~`Vinc-design`~~ | **폐기(2026-08-08)** — `Vinc/design-hub/` 로 이전 | §3 참조 |
| `Vinc-docs` | **구작업. 이행 중 (그래프로 이관)** | §4 참조 |

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

## 4. Vinc-docs — 폐기 대기 (그래프가 이미 정본이다)

**P5 컷오버는 2026-08-08 에 끝났다**(`_migration/STATE.md` 가 정본). 그래프가 신
체계다 — 영어 문서 54건 · 노드 192건 제자리 재작성 · 구 문서 54건 삭제 · 되살아나
있던 학습자료 53건 재삭제 · 잔여 doc-concept 39건 제거. **구 도메인 0**, 남은 것은
신 7종(design·engineering·infra·governance·security·product·ai)뿐이다.

- **`Vinc-docs/` 의 마크다운은 이제 스냅샷이다.** 지식을 찾을 때 여기를 읽지 마라 —
  낡았다. `vinc_search` 로 그래프를 봐라
- **새 문서를 여기 만들지 않는다.** 새 지식은 그래프로 간다
- `_migration/` 은 이행 원장·재작성본·실행 스크립트가 사는 곳이다. 리포를 지울 때
  이 디렉터리만 `Vinc/` 로 옮겨 이력으로 남긴다
- 남은 것: **엣지 재배선 판정 223건**(발주자 결정 대기). 그것까지 끝나면 리포 삭제

## 5. 테스트와 배포

**Vinc 테스트는 이 한 줄로만 제대로 돈다:**

```bash
env -u PYTHONHOME -u PYTHONPATH ./.venv/Scripts/python.exe -m pytest tests
```

전역 `PYTHONHOME`/`PYTHONPATH` 가 시스템 파이썬을 가리켜서 그냥 부르면 venv 조차 깨진다.
**정상은 883 passed · 4 skipped**(2026-08-08 기준. 늘어나는 값이니 중요한 건 skip=4).
skip 이 135 면 인터프리터를 잘못 잡은 것이고, 그
상태에서 "main 과 똑같이 실패한다"는 대조는 **무효다**(실제로 그걸 믿고 머지했다가
CI 에서 걸렸다).

배포 정책 정본은 `Vinc-docs/vinc/DEPLOYMENT.md`. 짚어 둘 것:

- `main` push 가 곧 배포다(`hub-deploy`·`site-deploy`·`release`). 브랜치에서 검증하고 머지
- **허브 env 를 `az` 로 바꿀 때는 PowerShell 을 쓴다.** Git Bash 는 MSYS 경로 변환이
  값을 바꿔치기한다(`/data/db` → `D:\git\data\db`, 실제 사고 이력)
- 허브 DB 는 Azure Files 위 단일 writer 다. FTS 인덱스가 손상되면 **재시작하는 순간**
  기동 실패한다(관용 옵션으로 안 막힌다). 복구는 `db` 를 다른 이름으로 서버측 복사
  (그 이름엔 `.wal` 이 없어 재생을 건너뛴다) 후 `VINC_DATA_DIR` 전환
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
