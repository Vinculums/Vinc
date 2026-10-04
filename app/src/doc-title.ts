import { COPY } from "./copy";

/** 문서 제목은 저장·목록·독자·확인창에서 같은 공백 규칙과 빈 제목 표기를 쓴다. */
export const docTitle = (title?: string | null): string =>
  (title || "").trim() || COPY.ui.viewx.edTitlePlaceholder;
