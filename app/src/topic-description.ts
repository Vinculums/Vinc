import type { TopicDescription } from "./api";
import type { COPY } from "./copy";

export type TopicDraft = { text: string; hash: string; uncertain?: boolean };
export function bindTopicDescription(options: {
  root: HTMLElement; description?: TopicDescription; copy: typeof COPY.ui.viewx;
  esc: (value: string) => string; drafts: Map<string, TopicDraft>; key: string;
  canEdit: boolean; current: () => boolean;
  save: (text: string, hash: string) => Promise<TopicDescription>;
  reload: () => void;
}) {
  const { root, description, copy: V, esc, drafts, key, current, save, reload } = options;
  const draft = drafts.get(key);
  const ready = description?.executed === true && description.status === "ok"
    && typeof description.text === "string" && typeof description.content_hash === "string";
  root.innerHTML = `<h3 class="hint rail-sec">${esc(V.topicDescription)}</h3>`;
  if (!ready) {
    root.insertAdjacentHTML("beforeend", `<p class="card-note" role="status">${esc(
      description ? V.detailFail : V.detailUnsupported)}</p>
      <button type="button" class="btn" data-topic-description-check>${esc(V.detailRetry)}</button>`);
    root.querySelector("[data-topic-description-check]")?.addEventListener("click", reload);
    return;
  }
  const text = description.text!;
  const hash = description.content_hash!;
  // 응답을 잃은 쓰기는 현재 원문을 읽어서 확인한다. 재전송하지 않는다.
  if (draft?.uncertain) {
    if (draft.text === text) drafts.delete(key);
    else if (draft.hash === hash) draft.uncertain = false;
  }
  const editing = drafts.get(key);
  const conflict = Boolean(editing && editing.hash !== hash);
  root.insertAdjacentHTML("beforeend", `<p class="hint">${esc(V.topicDescriptionScope)}</p>`);
  if (!editing) {
    root.insertAdjacentHTML("beforeend", `<p class="card-note" style="white-space:pre-wrap">${
      esc(text || V.topicDescriptionEmpty)}</p>${options.canEdit
      ? `<button type="button" class="btn" data-topic-description-edit>${esc(V.topicDescriptionEdit)}</button>` : ""}`);
    root.querySelector("[data-topic-description-edit]")?.addEventListener("click", () => {
      drafts.set(key, { text, hash }); bindTopicDescription(options);
      root.querySelector<HTMLTextAreaElement>("textarea")?.focus();
    });
    return;
  }
  root.insertAdjacentHTML("beforeend", `<textarea class="props-val" rows="6" maxlength="32768"
      aria-label="${esc(V.topicDescription)}" data-topic-description-input>${esc(editing.text)}</textarea>
    <p class="hint" role="status" data-topic-description-status>${esc(conflict
      ? V.topicDescriptionConflict : editing.uncertain ? V.topicDescriptionUnconfirmed : "")}</p>
    ${conflict ? `<details><summary>${esc(V.topicDescriptionCurrent)}</summary>
      <p class="card-note" style="white-space:pre-wrap">${esc(text || V.topicDescriptionEmpty)}</p></details>` : ""}
    <button type="button" class="btn" data-topic-description-save
      ${conflict || editing.uncertain || !options.canEdit ? "disabled" : ""}>${esc(V.topicDescriptionSave)}</button>
    <button type="button" class="btn" data-topic-description-cancel>${esc(V.topicDescriptionCancel)}</button>
    <button type="button" class="btn" data-topic-description-check>${esc(V.topicDescriptionCheck)}</button>`);
  const input = root.querySelector<HTMLTextAreaElement>("textarea")!;
  const status = root.querySelector<HTMLElement>("[data-topic-description-status]")!;
  input.addEventListener("input", () => { editing.text = input.value; });
  root.querySelector("[data-topic-description-check]")?.addEventListener("click", reload);
  root.querySelector("[data-topic-description-cancel]")?.addEventListener("click", () => {
    drafts.delete(key); bindTopicDescription(options);
  });
  root.querySelector("[data-topic-description-save]")?.addEventListener("click", async () => {
    if (!current() || conflict || editing.uncertain || !options.canEdit) return;
    const submitted = { ...editing };
    const controls = [...root.querySelectorAll<HTMLButtonElement | HTMLTextAreaElement>("button,textarea")];
    controls.forEach(control => { control.disabled = true; });
    status.textContent = V.edSaving;
    try {
      const result = await save(submitted.text, submitted.hash);
      if (result.executed && result.status === "ok" && result.text === submitted.text) {
        if (drafts.get(key)?.text === submitted.text) drafts.delete(key);
        if (current()) reload();
        return;
      }
      if (result.status === "unconfirmed") editing.uncertain = true;
      if (!current()) return;
      controls.forEach(control => { control.disabled = false; });
      status.textContent = result.status === "conflict" ? V.topicDescriptionConflict
        : result.status === "unconfirmed" ? V.topicDescriptionUnconfirmed
        : result.status === "unavailable" ? V.detailUnavailable : V.edSaveFail;
      root.querySelector<HTMLButtonElement>("[data-topic-description-save]")!.disabled =
        result.status === "conflict" || Boolean(editing.uncertain);
    } catch (error) {
      // 접근 거절은 쓰지 않은 것이 확실하다. 그 밖의 통신 실패는 결과를 먼저 읽는다.
      const denied = [401, 403, 404].includes(Number((error as { status?: number }).status));
      if (!denied) editing.uncertain = true;
      if (!current()) return;
      controls.forEach(control => { control.disabled = false; });
      status.textContent = denied ? V.detailUnavailable : V.topicDescriptionUnconfirmed;
      root.querySelector<HTMLButtonElement>("[data-topic-description-save]")!.disabled = Boolean(editing.uncertain);
    }
  });
}
