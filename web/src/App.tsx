import { useMemo, useState } from "react";
import { BrowserRouter, NavLink, Navigate, Route, Routes } from "react-router-dom";
import { CortexApi, loadConnection, type Connection } from "./lib/api";
import { ApiContext, useApi } from "./lib/useApi";
import { useSync } from "./lib/useSync";
import { Setup } from "./screens/Setup";
import { Capture } from "./screens/Capture";
import { Vault } from "./screens/Vault";
import { Chat } from "./screens/Chat";
import { Create } from "./screens/Create";
import { Pending } from "./screens/Pending";
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
        <Shell />
      </BrowserRouter>
    </ApiContext.Provider>
  );
}

/**
 * Inside the router, so the nav can show how many captures are still waiting
 * to file. A queue you cannot see is a queue you cannot trust.
 */
function Shell() {
  const { api } = useApi();
  const { pendingCount } = useSync(api);

  return (
    <div className="app">
          <nav className="nav">
            <div className="brand">Cortex</div>
            <NavLink to="/capture">
              <PenIcon />
              <span>Capture</span>
              {pendingCount > 0 && (
                <span className="badge" aria-label={`${pendingCount} waiting to file`}>
                  {pendingCount}
                </span>
              )}
            </NavLink>
            <NavLink to="/vault">
              <SearchIcon />
              <span>Vault</span>
            </NavLink>
            <NavLink to="/create">
              <SparkIcon />
              <span>Create</span>
            </NavLink>
            <NavLink to="/chat">
              <ChatIcon />
              <span>Chat</span>
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
              <Route path="/create" element={<Create />} />
              <Route path="/chat" element={<Chat />} />
              <Route path="/chat/:id" element={<Chat />} />
              <Route path="/pending" element={<Pending />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="*" element={<Navigate to="/capture" replace />} />
            </Routes>
          </main>
    </div>
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

function SparkIcon() {
  return (
    <svg {...iconProps}>
      <path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M18.4 5.6l-2.8 2.8M8.4 15.6l-2.8 2.8" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function ChatIcon() {
  return (
    <svg {...iconProps}>
      <path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.9 8.9 0 0 1-3.8-.9L3 20.5l1.6-4.9A8.4 8.4 0 0 1 12 3.1a8.4 8.4 0 0 1 9 8.4Z" />
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
