import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useApi } from "../lib/useApi";
import { useSync } from "../lib/useSync";
import { pending, remove, type QueuedCapture } from "../lib/queue";
import { Empty, Notice, Spinner } from "../components/ui";
import { PendingHero } from "../components/Illustrations";
import { localTime } from "../lib/time";

/**
 * What is waiting to file.
 */
export function Pending() {
  const { api } = useApi();
  const navigate = useNavigate();
  const { online, syncing, sync, refresh } = useSync(api);

  const [items, setItems] = useState<QueuedCapture[] | null>(null);
  const [result, setResult] = useState<string | null>(null);

  const load = useCallback(async () => {
    setItems(await pending());
    await refresh();
  }, [refresh]);

  useEffect(() => {
    void load();
  }, [load]);

  async function syncNow() {
    const outcome = await sync();
    const parts: string[] = [];
    if (outcome.stored) parts.push(`${outcome.stored} filed`);
    if (outcome.alreadyStored) parts.push(`${outcome.alreadyStored} already there`);
    if (outcome.duplicates) parts.push(`${outcome.duplicates} were duplicates`);
    if (outcome.failed) parts.push(`${outcome.failed} still waiting`);
    setResult(parts.length ? parts.join(", ") : "Nothing to send.");
    await load();
  }

  async function discard(id: string) {
    await remove(id);
    await load();
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Waiting to File 📦</h1>
          <p>Captured locally on this device, queued for vault sync.</p>
        </div>
      </div>

      <div className="stack">
        {!online && (
          <Notice kind="warn">
            Device is offline. Notes are safely saved locally and will auto-sync when connected.
          </Notice>
        )}
        {result && <Notice kind="info">{result}</Notice>}

        {items === null && <Spinner label="Checking offline queue..." />}

        {items?.length === 0 && (
          <>
            <Empty
              title="All caught up!"
              hint="Every captured note has been successfully filed in your vault."
              illustration={<PendingHero size={88} />}
            />
            <div className="row" style={{ justifyContent: "center" }}>
              <button className="primary bouncy-btn" onClick={() => navigate("/capture")} type="button">
                Capture a Note ✨
              </button>
            </div>
          </>
        )}

        {items && items.length > 0 && (
          <>
            <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
              <span>
                <strong>{items.length}</strong> {items.length === 1 ? "note" : "notes"} in local queue
              </span>
              <button
                className="primary bouncy-btn send-btn"
                onClick={() => void syncNow()}
                disabled={syncing || !online}
                type="button"
              >
                {syncing ? <Spinner label="Sending..." /> : "Sync All Now 🚀"}
              </button>
            </div>

            <div className="list">
              {items.map((item) => (
                <div key={item.id} className="item thread-card bouncy-card">
                  <div className="card-main-content">
                    <div className="card-top-row">
                      <div className="body-text card-title" style={{ fontSize: "0.92rem", fontWeight: 500 }}>
                        {item.text.length > 240 ? `${item.text.slice(0, 240)}...` : item.text}
                      </div>
                    </div>
                    <div className="card-bottom-row">
                      <div className="card-tags">
                        <span className="chip project-chip">{item.project ? `📁 ${item.project}` : "unfiled"}</span>
                      </div>
                      <div className="card-stats">
                        {item.attempts > 0 && (
                          <>
                            <span>{item.attempts} {item.attempts === 1 ? "try" : "tries"}</span>
                            <span className="dot">·</span>
                          </>
                        )}
                        <span>{localTime(item.created_at)}</span>
                      </div>
                    </div>
                    {item.last_error && (
                      <div className="pitch" style={{ color: "var(--warn)", marginTop: 2, fontSize: "0.82rem" }}>
                        ⚠️ {item.last_error}
                      </div>
                    )}
                  </div>
                  <button
                    className="quiet card-delete-btn bouncy-btn"
                    aria-label="Discard this note"
                    onClick={() => void discard(item.id)}
                    type="button"
                  >
                    &times;
                  </button>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </>
  );
}
