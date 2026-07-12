#!/usr/bin/env bash
# Vinc 서버 트랙 1단계 — 기초 하드닝(P5 계획 §2-1). Ubuntu LTS 전제, root 로 실행.
# 게이트: 외부 포트 스캔에서 22/80/443 외 응답 없음 · SSH 패스워드 로그인 거부.
set -euo pipefail

echo "[1/5] 보안 패치 자동화(unattended-upgrades)"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq unattended-upgrades fail2ban ufw
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "[2/5] 방화벽 — 22/80/443 만"
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "[3/5] SSH — 키 인증 전용"
mkdir -p /etc/ssh/sshd_config.d
# sshd 는 sshd_config.d/*.conf 를 이름순으로 읽고 **먼저 나온 값이 이긴다**. cloud-init
# 의 50-cloud-init/60-cloudimg-settings 가 PasswordAuthentication yes 를 먼저 선언하므로,
# 우리 설정은 그보다 앞서 읽히도록 00- 접두어로 둔다(90- 이면 덮여서 비번인증이 안 꺼짐).
cat > /etc/ssh/sshd_config.d/00-vinc-harden.conf <<'CONF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
CONF
# 방어적: 앞서 쓴 90- 잔재 제거 + 다른 드롭인의 'PasswordAuthentication yes' 무력화.
rm -f /etc/ssh/sshd_config.d/90-vinc-harden.conf
for f in /etc/ssh/sshd_config.d/*.conf; do
  [ "$f" = /etc/ssh/sshd_config.d/00-vinc-harden.conf ] && continue
  sed -i 's/^[[:space:]]*PasswordAuthentication[[:space:]]\+yes/PasswordAuthentication no/I' "$f" 2>/dev/null || true
done
sshd -t && { systemctl reload ssh || systemctl reload sshd; }

echo "[4/5] fail2ban 기동"
systemctl enable --now fail2ban

echo "[5/5] 배포 계정·디렉토리"
id -u vinc >/dev/null 2>&1 || useradd --create-home --shell /usr/sbin/nologin vinc
mkdir -p /opt/vinc /srv/www /etc/vinc
chown vinc:vinc /opt/vinc
chmod 750 /etc/vinc

# 스왑(4GB RAM 보조 — 이미 있으면 건너뜀)
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "완료. 다음: deploy/README.md 의 2단계(DNS·TLS)로."
