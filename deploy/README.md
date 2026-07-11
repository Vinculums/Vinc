# deploy/ — 원격 프로파일 배포 템플릿 (P5 선행 D)

[[vinc.c.p5-remote-rollout]] 서버 트랙의 적용물. **도메인만 치환하면 되는
템플릿**이다 — 값은 전부 env 로 주입하고, 시크릿은 어떤 파일도 레포에 두지
않는다(D36).

## 적용 순서 (서버 트랙과 1:1)

1. **하드닝**: `sudo bash deploy/harden.sh`
   (22/80/443 만 개방 · SSH 키 전용 · fail2ban · 자동 패치 · vinc 계정)
2. **DNS·TLS**: 레지스트라에서 A 레코드 2건(루트, `mcp.`) →
   `sudo apt install caddy` → 이 폴더의 Caddyfile 을 `/etc/caddy/Caddyfile` 로
   복사, `VINC_DOMAIN=vincs.app` 을 `/etc/caddy/caddy.env` 등으로 주입(확정 도메인, 2026-07-11) →
   `systemctl reload caddy`. 인증서는 자동(Let's Encrypt).
3. **사이드카**: P4 릴리스의 리눅스 `vinc-sidecar` 바이너리를 `/opt/vinc/` 에
   배치 → `/etc/vinc/sidecar.env` 에 `VINC_SIDECAR_TOKEN=<random>` (권한 600) →
   `vinc-sidecar.service` 설치·기동. 데이터 홈은 `~vinc/.vinc` 자동 생성(D43).
4. **MCP 어댑터**: `/etc/vinc/mcp.env` 에 `VINC_SIDECAR_TOKEN`(동일 값)과
   `VINC_OAUTH_ADMIN_KEY=<random>` → `vinc-mcp.service` 설치·기동 →
   claude.ai 커넥터에 `https://mcp.<도메인>/mcp` 등록. 인가 화면 대신
   `/authorize?...&key=<관리 키>` 1회 승인 모델이다(단일 소유자 전제).
5. **홈페이지**: `rsync -av site/ /srv/www/`
6. **백업**: `backup.cron` 을 `/etc/cron.d/vinc-backup` 으로 복사.
   복원 리허설(스냅샷 → 새 디렉토리 → /health·/domains 확인)까지가 완료선.

## 게이트 체크리스트

- [ ] 외부 포트 스캔: 22/80/443 외 응답 없음 (8787·8790 은 루프백 전용)
- [ ] `https://<도메인>` · `https://mcp.<도메인>` 유효 인증서
- [ ] VM 내부 `curl 127.0.0.1:8787/health` OK, 외부에서는 도달 불가
- [ ] 무토큰 `POST https://mcp.<도메인>/mcp` → 401
- [ ] claude.ai 커넥터에서 vinc_graph 실호출 왕복
- [ ] 복원 리허설 1회 PASS
