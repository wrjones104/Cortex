import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage, type Generation, type Project } from "../lib/api";
import { useApi } from "../lib/useApi";
import { Empty, Notice, ProjectPicker, Spinner } from "../components/ui";
import { localTime } from "../lib/time";

/**
 * Brainstorming, and taking only the parts you liked.
 *
 * The prototype banked a whole generation as one record. Here every idea is a
 * separate checkbox and a separate record, and nothing is filed until you say
 * which.
 */
export function Create() {
  const { api } = useApi();

  const [prompt, setPrompt] = useState("");
  const [mode, setMode] = useState<"options" | "freeform">("options");
  const [count, setCount] = useState(4);
  const [project, setProject] = useState("");
  const [projects, setProjects] = useState<Project[]>([]);

  const [history, setHistory] = useState<Generation[]>([]);
  const [current, setCurrent] = useState<Generation | null>(null);
  const [chosen, setChosen] = useState<Set<number>>(new Set());
  const [verbatim, setVerbatim] = useState(true);

  const [status, setStatus] = useState<string | null>(null);
  const [produced, setProduced] = useState(0);
  const [preview, setPreview] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [splitting, setSplitting] = useState(false);
  const [banking, setBanking] = useState(false);

  const abort = useRef<AbortController | null>(null);
  const busy = status !== null;

  const refresh = useCallback(async () => {
    try {
      setHistory(await api.generations());
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }, [api]);

  useEffect(() => {
    void refresh();
    api.projects().then(setProjects, () => setProjects([]));
  }, [api, refresh]);

  useEffect(() => () => abort.current?.abort(), []);

  function show(generation: Generation) {
    setCurrent(generation);
    setChosen(new Set());
    setResult(null);
    setError(null);
  }

  async function run() {
    if (!prompt.trim() || busy) return;

    setError(null);
    setResult(null);
    setPreview("");
    setProduced(0);
    setStatus("Starting");

    const controller = new AbortController();
    abort.current = controller;

    try {
      const done = await api.brainstorm(
        {
          prompt,
          mode,
          count,
          project: project.trim() || null,
        },
        {
          onStatus: setStatus,
          onToken: (text) => {
            setProduced((n) => n + text.length);
            // In options mode the tokens are JSON, so show the character count
            // rather than the text. In freeform they are prose worth reading.
            if (mode === "freeform") setPreview((current) => current + text);
          },
        },
        controller.signal,
      );

      show(await api.generation(done.generation_id));
      await refresh();
    } catch (cause) {
      if ((cause as Error)?.name !== "AbortError") setError(errorMessage(cause));
    } finally {
      setStatus(null);
      setPreview("");
      abort.current = null;
    }
  }

  async function runSplit() {
    if (!current) return;
    setSplitting(true);
    setError(null);
    try {
      const updated = await api.splitGeneration(current.id);
      setCurrent(updated);
      setChosen(new Set());
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setSplitting(false);
    }
  }

  async function bankChosen() {
    if (!current || chosen.size === 0) return;
    setBanking(true);
    setError(null);
    setResult(null);
    try {
      const outcome = await api.bankIdeas(current.id, {
        ordinals: [...chosen].sort((a, b) => a - b),
        project: project.trim() || null,
        verbatim,
      });

      const parts: string[] = [];
      if (outcome.banked.length) {
        parts.push(
          `Filed ${outcome.banked.length}: ${outcome.banked.map((r) => r.title).join(", ")}`,
        );
      }
      for (const skip of outcome.skipped) parts.push(`Skipped #${skip.ordinal}: ${skip.reason}`);
      setResult(parts.join(" — "));

      setCurrent(await api.generation(current.id));
      setChosen(new Set());
      await refresh();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBanking(false);
    }
  }

  async function remove(id: number) {
    try {
      await api.deleteGeneration(id);
      if (current?.id === id) setCurrent(null);
      await refresh();
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }

  function toggle(ordinal: number) {
    setChosen((current) => {
      const next = new Set(current);
      if (next.has(ordinal)) next.delete(ordinal);
      else next.add(ordinal);
      return next;
    });
  }

  const takeable = current?.ideas.filter((i) => !i.banked) ?? [];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Create</h1>
          <p>Brainstorm, then keep only what you liked.</p>
        </div>
      </div>

      <div className="stack">
        <textarea
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          placeholder="What do you want to explore?"
          disabled={busy}
          aria-label="Prompt"
          style={{ minHeight: 100 }}
        />

        <div className="row" style={{ gap: 14 }}>
          <div className="seg" role="group" aria-label="Mode">
            <button
              className={mode === "options" ? "on" : ""}
              onClick={() => setMode("options")}
              disabled={busy}
              type="button"
            >
              Alternatives
            </button>
            <button
              className={mode === "freeform" ? "on" : ""}
              onClick={() => setMode("freeform")}
              disabled={busy}
              type="button"
            >
              Ramble
            </button>
          </div>

          {mode === "options" && (
            <label className="toggle" style={{ gap: 6 }}>
              How many
              <input
                type="number"
                min={1}
                max={10}
                value={count}
                onChange={(event) => setCount(Number(event.target.value))}
                disabled={busy}
                style={{ width: 62 }}
                aria-label="How many alternatives"
              />
            </label>
          )}
        </div>

        <div>
          <label htmlFor="create-project">Project</label>
          <ProjectPicker value={project} onChange={setProject} projects={projects.map((p) => p.name)} />
        </div>

        <div className="row">
          <button
            className="primary"
            onClick={() => void run()}
            disabled={busy || !prompt.trim()}
            type="button"
          >
            {busy ? <Spinner label={status ?? "Working"} /> : "Generate"}
          </button>
          {busy && (
            <>
              <button className="quiet" onClick={() => abort.current?.abort()} type="button">
                Stop
              </button>
              {produced > 0 && (
                <span style={{ fontSize: "0.82rem", color: "var(--faint)" }}>
                  {produced.toLocaleString()} characters
                </span>
              )}
            </>
          )}
        </div>

        {busy && mode === "freeform" && preview && (
          <div className="card body-text" style={{ maxHeight: 260, overflowY: "auto" }}>
            {preview}
          </div>
        )}

        {error && <Notice kind="error">{error}</Notice>}
        {result && <Notice kind="ok">{result}</Notice>}

        {current && (
          <section className="stack">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <h2>{current.ideas.length > 0 ? "Pick what to keep" : "Your ramble"}</h2>
              {current.mode === "freeform" && (
                <button onClick={() => void runSplit()} disabled={splitting} type="button">
                  {splitting ? <Spinner label="Splitting..." /> : "Split into ideas"}
                </button>
              )}
            </div>

            {current.ideas.length === 0 && current.output && (
              <div className="card body-text">{current.output}</div>
            )}

            <div className="list">
              {current.ideas.map((idea) => (
                <label
                  key={idea.ordinal}
                  className={`item idea ${idea.banked ? "banked" : ""} ${
                    chosen.has(idea.ordinal) ? "picked" : ""
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={chosen.has(idea.ordinal)}
                    onChange={() => toggle(idea.ordinal)}
                    disabled={idea.banked || banking}
                    aria-label={`Keep ${idea.title}`}
                  />
                  <div style={{ minWidth: 0 }}>
                    <h3>
                      {idea.title}
                      {idea.banked && <span className="chip" style={{ marginLeft: 8 }}>filed</span>}
                    </h3>
                    {idea.pitch && <div className="pitch">{idea.pitch}</div>}
                    <details>
                      <summary>Detail</summary>
                      <div className="body-text">{idea.detail}</div>
                    </details>
                  </div>
                </label>
              ))}
            </div>

            {takeable.length > 0 && (
              <div className="row" style={{ justifyContent: "space-between" }}>
                <label className="toggle">
                  <input
                    type="checkbox"
                    checked={!verbatim}
                    onChange={(event) => setVerbatim(!event.target.checked)}
                    disabled={banking}
                  />
                  Let the Librarian tidy it first
                </label>
                <button
                  className="primary"
                  onClick={() => void bankChosen()}
                  disabled={banking || chosen.size === 0}
                  type="button"
                >
                  {banking ? (
                    <Spinner label="Filing..." />
                  ) : (
                    `Keep ${chosen.size || ""} ${chosen.size === 1 ? "idea" : "ideas"}`.trim()
                  )}
                </button>
              </div>
            )}
          </section>
        )}

        {history.length > 0 && (
          <section className="stack">
            <h2>Earlier</h2>
            <div className="list">
              {history.map((generation) => (
                <div key={generation.id} className="item thread-item">
                  <button className="thread-open" onClick={() => void showById(generation.id)}>
                    <h3 className="truncate">{generation.prompt}</h3>
                    <div className="meta">
                      <span>{generation.mode === "options" ? "alternatives" : "ramble"}</span>
                      <span className="dot">|</span>
                      <span>{generation.ideas.length} ideas</span>
                      <span className="dot">|</span>
                      <span>{generation.ideas.filter((i) => i.banked).length} kept</span>
                      <span className="dot">|</span>
                      <span>{localTime(generation.created_at)}</span>
                    </div>
                  </button>
                  <button
                    className="quiet thread-delete"
                    aria-label={`Delete generation ${generation.id}`}
                    onClick={() => void remove(generation.id)}
                    type="button"
                  >
                    &times;
                  </button>
                </div>
              ))}
            </div>
          </section>
        )}

        {!current && history.length === 0 && !busy && (
          <Empty title="Nothing yet" hint="Ask for a few alternatives and keep the good one." />
        )}
      </div>
    </>
  );

  async function showById(id: number) {
    try {
      show(await api.generation(id));
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }
}
