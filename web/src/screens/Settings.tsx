import { useEffect, useState } from "react";
import {
  errorMessage,
  type ModelInfo,
  type Settings as SettingsData,
  type Status,
} from "../lib/api";
import { useApi } from "../lib/useApi";
import { Notice, Spinner } from "../components/ui";
import { Projects } from "../components/Projects";
import { Account } from "../components/Account";

export function Settings() {
  const { api } = useApi();

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

  type ModelField =
    | "librarian_model"
    | "creative_model"
    | "utility_model"
    | "single_model";

  async function updateModel(field: ModelField, name: string) {
    await save({ [field]: name });
  }

  async function save(patch: Parameters<typeof api.updateSettings>[0]) {
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      setSettings(await api.updateSettings(patch));
      setSaved("Saved.");
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  const chatModels = models?.filter((m) => m.can_chat) ?? [];
  const problems = status ? Object.entries(status.integrity).filter(([, n]) => n > 0) : [];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <p>Which models do the work, and how your vault is doing.</p>
        </div>
      </div>

      <div className="stack" style={{ gap: 18 }}>
        {error && <Notice kind="error">{error}</Notice>}
        {saved && <Notice kind="ok">{saved}</Notice>}

        <section className="stack">
          <h2>Models</h2>
          <p style={{ margin: 0, fontSize: "0.84rem", color: "var(--muted)" }}>
            Yours alone. Everyone with an account here picks their own.
          </p>

          {modelsError && (
            <Notice kind="warn">
              Could not list models &mdash; {modelsError}
            </Notice>
          )}

          {!models && !modelsError && <Spinner label="Asking Ollama..." />}

          {settings && models && (
            <>
              <div className="card stack" style={{ gap: 10 }}>
                <strong style={{ fontSize: "0.9rem" }}>How the work is divided</strong>
                <label className="toggle">
                  <input
                    type="radio"
                    name="model-profile"
                    checked={settings.model_profile !== "single"}
                    disabled={busy}
                    onChange={() => void save({ model_profile: "split" })}
                  />
                  <span>A specialist per role</span>
                </label>
                <label className="toggle">
                  <input
                    type="radio"
                    name="model-profile"
                    checked={settings.model_profile === "single"}
                    disabled={busy}
                    onChange={() => void save({ model_profile: "single" })}
                  />
                  <span>One model for everything</span>
                </label>
                <p style={{ margin: 0, fontSize: "0.84rem", color: "var(--muted)" }}>
                  {settings.model_profile === "single"
                    ? "Filing, answering and brainstorming all run on one model, so Ollama never swaps weights between them. Your per-role choices are kept, and come back if you switch."
                    : "Each role gets the model that suits it. If your card can only hold one model at a time, moving between filing and brainstorming means reloading it."}
                </p>
              </div>

              {settings.model_profile === "single" ? (
                <ModelPicker
                  id="single"
                  label="Every role — files, answers and brainstorms"
                  value={settings.single_model}
                  models={chatModels}
                  busy={busy}
                  onChange={(name) => void updateModel("single_model", name)}
                />
              ) : (
                <>
                  <ModelPicker
                    id="librarian"
                    label="Librarian — files your notes, and answers in chat"
                    value={settings.librarian_model}
                    models={chatModels}
                    busy={busy}
                    onChange={(name) => void updateModel("librarian_model", name)}
                  />
                  <ModelPicker
                    id="creative"
                    label="Creative — brainstorming"
                    value={settings.creative_model}
                    models={chatModels}
                    busy={busy}
                    onChange={(name) => void updateModel("creative_model", name)}
                  />
                </>
              )}

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

        <Account />
      </div>
    </>
  );
}

/** One labelled model dropdown. Only chat-capable models are offered - the
 *  prototype listed every installed model, so picking an embedder as your
 *  Librarian was possible, and broke the next thing you generated. */
function ModelPicker({
  id,
  label,
  value,
  models,
  busy,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  models: ModelInfo[];
  busy: boolean;
  onChange: (name: string) => void;
}) {
  // Ollama treats a bare name as the :latest tag, so "embeddinggemma" and
  // "embeddinggemma:latest" are the same model. Compare them that way, or a
  // perfectly good setting is labelled "not installed".
  const tagged = (name: string) => (name.includes(":") ? name : `${name}:latest`);
  const match = models.find((m) => tagged(m.name) === tagged(value));

  return (
    <div>
      <label htmlFor={id}>{label}</label>
      <select
        id={id}
        value={match ? match.name : value}
        disabled={busy}
        onChange={(event) => onChange(event.target.value)}
      >
        {!match && <option value={value}>{value} (not installed)</option>}
        {models.map((model) => (
          <option key={model.name} value={model.name}>
            {model.name}
            {model.parameter_size ? ` — ${model.parameter_size}` : ""}
          </option>
        ))}
      </select>
    </div>
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
