import { useEffect, useState } from "react";
import { api } from "./lib/api";
import { useRoute } from "./lib/router";
import type { AppSettings } from "./lib/types";
import { History } from "./pages/History";
import { Home } from "./pages/Home";
import { NewResearch } from "./pages/NewResearch";
import { Settings } from "./pages/Settings";
import { Workspace } from "./pages/Workspace";

export default function App() {
  const route = useRoute();
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [settingsErr, setSettingsErr] = useState<string | null>(null);
  useEffect(() => { api.settings().then(setSettings).catch((e) => setSettingsErr(e.message)); }, []);
  const nav = (name: string, href: string, label: string) => (
    <a href={href} aria-current={route.name === name ? "page" : undefined}>{label}</a>
  );
  return (
    <div className="app">
      <header className="topbar">
        <a className="brand" href="#/">Multi-Agent Research <span>&amp; Report Generator</span></a>
        <nav>
          {nav("new", "#/new", "New research")}
          {nav("history", "#/history", "History")}
          {nav("settings", "#/settings", "Settings")}
        </nav>
        {settings && (
          <span className={`mode mode-${settings.mode}`} title={settings.mode === "not_configured" ? (settings.llm.problem ?? settings.search.problem ?? "") : ""}>
            {settings.mode === "live" ? `Live: ${settings.llm.provider_name}` : settings.mode === "demo" ? "DEMO MODE — SIMULATED AI"
              : !settings.llm.configured ? "LLM not configured" : "Search not configured"}
          </span>
        )}
        {settingsErr && <span className="mode mode-off" title={settingsErr}>Backend offline</span>}
      </header>
      {route.name === "home" && <Home settings={settings} />}
      {route.name === "new" && <NewResearch settings={settings} />}
      {route.name === "history" && <History />}
      {route.name === "settings" && <Settings settings={settings} error={settingsErr} />}
      {route.name === "research" && <Workspace key={route.id} id={route.id} tab={route.tab} />}
    </div>
  );
}
