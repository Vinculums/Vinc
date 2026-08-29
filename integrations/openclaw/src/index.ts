// OpenClaw 플러그인 엔트리 — Vinc 코퍼스 supplement 를 등록한다.
//
// SDK 를 의존성으로 끌지 않으려고 memory-wiki 선례의 엔트리 모양(id/name/description/
// register)을 평범한 객체로 미러한다. 업스트림의 definePluginEntry 는 타입 래퍼라
// 이 모양 그대로 로드된다(검증: openclaw/openclaw extensions/memory-wiki/index.ts,
// 2026-08-29). 라이브 게이트웨이 로드 e2e 는 사람 게이트 뒤에 있다.
//
// ⚠ claude-cli 프로바이더에서 플러그인 훅이 발화하지 않는 회귀가 보고돼 있다
// (openclaw 이슈 #65157). 이 supplement 는 훅이 아니라 memory_search/memory_get
// 도구 경로라 그 회귀의 직접 영향권 밖이지만, e2e 검증은 pi-embedded 런너에서 한다.
import {
  createVincCorpusSupplement,
  type MemoryCorpusSupplement,
} from "./supplement.ts";

export type VincPluginApi = {
  /** plugins.entries.<id>.config 의 플러그인 스코프 설정. */
  pluginConfig?: unknown;
  logger?: { warn(msg: string): void };
  registerMemoryCorpusSupplement(supplement: MemoryCorpusSupplement): void;
};

type VincPluginConfig = {
  url?: string;
  apiKey?: string;
  maxResults?: number;
  timeoutMs?: number;
};

export const DEFAULT_HUB_URL = "https://mcp.vincs.io/mcp";

export function register(api: VincPluginApi): void {
  const cfg = (api.pluginConfig ?? {}) as VincPluginConfig;
  // 키는 설정에서 받는다. env 폴백은 로컬 개발 편의다.
  const apiKey = cfg.apiKey ?? process.env.VINC_REMOTE_MCP_TOKEN ?? "";
  if (!apiKey) {
    api.logger?.warn(
      "vinc: apiKey 미설정이라 corpus supplement 를 등록하지 않는다 " +
        "(plugins.entries.vinc.config.apiKey 에 키를 넣을 것. " +
        "스코프 키 출시 전까지 전권 키 등록은 권장하지 않는다)",
    );
    return;
  }
  api.registerMemoryCorpusSupplement(
    createVincCorpusSupplement({
      url: cfg.url ?? DEFAULT_HUB_URL,
      apiKey,
      maxResults: cfg.maxResults,
      timeoutMs: cfg.timeoutMs,
      log: api.logger,
    }),
  );
}

export default {
  id: "vinc",
  name: "Vinc",
  description:
    "Audit-grade memory corpus supplement backed by the Vinc account graph (read-only).",
  register,
};
