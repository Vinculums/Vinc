// vinc-loaders.js(디자인 허브 사본)의 타입. 사본과 달리 이 파일은 앱이 쓴다.
export function loaderBlock(kind: "mark" | "branch", label?: string,
  opts?: { fill?: boolean; className?: string }): string;
export function loaderInline(label?: string, opts?: { tag?: string; className?: string }): string;
export function loaderRows(label: string, opts?: { count?: number; compact?: boolean }): string;
export function loaderTrack(label: string, done?: number, total?: number, opts?: { steps?: number[] }): string;
export function setTrack(el: Element | null, done: number, total: number, text?: string): void;
