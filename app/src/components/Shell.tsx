import { useState } from "react";
import { NAV, useApp } from "../lib/app";
import { Icon } from "./Icon";
import { Button, StatusMark } from "./primitives";
import { isTauri } from "../lib/ipc";
import { IngestScreen } from "../screens/Ingest";
import { RecallScreen } from "../screens/Recall";
import { DomainsScreen } from "../screens/Domains";
import { GraphScreen } from "../screens/Graph";
import { SearchScreen } from "../screens/Search";
import { PackScreen } from "../screens/PackExport";

const SCREENS = {
  ingest: IngestScreen,
  recall: RecallScreen,
  domains: DomainsScreen,
  graph: GraphScreen,
  search: SearchScreen,
  pack: PackScreen,
} as const;

export function Shell() {
  const { screen, navigate, session, logout } = useApp();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const Screen = SCREENS[screen];
  const title = NAV.find((n) => n.id === screen)?.label ?? "Vinc";

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="sidebar__brand">
          <Icon name="sparkle" size={20} />
          Vinc
        </div>
        <nav className="nav">
          {NAV.map((item) => (
            <button
              key={item.id}
              className="nav__item"
              aria-current={screen === item.id ? "page" : undefined}
              onClick={() => navigate(item.id)}
            >
              <Icon name={item.icon as any} size={18} />
              {item.label}
            </button>
          ))}
        </nav>
        <div className="sidebar__foot">
          <StatusMark
            state="success"
            label={session.driver ? `스토어 · ${session.driver}` : "오프라인 준비됨"}
          />
          <span>{isTauri ? "Tauri 셸" : "데모 모드(브라우저)"}</span>
        </div>
      </aside>

      <main className="screen">
        <header className="chrome">
          <div className="chrome__title">{title}</div>
          <div className="chrome__actions">
            <StatusMark state="idle" label="세션" />
            <div className="popover">
              <Button
                variant="ghost"
                size="sm"
                icon="settings"
                aria-expanded={settingsOpen}
                onClick={() => setSettingsOpen((v) => !v)}
              >
                설정
              </Button>
              {settingsOpen && (
                <div className="popover__panel" role="dialog" aria-label="설정">
                  <strong>설정</strong>
                  <div className="stack">
                    <label className="row" style={{ justifyContent: "space-between" }}>
                      <span>다크 모드</span>
                      <ThemeToggle />
                    </label>
                    <div className="muted" style={{ fontSize: "var(--fs-sm)" }}>
                      데이터 디렉토리
                      <div className="mono">{session.dataDir || "(미설정)"}</div>
                    </div>
                    <div className="muted" style={{ fontSize: "var(--fs-sm)" }}>
                      MCP 관찰 패널 · 로그(D11) — 후속
                    </div>
                    <Button variant="ghost" size="sm" icon="lock" onClick={logout}>
                      로그아웃
                    </Button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </header>
        <div className="screen__body">
          <Screen />
        </div>
      </main>
    </div>
  );
}

function ThemeToggle() {
  const [dark, setDark] = useState(
    () => document.documentElement.getAttribute("data-theme") === "dark",
  );
  return (
    <Button
      variant="ghost"
      size="sm"
      icon={dark ? "half-disc" : "disc"}
      onClick={() => {
        const next = !dark;
        setDark(next);
        document.documentElement.setAttribute("data-theme", next ? "dark" : "light");
      }}
    >
      {dark ? "다크" : "라이트"}
    </Button>
  );
}
