# 운영자 런북 — vincs.app 최초 배포 (실 컴퓨터 실행용)

이 문서는 **한 번, 손으로** 서버를 세우는 절차다. 계획 정본은
[[vinc.c.p5-remote-rollout]](Vinc-docs), 설계는 [[vinc.d.remote-profile]](D36).
각 단계 끝의 **게이트**를 통과해야 다음으로 넘어간다.

## 대상

| 항목 | 값 |
|---|---|
| 도메인 | **vincs.app** (커넥터 = `mcp.vincs.app`) |
| VM | Ubuntu 24.04 · 4 vCPU / 8GB / SSD 150GB |
| 공인 IP | `1.201.120.27` |

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
ssh root@1.201.120.27          # 최초 비밀번호로 1회
```
서버 안에서:
```bash
passwd                          # (1) 오염된 비밀번호 즉시 교체
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo "ssh-ed25519 AAAA...내_공개키" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

**게이트:** `exit` 후 `ssh root@1.201.120.27` 로 **비밀번호 없이** 재접속되면 OK.
(안 되면 비밀번호로 다시 들어와 원인 확인 — 하드닝 전이라 비밀번호는 아직 살아 있다.)

## 2. 클라우드 보안 그룹

콘솔의 보안 그룹에서 **인바운드 22/80/443(TCP)** 만 열려 있는지 확인한다.
서버 내부 UFW(3단계)와 별개로 여기서도 열려 있어야 외부 접속이 된다.

**게이트:** 22/80/443 외 인바운드 규칙 없음.

## 3. 코드 가져오기 + 하드닝

```bash
apt-get update -qq && apt-get install -y -qq git
git clone https://github.com/weg-9000/vinc.git /root/vinc
cd /root/vinc
git checkout claude/weg-vinc-gap-analysis-r1vpwl   # 배포 트랙 브랜치(머지 후엔 main)

sudo bash deploy/harden.sh
```
harden.sh 가 하는 일: UFW(22/80/443) · SSH 비밀번호 로그인 차단 · fail2ban ·
자동 보안 패치 · 스왑 · `vinc` 배포 계정 · `/opt/vinc /srv/www /etc/vinc` 생성.

**게이트:** 외부 포트 스캔에서 22/80/443 외 무응답. 비밀번호 SSH 거부(키만 허용).
> ⚠ 하드닝이 비밀번호 로그인을 끈다 — **1단계 키 로그인이 되는 걸 확인한 뒤** 실행할 것.

## 4. DNS — A 레코드 2건

레지스트라(도메인 관리)에서:

| 이름 | 타입 | 값 |
|---|---|---|
| `@` | A | `1.201.120.27` |
| `mcp` | A | `1.201.120.27` |

전파 확인(로컬 또는 서버):
```bash
dig +short vincs.app
dig +short mcp.vincs.app         # 둘 다 1.201.120.27 이 나와야 함
```

**게이트:** 두 이름 모두 `1.201.120.27` 로 해석.

## 5. Caddy — 리버스 프록시 + 자동 TLS

```bash
# Caddy 설치(공식 저장소)
apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt-get update -qq && apt-get install -y caddy

# 설정 배치 + 도메인 주입
cp /root/vinc/deploy/Caddyfile /etc/caddy/Caddyfile
mkdir -p /etc/systemd/system/caddy.service.d
printf '[Service]\nEnvironment=VINC_DOMAIN=vincs.app\n' > /etc/systemd/system/caddy.service.d/override.conf
systemctl daemon-reload && systemctl restart caddy
```

**게이트:** `curl -I https://vincs.app` 가 유효 인증서로 응답(콘텐츠는 아직 없어도 됨).
`https://mcp.vincs.app` 도 인증서 발급 확인(502 여도 TLS 만 되면 통과 — 어댑터는 7단계).

## 6. 사이드카 바이너리 빌드 + 서비스

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
외부에서 `1.201.120.27:8787` 직접 접근은 **안 돼야** 정상(UFW 차단).
데이터 홈 `~vinc/.vinc/{db,packs,inbox,backups}` 자동 생성 확인.

## 7. MCP 어댑터 + 커넥터 등록

```bash
umask 077
SIDE=$(grep -oP 'VINC_SIDECAR_TOKEN=\K.*' /etc/vinc/sidecar.env)
cat > /etc/vinc/mcp.env <<EOF
VINC_DOMAIN=vincs.app
VINC_SIDECAR_TOKEN=$SIDE
VINC_OAUTH_ADMIN_KEY=$(openssl rand -hex 24)
EOF
chmod 600 /etc/vinc/mcp.env

cp /root/vinc/deploy/vinc-mcp.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now vinc-mcp
```

**게이트(무인증 거부):**
```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://mcp.vincs.app/mcp -d '{}'   # 401
```

**커넥터 등록(claude.ai, 수동 1회):** 설정 → 커넥터 → 사용자 지정 커넥터 추가 →
URL `https://mcp.vincs.app/mcp`. OAuth 인가 단계에서 우리 게이트는 관리 키
승인 모델이다(단일 소유자) — 인가 URL 뒤에 `&key=<VINC_OAUTH_ADMIN_KEY 값>` 을
붙여 1회 승인한다. 관리 키는 `/etc/vinc/mcp.env` 안에만 있다.

**게이트:** 커넥터에서 `vinc_domains`·`vinc_graph` 실호출이 실데이터로 응답.

## 8. 홈페이지 게시

```bash
rsync -av /root/vinc/site/ /srv/www/
chown -R vinc:vinc /srv/www
```

**게이트:** 브라우저에서 `https://vincs.app` 가 홈페이지를 렌더, 다운로드 버튼이
GitHub Releases 로 연결(릴리스가 아직 없으면 P4 태그를 밀어 발행).

## 9. 백업 + 복원 리허설

```bash
cp /root/vinc/deploy/backup.cron /etc/cron.d/vinc-backup

# 복원 리허설 — 백업은 복원이 증명될 때까지 백업이 아니다
sudo -u vinc /opt/vinc/vinc-sidecar backup --out /tmp/rehearsal
MEMORY_DATA_DIR=/tmp/rehearsal/db /opt/vinc/vinc-sidecar domains --owner user:local
```

**게이트:** 복원본에서 조회가 동작하면 PASS. 결과를 p5-remote-rollout 장부에 기록.

---

## 완료 체크리스트

- [ ] 1. 키 로그인 OK · 비밀번호 교체
- [ ] 2. 보안 그룹 22/80/443 만
- [ ] 3. harden.sh · 비밀번호 SSH 거부
- [ ] 4. DNS 2건 → 1.201.120.27
- [ ] 5. https://vincs.app · https://mcp.vincs.app 인증서
- [ ] 6. 사이드카 /health OK · 8787 외부 차단
- [ ] 7. /mcp 무토큰 401 · 커넥터 실호출 왕복
- [ ] 8. 홈페이지 렌더
- [ ] 9. 복원 리허설 PASS

## 문제 대응

- `systemctl status vinc-sidecar` / `journalctl -u vinc-sidecar -n 50` — 서비스 로그.
- Caddy 인증서 실패: DNS 전파(4단계) 미완이면 발급 실패 → `dig` 재확인 후
  `systemctl restart caddy`.
- 커넥터 401 지속: `/etc/vinc/mcp.env` 의 `VINC_SIDECAR_TOKEN` 이
  `/etc/vinc/sidecar.env` 값과 동일한지 확인.
