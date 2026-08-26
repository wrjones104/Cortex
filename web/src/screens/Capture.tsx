import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { errorMessage, isApiError, type CaptureResult } from "../lib/api";
import { useApi } from "../lib/useApi";
import { useSync } from "../lib/useSync";
import { enqueue } from "../lib/queue";
import { isOnline } from "../lib/sync";
import { Notice, ProjectPicker, Spinner } from "../components/ui";
import { CaptureHero } from "../components/Illustrations";

const DRAFT_KEY = "cortex.draft";

/**
 * The fastest path in the app.
 *
 * Offline-first, but not offline-only: with a connection the note is filed
 * straight away so you see what it was called and where it went, which is
 * worth having on a desktop. Without one — or if the connection drops
 * mid-request — it goes to the queue and returns immediately.
 *
 * The rule that matters: a capture is never lost. It is in the queue or it is
 * in the vault, never in flight and forgotten.
 */
export function Capture() {
  const { api } = useApi();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const { pendingCount, online, syncing, sync } = useSync(api);

  const [text, setText] = useState(() => localStorage.getItem(DRAFT_KEY) ?? "");
  const [project, setProject] = useState("");
  const [verbatim, setVerbatim] = useState(false);
  const [projects, setProjects] = useState<string[]>([]);

  const [stage, setStage] = useState<string | null>(null);
  const [saved, setSaved] = useState<CaptureResult | null>(null);
  const [queued, setQueued] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [duplicateOf, setDuplicateOf] = useState<string | null>(null);

  const abort = useRef<AbortController | null>(null);
  const consumedShare = useRef<string | null>(null);

  useEffect(() => {
    api.projects().then(
      (list) => setProjects(list.map((p) => p.name)),
      () => setProjects([]),
    );
  }, [api]);

  useEffect(() => {
    localStorage.setItem(DRAFT_KEY, text);
  }, [text]);

  // Arriving from Android's share sheet: the manifest routes the share here
  // with the shared text as query parameters.
  useEffect(() => {
    const shared = [params.get("title"), params.get("text"), params.get("url")]
      .filter((part) => part && part.trim())
      .join("\n\n");
    if (!shared || consumedShare.current === shared) return;

    consumedShare.current = shared;
    setText((current) => (current.trim() ? `${current}\n\n${shared}` : shared));
    setParams(new URLSearchParams(), { replace: true });
  }, [params, setParams]);

  useEffect(() => () => abort.current?.abort(), []);

  const busy = stage !== null;

  function clearDraft() {
    setText("");
    localStorage.removeItem(DRAFT_KEY);
  }

  async function queueIt(reason: string) {
    await enqueue({ text, project: project.trim() || null, verbatim });
    clearDraft();
    setQueued(`Kept on this device — ${reason} It files itself when Cortex is reachable.`);
    void sync();
  }

  async function save(allowDuplicate = false) {
    if (!text.trim() || busy) return;

    setError(null);
    setSaved(null);
    setQueued(null);
    setDuplicateOf(null);

    // No signal: straight to the queue. No attempt, no waiting.
    if (!isOnline()) {
      await queueIt("you are offline.");
      return;
    }

    setStage("starting");
    const controller = new AbortController();
    abort.current = controller;

    try {
      const result = await api.captureStreaming(
        {
          text,
          project: project.trim() || null,
          verbatim,
          allow_duplicate: allowDuplicate,
        },
        (_stage, message) => setStage(message),
        controller.signal,
      );

      setSaved(result);
      clearDraft();
    } catch (cause) {
      if ((cause as Error)?.name === "AbortError") return;

      if (isApiError(cause) && cause.status === 409) {
        setDuplicateOf(cause.message);
      } else if (isApiError(cause) && (cause.status === 0 || cause.status >= 500)) {
        await queueIt("Cortex did not answer.");
      } else {
        setError(errorMessage(cause));
      }
    } finally {
      setStage(null);
      abort.current = null;
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Capture ⚡</h1>
          <p>Drop a thought, task, link or note. The Librarian files it.</p>
        </div>
      </div>

      <div className="stack">
        {!online && (
          <Notice kind="warn">
            Offline mode active. Anything you capture stays safely on this device and files itself when Cortex reconnects.
          </Notice>
        )}

        {pendingCount > 0 && (
          <div className="notice info row" style={{ justifyContent: "space-between" }}>
            <span>
              📦 {pendingCount} note{pendingCount === 1 ? "" : "s"} waiting to file
            </span>
            <div className="row">
              <button className="quiet bouncy-btn" onClick={() => navigate("/pending")} type="button">
                View queue
              </button>
              <button
                className="quiet bouncy-btn"
                onClick={() => void sync()}
                disabled={syncing || !online}
                type="button"
              >
                {syncing ? <Spinner label="Syncing" /> : "Sync now 🚀"}
              </button>
            </div>
          </div>
        )}

        <div className="capture-card card stack">
          <textarea
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder="What's on your mind? Drop ideas, research snippets, reminders..."
            disabled={busy}
            aria-label="Note"
            className="capture-textarea"
            onKeyDown={(event) => {
              if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                event.preventDefault();
                void save();
              }
            }}
          />

          <div>
            <label htmlFor="project">Project / Section</label>
            <ProjectPicker value={project} onChange={setProject} projects={projects} />
          </div>

          <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
            <label className="toggle">
              <input
                type="checkbox"
                checked={verbatim}
                onChange={(event) => setVerbatim(event.target.checked)}
                disabled={busy}
              />
              <span>Keep my words exactly (verbatim)</span>
            </label>

            <div className="row">
              {busy && (
                <button className="quiet bouncy-btn" onClick={() => abort.current?.abort()} type="button">
                  Cancel
                </button>
              )}
              <button
                className="primary bouncy-btn send-btn"
                onClick={() => void save()}
                disabled={busy || !text.trim()}
                type="button"
              >
                {busy ? <Spinner label={stage === "starting" ? "Filing..." : stage!} /> : "Save Note ✨"}
              </button>
            </div>
          </div>
        </div>

        {error && <Notice kind="error">{error}</Notice>}
        {queued && <Notice kind="ok">{queued}</Notice>}

        {duplicateOf && (
          <Notice kind="warn">
            <div className="stack" style={{ gap: 8 }}>
              <span>{duplicateOf}</span>
              <div>
                <button className="bouncy-btn" type="button" onClick={() => void save(true)}>
                  Save it anyway
                </button>
              </div>
            </div>
          </Notice>
        )}

        {saved && (
          <Notice kind="ok">
            <div className="stack" style={{ gap: 8 }}>
              <span>
                🎉 Filed in <strong>{saved.record.project}</strong> as{" "}
                <strong>{saved.record.title}</strong>
                {saved.record.category && ` (${saved.record.category})`}
              </span>
              {saved.warnings.map((warning) => (
                <span key={warning}>{warning}</span>
              ))}
              <div>
                <button className="bouncy-btn primary" type="button" onClick={() => navigate(`/vault/${saved.record.id}`)}>
                  Open in Vault &rarr;
                </button>
              </div>
            </div>
          </Notice>
        )}

        {!text && !saved && !queued && (
          <div className="hero-hint-box">
            <CaptureHero size={88} />
            <p style={{ margin: "10px 0 0", color: "var(--muted)", fontSize: "0.88rem" }}>
              💡 <em>Tip: Press <strong>Ctrl + Enter</strong> (or <strong>Cmd + Enter</strong>) to quickly save your note!</em>
            </p>
          </div>
        )}
      </div>
    </>
  );
}
