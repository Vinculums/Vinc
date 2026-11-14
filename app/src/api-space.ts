// 스페이스: 지금 스페이스에 쓸 수 있는가, 팀 · 커뮤니티 목록, 주소가 싣는 스페이스(`?space=`)를 읽고 쓰는 일.
// 설정은 `api-config.ts`, 호출은 `api-transport.ts` 에서 읽는다. 이 파일은 `api.ts` 를 읽지 않는다.
import { cfg, WEB, accountMode, remote } from "./api-config";
import { call, webFetch } from "./api-transport";
import { communityFromAddress } from "./space-picker";

/** 지금 스페이스에서 편집 단추를 보일지. 화면 힌트일 뿐이고 최종 판정은 서버다(팀 역할 검사).
 *  개인 그래프는 쓸 수 있고, 커뮤니티는 읽기 전용이며, 팀은 editor·admin 만 쓴다. 팀 목록을 못
 *  읽으면 숨기지 않는다(서버가 거절하면 그 자리에서 말한다). 스페이스마다 한 번만 묻는다. */
const writableCache = new Map<string, Promise<boolean>>();
export function canWriteHere(team: string = cfg.team): Promise<boolean> {
  if (!team) return Promise.resolve(true);
  if (isCommunitySpace(team)) return Promise.resolve(false);
  let p = writableCache.get(team);
  if (!p) {
    p = teamsApi.list({ throwOnError: true })
      .then((rows) => rows.some((t) => t.id === team && ["editor", "admin"].includes(t.role)))
      .catch(() => { writableCache.delete(team); return true; });
    writableCache.set(team, p);
  }
  return p;
}

/** 팀 목록 — 그래프 프록시가 아니라 계정 라우트(/api/teams). 세션 쿠키만 필요하다.
 *  웹모드가 아니면 팀 자체가 없으므로 빈 배열로 끝낸다(호출부 분기 제거). */
export const teamsApi = {
  list: async (options?: { throwOnError?: boolean }): Promise<Array<{ id: string; name: string; role: string; status: string }>> => {
    // 셸 계정 모드: 쿠키가 없으니 `/api/teams` 는 못 부른다. 같은 목록을 계정의 그래프
    // 평면(`/api/graph/spaces`, 키로 열린다)에서 통과로로 받는다.
    try {
      if (accountMode()) {
        const data = await call("/spaces", undefined, undefined, { team: null }) as { teams?: unknown };
        if (!Array.isArray(data.teams)) throw new Error("team_list_unavailable");
        return data.teams;
      }
      if (!WEB) return [];
      const res = await webFetch("/api/teams");
      if (!res.ok) throw new Error("team_list_unavailable");
      const data = await res.json() as { teams?: unknown };
      if (!Array.isArray(data.teams)) throw new Error("team_list_unavailable");
      return data.teams;
    } catch (error) {
      if (options?.throwOnError) throw error;
      return [];
    }
  },
};

/** 가입한 커뮤니티(`/api/graph/spaces` 의 `communities`). id 는 `community:<uuid>` 이고 그대로 스페이스로 쓴다.
 *  커뮤니티 스페이스는 읽기 전용이다: 계정이 읽기만 공개 커뮤니티 그래프로 넘기고 쓰기는 거절한다. */
export const communitiesApi = {
  list: async (): Promise<Array<{ id: string; name: string; role: string }>> => {
    // 로컬 모드에는 계정이 없다. 웹과 셸 계정 모드는 같은 계정 평면(`/api/graph/spaces`)을 읽는다.
    if (!WEB && !accountMode()) return [];
    try {
      const data = await call("/spaces", undefined, undefined, { team: null }) as { communities?: unknown };
      return Array.isArray(data.communities) ? data.communities : [];
    } catch { return []; }
  },
};
export const isCommunitySpace = (team: string | null | undefined): boolean => Boolean(team && team.startsWith("community:"));

// ── 주소의 스페이스 ─────────────────────────────────────────────────────────
// 노드 주소는 자기 스페이스를 말한다: 해시 앞 쿼리 `?space=`. 값은 셋 중 하나다 —
// `personal` · 팀 id · `local`(이 기기에만 있는 그래프, 로컬 모드). 그래서 채팅에
// 붙인 주소 하나로 어시스턴트가 스페이스를 두드려 보지 않고 곧장 간다(허브가 같은
// 쿼리를 읽는다, `vinc_mcp/viewurl.space_of`).
// 근거: [[decision:a-node-address-names-its-space]]

/** 지금 보고 있는 그래프의 주소용 이름. */
export function spaceName(): string {
  if (!remote()) return "local";
  return cfg.team || "personal";
}

/** 지금 그래프의 정체(계정과 스페이스). 나침반의 저장 키와 주제 색 표가 이 값으로 스페이스를 가른다. */
export const spaceKey = (): string => `${cfg.owner}|${cfg.team || ""}`;

// 주소창의 `?space=` 가 **우리가 쓴 것인지** 가리는 표식. 스페이스 전환·모드 전환·
// 로그아웃은 상태를 바꾼 뒤 새로고침하는데, 주소창에는 옛 `?space=` 가 남아 있다.
// 그걸 밖에서 온 링크로 읽으면 방금 바꾼 스페이스를 도로 되돌린다. 탭마다 따로인
// sessionStorage 라서 새 탭에 붙인 링크는 표식이 없고 그대로 따른다.
const SPACE_MARK = "vinc.space.written";

/** 주소창의 `?space=` 를 지금 스페이스로 맞춘다 — 주소창에서 복사해도 따라가게. */
export function writeSpaceToAddress(): void {
  const q = new URLSearchParams(location.search);
  const name = spaceName();
  q.set("space", name);
  try { sessionStorage.setItem(SPACE_MARK, name); } catch {}
  history.replaceState(history.state, "",
    location.pathname + "?" + q.toString() + location.hash);
}

/** 부팅에서 첫 화면 전에 한 번. 주소가 들고 온 스페이스로 연다.
 *  못 열면 그 이유("member" | "local")를, 아니면 ""를 돌려준다 — 화면이 알린다.
 *  멤버인지는 팀 목록으로 본다: 모르는 팀을 그냥 넣으면 모든 조회가 거절되고
 *  그 값이 localStorage 에 남아 다음 실행도 망가진다. */
export async function adoptSpaceFromAddress(): Promise<"" | "member" | "local"> {
  const raw = (new URLSearchParams(location.search).get("space") || "").trim().toLowerCase();
  let mark = "";
  try { mark = sessionStorage.getItem(SPACE_MARK) || ""; } catch {}
  let refused: "" | "member" | "local" = "";
  // 로컬 모드는 고를 스페이스가 없다(셸은 밖에서 오는 링크도 받지 않는다).
  if (raw && raw !== mark && remote() && raw !== spaceName().toLowerCase()) {
    if (raw === "personal") cfg.team = "";
    else if (raw === "local") refused = "local";
    else if (isCommunitySpace(raw)) {
      const hit = communityFromAddress(raw, await communitiesApi.list());
      if (hit) { cfg.team = hit.id; cfg.teamName = hit.name; }
      else refused = "member";
    } else {
      const rows = await teamsApi.list();
      const hit = rows.find((t) => t.status === "active" && t.id.toLowerCase() === raw);
      if (hit) { cfg.team = hit.id; cfg.teamName = hit.name; }
      else refused = "member";
    }
  }
  writeSpaceToAddress();
  return refused;
}
