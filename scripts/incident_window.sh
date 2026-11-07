#!/usr/bin/env bash
# 장애 감시의 배포 창을 열거나 닫는다. 창이 열린 서비스는 실패해도 장애 메일이 나가지 않는다.
#
#   scripts/incident_window.sh open  hub "<이유 꼬리표>"
#   scripts/incident_window.sh close hub
#
# 토큰(INCIDENT_MAINTENANCE_TOKEN)이 없거나 호출이 실패해도 **0 으로 끝난다.** 배포 창은
# 메일을 덜 보내려는 장치이지 배포의 전제가 아니다. 창은 서버에서 최대 30분이고 저절로 끝난다.
# 근거: [[vinc.plan.incident-mail-automation-2026-10-18]] §2
set -u

action="${1:-}"
service="${2:-}"
reason="${3:-}"
url="${INCIDENT_MAINTENANCE_URL:-https://vincs.io/api/admin/incident/maintenance}"

case "$service" in hub|account) ;; *) echo "::warning::incident window: unknown service '$service'"; exit 0 ;; esac
if [ -z "${INCIDENT_MAINTENANCE_TOKEN:-}" ]; then
  echo "::notice::INCIDENT_MAINTENANCE_TOKEN is not set, so no incident window was ${action:-touched}"
  exit 0
fi

# 이유는 서버가 영숫자·점·하이픈·밑줄 40자로 자른다. 여기서도 같은 문자만 남겨 JSON 을 깨지 않게 한다.
reason="$(printf '%s' "$reason" | tr -cd 'A-Za-z0-9._-' | cut -c1-40)"
case "$action" in
  open)  body="{\"service\":\"$service\",\"minutes\":30,\"reason\":\"$reason\"}" ;;
  close) body="{\"service\":\"$service\",\"action\":\"close\"}" ;;
  *) echo "::warning::incident window: unknown action '$action'"; exit 0 ;;
esac

code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -X POST "$url" \
  -H 'content-type: application/json' -H "x-incident-token: $INCIDENT_MAINTENANCE_TOKEN" \
  -d "$body" 2>/dev/null)" || code="${code:-000}"
if [ "$code" = "200" ]; then
  echo "incident window $action $service"
else
  echo "::warning::incident window $action $service failed (HTTP $code); the run continues"
fi
exit 0
