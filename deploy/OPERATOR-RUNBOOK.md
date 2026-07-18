# 운영자 런북 — vincs.app 최초 배포 (실 컴퓨터 실행용)

이 문서는 **한 번, 손으로** 서버를 세우는 절차다. 계획 정본은
[[vinc.c.p5-remote-rollout]](Vinc-docs), 설계는 [[vinc.d.remote-profile]](D36).
각 단계 끝의 **게이트**를 통과해야 다음으로 넘어간다.

> 이 런북은 **Vinc 앱 서버 트랙**(사이드카 + MCP 어댑터 + 정적 홈페이지)을
> 세운다. 라이브 vincs.app 은 그 위에 **계정 백엔드·웹 Vinc·커뮤니티**가
> 같은 오리진으로 동거한다(2026-07-18 라이브). 계정/이메일/홈페이지/커뮤니티는
> **별도 비공개 리포 `Vinc-homepage`** 가 정본이며(6·9단계에서 연동), 이 런북은
> 그 연동 지점을 명시한다.

## 대상

| 항목 | 값 |
|---|---|
| 도메인 | **vincs.app** (커넥터 = `mcp.vincs.app`) |
| 웹 VM | Ubuntu 24.04 · 4 vCPU / 8GB / SSD 150GB |
| 공인 IP | `1.201.120.125` |
| 로그인 계정 | **`ubuntu`**(클라우드 키 주입 — root 아님, `sudo` 로 권한 상승) |
| 계정 DB | 관리형 MySQL, 사설 **`192.168.0.38:3306`**(별도 인스턴스 — 웹 VM에서만 접속, 공개 엔드포인트 없음) |

## 시크릿 규칙 (D36)

- 이 레포에 **어떤 비밀번호·토큰·키도 커밋하지 않는다.** 전부 서버에서
  생성하고 서버 안(권한 600 파일)에만 둔다.
- VM 최초 비밀번호는 채팅·문서에 노출됐다면 **오염된 값**이다 — 1단계에서
  즉시 교체한다.
- 어떤 시크릿도 캡처해 외부로 보내지 않는다. 아래 명령의 `openssl rand` 출력은
  화면을 벗어나지 않게 파일로 바로 넣는다.

---

## 0. (로컬) SSH 키 준비

내 컴퓨터에 키가 없다면:

```bash
ssh-keygen -t ed25519          # 엔터 몇 번
cat ~/.ssh/id_ed25519.pub      # 이 한 줄을 1단계에서 서버에 등록
```

## 1. 로그인 · 비밀번호 교체 · 키 등록

```bash
ssh root@1.201.120.125          # 최초 비밀번호로 1회
```
서버 안에서:
```bash
passwd                          # (1) 오염된 비밀번호 즉시 교체
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo "ssh-ed25519 AAAA...내_공개키" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

**게이트:** `exit` 후 `ssh root@1.201.120.125` 로 **비밀번호 없이** 재접속되면 OK.
(안 되면 비밀번호로 다시 들어와 원인 확인 — 하드닝 전이라 비밀번호는 아직 살아 있다.)

## 2. 클라우드 보안 그룹

콘솔의 보안 그룹에서 **인바운드 22/80/443(TCP)** 만 열려 있는지 확인한다.
서버 내부 UFW(3단계)와 별개로 여기서도 열려 있어야 외부 접속이 된다.

**게이트:** 22/80/443 외 인바운드 규칙 없음.

## 3. 코드 가져오기 + 하드닝

레포가 비공개라 **SSH 배포키(read-only)** 로 클론한다. 서버에서 키를 만들고
공개키만 GitHub 레포에 등록한다 — 개인키는 서버 밖으로 나가지 않는다.

```bash
apt-get update -qq && apt-get install -y -qq git

# (1) 서버에서 배포키 생성(암호 없이 — 무인 클론용)
ssh-keygen -t ed25519 -f /root/.ssh/vinc_deploy -N "" -C "vinc-deploy@vincs"
cat /root/.ssh/vinc_deploy.pub          # 이 한 줄을 아래 (2)에 등록
```

**(2) GitHub 등록:** `github.com/Vinclums/Vinc` → Settings → Deploy keys →
Add deploy key → 위 공개키 붙여넣기. **"Allow write access" 는 체크하지 않는다**
(배포는 읽기만 필요). 저장.

```bash
# (3) 이 키로만 github 에 접속하도록 SSH 설정
cat >> /root/.ssh/config <<'EOF'
Host github.com
    IdentityFile /root/.ssh/vinc_deploy
    IdentitiesOnly yes
EOF
chmod 600 /root/.ssh/config
ssh -o StrictHostKeyChecking=accept-new -T git@github.com   # "Hi Vinclums/Vinc ..." 나오면 OK

# (4) SSH 로 클론
git clone git@github.com:Vinclums/Vinc.git /root/vinc
cd /root/vinc
git checkout main                       # 배포 트랙은 main 에 머지됨(P5 롤아웃 이후)

sudo bash deploy/harden.sh
```
> Deploy key 는 **이 레포 하나**에만 유효하다(계정 전체 권한이 아님) — 서버가
> 노출돼도 피해 범위가 이 레포 읽기로 한정된다.
harden.sh 가 하는 일: UFW(22/80/443) · SSH 비밀번호 로그인 차단 · fail2ban ·
자동 보안 패치 · 스왑 · `vinc` 배포 계정 · `/opt/vinc /srv/www /etc/vinc` 생성.

**게이트:** 외부 포트 스캔에서 22/80/443 외 무응답. 비밀번호 SSH 거부(키만 허용).
> ⚠ 하드닝이 비밀번호 로그인을 끈다 — **1단계 키 로그인이 되는 걸 확인한 뒤** 실행할 것.

## 4. DNS — A 레코드 2건

레지스트라(도메인 관리)에서:

| 이름 | 타입 | 값 |
|---|---|---|
| `@` | A | `1.201.120.125` |
| `mcp` | A | `1.201.120.125` |

전파 확인(로컬 또는 서버):
```bash
dig +short vincs.app
dig +short mcp.vincs.app         # 둘 다 1.201.120.125 이 나와야 함
```

**게이트:** 두 이름 모두 `1.201.120.125` 로 해석.

## 5. Caddy — 리버스 프록시 + 자동 TLS + 엣지 하드닝

`deploy/Caddyfile` 은 **배포 정본과 1:1**(편집 시 서버에도 반영)이며, 단순
프록시가 아니다. 루트 블록이 **`/api/*`→계정 백엔드(127.0.0.1:8080, 6단계)**,
`/app/*`·`/community/*` 정적 서빙, 캐시 정책(해시에셋 1년 immutable·HTML no-cache·
`/api`·`/internal` no-store), 보안 헤더(HSTS includeSubDomains·CSP
`frame-ancestors 'none'`·X-Frame-Options DENY·X-Content-Type-Options nosniff·
Referrer-Policy no-referrer), `encode zstd gzip` 를 모두 담는다. `mcp.` 블록은
루프백 8790 으로 프록시하되 **`/internal/*` 는 공개 404**(graph-owner/purge-owner 는
계정 백엔드가 루프백으로만 호출 — 심층방어).

```bash
# Caddy 설치(공식 저장소)
apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt-get update -qq && apt-get install -y caddy

# 설정 배치 + 도메인 주입
cp /root/vinc/deploy/Caddyfile /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile     # 문법 확인
mkdir -p /etc/systemd/system/caddy.service.d
printf '[Service]\nEnvironment=VINC_DOMAIN=vincs.app\n' > /etc/systemd/system/caddy.service.d/override.conf
systemctl daemon-reload && systemctl restart caddy
```

**게이트:** `curl -I https://vincs.app` 가 유효 인증서로 응답(콘텐츠는 아직 없어도 됨).
`https://mcp.vincs.app` 도 인증서 발급 확인(어댑터 미기동이면 502 여도 TLS 만 되면 통과).
`/api/*`·`/app/`·`/community/` 는 백엔드/자산이 아직 없으면 502·404 가 정상(각각 6·9단계 이후 채워짐).
엣지 하드닝 확인:
```bash
curl -sI https://vincs.app | grep -i 'strict-transport\|x-frame-options'         # HSTS·DENY 헤더 존재
curl -s -o /dev/null -w '%{http_code}\n' https://mcp.vincs.app/internal/graph-owner   # 404(공개 차단)
```

## 6. 계정 백엔드 (Hono 8080) + DB — /api·웹 Vinc·커뮤니티 전제

정본은 **`Vinc-homepage` 리포**(`account/README.md`, `account/db/README.md`)다.
이 서비스가 있어야 `/api/*`(로그인·뉴스레터·API 키·커넥터 온보딩·그래프 프록시·
커뮤니티)가 살고, 그 위에서 웹 Vinc·커뮤니티가 동작한다. 앱 사이드카(8787)와는
시크릿을 공유하지 않는 **물리적·인증적 별개 서비스**(전용 유저 `vinc-acct`).

```bash
# (1) DB — 관리형 MySQL(192.168.0.38:3306). 콘솔에서 bootstrap DDL 1회,
#     이후 dbmate 로 마이그레이션 001~008 적용(계정 리포 db/README.md 절차).
#     require_secure_transport=ON · ACG 는 웹 VM 사설 IP(192.168.0.38/32)만 허용.
#     008 = community_packs(커뮤니티), 007 = users.plan ENUM('free','pro'),
#     006 = api_keys(커넥터 API 키). dbmate 는 forward-only.
dbmate up && dbmate status                    # 001→008 적용 확인

# (2) 앱 배치 — Node 22 + Hono 빌드 산출물을 /opt/vinc-account 에.
#     시크릿(pepper/세션/CSRF/DB pw/서명키)은 /etc/vinc-account/env(0600, vinc-acct)에만.
cp account/deploy/vinc-account.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now vinc-account
```

**게이트:**
```bash
curl -s http://127.0.0.1:8080/api/health        # 내부 헬스 OK
curl -sI https://vincs.app/api/health           # Caddy /api/*→8080 프록시 왕복(외부 오리진 경유)
```
DB 는 웹 VM에서만(사설 IP·SSL) 연결되고 외부/평문 연결은 거부돼야 정상.

## 7. 사이드카 바이너리 빌드 + 서비스

최초 배포는 서버에서 직접 동결한다(이후 릴리스는 P4 태그 파이프라인 산출물 사용).

```bash
cd /root/vinc
apt-get install -y python3-venv python3-pip
python3 -m venv /root/.build-venv
/root/.build-venv/bin/pip install -q kuzu==0.11.3 pyyaml pyinstaller
/root/.build-venv/bin/pyinstaller -y packaging/sidecar.spec
install -o vinc -g vinc -m 755 dist/vinc-sidecar /opt/vinc/vinc-sidecar

# 데이터 홈 사전 생성(systemd ReadWritePaths 는 존재하는 경로를 기대)
install -d -o vinc -g vinc /home/vinc/.vinc

# 사이드카 세션 토큰 — 서버에서 생성, 파일에만
umask 077
printf 'VINC_SIDECAR_TOKEN=%s\n' "$(openssl rand -hex 24)" > /etc/vinc/sidecar.env
chmod 600 /etc/vinc/sidecar.env

cp deploy/vinc-sidecar.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now vinc-sidecar
```

**게이트:**
```bash
source /etc/vinc/sidecar.env
curl -s -H "Authorization: Bearer $VINC_SIDECAR_TOKEN" http://127.0.0.1:8787/health   # {"status":"ok"}
```
외부에서 `1.201.120.125:8787` 직접 접근은 **안 돼야** 정상(UFW 차단).
데이터 홈 `~vinc/.vinc/{db,packs,inbox,backups}` 자동 생성 확인.

## 8. MCP 어댑터 + 커넥터 등록

어댑터는 루프백 8790 에서 Streamable HTTP + OAuth 로 뜨고, 공개는 Caddy `mcp.`
블록이 전담한다. `vinc-mcp.service` 는 **사이드카 토큰 외에 서명키·계정 공개키·
계정 내부 URL** 을 요구한다(웹 Vinc·회원 인증 경로가 이 값들로 동작 — 빠지면
기동·회원경로 실패). 티어 쿼터와 OAuth/쿼터 상태 영속 경로(`StateDirectory=
vinc-mcp` → `/var/lib/vinc-mcp`)는 유닛 파일에 이미 박혀 있다.

```bash
umask 077
SIDE=$(grep -oP 'VINC_SIDECAR_TOKEN=\K.*' /etc/vinc/sidecar.env)
cat > /etc/vinc/mcp.env <<EOF
VINC_DOMAIN=vincs.app
VINC_SIDECAR_TOKEN=$SIDE
VINC_OAUTH_ADMIN_KEY=$(openssl rand -hex 24)
VINC_MCP_ED25519_SEED=$(openssl rand -base64 32)
VINC_ACCOUNT_PUBLIC_KEY=<계정 백엔드 서명 공개키 base64 — /etc/vinc-account/env 값과 동일>
VINC_ACCOUNT_INTERNAL_URL=http://127.0.0.1:8080
EOF
chmod 600 /etc/vinc/mcp.env

cp /root/vinc/deploy/vinc-mcp.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now vinc-mcp
```
> `VINC_MCP_ED25519_SEED` 는 MCP 가 계정에 보내는 어서션의 서명키(계정이 공개키를
> 핀). `VINC_ACCOUNT_PUBLIC_KEY` 는 그 역방향(계정 어서션 검증). 두 값은 계정
> 백엔드와 **짝을 맞춰** 발급/공유해야 한다(공개키만 교환 — B2 불변식).

**게이트(무인증 거부):**
```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://mcp.vincs.app/mcp -d '{}'   # 401
ls -ld /var/lib/vinc-mcp        # StateDirectory 생성 확인(OAuth·쿼터 SQLite 영속 → 재시작해도 커넥터 재연결 불필요)
```

**커넥터 등록:** 정상 경로는 **회원 로그인 온보딩**이다 — 회원이 `vincs.app/connect`
에서 커넥터를 연결하고 API 키(`vinc_sk_…`)를 발급받는다(무료/Pro 티어 쿼터 적용).
`&key=<VINC_OAUTH_ADMIN_KEY>` 관리 키 승인은 **단일 소유자용 레거시 폴백**으로만
남아 있다(회원 경로 활성 후 폐기 가능 — 유닛 주석). claude.ai 에서 사용자 지정
커넥터로 `https://mcp.vincs.app/mcp` 를 등록한다.

**게이트:** 커넥터에서 `vinc_domains`·`vinc_search` 실호출이 실데이터로 응답(회원 owner 스코프).

> 커넥터 **제거**는 서버가 아니라 클라이언트 쪽 — 앱 Settings 의
> 'Remove from Desktop/Code' 버튼 또는 `vinc mcp uninstall --client desktop|code`
> (desktop 은 백업 후 Vinc 키만 제거).

## 9. 홈페이지 + 웹 Vinc + 커뮤니티 게시

공개 사이트(홈·로그인·`/connect`·`/privacy`·구독/해지 인터스티셜·**커뮤니티
갤러리 `/community/`**)의 정본은 **`Vinc-homepage` 리포의 `site/`** 다. 웹 Vinc
(`/app/`)는 데스크탑 앱과 **같은 dist 를 서브패스로** 서빙한다 — 웹 빌드만
`VITE_BASE=/app/` 로 뽑아 자산이 `/app/assets/*` 를 가리키게 하고 `/srv/www/app/`
에 배포한다(해시 라우팅 SPA → 미존재 경로는 `/app/index.html` 폴백).

```bash
# (1) 공개 홈페이지 + 커뮤니티 갤러리 — Vinc-homepage/site 정본
rsync -av /root/vinc-homepage/site/ /srv/www/

# (2) 웹 Vinc — 앱 리포 web 빌드를 /app 서브패스로
cd /root/vinc/app
VITE_BASE=/app/ npm ci && VITE_BASE=/app/ npm run build
rsync -av dist/ /srv/www/app/

chown -R vinc:vinc /srv/www
```
> 앱 리포에도 `Vinc/site/` 스냅샷이 있으나, 라이브 공개 사이트·커뮤니티·계정
> 인터스티셜의 정본은 `Vinc-homepage` 리포다(2026-07 홈페이지 분리). 최초 실
> 배포는 로컬 작업트리를 tar/scp 로 전송해 GitHub 배포키 등록을 생략했다(하단 참고).

**게이트:** 브라우저에서 `https://vincs.app` 홈페이지 렌더, 다운로드 버튼이
GitHub Releases(현행 v0.3.1)로 연결. `https://vincs.app/app/` 웹 Vinc 부팅(로그인
안 됐으면 `/login` 리다이렉트 — `ensureWebAuth` 게이트), 로그인 후 그래프 왕복.
`https://vincs.app/community/` 갤러리 렌더. 회원이 웹 Packs 표면에서 지식팩을
게시하면 갤러리에 시각요약(도메인 분포·교차도메인%·top 태그·엣지 타입)과 함께 노출.

## 10. 백업 + 복원 리허설

야간 백업은 **서버 프로세스 내부 스냅샷**(HTTP `POST /backup`)이다 — 사이드카가
소유한 Database 에서 CHECKPOINT+복사를 수행하므로 **무중단·무락**이다. CLI
`vinc-sidecar backup` 은 별도 프로세스로 kuzu 를 다시 열어 **서비스 기동 중 배타
락으로 실패**한다(브링업 중 실측) — 그래서 백업 헬퍼가 HTTP 경로를 쓴다.

```bash
# 백업 헬퍼 + cron 설치
install -m 755 /root/vinc/deploy/vinc-backup.sh /opt/vinc/vinc-backup.sh
cp /root/vinc/deploy/backup.cron /etc/cron.d/vinc-backup

# 복원 리허설 — 백업은 복원이 증명될 때까지 백업이 아니다
/opt/vinc/vinc-backup.sh /home/vinc/.vinc/backups/rehearsal        # 무중단 스냅샷
sudo -u vinc env MEMORY_DATA_DIR=/home/vinc/.vinc/backups/rehearsal/db \
    /opt/vinc/vinc-sidecar domains --owner user:local             # 복원본 조회
rm -rf /home/vinc/.vinc/backups/rehearsal
```

**게이트:** 스냅샷 생성 + 복원본 조회가 동작하면 PASS(빈 DB 는 graceful dry-plan —
첫 ingest 후 실데이터 왕복 재확인 권장). 결과를 p5-remote-rollout 장부에 기록.

> 참고(계정): 이 VM 은 클라우드가 키를 `ubuntu` 계정에 주입했다 — 위 명령의
> `/root/vinc` 경로·root 실행은 실제로는 `~ubuntu/vinc` + `sudo` 로 수행했다.
> 비공개 레포 클론(3단계) 대신 **로컬 작업트리를 tar/scp 로 전송**해 GitHub 배포키
> 등록을 생략했다(둘 다 유효 — 산출물 동일). 계정 백엔드·공개 홈페이지·커뮤니티는
> 별도 `Vinc-homepage` 리포에서 같은 방식으로 전송·배포했다(6·9단계).

---

## 완료 체크리스트

- [ ] 1. 키 로그인 OK · 비밀번호 교체
- [ ] 2. 보안 그룹 22/80/443 만
- [ ] 3. harden.sh · 비밀번호 SSH 거부 · main 체크아웃
- [ ] 4. DNS 2건 → 1.201.120.125
- [ ] 5. https://vincs.app · https://mcp.vincs.app 인증서 · HSTS/X-Frame 헤더 · /internal 404
- [ ] 6. 계정 백엔드 /api/health OK · DB 마이그레이션 008 적용 · /api 프록시 왕복
- [ ] 7. 사이드카 /health OK · 8787 외부 차단
- [ ] 8. /mcp 무토큰 401 · StateDirectory 영속 · 회원 커넥터 실호출 왕복
- [ ] 9. 홈페이지 렌더 · 웹 Vinc(/app/) 부팅 · 커뮤니티(/community/) 갤러리
- [ ] 10. 복원 리허설 PASS

## 문제 대응

- `systemctl status vinc-sidecar` / `journalctl -u vinc-sidecar -n 50` — 사이드카 로그.
- `systemctl status vinc-account` / `journalctl -u vinc-account -n 50` — 계정 백엔드 로그
  (DB 연결 실패면 사설 IP·SSL CA `/etc/vinc-account/db-ca.pem`·ACG 확인).
- `journalctl -u vinc-mcp -n 50` — 어댑터 로그. 기동 즉시 죽으면 `/etc/vinc/mcp.env`
  의 `VINC_MCP_ED25519_SEED`·`VINC_ACCOUNT_PUBLIC_KEY` 누락 또는 `/var/lib/vinc-mcp`
  (StateDirectory) 쓰기 불가를 먼저 의심.
- Caddy 인증서 실패: DNS 전파(4단계) 미완이면 발급 실패 → `dig` 재확인 후
  `systemctl restart caddy`.
- 커넥터 401 지속: `/etc/vinc/mcp.env` 의 `VINC_SIDECAR_TOKEN` 이
  `/etc/vinc/sidecar.env` 값과 동일한지 확인.
- `/api/*` 502: 계정 백엔드(6단계) 미기동 — `vinc-account` 상태 확인.
- 웹 Vinc(`/app/`)가 계속 `/login` 으로 튕김: 계정 세션쿠키(`__Host-session`)
  미발급 — 로그인 흐름·계정 백엔드 정상 여부 확인(웹 Vinc 는 계정 세션으로 인증).
