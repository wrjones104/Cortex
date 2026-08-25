import { useEffect, useState } from "react";
import {
  clearConnection,
  errorMessage,
  type ModelInfo,
  type Settings as SettingsData,
  type Status,
} from "../lib/api";
import { useApi } from "../lib/useApi";
import { Notice, Spinner } from "../components/ui";
import { Projects } from "../components/Projects";

export function Settings() {
  const { api, baseUrl, disconnect } = useApi();

  const [status, setStatus] = useState<Status | null>(null);
  const [settings, setSettings] = useState<SettingsData | null>(null);
  const [models, setModels] = useState<ModelInfo[] | null>(null);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.status().then(setStatus, (cause) => setError(errorMessage(cause)));
    api.settings().then(setSettings, () => undefined);
    api.models().then(setModels, (cause) =>
      setModelsError(errorMessage(cause)),
    );
  }, [api]);

  async function updateModel(field: "librarian_model" | "creative_model", name: string) {
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      setSettings(await api.updateSettings({ [field]: name }));
      setSaved("Saved.");
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  // Only chat-capable models are offered. The prototype listed every installed
  // model, so picking an embedder as your Librarian was possible - and broke
  // everything the next time you generated anything.
  const chatModels = models?.filter((m) => m.can_chat) ?? [];

  // Ollama treats a bare name as the :latest tag, so "embeddinggemma" and
  // "embeddinggemma:latest" are the same model. Compare them that way, or a
  // perfectly good setting is labelled "not installed".
  const tagged = (name: string) => (name.includes(":") ? name : `${name}:latest`);
  const isInstalled = (name: string) =>
    chatModels.some((m) => tagged(m.name) === tagged(name));
  const problems = status ? Object.entries(status.integrity).filter(([, n]) => n > 0) : [];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <p>Which models do the work, and how the vault is doing.</p>
        </div>
      </div>

      <div className="stack" style={{ gap: 18 }}>
        {error && <Notice kind="error">{error}</Notice>}
        {saved && <Notice kind="ok">{saved}</Notice>}

        <section className="stack">
          <h2>Models</h2>

          {modelsError && (
            <Notice kind="warn">
              Could not list models &mdash; {modelsError}
            </Notice>
          )}

          {!models && !modelsError && <Spinner label="Asking Ollama..." />}

          {settings && models && (
            <>
              <div>
                <label htmlFor="librarian">Librarian &mdash; files your notes</label>
                <select
                  id="librarian"
                  value={isInstalled(settings.librarian_model)
                    ? chatModels.find((m) => tagged(m.name) === tagged(settings.librarian_model))!.name
                    : settings.librarian_model}
                  disabled={busy}
                  onChange={(event) => void updateModel("librarian_model", event.target.value)}
                >
                  {!isInstalled(settings.librarian_model) && (
                    <option value={settings.librarian_model}>
                      {settings.librarian_model} (not installed)
                    </option>
                  )}
                  {chatModels.map((model) => (
                    <option key={model.name} value={model.name}>
                      {model.name}
                      {model.parameter_size ? ` — ${model.parameter_size}` : ""}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label htmlFor="creative">Creative &mdash; brainstorming</label>
                <select
                  id="creative"
                  value={isInstalled(settings.creative_model)
                    ? chatModels.find((m) => tagged(m.name) === tagged(settings.creative_model))!.name
                    : settings.creative_model}
                  disabled={busy}
                  onChange={(event) => void updateModel("creative_model", event.target.value)}
                >
                  {!isInstalled(settings.creative_model) && (
                    <option value={settings.creative_model}>
                      {settings.creative_model} (not installed)
                    </option>
                  )}
                  {chatModels.map((model) => (
                    <option key={model.name} value={model.name}>
                      {model.name}
                      {model.parameter_size ? ` — ${model.parameter_size}` : ""}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label>Embedding &mdash; powers search</label>
                <div className="card" style={{ padding: "10px 12px" }}>
                  <div style={{ fontFamily: "var(--mono)", fontSize: "0.9rem" }}>
                    {settings.embed_model}
                  </div>
                  <p style={{ margin: "6px 0 0", fontSize: "0.84rem", color: "var(--muted)" }}>
                    Changing this invalidates every vector in the vault, so it is a
                    deliberate rebuild: <code>cortex reindex</code>.
                  </p>
                </div>
              </div>
            </>
          )}
        </section>

        <Projects />

        <section className="stack">
          <h2>Vault</h2>
          {!status && !error && <Spinner label="Loading..." />}
          {status && (
            <div className="card stack" style={{ gap: 8, fontSize: "0.9rem" }}>
              <Line label="Notes" value={String(status.records)} />
              <Line label="Projects" value={String(status.projects)} />
              <Line label="Ollama" value={status.ollama_reachable ? "connected" : "unreachable"} />
              <Line label="Version" value={status.version} />
              <div style={{ fontFamily: "var(--mono)", fontSize: "0.74rem", color: "var(--faint)", wordBreak: "break-all" }}>
                {status.vault_path}
              </div>
            </div>
          )}

          {problems.length > 0 && (
            <Notice kind="warn">
              <div className="stack" style={{ gap: 6 }}>
                <strong>The index and the notes disagree.</strong>
                {problems.map(([key, count]) => (
                  <span key={key}>
                    {key.replaceAll("_", " ")}: {count}
                  </span>
                ))}
                <span>
                  Run <code>cortex reindex</code> to rebuild it from your notes.
                </span>
              </div>
            </Notice>
          )}

          {status && !status.ollama_reachable && (
            <Notice kind="warn">
              Ollama is not answering{status.ollama_detail ? ` — ${status.ollama_detail}` : ""}.
              Browsing and reading still work; capturing and searching need it.
            </Notice>
          )}
        </section>

        <section className="stack">
          <h2>Connection</h2>
          <div className="card stack" style={{ gap: 10 }}>
            <div style={{ fontFamily: "var(--mono)", fontSize: "0.85rem", wordBreak: "break-all" }}>
              {baseUrl}
            </div>
            <div>
              <button
                type="button"
                onClick={() => {
                  clearConnection();
                  disconnect();
                }}
              >
                Disconnect
              </button>
            </div>
          </div>
        </section>
      </div>
    </>
  );
}

function Line({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
      <span style={{ color: "var(--muted)" }}>{label}</span>
      <span style={{ fontWeight: 600 }}>{value}</span>
    </div>
  );
}
