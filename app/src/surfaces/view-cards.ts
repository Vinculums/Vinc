// 지도 위 노드 카드(관계 카드)와 연결 목록: 카드의 줄(상세 열기 · 연결 · Ask · 유틸리티)과, 넓은 열의 연결 절도 그리는 연결 줄.
// 그래프 표면(`view.ts`)이 `ViewHost` 를 넘겨 만든다. 주제 카드와 상세 레일의 내용은 표면이 그린다.
import { api, cfg, viewScope, spaceName, type NodeLinks } from "../api";
import { COPY } from "../copy";
import { esc, confirmDestructive } from "../components";
import { absoluteViewUrl } from "../view-url";
import { openShareSheet } from "../sheets";
import { wireAsk, askRowHTML, nodeAskItems, chev, SHARE_DEPTH } from "./view-ask";
import { openFloatPanel, closeFloatPanel } from "../float-panel";
import { icon } from "../icons";
import { loaderInline } from "../vinc-loaders.js";
import { detailState, detailFailure, detailMessage, type DetailState } from "../topic-detail";
import type { N3, ViewHost } from "./view-host";

/** 복사되는 값 — 한 마디 + 주소, **한 줄**로.
 *
 *  줄을 안 나눈다: 붙여넣는 자리가 대부분 한 줄짜리 입력이라 나누면 거기서 잘린다.
 *  `@` 도 안 붙인다 — Figma 가 붙이는 것은 그쪽 클라이언트에서 그 토큰이 첨부를 뜻해서고,
 *  여기서는 아무 뜻이 없으며 오히려 `vinc_graph(url=…)` 인자에 실려 주소를 깨뜨린다. */
function promptFor(url: string): string {
  return `${COPY.ui.viewx.promptLead} ${url}`;
}

/** 복사 버튼 한 벌 — 레일·문서 카드·영역이 같은 처리를 쓴다.
 *
 *  복사가 막혀도 **오류로 키우지 않는다**: 주소는 어차피 주소창에 그대로 있고,
 *  클립보드 권한은 사용자 환경의 일이다. settings 의 MCP 주소 복사와 같은 태도다.
 *  버튼 라벨을 잠깐 바꾸는 것으로 끝낸다 — 알림을 띄우면 복사 한 번이 사건이 된다. */
function bindCopyLink(btn: Element | null, make: () => string): void {
  if (!btn) return;
  // span 에 role=button 을 달았으므로 Enter·Space 를 직접 받아야 한다 — 진짜 button
  // 이 공짜로 주던 것이고, 안 달면 키보드만 쓰는 사람에게는 이 기능이 없는 것과 같다.
  btn.addEventListener("keydown", (e) => {
    const k = (e as KeyboardEvent).key;
    if (k === "Enter" || k === " ") { e.preventDefault(); (btn as HTMLElement).click(); }
  });
  btn.addEventListener("click", (e) => {
    e.stopPropagation();          // 행의 다른 일로 새면 안 된다(레일에서는 노드 선택)
    const tip = COPY.ui.viewx;
    const el = btn as HTMLElement;
    const label = el.textContent || "";
    // **글귀를 그 자리에서 바꾼다.** 여태 되먹임이 툴팁(`data-tip`)뿐이었는데, 툴팁은
    // 이미 떠 있으면 갱신되지 않고 손을 뗐다 다시 올려야 보였다 — 눌렀는데 아무 일도
    // 안 일어난 것처럼 읽힌다. 쓰기를 기다리지 않고 **먼저** 바꾸고, 실패하면 되돌린다:
    // 로컬 클립보드 쓰기는 사실상 즉시라 낙관적으로 보여 주는 편이 정직하다.
    el.textContent = tip.copyLinkDone;
    window.setTimeout(() => { el.textContent = label; }, 1400);
    void navigator.clipboard.writeText(make()).catch(() => {
      el.textContent = tip.copyLinkFail;
    });
  });
}

/** 상세를 그리지 못했을 때의 한 줄. 실패면 다시 시도 단추를 단다. 카드 · 주제 상세 · 레일의 수치와 근거 절이 같이 쓴다. */
export function detailErrorHTML(state: DetailState) {
  return `<p class="card-note" role="status">${esc(detailMessage(state, COPY.ui.viewx))}</p>`
    + (state === "failed" ? `<button type="button" class="btn" data-detail-retry>${esc(COPY.ui.viewx.detailRetry)}</button>` : "");
}

/** 표면이 쥐는 카드의 손잡이. */
export type Cards = {
  /** 노드 카드를 지도 카드의 좌상단에 띄운다. 주제(`domain:`)면 주제 카드를 띄운다. */
  openRelsCard(nd: N3): Promise<void>;
  /** 연결 목록의 마크업. 카드의 연결 절과 넓은 열의 연결 절이 같이 쓴다. */
  relsHTML(links: NodeLinks): string;
  /** 그 목록의 줄을 배선한다: 이름은 그 노드로 가고, 관계 이름은 관계 상세를, `+N more` 는 레일을 연다. */
  wireRels(box: HTMLElement, nd: N3, links: NodeLinks): void;
};

/** 노드 카드와 연결 목록을 그 표면에 묶는다. 표면의 상태(초점 · 노드 맥락 메모 · 레일 · 층)에는 `host` 로만 닿는다. */
export function createCards(host: ViewHost): Cards {
  const { detail } = host;

  /** 노드 카드 — 디자인 정본 `Vinc Node Card.dc.html` (2a Final) 그대로.
   *
   *      Details and source   3 documents  >     오른쪽 캐럿 = 문서 열로 나간다
   *      Links                -> 4 · <- 1  v     아래 캐럿 = 카드 안에서 펼친다(기본 펼침)
   *        -> Reciprocal rank fusion  requires
   *      Ask an assistant     [Copy prompt v]    이름 붙은 프롬프트 셋
   *      Zoom                 [ - | + ]
   *      Copy link  Share            Delete      조용한 유틸리티
   *
   *  깊이 선택(1/2/3)이 사라진 자리를 **이름 붙은 프롬프트 셋**이 대신한다. 숫자는
   *  크기를 말해 주지 않아 늘 경고를 달고 다녀야 했는데, 이름은 무엇을 담아 가는지
   *  스스로 말한다("내 문서 안에서만" · "연결 N개, 근거 포함").
   *
   *  인과 원뿔은 줄에서 뺐다 — 노드를 고르는 순간 `focusOnMap` 이 이미 과거·미래
   *  폐포를 점등하므로 카드가 다시 부를 일이 아니다. */
  async function openRelsCard(nd: N3) {
    if (nd.id.startsWith("domain:")) { host.openTopicCard(nd); return; }
    // 카드의 **집은 지도 카드의 좌상단**이다(발주자 지시 2026-09-09,
    // `수정항목/노드 클릭 플로팅 모달.JPG` 가 그 자리를 가리킨다). 여태는 노드의
    // 화면좌표 옆이었고, 그것이 낸 문제 둘:
    //   ① 자리가 매번 다르다 — 카드를 읽으려면 눈이 카드를 먼저 찾아야 했다.
    //   ② 읽는 면이 오른쪽으로 온 뒤(R1) 오른쪽 노드를 누르면 카드가 본문 위에 겹쳤다.
    // 고정 자리는 그 둘을 함께 없앤다. 노드와의 연결은 좌표가 아니라 **인과 원뿔의
    // 점등**이 말한다(focusOnMap 이 이미 그 일을 한다) — 카드가 노드 옆에 있어야
    // 한다는 요구는 그 점등이 없던 시절의 것이다.
    // 사용자가 끌어 옮기면 그 자리를 기억한다(float-panel.ts) — 고정은 집일 뿐이고
    // 손이 옮긴 자리를 되돌리지 않는다.
    const home = host.homeCardPos();
    const V = COPY.ui.viewx;
    const card = openFloatPanel({
      title: nd.label, x: home.x, y: home.y, closeLabel: V.railClose,
    });
    // 머리의 표식 — 지도가 그리는 마크 그대로(채운 원 = 개념), 색은 그 노드의 주제 색.
    // 나침반의 **그 표**(`compass.colour`, 사이드바 그래프 트리도 읽는다)에서 읽는다. 표를 여기서 따로 지으면
    // 넣는 집합(조상 경로 포함 여부)이 갈려 순위가 밀리고, 같은 주제가 카드와 지도에서 두 색이 된다.
    // `data-domain` 은 테마 전환(retheme)이 이 표식을 다시 칠할 때 읽는다.
    const tint = nd.domain ? host.topicColour(nd.domain.trim()) || undefined : undefined;
    card.el.querySelector(".float-title")?.insertAdjacentHTML("beforebegin",
      `<span class="float-mark" aria-hidden="true"${
        nd.domain ? ` data-domain="${esc(nd.domain.trim())}"` : ""}${
        tint ? ` style="color:${tint}"` : ""}>${icon("dot", 11)}</span>`);

    card.body.innerHTML = `
      <div class="card-rows">
        ${host.hasHistory() ? `<button class="btn" type="button" data-detail-back>${esc(V.readerBack)}</button>` : ""}
        <button type="button" class="card-row" id="c-open">
          <span class="row-name">${V.cardOpen}</span>
          <span class="row-meta" id="c-docs"></span>
          ${chev("right")}
        </button>
        <div class="card-sec">
          <button type="button" class="card-row" id="c-links" aria-expanded="false">
            <span class="row-name row-name--strong">${V.cardLinks}</span>
            <span class="row-meta" id="c-linkmeta"></span>
            ${chev("down")}
          </button>
          <div class="card-links" id="c-rels" hidden><p class="card-note">${loaderInline(V.sourceLoading)}</p></div>
        </div>
        ${askRowHTML()}
        <div class="card-util">
          <button type="button" id="c-copylink">${V.cardCopyLink}</button>
          <button type="button" id="c-share">${V.cardShare}</button>
          <button type="button" id="c-connect" data-tip="${esc(COPY.author.connectTip)}">${
            esc(COPY.author.connect)}</button>
          <button type="button" id="c-del" class="card-util-del">${V.cardDelete}</button>
        </div>
      </div>`;

    const q = <T extends HTMLElement>(s: string) => card.body.querySelector<T>(s);
    host.wireDetailBack(card.body, false);
    const url = (depth: number) =>
      absoluteViewUrl({ kind: "node", id: nd.id }, { depth }, spaceName());

    // 행동은 응답과 무관하므로 **지금** 묶는다. 늦게 묶으면 응답이 늦거나 카드가
    // 교체될 때 통째로 안 묶인다(실측으로 한 번 물렸다 — 관계가 통째로 비었다).
    // 다시 누르면 닫힌다. 여는 손짓과 닫는 손짓이 다른 자리에 있으면(레일의 ✕)
    // 방금 연 것을 되돌리려고 눈이 화면을 가로질러야 한다.
    q("#c-open")!.addEventListener("click", () => {
      const openHere = !detail.hidden && host.lastNode()?.id === nd.id;
      if (openHere) host.closeRail(); else host.railFor(nd);
    });
    bindCopyLink(q("#c-copylink"), () => promptFor(url(1)));
    q("#c-share")!.addEventListener("click", () =>
      openShareSheet({ seed: nd.id, depth: SHARE_DEPTH }));
    q("#c-connect")!.addEventListener("click", () => void import("../connect")
      .then((m) => m.startConnect({ id: nd.id, title: nd.label })));
    q("#c-del")!.addEventListener("click", async () => {
      const guard = api.previewDeleteNode(cfg.owner, nd.id);   // 확인 창과 함께 미리본다
      if (!await confirmDestructive(V.delNodeAsk(nd.label), V.delConfirm, V.delCancel)) return;
      try {
        const res = await api.deleteNode(cfg.owner, nd.id, await guard);
        if (!res.executed) throw new Error(res.note || res.error || "delete failed");
        closeFloatPanel();
        host.closeRail();
        void host.showLevel();
      } catch {
        // 지워진 것은 없지만 조용히 넘기지 않는다: 상세 열의 삭제와 같은 문장을 카드 바닥에 남긴다.
        // 유틸 줄의 단추는 좁아서 문장을 받지 못하므로 그 아래 한 줄이다. 다시 누르면 다시 시도한다.
        let note = card.body.querySelector<HTMLElement>("[data-del-fail]");
        if (!note) {
          note = document.createElement("p");
          note.className = "card-note";
          note.dataset.delFail = "";
          note.setAttribute("role", "alert");
          card.body.querySelector(".card-rows")?.append(note);
        }
        note.textContent = V.delFail;
      }
    });
    // Links 는 기본 접힘. 펼치는 것도 접는 것도 카드 안의 일이라 캐럿이 아래를 본다.
    const linksBox = q("#c-rels")!;
    q("#c-links")!.addEventListener("click", (e) => {
      const b = e.currentTarget as HTMLElement;
      const open = b.getAttribute("aria-expanded") !== "true";
      b.setAttribute("aria-expanded", String(open));
      linksBox.hidden = !open;
    });

    // 프롬프트 메뉴 — 카드면 위의 카드면이다(유리 아님). 디자인 주석의 이유:
    // 지도 위 카드 위에 또 유리를 얹으면 글자가 배경을 통과한다.
    const setCounts = wireAsk(card.body, () => card.el.isConnected,
      (deg, docs) => nodeAskItems(nd, deg, docs));
    const scopeKey = viewScope().key;

    try {
      const res = await host.nodeContextOnce(nd.id);
      if (!card.el.isConnected || !host.isLive() || host.focusedId() !== nd.id || viewScope().key !== scopeKey) return;
      const state = detailState(res);
      if (state !== "ready") {
        linksBox.hidden = false; linksBox.innerHTML = detailErrorHTML(state);
        linksBox.querySelector("[data-detail-retry]")?.addEventListener("click", () => {
          host.dropNodeContext(); void openRelsCard(nd);
        });
        return;
      }
      card.setTitle(res.title || nd.label);
      const docs = (res.excerpts || []).length;
      const dq = q("#c-docs");
      if (dq && docs) dq.textContent = V.cardDocs(docs);
      const links = res.links;
      if (!links?.executed) {
        linksBox.hidden = false; linksBox.innerHTML = detailErrorHTML("failed");
        linksBox.querySelector("[data-detail-retry]")?.addEventListener("click", () => {
          host.dropNodeContext(); void openRelsCard(nd);
        });
        return;
      }
      const mq = q("#c-linkmeta");
      if (mq) mq.textContent = V.cardLinkMeta(links.out, links.in);
      linksBox.innerHTML = relsHTML(links);
      wireRels(linksBox, nd, links);
      setCounts(links.degree + links.attached, docs);
    } catch (error) {
      if (card.el.isConnected && host.isLive() && host.focusedId() === nd.id && viewScope().key === scopeKey) {
        linksBox.hidden = false; linksBox.innerHTML = detailErrorHTML(detailFailure(error));
        linksBox.querySelector("[data-detail-retry]")?.addEventListener("click", () => {
          host.dropNodeContext(); void openRelsCard(nd);
        });
      }
    }
  }

  /** 연결 목록 — 방향 화살표 · 상대 이름 · 관계 이름.
   *
   *  관계 이름은 `COPY.ui.ask.relTag` 가 이미 사람 말로 갖고 있다("derives from").
   *  타입 코드를 그대로 내던 것을 바꾼 것이라 Ask 와 카드가 같은 어휘를 쓴다.
   *  근거가 있는 관계는 **그 이름 자체가** 근거로 들어가는 자리다 — 디자인에 별도
   *  버튼이 없고, 줄을 누르는 것은 이동이므로 둘이 과녁을 나눠 가진다. */
  function relsHTML(links: NodeLinks): string {
    const V = COPY.ui.viewx;
    if (links.items.length === 0) return `<p class="card-note">${V.relsNone}</p>`;
    const TAG = COPY.ui.ask.relTag;
    const arrow = (d: string) => (d === "out" ? "→" : d === "in" ? "←" : "·");
    const rest = links.degree + links.attached - links.items.length;
    return links.items.map((it, i) => {
      const ev = it.evidence && it.dir !== "att";
      // 관계 이름은 **관계 상세로 가는 문**이다 — 근거가 없어도 연다(거기서 지운다).
      // 부착(att)은 지우는 길이 없는 관계라 문을 내지 않는다.
      const open = it.dir !== "att";
      return `
        <button type="button" class="link-row" data-i="${i}">
          <span class="link-dir" aria-hidden="true">${arrow(it.dir)}</span>
          <span class="link-title">${esc(it.title)}</span>
          <span class="link-tag${ev ? " link-tag--ev" : ""}${open ? " link-tag--open" : ""}"${
            open ? ` data-ev="${i}" data-tip="${ev ? V.relEvidence : esc(COPY.author.linkDetailTip)}"` : ""}>${
            esc(TAG[`${it.type}:${it.dir}`] || it.type.toLowerCase())}</span>
        </button>`;
    }).join("")
      + (links.truncated && rest > 0
        ? `<button type="button" class="link-more">${V.relsMore(rest)}</button>` : "");
  }

  function wireRels(box: HTMLElement, nd: N3, links: NodeLinks) {
    box.querySelectorAll<HTMLElement>(".link-row").forEach((r) =>
      r.addEventListener("click", (e) => {
        const it = links.items[Number(r.dataset.i)];
        if ((e.target as HTMLElement).closest<HTMLElement>(".link-tag--open")) {
          e.stopPropagation();
          const here = (detail.contains(box) && detail.querySelector("h2")?.textContent) || nd.label;
          void host.openEdgeDetail(it.dir === "out"
            ? { source: nd.id, target: it.id, type: it.type, srcTitle: here, dstTitle: it.title }
            : { source: it.id, target: nd.id, type: it.type, srcTitle: it.title, dstTitle: here });
          return;
        }
        // 문서 열 안의 연결 줄이면 **열 안에서** 옮겨 간다(지금 크기 그대로). 카드의
        // 줄이면 지금처럼 지도에서 그 노드로 간다.
        if (detail.contains(box)) void openInPane(it.id, it.domain || undefined);
        else void host.focusNode(it.id);
      }));
    // `+N more` 는 레일을 연다 — 카드는 좁고, 전부 보려면 넓은 자리가 필요하다.
    box.querySelector(".link-more")?.addEventListener("click", () => host.railFor(nd));
  }

  /** 문서 열 안에서 다른 노드로 — 카드는 띄우지 않고 열의 크기는 그대로 둔다. */
  async function openInPane(id: string, domain?: string) {
    const nd = await host.focusNode(id, domain, false);
    if (nd && host.isLive()) host.railFor(nd);
  }

  return { openRelsCard, relsHTML, wireRels };
}
