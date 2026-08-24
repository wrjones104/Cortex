import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { errorMessage, isApiError, type CaptureResult } from "../lib/api";
import { useApi } from "../lib/useApi";
import { Notice, ProjectPicker, Spinner } from "../components/ui";

const DRAFT_KEY = "cortex.draft";

/**
 * The fastest path in the app: type, pick a project, save.
 *
 * The draft is mirrored to localStorage on every keystroke. Phone browsers
 * discard background tabs freely, and losing a note you just typed is the
 * worst thing a capture tool can do.
 */
export function Capture() {
  const { api } = useApi();
  const navigate = useNavigate();

  const [text, setText] = useState(() => localStorage.getItem(DRAFT_KEY) ?? "");
  const [project, setProject] = useState("");
  const [verbatim, setVerbatim] = useState(false);
  const [projects, setProjects] = useState<string[]>([]);

  const [stage, setStage] = useState<string | null>(null);
  const [saved, setSaved] = useState<CaptureResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [duplicateOf, setDuplicateOf] = useState<string | null>(null);

  const abort = useRef<AbortController | null>(null);

  useEffect(() => {
    api.projects().then(
      (list) => setProjects(list.map((p) => p.name)),
      () => setProjects([]),
    );
  }, [api]);

  useEffect(() => {
    localStorage.setItem(DRAFT_KEY, text);
  }, [text]);

  useEffect(() => () => abort.current?.abort(), []);

  const busy = stage !== null;

  async function save(allowDuplicate = false) {
    if (!text.trim() || busy) return;

    setError(null);
    setSaved(null);
    setDuplicateOf(null);
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
      setText("");
      localStorage.removeItem(DRAFT_KEY);
      // Keep the project selected: notes usually arrive in runs.
    } catch (cause) {
      if ((cause as Error)?.name === "AbortError") return;
      if (isApiError(cause) && cause.status === 409) {
        setDuplicateOf(cause.message);
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
          <h1>Capture</h1>
          <p>Drop a thought in. The Librarian files it.</p>
        </div>
      </div>

      <div className="stack">
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="What are you thinking?"
          disabled={busy}
          aria-label="Note"
          onKeyDown={(event) => {
            // Ctrl/Cmd+Enter saves, so a long note never needs the mouse.
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
              event.preventDefault();
              void save();
            }
          }}
        />

        <div>
          <label htmlFor="project">Project</label>
          <ProjectPicker value={project} onChange={setProject} projects={projects} />
        </div>

        <div className="row" style={{ justifyContent: "space-between" }}>
          <label className="toggle">
            <input
              type="checkbox"
              checked={verbatim}
              onChange={(event) => setVerbatim(event.target.checked)}
              disabled={busy}
            />
            Keep my words exactly
          </label>

          <div className="row">
            {busy && (
              <button className="quiet" onClick={() => abort.current?.abort()} type="button">
                Cancel
              </button>
            )}
            <button
              className="primary"
              onClick={() => void save()}
              disabled={busy || !text.trim()}
              type="button"
            >
              {busy ? <Spinner label={stage === "starting" ? "Saving..." : stage!} /> : "Save"}
            </button>
          </div>
        </div>

        {error && <Notice kind="error">{error}</Notice>}

        {duplicateOf && (
          <Notice kind="warn">
            <div className="stack" style={{ gap: 8 }}>
              <span>{duplicateOf}</span>
              <div>
                <button type="button" onClick={() => void save(true)}>
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
                Filed in <strong>{saved.record.project}</strong> as{" "}
                <strong>{saved.record.title}</strong>
                {saved.record.category && ` (${saved.record.category})`}
              </span>
              {saved.warnings.map((warning) => (
                <span key={warning}>{warning}</span>
              ))}
              <div>
                <button type="button" onClick={() => navigate(`/vault/${saved.record.id}`)}>
                  Open it
                </button>
              </div>
            </div>
          </Notice>
        )}
      </div>
    </>
  );
}
