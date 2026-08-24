import { useMemo, useState } from "react";
import { BrowserRouter, NavLink, Navigate, Route, Routes } from "react-router-dom";
import { CortexApi, loadConnection, type Connection } from "./lib/api";
import { ApiContext } from "./lib/useApi";
import { Setup } from "./screens/Setup";
import { Capture } from "./screens/Capture";
import { Vault } from "./screens/Vault";
import { RecordDetail } from "./screens/RecordDetail";
import { Settings } from "./screens/Settings";

export default function App() {
  const [connection, setConnection] = useState<Connection | null>(() => loadConnection());

  const value = useMemo(
    () =>
      connection
        ? {
            api: new CortexApi(connection),
            baseUrl: connection.baseUrl,
            disconnect: () => setConnection(null),
          }
        : null,
    [connection],
  );

  if (!value) return <Setup onConnected={setConnection} />;

  return (
    <ApiContext.Provider value={value}>
      <BrowserRouter>
        <div className="app">
          <nav className="nav">
            <div className="brand">Cortex</div>
            <NavLink to="/capture">
              <PenIcon />
              <span>Capture</span>
            </NavLink>
            <NavLink to="/vault">
              <SearchIcon />
              <span>Vault</span>
            </NavLink>
            <NavLink to="/settings">
              <GearIcon />
              <span>Settings</span>
            </NavLink>
          </nav>

          <main className="main">
            <Routes>
              <Route path="/" element={<Navigate to="/capture" replace />} />
              <Route path="/capture" element={<Capture />} />
              <Route path="/vault" element={<Vault />} />
              <Route path="/vault/:id" element={<RecordDetail />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="*" element={<Navigate to="/capture" replace />} />
            </Routes>
          </main>
        </div>
      </BrowserRouter>
    </ApiContext.Provider>
  );
}

const iconProps = {
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

function PenIcon() {
  return (
    <svg {...iconProps}>
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
    </svg>
  );
}

function SearchIcon() {
  return (
    <svg {...iconProps}>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  );
}

function GearIcon() {
  return (
    <svg {...iconProps}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z" />
    </svg>
  );
}
