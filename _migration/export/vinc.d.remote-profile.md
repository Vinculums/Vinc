<!-- vinc-export
doc_id: vinc.d.remote-profile
title: 원격 MCP 프로파일 — 클라우드 VM 배포 (D36)
origin: Vinc-docs/vinc/D/vinc.d.remote-profile.md@5090690
chunks: 3
verified: true
content_hash: f52ab4175512c0826488936cbdd85b1cc446fbc4c458193bb86f3c45da9c1c1e
updated_at: 2026-08-01T16:20:38.603Z
-->

---
doc_id: vinc.d.remote-profile
layer: D
domain: remote-profile
title: 원격 MCP 프로파일 — 클라우드 VM 배포·커넥터·공식 홈페이지 동거 (D36)
status: draft
authority: SSoT
version: v0.3
owner: vinc
last_updated: 2026-07-29
inherits_from: [vinc.b.architecture, vinc.d.desktop-shell]
tags: [vinc, D, remote, mcp, connector, deployment, dec-candidate]
---

# 원격 MCP 프로파일 — 클라우드 VM 배포 (D36)

> 발주자 결정(2026-07-10): 자기 소유 클라우드 VM 에서 Vinc 를 구동해
> claude.ai **커넥터**로 등록하고, 같은 서버에서 **공식 홈페이지**를 함께
> 운영한다. 로컬 기본 프로파일(D7·F4)은 불변 — 본 문서는 **옵트인 별도
> 프로파일**이다.

## 1. 두 프로파일의 관계

| | 로컬 기본(현행) | 원격(D36, 옵트인) |
|---|---|---|
| 바인딩 | 127.0.0.1 + 세션 토큰(F4) | VM 내부 루프백 유지 — 공개 노출은 앞단이 전담 |
| MCP | stdio(로컬 호스트: Claude Code·Desktop·Cursor·Codex CLI·Kimi CLI…) | Streamable HTTP + OAuth → 원격 커넥터(claude.ai · ChatGPT 개발자 모드) + 전 기기 |
| 데이터 위치 | 사용자 기계 | 사용자 소유 VM(주권 경계 1칸 이동 — SaaS 아님) |
| 스토어 관계 | — | **독립**이 1단계. 이관은 `.vincpack`(D19) 재사용 |

## 2. 아키텍처

```
[원격 커넥터(claude.ai · ChatGPT) / 로컬 호스트(Claude Desktop·Code · Cursor · Codex CLI)]
        │ HTTPS(OAuth 2.0)
        ▼
도메인 DNS ─→ Caddy(자동 TLS·리버스 프록시) ─┬→ /        홈페이지(정적)
                                             └→ mcp.*    MCP 어댑터(Streamable HTTP)
                                                   │ 루프백
                                                   ▼
                                    Vinc 사이드카(127.0.0.1:8787 — 기존 그대로)
                                                   ▼
                          LadybugDB 스토어(단일 writer R4) + R9 야간 스냅샷
```

원칙: **사이드카는 무변경** — F4(루프백)를 VM 안에서 그대로 유지하고, 공개
표면은 Caddy+어댑터가 전담한다. 기존 코드·verify 가 전부 재사용된다.

## 3. 서버 사양 (권장)

| 항목 | 권장 | 근거 |
|---|---|---|
| vCPU | 2 | 사이드카 CPU 경량(실측: organic 레이아웃 최악 167ms/93노드) |
| RAM | **4GB** | 실 임베딩 모델(bge-base-en) 상주 1~2GB 포함. BM25 만이면 2GB 가능 |
| 디스크 | 40~60GB NVMe | 스토어는 수십 MB — 큰 것은 모델(~0.5GB)·스냅샷 누적 |
| OS | Ubuntu LTS 24.04 | Caddy·systemd·PyInstaller 표준 경로 |
| 리전 | 서울 | 사용자 접속 지연 기준 |
| 비용 | 월 1~3만원 | Hetzner €5 / Vultr·DO 서울 $12~24 / 국내 CSP 상단 |

## 4. 도메인·TLS

- 커넥터·OAuth 등록이 URL 에 묶이므로 **자기 소유 도메인이 앵커**(연 1~2만원).
  서브도메인 분할: `mcp.` 커넥터 / 루트 홈페이지 / (후속) `updates.` Tauri 피드.
- 파일럿 무료 대안: DuckDNS(+LE), Tailscale Funnel(`*.ts.net`) — 스택 검증 후
  도메인 구매 시 Caddy 한 줄 + 커넥터 재등록 1회로 이전.
- TLS 는 Caddy 자동(Let's Encrypt) — 수동 갱신 없음.

## 5. 홈페이지 동거 원칙

1. **정적 전용** — 동적 CMS 금지(지식 그래프가 사는 서버의 최대 공격 표면이
   되기 때문). Astro/Hugo 빌드 산출물만 서빙. 디자인은 제품의 Ink & Paper
   토큰·Notation 아이콘을 재사용해 제품과 같은 얼굴로.
2. **격리** — 방화벽 80/443 + SSH(키 인증)만. 사이드카·어댑터 포트 외부 비노출.

Caddyfile 핵심:

```
example.com {
    root * /srv/www
    file_server
}
mcp.example.com {
    reverse_proxy 127.0.0.1:8790   # MCP 어댑터(Streamable HTTP)
}
```

## 6. 운영 런북

- systemd 유닛 2: `vinc-sidecar.service`(`api.cli serve --port 8787`),
  `vinc-mcp.service`(어댑터) — `Restart=on-failure`, 사이드카는 어댑터의 After.
- 백업: R9 `backup` 을 cron 야간 실행 → 오브젝트 스토리지 업로드(이탈 백업).
  복원 경로는 스냅샷 복원 왕복 테스트(기구현)가 보증.
- unattended-upgrades(보안 패치 자동) + fail2ban.

## 7. 구현 순서 (코드 델타 — 본 문서는 계획 정본)

> 실행 전개는 [[vinc.c.p5-remote-rollout]] — 도메인·VM 확보 이후의
> 단계·게이트·선행(서버 불요) 트랙 분리를 그쪽이 장부로 관리한다.

1. **MCP 어댑터 공통 코어**: 사이드카 계약(D11 도구군: ingest fragment·graph·
   search·recall·suggest-ties…)을 MCP 도구로 노출. 전송 2종 —
   stdio(로컬 등록용)·Streamable HTTP(원격용) — 는 같은 코어를 감싼다.
2. **로컬 자동 등록** `vinc mcp install --client code|desktop|cursor|codex`:
   등록 관례는 **2종뿐**이고 호스트별 차이는 `vinc_mcp/install.py` 하나가 흡수한다.
   - *CLI 위임* — 호스트 공식 명령 호출: `claude mcp add`(user 스코프 `~/.claude.json`) ·
     `codex mcp add`(`~/.codex/config.toml`). CLI 부재 시 붙여넣을 명령을 안내로 반환.
   - *JSON 병합* — `mcpServers.vinc` 키만 **백업 후 병합**(나머지 보존):
     Claude Desktop(OS 3종 경로 `%APPDATA%\Claude\` · `~/Library/Application Support/Claude/`
     · `~/.config/Claude/`의 `claude_desktop_config.json`) · Cursor(`~/.cursor/mcp.json`).
   레지스트리 밖 호스트(Kimi CLI `~/.kimi/mcp.json` · VS Code · Zed)는 `vinc mcp manual`
   이 내는 설정 조각으로 수동 등록 — 자동화만 없을 뿐 서버 동작은 동일하다.
   원격 커넥터는 URL 등록(수동 1회) — 로컬 루프백은 구조적으로 원격 호스트에 노출 불가.
3. **OAuth 게이트**(원격 전용): MCP 스펙 범위 최소 구현 또는 경량 IdP 연동.
   **벤더 중립** — RFC 9728/8414/7591 + PKCE 만 구현하므로 claude.ai 커넥터와
   ChatGPT 커스텀 커넥터(개발자 모드)가 같은 게이트를 쓴다([[vinc.mcp#§8]]).
4. **배포 스크립트**: Caddyfile·systemd·cron 템플릿(§5–6 구체화).

## 8. DEC

| # | 결정 | 상태 |
|---|---|---|
| D36 | 원격 MCP 프로파일(클라우드 VM, 옵트인) — 본 문서 §1–7. 로컬 기본(D7·F4) 불변, 사이드카 무변경, 홈페이지 정적 동거 | 채택 — 발주자 결정 2026-07-10 |

## 부록 Z. 미분류 원문 사실
(없음)
