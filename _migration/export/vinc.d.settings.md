<!-- vinc-export
doc_id: vinc.d.settings
title: Settings (팝오버 — 표면 아님)
origin: Vinc-docs/vinc/D/vinc.d.settings.md@5090690
chunks: 1
verified: true
content_hash: 10f9e2dac564f476202be5f660718628cda0f3224e27b5c0f189d8d4cc17c8d3
updated_at: 2026-08-01T16:20:39.439Z
-->

---
doc_id: vinc.d.settings
layer: D
domain: settings
title: Vinc Settings 팝오버 — 옵트인 잠금·MCP 관찰·데이터·작업공간·테마·언어·고급
status: draft
authority: SSoT
version: v0.2
owner: vinc
last_updated: 2026-07-16
inherits_from: [vinc.d.index]
tags: [vinc, D, settings, lock, mcp, theme, language, workspace]
---

# Settings (팝오버 — 표면 아님)

> 전역 크롬의 팝오버. **표면 트리에 들어가지 않는다**(D15). 설정 과잉 금지가 본 팝오버의
> 설계 원칙이며([[vinc.d.ux-research]] §2), 그 원칙은 **항목 수 고정이 아니라 "개발자 설정을
> 전면에 두지 않는다"** 로 지킨다 — 저수준 항목은 `Advanced` 접힘으로 강등한다(D38).
>
> **v0.2(2026-07-16) 개정:** v0.1 의 "항목은 **3개로 고정**" 은 as-built 와 어긋난다. D38
> (Advanced 접힘 + Workspace 개명) · D41(테마 세그) · D42(언어 세그)가 항목을 늘렸다.
> 코드 실측 `app/src/surfaces/settings.ts` 기준으로 정정.
> ⚠ 해당 파일 상단 주석의 `exactly three items` 서술도 낡았다(코드↔문서 순환 인용).

## 1. 항목

카피 정본은 코드(`copy.ts` `copy.settings` + `ui.settingsUi`) — 전수 목록 [[vinc.a.ui-copy#§9]].

| 항목 | 내용 | 근거 |
|---|---|---|
| **Lock** | D7 — 기본 무암호. 옵트인 passphrase 잠금(argon2id, 로컬) | D7 |
| **MCP** | D11 관찰 패널 — 연결 상태 + 도구 호출 로그. `Connect Claude Desktop` / `Connect Claude Code` 등록 버튼(`vinc mcp install` 경로) | D11 |
| **Data** | 데이터 디렉토리 표시(기본 `~/.vinc`, D43) · 백업(`/backup` 잡, `Back up now`) | D43 |
| **Workspace** | 지금 보고 있는 공간(owner 스코프). 샘플 팩 체험 후 본 공간 복귀 동선 | D38(개명) · D30(샘플 팩) |
| **Theme** | 세그먼티드 컨트롤 `dark` / `light` / `auto` — 기본 다크 | D38(다크 기본) · D41(세그 문법) |
| **Language** | 세그먼티드 컨트롤 `English` / `한국어` — 무설정 시 OS 로케일 추종 | D42 |
| **Advanced** | **접힘**(`details`) — 엔진 주소 · 연결 시크릿(앱이 자동 기입, 직접 운영 시에만 변경) | D38 |

## 2. 규약
- **개발자 정보 은닉(D38):** 엔진 주소·연결 시크릿은 노출하되 `Advanced` 접힘으로 강등한다.
  임베딩 모델·백엔드 드라이버 등 그 아래 계층은 **여전히 UI 미노출** — env/config 단일 출처
  (`store/config.py`). 설정 화면의 비대화는 항목 수가 아니라 **계층 강등**으로 막는다.
- 잠금 활성 시에만 앱 시작이 잠금 해제 다이얼로그를 거친다(별도 화면 아님).
- 테마·언어는 즉시 반영(언어 변경은 전 표면 재렌더 — 덱 Proxy, D42).

## 3. 참조
- 데이터 계약: `/health`·`/backup`·잠금은 로컬 프로필(사이드카 auth).
- 구현: `app/src/surfaces/settings.ts`.
- 컴포넌트: `ListRow`·`Button`·`Badge`·세그먼티드 컨트롤(`.seg`, D41).

## 부록 Z. 미분류 원문 사실
(없음)
