/** 공간(개인 · 팀 · 커뮤니티) 고르기의 상태와 판단. 그리는 쪽은 사이드바 머리(`space-switcher.ts`)다.
 *
 *  종류가 다르면 규칙도 다르다: 팀은 활성화 상태가 있어 고를 수 없는 것이 있고, 커뮤니티는 읽기
 *  전용이다. 메뉴는 종류별로 묶어 한눈에 보이고, 줄을 누르면 그 종류로 맞춘 뒤(`setKind`) 고른다(`choose`).
 *
 *  전환: 떠나는 스페이스의 완료 항목을 잠깐 보관하고(`retainBeforeSwitch`), `cfg.team` 과 그 이름을
 *  바꾸고, 화면을 통째로 다시 연다. 스페이스는 전역이라 부분 갱신하지 않는다: 어떤 표면이 다른
 *  스코프의 데이터를 들고 남으면 지금 어느 그래프를 보는지 알 수 없게 된다.
 *
 *  이 파일은 DOM 을 모른다. 의존은 전부 인자로 받는다(`app/tests/space-picker.test.mjs` 가 그대로 부른다). */

export type SpaceKind = "personal" | "team" | "community";
export type RowState = "active" | "pending" | "suspended";
export type SpaceRow = { id: string; name: string; state: RowState; current: boolean };
type TeamRow = { id: string; name: string; status: string };
type CommunityRow = { id: string; name: string };

export type SpaceDeps = {
  /** `teamName` 은 지금 스페이스의 표시 이름이다(`api.ts` `cfg.teamName`). 고를 때 함께 남긴다. */
  cfg: { team: string | null; teamName?: string };
  teamsApi: { list: (opts: { throwOnError: true }) => Promise<TeamRow[]> };
  communitiesApi: { list: () => Promise<CommunityRow[]> };
  isCommunitySpace: (team: string | null | undefined) => boolean;
  retainBeforeSwitch: () => Promise<unknown>;
  reload: () => void;
  /** 팀으로 옮기기 전에 그 팀을 열 자격을 갖춘다(`team-key.ts`). 거짓이면 옮기지 않는다. 없으면 묻지 않는다. */
  ensureTeam?: (team: string, name: string) => Promise<boolean>;
};

type Teams = { load: "loading" } | { load: "failed" } | { load: "ok"; rows: SpaceRow[] };
export type SpaceModel = {
  kind: SpaceKind; teams: Teams; communities: SpaceRow[] | null; busy: boolean;
};

/** 목록에 보이는 팀 상태. 그 밖(삭제 등)은 아예 안 보인다. */
const SHOWN: RowState[] = ["active", "pending", "suspended"];

export function kindOf(team: string | null | undefined, isCommunity: SpaceDeps["isCommunitySpace"]): SpaceKind {
  if (!team) return "personal";
  return isCommunity(team) ? "community" : "team";
}

export function teamRows(rows: TeamRow[], current: string): SpaceRow[] {
  return rows.filter((t) => (SHOWN as string[]).includes(t.status)).map((t) => ({
    id: t.id, name: t.name, state: t.status as RowState,
    current: t.status === "active" && t.id === current }));
}

export function communityRows(rows: CommunityRow[], current: string): SpaceRow[] {
  return rows.map((c) => ({ id: c.id, name: c.name, state: "active" as const, current: c.id === current }));
}

export function createSpacePicker(d: SpaceDeps) {
  const now = () => d.cfg.team || "";
  const model: SpaceModel = { kind: kindOf(d.cfg.team, d.isCommunitySpace),
    teams: { load: "loading" }, communities: null, busy: false };

  /** 지금 탭에 보일 줄. 개인은 늘 한 줄이다. */
  const rows = (kind: SpaceKind = model.kind): SpaceRow[] => {
    if (kind === "personal") return [{ id: "", name: "", state: "active", current: !now() }];
    if (kind === "team") return model.teams.load === "ok" ? model.teams.rows : [];
    return model.communities || [];
  };

  return {
    model, rows,
    /** 목록 둘을 읽는다. 한쪽이 실패해도 다른 쪽은 산다(커뮤니티 목록 실패는 탭을 감춘다). */
    async load(): Promise<void> {
      const [teams, communities] = await Promise.allSettled([
        d.teamsApi.list({ throwOnError: true }), d.communitiesApi.list()]);
      model.teams = teams.status === "fulfilled"
        ? { load: "ok", rows: teamRows(teams.value, now()) } : { load: "failed" };
      model.communities = communities.status === "fulfilled" ? communityRows(communities.value, now()) : [];
      // 받은 목록이 지금 스페이스의 이름을 말하면 남긴 이름을 맞춘다(그 사이 이름이 바뀌었을 수 있다).
      const kind = kindOf(d.cfg.team, d.isCommunitySpace);
      const here = kind === "personal" ? undefined : rows(kind).find((r) => r.current);
      if (here && here.name && d.cfg.teamName !== here.name) d.cfg.teamName = here.name;
    },
    setKind(kind: SpaceKind): void { if (!model.busy) model.kind = kind; },
    /** 줄을 눌렀다. 고를 수 없거나 이미 그 스페이스면 아무것도 안 한다. */
    async choose(id: string, repaint: () => void = () => {}): Promise<boolean> {
      if (model.busy) return false;
      const row = rows().find((r) => r.id === id);
      if (!row || row.state !== "active" || row.current) return false;
      model.busy = true;   // 보관하는 동안 두 번째 선택이 끼어 다른 스페이스로 열리지 않게
      repaint();
      // 팀 키가 있어야 하는 계정이면 여기서 받는다. 받지 못하면 보관도 전환도 하지 않는다.
      if (model.kind === "team" && d.ensureTeam && !await d.ensureTeam(id, row.name).catch(() => false)) {
        model.busy = false; repaint();
        return false;
      }
      // 떠나는 스페이스의 완료 항목을 잠깐 보관한다: 돌아왔을 때 다시 읽지 않도록.
      // 최선 노력이고 오래 걸리면 기다리지 않는다. 쓰는 때는 이 한 번뿐이다.
      await d.retainBeforeSwitch().catch(() => false);
      d.cfg.team = id;
      d.cfg.teamName = row.name;   // 개인 줄은 이름이 비어 있어 남긴 이름을 지운다
      d.reload();
      return true;
    },
  };
}

/** 지금 공간을 말하는 데 필요한 것. `name` 이 비면 그리는 쪽이 종류의 이름으로 부른다
 *  (개인은 늘 비어 있다: 화면이 "개인"이라고 부른다). */
export type CurrentSpace = { kind: SpaceKind; id: string; name: string; readOnly: boolean };

/** 지금 공간. 이름은 받은 목록에서 찾고, 못 받았거나 목록에 없으면 남긴 이름(`remembered`)을 쓴다. */
export function currentSpace(model: SpaceModel, team: string | null, remembered: string,
                             isCommunity: SpaceDeps["isCommunitySpace"]): CurrentSpace {
  const id = team || "";
  const kind = kindOf(id, isCommunity);
  if (kind === "personal") return { kind, id, name: "", readOnly: false };
  const listed = kind === "team" ? (model.teams.load === "ok" ? model.teams.rows : []) : model.communities || [];
  const name = listed.find((r) => r.id === id)?.name || remembered || "";
  return { kind, id, name, readOnly: kind === "community" };
}

/** 이보다 많으면 메뉴 맨 위에 찾기 칸을 둔다. */
export const FIND_AT = 8;

/** 메뉴에 보일 것. `teams.state` 가 `none` 이면 가입한 팀이 없는 것이고 `failed` 와 다르다. */
export type MenuView = {
  find: boolean;
  personal: boolean;
  teams: { state: "loading" | "failed" | "none" | "ok"; rows: SpaceRow[] };
  communities: SpaceRow[];
  /** 찾기에 맞는 것이 하나도 없다. */
  empty: boolean;
  /** 메뉴 바닥의 한 줄: 커뮤니티에 있으면 읽기 전용 안내, 고를 수 없는 팀이 있으면 활성화 안내. */
  foot: "readOnly" | "activation" | "";
};

/** 묶음(개인 · 팀 · 커뮤니티)과 찾기. 이름에 `query` 가 들어 있는 줄만 남긴다(대소문자 무관).
 *  `here` 는 지금 공간의 종류다(바닥 줄이 읽는다). 커뮤니티 묶음은 가입한 곳이 있을 때만 생긴다. 팀 목록 실패와 불러오는 중은 찾기와 무관하게 보인다. */
export function menuView(model: SpaceModel, query: string, personalName: string, here: SpaceKind): MenuView {
  const q = query.trim().toLowerCase();
  const hit = (name: string) => !q || name.toLowerCase().includes(q);
  const listed = model.teams.load === "ok" ? model.teams.rows : [];
  const all = model.communities || [];
  const find = 1 + listed.length + all.length > FIND_AT;
  const personal = hit(personalName);
  const state = model.teams.load === "ok" ? (listed.length || q ? "ok" : "none") : model.teams.load;
  const teams = { state, rows: listed.filter((r) => hit(r.name)) } as MenuView["teams"];
  const communities = all.filter((r) => hit(r.name));
  const empty = !personal && state !== "failed" && state !== "loading" && !teams.rows.length && !communities.length;
  const foot = here === "community" ? "readOnly"
    : !q && listed.some((t) => t.state !== "active") ? "activation" : "";
  return { find, personal, teams, communities, empty, foot };
}
