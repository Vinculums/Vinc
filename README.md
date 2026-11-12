# Vinc

Vinc의 데스크톱·웹 앱, 그래프 스토어, API, MCP 서버(허브), 검색 코어를 담은 코드 리포입니다.

이 README는 제품 소개가 아니라 **같이 작업하는 분이 확인해야 할 것**을 적습니다. 제품 설명은 [vincs.io/docs](https://vincs.io/docs/)에 있습니다.

## 1. 처음 오셨다면

1. 초대를 수락합니다. GitHub는 Vinculums 조직의 자기 팀, Vinc는 VincTeam 공간(편집자)입니다.
2. 아래 3절에서 자기 팀의 온보딩·매뉴얼을 찾아 먼저 읽습니다.
3. 5절대로 개발 환경을 만들고 자기 팀 테스트를 돌려 봅니다.
4. 작업은 브랜치에서 하고 PR로 올립니다. 4절의 규칙을 지켜 주세요.

## 2. 리포 지도

| 리포 | 무엇 | 공개 |
|---|---|---|
| `Vinculums/Vinc` (여기) | 앱(`app/`), 그래프 스토어(`store/`), API(`api/`), MCP 서버(`vinc_mcp/`), 검색 코어(`vinc_rag/`), 평가(`rag_eval/`), 디자인 토큰 도구(`scripts/design/`), 배포·CI | 비공개 |
| `Vinculums/Vinc-homepage` | vincs.io 사이트(`site/`)와 계정 백엔드(`account/`) | 비공개 |
| `Vinculums/Vincs-app` | 데스크톱 설치 파일과 자동 업데이트 정보를 내보내는 곳. 릴리스 워크플로가 올립니다 | 공개 |
| `Vinculums/vinc-rag-assets` | RAG 평가용 큰 파일(평가 스토어, 인코더 모델) | 비공개, `rag` 팀 |

## 3. 영역별 안내

| 팀 (GitHub) | 맡는 곳 | 먼저 읽을 문서 | 테스트 |
|---|---|---|---|
| RAG `@Vinculums/rag` | `vinc_rag/`, `rag_eval/`, 인코더·리랭커·임베딩 워커 | 온보딩 `vinc.rag.onboarding-2026-09-20`(한국어) → 매뉴얼 `vinc.rag.manual` | `tests/rag` |
| Core `@Vinculums/core` | `store/`, `engines/`, `api/`, `vinc_mcp/`, `scripts/`, 그리고 Vinc-homepage의 `account/` | `vinc.manual.core` | `tests/core`, `tests/mcp` |
| App `@Vinculums/app` | `app/` (Vite + Tauri, 데스크톱과 웹 프론트엔드) | `vinc.manual.app` | `tests/app`, `npm run typecheck` |
| Design·Content `@Vinculums/design-content` | `scripts/design/`(토큰 값은 그래프), `starter-packs/`, `emails/`, 이 README, 그리고 Vinc-homepage의 `site/` | `vinc.manual.design` | `tests/design` |
| Platform `@Vinculums/platform` | `.github/`, `infra/`, `packaging/`, `scripts/gate_*`, `agents/` | `vinc.manual.platform` | `tests/platform` |

- 정확한 경로 목록은 `agents/teams.json`입니다. `.github/CODEOWNERS`는 거기서 만들어지므로 손으로 고치지 않습니다(`scripts/agents_sync.py --gen`).
- 두 팀이 같이 보는 파일이 있습니다. 예를 들어 `app/src/copy.ts`는 App과 Design·Content가, `api/auth.py`와 `vinc_mcp/oauth.py`는 Core와 Platform이 같이 리뷰합니다(`teams.json`의 `shared`).
- 두 팀에 걸친 변경이면 두 매뉴얼을 모두 읽습니다.

### 문서 여는 법

VincTeam 공간의 문서는 웹에서 로그인한 뒤 아래 주소로 엽니다. 끝의 `<문서 id>`만 바꾸면 됩니다. 로그인을 먼저 해야 합니다. 로그인하지 않고 열면 로그인 화면으로 넘어가고 원래 문서로 돌아오지 않습니다.

```
https://vincs.io/app/?space=01a0bc92-ce1d-7867-b970-72cffed2dd89#view/doc/<문서 id>
```

AI 도구에 Vinc MCP를 연결해 쓰신다면 "VincTeam 공간의 `<문서 id>` 문서를 읽어 줘"라고 요청해도 됩니다.

> 지금 VincTeam 공간에 있는 것은 RAG 문서뿐입니다. 나머지 팀 매뉴얼(`vinc.manual.core`, `vinc.manual.app`, `vinc.manual.design`, `vinc.manual.platform`)은 아직 운영자 개인 그래프에만 있어서 팀원이 열 수 없습니다. 필요하면 운영자에게 요청해 주세요.

## 4. 작업 규칙

1. **`main`에 올라가는 순간 배포됩니다.** 허브(`hub-deploy`)와 데스크톱 릴리스(`release`)가 `main`을 따라갑니다. 브랜치에서 작업하고 PR로 올립니다. 머지는 운영자가 한 번에 하나씩 합니다.
2. **PR 제목은 영어로 `type(scope): subject` 형식입니다.**
   - 타입은 소문자 중 하나입니다: `feat` `fix` `ci` `chore` `design` `docs` `release` `test` `infra` `agents` `refactor` `security` `perf` `build` `style` `revert`
   - subject는 소문자로 시작하고 마침표로 끝내지 않습니다. 대시(`—`, `–`)는 쓰지 않습니다.
   - 본문은 한국어로 써도 됩니다.
   - 예: `fix(share): unchecked documents stay behind`
   - CI가 `scripts/gate_pr_title.py`로 검사합니다. 스쿼시 머지라 PR 제목이 그대로 `main`의 커밋 제목이 됩니다.
3. **GitHub 워크플로를 손으로 실행하지 않습니다.** 배포, 운영 데이터 수리, Azure 비용이 드는 프로브 같은 것들이라 저장소 변수 `PROD_OPERATORS`에 있는 계정만 실행됩니다. 필요하면 운영자에게 요청해 주세요.
4. **설계 이유와 측정 기록은 파일이 아니라 그래프에 씁니다.** 코드 주석에는 "무엇을 하는가, 무엇을 깨면 안 되는가"만 적고, 이유는 `근거: [[문서 id §절]]`처럼 그래프 문서를 가리킵니다. CI가 `scripts/gate_graph_refs.py`로 그 문서가 실제로 있는지 확인합니다. 인용을 새로 넣었다면 `scripts/refresh_graph_refs.py`로 `scripts/graph-refs.json`을 갱신해 같은 커밋에 넣습니다.
5. **금지어 게이트가 있습니다.** 쓰면 안 되는 단어 목록은 `scripts/gate_forbidden.py`에 있고, CI에서 0건이어야 통과합니다.
6. **화면에 보이는 문구에는 em-dash(`—`)를 쓰지 않습니다.** 대신 가운뎃점·괄호·콜론을 씁니다. 나머지 문구 규칙은 `vinc.manual.app` 3절에 있습니다.
7. **허브 경로를 바꾸는 머지마다 Vinc MCP가 2분쯤 끊깁니다.** 허브는 그래프 스토어에 쓰는 프로세스가 하나뿐이어야 해서, 새 버전을 띄우기 전에 옛 버전을 먼저 내립니다. 그 사이에 잰 실패를 고장으로 읽지 마세요. 어떤 경로가 허브 배포를 부르는지는 `scripts/hub_deploy_pick.py`의 `HUB_PATHS`에 있습니다.

## 5. 개발 환경과 자주 쓰는 명령

### 파이썬 (core, rag, mcp, platform, design 테스트)

```bash
python -m venv .venv
.venv/Scripts/activate            # macOS·Linux 는 source .venv/bin/activate
pip install ladybug==0.20.4 argon2-cffi pyyaml pytest pypdf
python -c "import ladybug as l; c=l.Connection(l.Database('_t')); [c.execute(f'INSTALL {e}') for e in ('vector','fts','algo')]"
python -m pytest tests            # 전체
python -m pytest tests/rag        # 팀 폴더만
```

- 첫 `pip` 줄은 CI가 쓰는 것과 같습니다.
- 마지막 `python -c` 줄은 그래프 DB 확장 셋을 한 번 내려받습니다. 만들어진 `_t`는 지워도 됩니다.
- Linux에서는 확장을 내려받는 대신 `VINC_LBUG_EXT_DIR=packaging/lbug_extensions`를 주어도 됩니다(CI와 같은 방식).
- skip이 평소보다 크게 늘었다면 그래프 DB 드라이버(`ladybug`)가 안 깔린 것입니다. 그 상태의 "통과"는 아무것도 안 돈 것입니다.
- 운영자의 Windows 기계처럼 전역 `PYTHONHOME`이 잡힌 곳에서는 `CLAUDE.md` 5절의 명령을 씁니다.

### 앱

```bash
cd app
npm ci
npm run typecheck
npm run dev                       # 브라우저에서 화면 확인
```

### 게이트를 손으로 돌려 보기

```bash
python -X utf8 scripts/gates.py                    # origin/main 대비 바뀐 파일이 닿는 게이트 전부
python -X utf8 scripts/gates.py --all              # 전부. --list 는 목록만 보여 줍니다
python -X utf8 scripts/gates.py forbidden graph-refs
python -X utf8 scripts/gate_pr_title.py "fix(scope): subject"
```

게이트 목록은 `scripts/gates.py` 한 곳에 있고, CI(`ci.yml` 의 `changes`·`core` 잡)도 같은 목록을 돕니다.

## 6. 더 볼 곳

- `CLAUDE.md`: AI 에이전트가 이 리포에서 일할 때 따르는 규칙입니다. 사람이 읽어도 함정 목록으로 쓸모가 있습니다.
- `CHANGELOG.md`: 개발자용 릴리스 기록입니다.
- `agents/teams.json`: 팀과 경로의 정본입니다.
- Linear: 팀 `Vincs`에서 이슈를 관리합니다. 등록 규칙(Area 라벨, 프로젝트를 붙이는 기준, 보관)은 그래프 [[vinc.canon.linear-workflow]] 이고, Linear 안에서는 팀 문서 "How Vinc uses Linear" 가 그 요약입니다.
