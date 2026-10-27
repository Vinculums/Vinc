import { cfg, conversionApi } from "./api";
import { COPY } from "./copy";
import { esc } from "./components";
import { TopicConversion, type Checkpoint } from "./topic-conversion-state";
import { retainBeforeSwitch } from "./view-retained";
import { loaderInline } from "./vinc-loaders.js";

const namedBlockers = new Set([
  "boundary_relation", "bridge_crosses_topic", "external_reference", "incoming_reference",
  "external_attachments_not_supported", "search_index_repair_required",
  "entity_topic_membership_not_supported", "owner_vocabulary_sidecar_not_supported",
  "unclassified_tombstone_topic_history", "document_integrity_failed", "target_not_empty",
  "conversion_inventory_limit",
  "conversion_inventory_pending", "conversion_inventory_busy", "conversion_inventory_unknown",
  "conversion_inventory_stale", "conversion_inventory_failed", "conversion_scope_inconsistent",
  "conversion_bounded_executor_required", "empty_topic",
  "document_owner_missing", "document_tombstone_conflicts_with_live_doc",
  "existing_tombstone_history_not_supported", "merge_rejection_history_not_supported",
  "merge_rejection_topic_membership_unknown", "owner_conventions_require_separate_decision",
  "external_resource_reference_not_supported", "external_source_file_not_supported",
  "owner_vocabulary_not_supported", "opaque_props", "external_document_successor",
  "document_chunks_not_intact", "shared_document_owner", "chunk_vector_join_mismatch",
]);

export function mountTopicConversion(el: HTMLElement): () => void {
  let alive = true;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const U = COPY.ui.settingsUi;
  void conversionApi.capabilities().then(async cap => {
    if (!alive || !el.isConnected || (!cap.enabled && !cap.pending) || cfg.team) return;
    const key = "vinc.topic-conversion." + cap.subject;
    let saved: Checkpoint | null = null;
    try {
      const value = JSON.parse(sessionStorage.getItem(key) || "null");
      if (value && typeof value.id === "string" && /^[a-f0-9-]{36}$/.test(value.id) &&
          typeof value.topic === "string" && typeof value.name === "string" && /^[a-f0-9]{64}$/.test(value.fingerprint) &&
          (value.conventions_policy === undefined || ["retain", "move_original"].includes(value.conventions_policy))) saved = value;
    } catch { /* An invalid checkpoint never authorizes a write. */ }
    if (cap.pending) saved = { id: cap.pending.id, name: cap.pending.name,
      topic: cap.pending.topic, fingerprint: cap.pending.fingerprint,
      conventions_policy: cap.pending.conventionsPolicy ?? "retain" };
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
      clearTimeout(timer);
      const reserved = !!flow.checkpoint;
      const state = flow.run?.state;
      const done = state === "active";
      const check = flow.check;
      const history = flow.run?.progress?.retained_personal_history ?? check?.retained_personal_history;
      const retained = Object.entries(history ?? {}).filter(([key, value]) =>
        ["_Tombstone", "_DocTombstone", "_EdgeTombstone", "_MergeRejection"].includes(key) &&
        Number.isSafeInteger(value) && value > 0).reduce((total, [, value]) => total + value, 0);
      const preserved = check?.preserved_references;
      const count = (value: unknown) => typeof value === "number" && Number.isSafeInteger(value) && value > 0 ? value : 0;
      const outsideLinks = count(preserved?.external_references);
      const externalAttachments = count(preserved?.external_attachments);
      const documentLabels = U.conversionDocumentEvidence;
      const evidence = check ? Object.entries({ ...check.document_ownership, ...check.document_missing_evidence })
        .filter(([, count]) => Number.isSafeInteger(count) && count > 0)
        .map(([key, count]) => {
          const label = documentLabels[key as keyof typeof documentLabels];
          return typeof label === "string" && label ? `${label}: ${count}` : "";
        }).filter(Boolean) : [];
      const countsKnown = check && check.counts !== null && check.documents !== null &&
        check.counts_known !== false &&
        (check.counts_known === true || !check.blockers.includes("conversion_inventory_limit"));
      el.innerHTML = `<label>${esc(U.conversionTitle)}</label>
        <p class="hint">${esc(U.conversionIntro)}</p>
        <label for="conversion-topic">${esc(U.conversionTopic)}</label>
        <input id="conversion-topic" value="${esc(flow.topic)}" ${reserved || flow.busy ? "disabled" : ""} placeholder="project/topic" maxlength="512">
        <label for="conversion-name">${esc(U.conversionName)}</label>
        <input id="conversion-name" value="${esc(flow.name)}" ${reserved || flow.busy ? "disabled" : ""} maxlength="120">
        <p class="hint">${esc(U.conversionLimits)}</p>
        <p><label><input id="conversion-conventions" type="checkbox" ${flow.conventionsPolicy === "move_original" ? "checked" : ""} ${reserved || flow.busy ? "disabled" : ""}> ${esc(U.conversionConventions)}</label></p>
        ${flow.conventionsPolicy === "move_original" ? `<p class="hint">${esc(U.conversionConventionsEffect)}</p>` : ""}
        ${retained ? `<p class="hint">${esc(U.conversionRetainedHistory(retained))}</p>` : ""}
        ${outsideLinks ? `<p class="hint">${esc(U.conversionPreservedReferences(outsideLinks))}</p>` : ""}
        ${externalAttachments ? `<p class="hint">${esc(U.conversionPreservedAttachments(externalAttachments))}</p>` : ""}
        ${evidence.length ? `<details><summary>${esc(U.conversionDocumentChecks)}</summary><ul>${evidence.map(line => `<li>${esc(line)}</li>`).join("")}</ul></details>` : ""}
        ${!reserved && flow.busy ? `<p>${loaderInline(U.conversionInspecting)}</p><p class="hint">${esc(U.conversionInspectingNote)}</p>` : ""}
        ${check ? `${countsKnown ? `<p>${esc(U.conversionCounts(check.documents!, Object.entries(check.counts!).filter(([kind]) => ["Concept","Record","Decision","Episode","LearnerPattern","Bridge"].includes(kind)).reduce((n,[,count]) => n+count,0)))}</p>` : ""}
          ${check.blockers.length ? `<p role="alert">${esc(U.conversionBlocked)}: ${check.blockers.map(code => esc(U.conversionDependencyMessage(namedBlockers.has(code) ? code : "") || U.conversionInventoryMessage(code) || U.conversionBlocker(namedBlockers.has(code) ? code : ""))).join("; ")}</p>` : ""}` : ""}
        ${reserved ? `<p role="status" aria-live="polite">${esc(flow.busy ? U.conversionWorking : done ? U.conversionDone : state === "graph_committed" ? U.conversionVerifying : state === "committing" ? U.conversionPending : U.conversionPrepared)}</p>` : ""}
        ${!done && flow.run?.progress?.batches !== undefined ? `<p class="hint">${esc(U.conversionBatches(flow.run.progress.batches))}</p>` : ""}
        ${flow.error ? `<p role="alert">${reserved ? esc(U.conversionFailed) + " " + esc(U.conversionError(flow.error)) : esc(U.conversionInventoryMessage(flow.error) || U.conversionCheckFailed)}</p>` : ""}
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
      el.querySelector<HTMLInputElement>("#conversion-conventions")?.addEventListener("change", event => {
        flow.chooseConventions((event.currentTarget as HTMLInputElement).checked ? "move_original" : "retain"); paint();
      });
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
      if (!flow.busy && !flow.error && (state === "committing" || state === "graph_committed")) {
        timer = setTimeout(async () => {
          if (!alive || !el.isConnected || cfg.team) return;
          await flow.status();
          if (alive && !cfg.team) paint();
        }, 5000);
      }
    }
    paint();
    if (saved) await act(() => flow.status());
  }).catch(() => { /* Feature switches expose no conversion UI before readiness. */ });
  return () => { alive = false; clearTimeout(timer); };
}
