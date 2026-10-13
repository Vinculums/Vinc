import { cfg, conversionApi } from "./api";
import { COPY } from "./copy";
import { esc } from "./components";
import { TopicConversion, type Checkpoint } from "./topic-conversion-state";
import { retainBeforeSwitch } from "./view-retained";

const namedBlockers = new Set([
  "boundary_relation", "bridge_crosses_topic", "external_reference", "incoming_reference",
  "external_attachments_not_supported", "search_index_repair_required",
  "entity_topic_membership_not_supported", "owner_vocabulary_sidecar_not_supported",
  "unclassified_tombstone_topic_history", "document_integrity_failed", "target_not_empty",
]);

export function mountTopicConversion(el: HTMLElement): () => void {
  let alive = true;
  const U = COPY.ui.settingsUi;
  void conversionApi.capabilities().then(async cap => {
    if (!alive || !el.isConnected || (!cap.enabled && !cap.pending) || cfg.team) return;
    const key = "vinc.topic-conversion." + cap.subject;
    let saved: Checkpoint | null = null;
    try {
      const value = JSON.parse(sessionStorage.getItem(key) || "null");
      if (value && typeof value.id === "string" && /^[a-f0-9-]{36}$/.test(value.id) &&
          typeof value.topic === "string" && typeof value.name === "string" && /^[a-f0-9]{64}$/.test(value.fingerprint)) saved = value;
    } catch { /* An invalid checkpoint never authorizes a write. */ }
    saved ??= cap.pending ? { id: cap.pending.id, name: cap.pending.name,
      topic: cap.pending.topic, fingerprint: cap.pending.fingerprint } : null;
    const flow = new TopicConversion(conversionApi, value => {
      if (value) sessionStorage.setItem(key, JSON.stringify(value));
      else sessionStorage.removeItem(key);
    }, saved);
    async function act(fn: () => Promise<void>) {
      const pending = fn(); paint(); await pending;
      if (alive && !cfg.team) paint();
    }
    function paint() {
      if (!alive || !el.isConnected) return;
      const reserved = !!flow.checkpoint;
      const state = flow.run?.state;
      const done = state === "active";
      el.innerHTML = `<label>${esc(U.conversionTitle)}</label>
        <p class="hint">${esc(U.conversionIntro)}</p>
        <label for="conversion-topic">${esc(U.conversionTopic)}</label>
        <input id="conversion-topic" value="${esc(flow.topic)}" ${reserved || flow.busy ? "disabled" : ""} placeholder="project/topic" maxlength="512">
        <label for="conversion-name">${esc(U.conversionName)}</label>
        <input id="conversion-name" value="${esc(flow.name)}" ${reserved || flow.busy ? "disabled" : ""} maxlength="120">
        <p class="hint">${esc(U.conversionLimits)}</p>
        ${flow.check ? `<p>${esc(U.conversionCounts(flow.check.documents, Object.entries(flow.check.counts).filter(([kind]) => ["Concept","Record","Decision","Episode","LearnerPattern","Bridge"].includes(kind)).reduce((n,[,count]) => n+count,0)))}</p>
          ${flow.check.blockers.length ? `<p role="alert">${esc(U.conversionBlocked)}: ${flow.check.blockers.map(code => esc(U.conversionBlocker(namedBlockers.has(code) ? code : ""))).join("; ")}</p>` : ""}` : ""}
        ${reserved ? `<p role="status" aria-live="polite">${esc(flow.busy ? U.conversionWorking : done ? U.conversionDone : state === "graph_committed" ? U.conversionVerifying : state === "committing" ? U.conversionPending : U.conversionPrepared)}</p>` : ""}
        ${flow.error ? `<p role="alert">${reserved ? esc(U.conversionFailed) + " " : ""}${esc(U.conversionError(flow.error))}</p>` : ""}
        ${!reserved ? `<p><button class="btn" id="conversion-check" ${flow.busy || !flow.topic.trim() ? "disabled" : ""}>${esc(U.conversionCheck)}</button></p>
          ${flow.check?.ready ? `<p><label><input id="conversion-consent" type="checkbox"> ${esc(U.conversionConsent)}</label></p>
            <button class="btn" id="conversion-prepare" ${flow.busy || !flow.name.trim() ? "disabled" : ""}>${esc(U.conversionPrepare)}</button>` : ""}` : done ?
          `<button class="btn" id="conversion-open">${esc(U.conversionOpen)}</button>` :
          `<p><button class="btn" id="conversion-resume" ${flow.busy ? "disabled" : ""}>${esc(state === "prepared" ? U.conversionMove : U.conversionResume)}</button>
          <button class="btn" id="conversion-cancel" ${flow.busy ? "disabled" : ""}>${esc(U.conversionCancel)}</button></p>`}`;
      const topic = el.querySelector<HTMLInputElement>("#conversion-topic")!;
      const name = el.querySelector<HTMLInputElement>("#conversion-name")!;
      for (const input of [topic, name]) input.addEventListener("input", () => {
        const focus = input.id, cursor = input.selectionStart;
        flow.edit(topic.value, name.value); paint();
        const next = el.querySelector<HTMLInputElement>("#" + focus)!;
        next.focus(); next.setSelectionRange(cursor,cursor);
      });
      el.querySelector("#conversion-check")?.addEventListener("click", () => void act(() => flow.preflight()));
      el.querySelector("#conversion-prepare")?.addEventListener("click", () => {
        const consent = el.querySelector<HTMLInputElement>("#conversion-consent")?.checked ?? false;
        void act(() => flow.reserve(consent));
      });
      el.querySelector("#conversion-resume")?.addEventListener("click", () => void act(() => flow.resume()));
      el.querySelector("#conversion-cancel")?.addEventListener("click", () => void act(() => flow.cancel()));
      el.querySelector("#conversion-open")?.addEventListener("click", async () => {
        if (!flow.run?.team || flow.busy) return;
        await retainBeforeSwitch().catch(() => false);
        cfg.team = flow.run.team; sessionStorage.removeItem(key); location.reload();
      });
    }
    paint();
    if (saved) await act(() => flow.status());
  }).catch(() => { /* Feature switches expose no conversion UI before readiness. */ });
  return () => { alive = false; };
}
