# starter-packs/ — 공식 스타터팩의 원천

커뮤니티 갤러리(`vincs.io/community`)에 **공식팩**으로 올라가는 4개 팩의 원고·파이프라인·
산출물. 회원 게시물과 같은 테이블(`community_packs`)에 `is_official=1` 로 들어간다.

```
work/{art,economics,math,physics}/   원고(마크다운) — 팩의 실제 내용
tools/                                생성·검증 파이프라인
official-packs.json                   시딩 매니페스트(제목·설명·파일명)
vinc-pack-{도메인}.vincpack            산출물(zip 컨테이너, D55)
```

> **2026-07-31 리포 편입.** 이 폴더는 그 전까지 **어떤 리포에도 속하지 않았다** —
> 워크스페이스 루트에 로컬로만 있었다. 개발 기기를 잃으면 갤러리의 DB 행은 남아 서비스는
> 살지만 **팩을 고치거나 다시 만들 길이 사라지는** 상태였다. 편입하면서 개발 기기
> 절대경로 하드코딩(`export_vinc_pack.py` 의 `sys.path`)도 리포 상대경로로 고쳤다 —
> 예전엔 그 기기 밖에서는 아예 돌지 않았다.
>
> 제외한 것: `*.bak`(D55 전 JSON 백업 3.9MB — zip 이 정본이고 재생성 가능) ·
> `__pycache__`. `.dockerignore` 에도 `starter-packs` 를 넣었다 — 허브 이미지는 팩을
> 읽지 않으므로 빌드 컨텍스트를 3.7MB 불릴 이유가 없다.

## agents/ — 기본·분야 Agent 팩 (2026-09-05)

`agents/*.json` 은 역할(`concept:agent-*`, 주제 `vinc/agent`)만 담은 팩 다섯이다. 원고 폴더
(`work/`)가 없고 **JSON 이 원천**이다 — 갤러리가 JSON 을 저장하고 팩이 작아서 zip 을 거칠
이유가 없다. `packfile` 은 첫 바이트로 두 형식을 가르므로 검증·시딩 도구가 그대로 읽는다.

| 파일 | 갤러리 제목 | 역할 |
|---|---|---|
| `default-agents.json` | Default Agents | steward · researcher · curator · reviewer. **모든 새 스페이스에 자동 적재**된다(계정 백엔드가 첫 로그인·팀 생성 직후 이 공식팩 행을 읽어 `ingest-pack` 으로 넣는다) |
| `software-agents.json` | Software Agents | developer · gate-runner · reviewer-checklist · reviewer-judgement · security |
| `design-agents.json` | Design Agents | designer · design-reviewer · design-researcher |
| `infra-ops-agents.json` | Infra and Ops Agents | infra · ops · release |
| `copy-marketing-agents.json` | Copy and Marketing Agents | copy-writer · copy-owner · marketing |

규율: 영어만 · `reads`·`gates`·`paths` 는 비워 둔다(사용자의 것) · 오케스트레이터 역할은
어디에도 없다(그래프는 스케줄러가 아니다) · 팩 간 엣지는 두지 않는다(같은 페이로드 밖의
엣지는 조용히 skip 된다) · `vinc/agent` 는 무료 주제 상한에서 빠진다(`engines/domain_hierarchy.
RESERVED_TOPICS`). `tests/design/test_agent_packs.py` 가 이 표와 규율을 고정한다.

⚠ 기본 팩의 자동 적재는 **갤러리 행이 있을 때만** 된다. 시딩이 수동인 것은 여기도 같아서,
DB 를 새로 만들면 새 스페이스는 빈 로스터로 태어난다(계정 로그에 `default pack not seeded`).

## 팩 재생성

```bash
python tools/export_vinc_pack.py            # work/ → *.vincpack (zip)
python tools/validate_pack.py               # 형식·엣지 정합 검증
```

## 갤러리에 시딩 (운영자 수동)

⚠ **이 단계는 마이그레이션이 아니다.** `010_official_packs.sql` 은 스키마만 바꾸고
행은 사람이 넣는다. 그래서 **DB 를 새로 만들면 공식팩만 조용히 사라진다** — Azure
이관에서 실제로 그랬고(2026-07-30 `community_packs` 0행) 갤러리가 빈 채 서빙됐다.

```bash
# 1) zip → JSON 변환 + 매니페스트 (커뮤니티 저장 경로가 JSON 전용이라 필요)
python tools/make_seed_manifest.py

# 2) 시딩 (Vinc-homepage/account 에서, DB env 주입 후)
npx tsx scripts/seed-official-packs.ts <Vinc>/starter-packs/seed-json/official-packs.json
```

**왜 JSON 으로 넣나.** `community_packs.pack_json` 이 `MEDIUMTEXT` 이고 다운로드가
`Content-Type: application/json` 을 낸다. `api/packfile.py` 의 계약이 "**읽기는 두 형식
모두** 받고 **쓰기만** zip" 이므로 갤러리에 JSON 을 넣는 것이 정상이고 앱은 하위호환으로
읽는다. `documents`(원문)는 그대로 실려 자기완결 공유(D45)가 갤러리에서도 유지된다.

**함정 셋**:
- 크기 상한 **2MB**(`routes/community.ts`). 실측: physics 1.57MB · art 0.91 · math 0.79 ·
  economics 0.63 — 여유가 크지 않다. `make_seed_manifest.py` 가 초과를 먼저 알려준다.
- 시딩 스크립트가 `env.ts` 를 import 해 **`SERVER_PEPPER`·`SESSION_SECRET`·`CSRF_SECRET`
  까지 요구**한다. 셋 다 시딩 경로에서 안 쓰이므로 실제 시크릿 대신 **더미 32B base64**
  로 통과시킬 것(실제 시크릿을 끌어올 이유가 없다).
- Azure MySQL 은 방화벽이 **Azure 서비스만** 허용한다. 로컬에서 돌리려면 임시 규칙을
  넣고 **끝나면 반드시 지울 것**.

제목 기준 멱등 upsert 라 재실행은 안전하다.

정본 문서: `Vinc-docs/vinc/WEB-vincs-app.md` §8.2
