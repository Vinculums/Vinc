"""Agent 파일의 읽기 능력 계약. 앱 릴리스 번호와 별개이며 일반 팩 v1을 바꾸지 않는다."""
AGENT_PACK_VERSION = 2
AGENT_PACK_KIND = "community-agent"
AGENT_IMPORT_CAPABILITY = "community-agent-safe-import"


def required_reader():
    return {"capability": AGENT_IMPORT_CAPABILITY, "version": AGENT_PACK_VERSION}
