// OpenClaw 플러그인 엔트리 — Vinc 를 memory 슬롯과 코퍼스 supplement 로 등록한다.
//
// SDK 를 의존성으로 끌지 않으려고 memory-wiki 선례의 엔트리 모양(id/name/description/
// register)을 평범한 객체로 미러한다. 업스트림의 definePluginEntry 는 타입 래퍼라
// 이 모양 그대로 로드된다(검증: openclaw/openclaw extensions/memory-wiki/index.ts,
// 2026-08-29). 라이브 게이트웨이 로드 e2e 는 사람 게이트 뒤에 있다.
//
// 둘 다 읽기만 한다:
// - `plugins.slots.memory` 가 "vinc" 이면 슬롯 주인으로서 memory_search / memory_get 과
//   호스트의 메모리 표면이 Vinc 를 읽는다(memory.ts).
// - 슬롯이 다른 플러그인(기본 memory-core)이면 supplement 가 그 플러그인의 memory_search 에
//   Vinc 코퍼스를 더한다.
// 호스트에 등록 API 가 없으면 있는 것만 등록한다.
//
// ⚠ claude-cli 프로바이더에서 플러그인 훅이 발화하지 않는 회귀가 보고돼 있다
// (openclaw 이슈 #65157). 둘 다 훅이 아니라 도구·런타임 경로라 그 회귀의 직접 영향권
// 밖이지만, e2e 검증은 pi-embedded 런너에서 한다.
import {
  createVincCorpusSupplement,
  hubConnection,
  type MemoryCorpusSupplement,
} from "./supplement.ts";
import {
  TOOL_NAMES,
  createVincMemoryCapability,
  createVincMemoryTools,
  type PluginTool,
  type VincMemoryCapability,
} from "./memory.ts";

export type VincPluginApi = {
  /** 게이트웨이 설정 전체. `plugins.slots.memory` 만 읽는다. */
  config?: unknown;
  /** plugins.entries.<id>.config 의 플러그인 스코프 설정. */
  pluginConfig?: unknown;
  logger?: { warn(msg: string): void };
  registerMemoryCorpusSupplement(supplement: MemoryCorpusSupplement): void;
  /** OpenClaw 2026.10 이후. 없으면 슬롯을 등록하지 않는다. */
  registerMemoryCapability?(capability: VincMemoryCapability): void;
  /** 에이전트 도구. 팩토리는 실행마다 불린다. */
  registerTool?(factory: (ctx: unknown) => PluginTool[] | null, opts?: { names?: string[] }): void;
};

type VincPluginConfig = {
  url?: string;
  apiKey?: string;
  maxResults?: number;
  timeoutMs?: number;
  /** 에이전트 도구 memory_search / memory_get 을 받는 에이전트 id. 비우면 아무 에이전트도 받지 않는다. */
  agents?: string[];
};

export const DEFAULT_HUB_URL = "https://mcp.vincs.io/mcp";
export const PLUGIN_ID = "vinc";

/** 이 게이트웨이가 memory 슬롯을 Vinc 에 주었는가. */
export function ownsMemorySlot(config: unknown): boolean {
  const slots = (config as { plugins?: { slots?: { memory?: unknown } } } | undefined)?.plugins?.slots;
  return slots?.memory === PLUGIN_ID;
}

/** 도구 팩토리가 받는 실행 문맥에서 에이전트 id 만 읽는다. */
function agentOf(ctx: unknown): string | undefined {
  const id = (ctx as { agentId?: unknown } | null | undefined)?.agentId;
  return typeof id === "string" ? id : undefined;
}

export function register(api: VincPluginApi): void {
  const cfg = (api.pluginConfig ?? {}) as VincPluginConfig;
  // 키는 설정에서 받는다. env 폴백은 로컬 개발 편의다.
  const apiKey = cfg.apiKey ?? process.env.VINC_REMOTE_MCP_TOKEN ?? "";
  if (!apiKey) {
    api.logger?.warn(
      "vinc: apiKey 미설정이라 등록하지 않는다 " +
        "(plugins.entries.vinc.config.apiKey 에 읽기 전용 키 vinc_ro_ 를 넣을 것)",
    );
    return;
  }
  const options = {
    url: cfg.url ?? DEFAULT_HUB_URL,
    apiKey,
    maxResults: cfg.maxResults,
    timeoutMs: cfg.timeoutMs,
    log: api.logger,
  };
  api.registerMemoryCorpusSupplement(createVincCorpusSupplement(options));
  // 슬롯 주인이 아닐 때 등록하면 memory-core 의 같은 이름 도구와 부딪히고, 주인에게 없는
  // providerRuntime 을 보태 그 검색을 가로챈다. 그래서 슬롯을 받았을 때만 등록한다.
  if (!ownsMemorySlot(api.config)) return;
  const hub = hubConnection(options);
  api.registerMemoryCapability?.(createVincMemoryCapability(hub, options));
  // 에이전트 도구는 호출한 세션의 청중을 알 수 없다(그룹 대화의 다른 사람도 부를 수 있다).
  // 그래서 그 사람만 쓰는 에이전트로 명시한 것에만 내준다. 비우면 내주지 않는다.
  const agents = Array.isArray(cfg.agents) ? cfg.agents.filter((a) => typeof a === "string") : [];
  if (!agents.length) {
    api.logger?.warn("vinc: config.agents 가 비어 memory_search / memory_get 도구를 어느 에이전트에도 내주지 않는다");
  }
  api.registerTool?.(
    (ctx) => {
      const agent = agentOf(ctx);
      return agent && agents.includes(agent) ? createVincMemoryTools(hub, options) : null;
    },
    { names: [...TOOL_NAMES] },
  );
}

export default {
  id: PLUGIN_ID,
  name: "Vinc",
  description:
    "Vinc account graph as OpenClaw memory: the memory slot and a corpus supplement, both read only.",
  register,
};
