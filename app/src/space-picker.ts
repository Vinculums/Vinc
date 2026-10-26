/** 설정 > 계정의 그래프 선택 — 개인 · 팀 · 커뮤니티를 나눠 고른다.
 *
 *  예전에는 `<select>` 하나에 개인, 팀 목록, 그 아래 커뮤니티 묶음이 섞여 있었다. 종류가 다르면
 *  규칙도 다르다: 팀은 활성화 상태가 있어 고를 수 없는 것이 있고, 커뮤니티는 읽기 전용이다.
 *  그래서 위에서 종류를 보고(탭), 아래에서 그 종류 안의 하나를 고른다(줄). **탭은 둘러보기만 하고
 *  줄이 전환한다** — 탭을 눌렀다고 다른 그래프로 열리지 않는다.
 *
 *  전환은 전과 같다: 떠나는 스페이스의 완료 항목을 잠깐 보관하고(`retainBeforeSwitch`), `cfg.team` 을
 *  바꾸고, 화면을 통째로 다시 연다. 스페이스는 전역이라 부분 갱신하지 않는다 — 어떤 표면이 옛
 *  스코프의 데이터를 들고 남으면 지금 어느 그래프를 보는지 알 수 없게 된다.
 *
 *  상태와 판단(`createSpacePicker`)은 DOM 을 모르고, 그리는 쪽(`spacePickerHtml` · `mountSpacePicker`)은
 *  얇다. 의존은 전부 인자로 받는다(`app/tests/space-picker.test.mjs` 가 그대로 부른다). */

export type SpaceKind = "personal" | "team" | "community";
export type RowState = "active" | "pending" | "suspended";
export type SpaceRow = { id: string; name: string; state: RowState; current: boolean };
type TeamRow = { id: string; name: string; status: string };
type CommunityRow = { id: string; name: string };

export type SpaceCopy = {
  space: string; spacePersonal: string; spacePersonalHint: string; spaceTeams: string;
  spaceCommunities: string; spaceInUse: string; spaceNone: string; spacePending: string;
  spaceSuspended: string; spaceActivationHint: string; spaceListFailed: string;
  spaceCommunityReadOnly: string;
};

export type SpaceDeps = {
  cfg: { team: string | null };
  teamsApi: { list: (opts: { throwOnError: true }) => Promise<TeamRow[]> };
  communitiesApi: { list: () => Promise<CommunityRow[]> };
  isCommunitySpace: (team: string | null | undefined) => boolean;
  retainBeforeSwitch: () => Promise<unknown>;
  reload: () => void;
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
    },
    setKind(kind: SpaceKind): void { if (!model.busy) model.kind = kind; },
    /** 줄을 눌렀다. 고를 수 없거나 이미 그 스페이스면 아무것도 안 한다. */
    async choose(id: string, repaint: () => void = () => {}): Promise<boolean> {
      if (model.busy) return false;
      const row = rows().find((r) => r.id === id);
      if (!row || row.state !== "active" || row.current) return false;
      model.busy = true;   // 보관하는 동안 두 번째 선택이 끼어 다른 스페이스로 열리지 않게
      repaint();
      // 떠나는 스페이스의 완료 항목을 잠깐 보관한다: 돌아왔을 때 다시 읽지 않도록.
      // 최선 노력이고 오래 걸리면 기다리지 않는다. 쓰는 때는 이 한 번뿐이다.
      await d.retainBeforeSwitch().catch(() => false);
      d.cfg.team = id;
      d.reload();
      return true;
    },
  };
}

type Esc = (s: string) => string;

/** 탭 아래 한 줄 알림. 종류마다 다르다. */
export function noteOf(model: SpaceModel, U: SpaceCopy): string {
  if (model.kind === "community") return U.spaceCommunityReadOnly;
  if (model.kind !== "team") return "";
  if (model.teams.load === "failed") return U.spaceListFailed;
  if (model.teams.load !== "ok") return "";
  if (!model.teams.rows.length) return U.spaceNone;
  return model.teams.rows.some((t) => t.state !== "active") ? U.spaceActivationHint : "";
}

/** `rows` 는 `picker.rows()` 다 — 지금 탭에 보일 줄. */
export function spacePickerHtml(model: SpaceModel, rows: SpaceRow[], U: SpaceCopy, esc: Esc): string {
  const kinds: [SpaceKind, string][] = [["personal", U.spacePersonal], ["team", U.spaceTeams]];
  // 커뮤니티 탭은 가입한 곳이 있을 때만 둔다(목록을 못 받으면 칸 없이 간다). 지금 거기 있으면 둔다.
  if ((model.communities && model.communities.length) || model.kind === "community")
    kinds.push(["community", U.spaceCommunities]);
  const tabs = kinds.map(([k, label]) => `<button type="button" class="chip${k === model.kind ? " active" : ""}"
      role="tab" aria-selected="${k === model.kind}" data-kind="${k}"${model.busy ? " disabled" : ""}>${esc(label)}</button>`).join("");

  const line = (r: SpaceRow) => {
    const personal = model.kind === "personal";
    const tag = r.current ? U.spaceInUse : r.state === "pending" ? U.spacePending
      : r.state === "suspended" ? U.spaceSuspended : "";
    const off = model.busy || r.state !== "active";
    return `<button type="button" class="space-row" role="radio" aria-checked="${r.current}"
        data-space="${esc(r.id)}"${off ? " disabled" : ""}>
      <span class="space-row-text"><span class="space-row-name">${esc(personal ? U.spacePersonal : r.name)}</span>${
        personal ? `<span class="space-row-sub">${esc(U.spacePersonalHint)}</span>` : ""}</span>
      ${tag ? `<span class="space-row-tag">${esc(tag)}</span>` : ""}</button>`;
  };
  const note = noteOf(model, U);
  return `<div class="seg space-kinds" role="tablist" aria-label="${esc(U.space)}">${tabs}</div>
    <div class="space-list" role="radiogroup" aria-label="${esc(U.space)}">${rows.map(line).join("")}</div>
    ${note ? `<p class="hint space-note">${esc(note)}</p>` : ""}`;
}

/** 설정 화면 한 칸에 붙인다. 목록은 뒤늦게 와도 칸이 아직 화면에 있을 때만 그린다. */
export function mountSpacePicker(root: HTMLElement, d: SpaceDeps & { copy: SpaceCopy; esc: Esc }): void {
  const picker = createSpacePicker(d);
  const paint = () => { if (root.isConnected) root.innerHTML = spacePickerHtml(picker.model, picker.rows(), d.copy, d.esc); };
  root.addEventListener("click", (e) => {
    const hit = (e.target as HTMLElement).closest<HTMLElement>("[data-kind], [data-space]");
    if (!hit || (hit as HTMLButtonElement).disabled) return;
    if (hit.dataset.kind) { picker.setKind(hit.dataset.kind as SpaceKind); paint(); }
    else void picker.choose(hit.dataset.space || "", paint);
  });
  paint();
  void picker.load().then(paint);
}
