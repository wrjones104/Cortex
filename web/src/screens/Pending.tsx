import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useApi } from "../lib/useApi";
import { useSync } from "../lib/useSync";
import { pending, remove, type QueuedCapture } from "../lib/queue";
import { Empty, Notice, Spinner } from "../components/ui";
import { localTime } from "../lib/time";

/**
 * What is waiting to file.
 *
 * A queue you cannot see is a queue you cannot trust. Everything captured
 * offline is listed here with why it has not landed yet, and can be retried
 * or discarded deliberately.
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
          <h1>Waiting to file</h1>
          <p>Captured on this device, not yet in the vault.</p>
        </div>
      </div>

      <div className="stack">
        {!online && <Notice kind="warn">Offline. These will send when you are back.</Notice>}
        {result && <Notice kind="info">{result}</Notice>}

        {items === null && <Spinner label="Loading..." />}

        {items?.length === 0 && (
          <>
            <Empty title="Nothing waiting" hint="Everything you captured has been filed." />
            <div className="row" style={{ justifyContent: "center" }}>
              <button onClick={() => navigate("/capture")} type="button">
                Back to capture
              </button>
            </div>
          </>
        )}

        {items && items.length > 0 && (
          <>
            <div className="row">
              <button
                className="primary"
                onClick={() => void syncNow()}
                disabled={syncing || !online}
                type="button"
              >
                {syncing ? <Spinner label="Sending..." /> : "Send them now"}
              </button>
            </div>

            <div className="list">
              {items.map((item) => (
                <div key={item.id} className="item thread-item">
                  <div className="thread-open" style={{ cursor: "default" }}>
                    <div className="body-text" style={{ fontSize: "0.94rem" }}>
                      {item.text.length > 240 ? `${item.text.slice(0, 240)}...` : item.text}
                    </div>
                    <div className="meta">
                      <span>{item.project ?? "unfiled"}</span>
                      <span className="dot">|</span>
                      <span>{localTime(item.created_at)}</span>
                      {item.attempts > 0 && (
                        <>
                          <span className="dot">|</span>
                          <span>
                            {item.attempts} attempt{item.attempts === 1 ? "" : "s"}
                          </span>
                        </>
                      )}
                    </div>
                    {item.last_error && (
                      <div className="pitch" style={{ color: "var(--warn)" }}>
                        {item.last_error}
                      </div>
                    )}
                  </div>
                  <button
                    className="quiet thread-delete"
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
