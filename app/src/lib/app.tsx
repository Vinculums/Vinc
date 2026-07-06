import { createContext, useContext } from "react";
import type { SidecarClient } from "./api";

// 스크린 식별자 = 8화면([[vinc.d.index]] §2).
export type ScreenId =
  | "ingest"
  | "recall"
  | "domains"
  | "graph"
  | "search"
  | "pack";

export type Session = {
  dataDir: string;
  driver?: string | null;
};

export type AppCtx = {
  client: SidecarClient;
  session: Session;
  screen: ScreenId;
  navigate: (s: ScreenId) => void;
  logout: () => void;
};

const Ctx = createContext<AppCtx | null>(null);

export const AppProvider = Ctx.Provider;

export function useApp(): AppCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useApp must be used within AppProvider");
  return v;
}

// 사이드바 내비 항목 — 6 마이닝/내보내기 뷰(로그인·온보딩은 게이트라 제외).
export const NAV: { id: ScreenId; label: string; icon: string }[] = [
  { id: "ingest", label: "적재", icon: "ingest" },
  { id: "recall", label: "레코드 탐색", icon: "recall" },
  { id: "domains", label: "데이터셋 분포", icon: "domains" },
  { id: "graph", label: "그래프 마이닝", icon: "graph" },
  { id: "search", label: "검색", icon: "search" },
  { id: "pack", label: "팩 내보내기", icon: "pack-export" },
];
