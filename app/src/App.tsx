import { useCallback, useEffect, useMemo, useState } from "react";
import { SidecarClient } from "./lib/api";
import { startSidecar, stopSidecar, type SidecarInfo } from "./lib/ipc";
import { AppProvider, type ScreenId, type Session } from "./lib/app";
import { Login } from "./screens/Login";
import { Onboarding } from "./screens/Onboarding";
import { Shell } from "./components/Shell";

type Phase = "boot" | "onboarding" | "login" | "ready";

const DATADIR_KEY = "vinc.dataDir";
const credPathFor = (dataDir: string) => `${dataDir}/auth.json`;

export function App() {
  const [phase, setPhase] = useState<Phase>("boot");
  const [info, setInfo] = useState<SidecarInfo | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [screen, setScreen] = useState<ScreenId>("recall");
  const [bootError, setBootError] = useState<string | null>(null);

  // 부팅: 사이드카 기동 → 첫 실행(온보딩) vs 재실행(로그인) 판별.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const si = await startSidecar();
        if (!alive) return;
        setInfo(si);
        const savedDir = localStorage.getItem(DATADIR_KEY);
        setPhase(savedDir ? "login" : "onboarding");
      } catch (e) {
        if (!alive) return;
        setBootError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const client = useMemo(() => (info ? new SidecarClient(info) : null), [info]);

  const onOnboarded = useCallback((dataDir: string) => {
    localStorage.setItem(DATADIR_KEY, dataDir);
    setPhase("login");
  }, []);

  const onLogin = useCallback(() => {
    const dataDir = localStorage.getItem(DATADIR_KEY) ?? "";
    setSession({ dataDir, driver: info?.driver ?? null });
    setPhase("ready");
  }, [info]);

  const logout = useCallback(() => {
    setSession(null);
    setPhase("login");
  }, []);

  // 창 종료 시 사이드카 회수(백업 경로 — Rust 도 Destroyed 에서 정리).
  useEffect(() => {
    const onUnload = () => void stopSidecar();
    window.addEventListener("beforeunload", onUnload);
    return () => window.removeEventListener("beforeunload", onUnload);
  }, []);

  if (bootError) {
    return (
      <div className="boot boot--error">
        <h1>사이드카 기동 실패</h1>
        <p className="mono">{bootError}</p>
        <p>데이터 플레인(api/server.py)이 없으면 조회가 강등된다. 로그를 확인하라.</p>
      </div>
    );
  }

  if (phase === "boot" || !client) {
    return (
      <div className="boot">
        <h1>Vinc</h1>
        <p>오프라인 데이터 마이닝 · 사이드카 준비 중…</p>
      </div>
    );
  }

  if (phase === "onboarding") {
    return <Onboarding onDone={onOnboarded} credPathFor={credPathFor} />;
  }

  if (phase === "login") {
    const dataDir = localStorage.getItem(DATADIR_KEY) ?? "";
    return <Login credPath={credPathFor(dataDir)} onLogin={onLogin} />;
  }

  return (
    <AppProvider
      value={{
        client,
        session: session!,
        screen,
        navigate: setScreen,
        logout,
      }}
    >
      <Shell />
    </AppProvider>
  );
}
