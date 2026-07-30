> # ⛔ 이 폴더는 삭제된 인프라를 설명한다 (2026-07-30)
>
> 가비아 VM(`1.201.120.125`)·Caddy·systemd·관리형 MySQL 이 전부 삭제됐다. 아래 절차는
> **적용될 서버가 없다.** 지금 라이브는 Azure 이고 배포는 GitHub Actions(OIDC)가 한다 —
> 손으로 올리는 경로 자체가 없다:
>
> | 표면 | 워크플로 |
> |---|---|
> | 허브 | `Vinc/.github/workflows/hub-deploy.yml` |
> | 계정 | `Vinc-homepage/.github/workflows/account-deploy.yml` |
> | 사이트·웹앱 | `Vinc-homepage/.github/workflows/site-deploy.yml` |
>
> **정본**: `Vinc-docs/vinc/DEPLOYMENT.md` · `AZURE-ARCHITECTURE.md`
>
> 지우지 않고 남긴 이유: 문서·CHANGELOG 여러 곳이 이 경로를 인용하고 있고(역사 서술이라
> 고칠 대상이 아니다), 가비아 시절 구성을 읽을 근거로도 쓰인다. 특히 `Caddyfile` 은
> 지금 SWA 로 옮긴 CSP·캐시 규칙의 **출처**다(`site/staticwebapp.config.json`).
> 실행 가능한 스크립트 두 개(`deploy-hub.sh`·`deploy-embed-worker.sh`)에는 즉시 종료
> 가드를 넣어 두었다. `embed_worker.py` 는 `Vinc/packaging/embed_worker.py`(ONNX)로
> 대체됐다.

# deploy/ — 원격 프로파일 배포 템플릿 (P5 선행 D)

[[vinc.c.p5-remote-rollout]] 서버 트랙의 적용물. **도메인만 치환하면 되는
템플릿**이다 — 값은 전부 env 로 주입하고, 시크릿은 어떤 파일도 레포에 두지
않는다(D36).

> 오리진은 이제 정적 홈페이지 하나가 아니다 — 같은 도메인에 계정 백엔드
> (Hono 8080, 루프백)·웹 Vinc(`/app/`)·커뮤니티(`/community/`)가 동거하고,
> 이 폴더의 **Caddyfile 이 그 앞단**(라우팅·캐시·보안헤더·`/internal` 비공개)을
> 겸한다. 계정 백엔드와 웹/커뮤니티 번들의 게시 자체는 이 앱 배포 템플릿의
> 범위 밖(계정/홈페이지 트랙)이며, 이 템플릿은 앱 사이드카·MCP 어댑터·
> 엣지(Caddy)·백업을 담당한다.

## 적용 순서 (서버 트랙과 1:1)

1. **하드닝**: `sudo bash deploy/harden.sh`
   (22/80/443 만 개방 · SSH 키 전용 · fail2ban · 자동 패치 · vinc 계정)
2. **DNS·TLS + 엣지**: 레지스트라에서 A 레코드 2건(루트, `mcp.`) →
   `sudo apt install caddy` → 이 폴더의 Caddyfile 을 `/etc/caddy/Caddyfile` 로
   복사, `VINC_DOMAIN=vincs.app` 을 `/etc/caddy/caddy.env` 등으로 주입(확정 도메인, 2026-07-11) →
   `systemctl reload caddy`. 인증서는 자동(Let's Encrypt). 이 Caddyfile 은
   배포 정본과 1:1 이고 단순 리버스 프록시가 아니다 — 루트 도메인에서
   `/api/*`→계정 백엔드(`127.0.0.1:8080`)·`/app/`(웹 Vinc)·`/community/`·
   정적 `site/` 를 함께 서빙하고, 캐시 정책(해시에셋 1년 immutable · HTML
   no-cache · `/api`·`/internal` no-store), 보안헤더(HSTS includeSubDomains ·
   CSP frame-ancestors 'none' · X-Frame-Options DENY · nosniff · no-referrer),
   `mcp.` 서브도메인의 `/internal/*` 공개 404(graph-owner/purge-owner 는
   루프백 8790 전용)를 모두 싣는다.
3. **사이드카**: P4 릴리스의 리눅스 `vinc-sidecar` 바이너리를 `/opt/vinc/` 에
   배치 → `/etc/vinc/sidecar.env` 에 `VINC_SIDECAR_TOKEN=<random>` (권한 600) →
   `vinc-sidecar.service` 설치·기동. 데이터 홈은 `~vinc/.vinc` 자동 생성(D43).
4. **MCP 어댑터**: `/etc/vinc/mcp.env`(권한 600)에 다음을 채운다 —
   `VINC_SIDECAR_TOKEN`(사이드카와 동일 값), `VINC_MCP_ED25519_SEED`(base64 32B,
   MCP 서명키 — 계정이 공개키를 핀), `VINC_ACCOUNT_PUBLIC_KEY`(base64 32B,
   계정 공개키 · AA 어서션 검증용), `VINC_ACCOUNT_INTERNAL_URL=http://127.0.0.1:8080`
   (verify-key 루프백). 부트스트랩용 `VINC_OAUTH_ADMIN_KEY=<random>` 는 커넥터
   1회 인가에만 쓰고 **회원 로그인 경로 활성 후 폐기 가능**하다. 티어 쿼터
   (`VINC_QUOTA`/`VINC_FREE_DAILY_LIMIT`/`VINC_PRO_DAILY_LIMIT` 등)와 OAuth·쿼터
   상태 영속 경로(`StateDirectory=vinc-mcp` → `/var/lib/vinc-mcp`)는
   `vinc-mcp.service` 유닛에 박혀 있다 — 이 StateDirectory 가 없으면 SQLite
   쓰기가 막혀 기동이 실패한다(배포 게이트). → `vinc-mcp.service` 설치·기동 →
   claude.ai 커넥터에 `https://mcp.<도메인>/mcp` 등록. 인가는 **회원 로그인
   경로가 정본**이고(무료/Pro 티어 쿼터, v0.2.0), 부트스트랩 시에만
   `/authorize?...&key=<관리 키>` 1회 승인을 쓴다.
5. **홈페이지 + 웹/커뮤니티**: `rsync -av site/ /srv/www/`. 웹 Vinc(`/app/`)·
   커뮤니티(`/community/`) 번들과 `/api/*` 계정 백엔드(Hono 8080, DB 마이그레이션
   008 까지)는 계정/홈페이지 트랙에서 `/srv/www` 및 루프백 8080 으로 게시한다 —
   Caddyfile 은 이들을 이미 전제한다(위 2단계).
6. **백업**: `backup.cron` 을 `/etc/cron.d/vinc-backup` 으로 복사.
   복원 리허설(스냅샷 → 새 디렉토리 → /health·/domains 확인)까지가 완료선.

## 재배포 (최초 구축 이후)

위 1~6 은 **신규 서버 브링업**이다. 이미 떠 있는 허브에 새 코드를 올릴 때는:

```bash
bash deploy/deploy-hub.sh
```

빌드 환경 보장(ladybug venv + G0 확장 시드) → 서버 빌드 → **교체 전 격리 스모크**
(`/health=="ok"`) → DB 스냅샷 → 바이너리 백업·교체 → 재시작 → **라이브 검증, 실패 시
자동 롤백** 순으로 진행한다.

> ⚠ **허브는 CI 산출물이 아니다.** 데스크탑 릴리스 CI(`release.yml`)는 GitHub Releases 용
> 설치본만 만들고 이 서버엔 손대지 않는다. 데스크탑과 `api/`·`packaging/` 소스를 공유하지만
> 배포는 완전히 분리돼 있어, 데스크탑 쪽 결정이 허브를 조용히 깨뜨릴 수 있다
> (실제 사고: ladybug 전용 빌드를 kuzu 데이터 허브에 배포 → 즉시 장애).
> 배포 정책 정본: `Vinc-docs vinc/DEPLOYMENT.md` §4.

과거 일회성 스크립트는 `archive/` 에 이력으로 보존한다 — **실행 금지**(`archive/README.md`).

## 게이트 체크리스트

- [ ] 외부 포트 스캔: 22/80/443 외 응답 없음 (8787·8790·8080 은 루프백 전용)
- [ ] `https://<도메인>` · `https://mcp.<도메인>` 유효 인증서
- [ ] VM 내부 `curl 127.0.0.1:8787/health` OK, 외부에서는 도달 불가
- [ ] 무토큰 `POST https://mcp.<도메인>/mcp` → 401
- [ ] 공개 `GET https://mcp.<도메인>/internal/...` → 404 (내부 표면 비공개)
- [ ] 응답 헤더에 HSTS·CSP(frame-ancestors 'none')·X-Frame-Options DENY 존재;
      해시에셋 immutable · `/api` no-store
- [ ] `https://<도메인>/app/`(웹 Vinc) · `/community/`(갤러리) 렌더
- [ ] claude.ai 커넥터에서 vinc_graph 실호출 왕복(회원 로그인 경로)
- [ ] 복원 리허설 1회 PASS
