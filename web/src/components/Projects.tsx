import { useCallback, useEffect, useState } from "react";
import { errorMessage, isApiError, type Project } from "../lib/api";
import { useApi } from "../lib/useApi";
import { Empty, Notice, Spinner } from "./ui";

/**
 * Managing projects: rename them, and say what they are about.
 *
 * The description is not a label. It is prepended to the grounding for
 * anything filed, generated or asked under the project, ahead of the notes
 * retrieved by similarity — so it frames the whole project in a way that
 * five sampled records cannot.
 */
export function Projects() {
  const { api } = useApi();

  const [projects, setProjects] = useState<Project[] | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const [draftDescription, setDraftDescription] = useState("");
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setProjects(await api.projects());
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }, [api]);

  useEffect(() => {
    void load();
  }, [load]);

  function open(project: Project) {
    setEditing(project.slug);
    setDraftName(project.name);
    setDraftDescription(project.description);
    setConfirmingDelete(null);
    setError(null);
  }

  async function save(slug: string) {
    setBusy(true);
    setError(null);
    try {
      await api.updateProject(slug, { name: draftName, description: draftDescription });
      setEditing(null);
      await load();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  async function remove(project: Project, force: boolean) {
    setBusy(true);
    setError(null);
    try {
      await api.deleteProject(project.slug, force);
      setConfirmingDelete(null);
      await load();
    } catch (cause) {
      // 409 means it still holds notes, which is a decision to put in front
      // of someone rather than an error to shrug at.
      if (isApiError(cause) && cause.status === 409) setConfirmingDelete(project.slug);
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  if (projects === null) return <Spinner label="Loading..." />;

  return (
    <section className="stack">
      <h2>Projects</h2>

      {error && <Notice kind="error">{error}</Notice>}

      {projects.length === 0 && (
        <Empty title="No projects yet" hint="They appear as you file notes." />
      )}

      <div className="list">
        {projects.map((project) =>
          editing === project.slug ? (
            <div key={project.slug} className="card stack">
              <div>
                <label htmlFor={`name-${project.slug}`}>Name</label>
                <input
                  id={`name-${project.slug}`}
                  type="text"
                  value={draftName}
                  onChange={(event) => setDraftName(event.target.value)}
                  disabled={busy}
                />
              </div>

              <div>
                <label htmlFor={`desc-${project.slug}`}>What is this project about?</label>
                <textarea
                  id={`desc-${project.slug}`}
                  value={draftDescription}
                  onChange={(event) => setDraftDescription(event.target.value)}
                  placeholder="A coastal town that forgets its own history..."
                  disabled={busy}
                  style={{ minHeight: 96 }}
                />
                <p className="hint">
                  Used to ground everything filed, generated or asked in this project.
                </p>
              </div>

              <div className="row" style={{ justifyContent: "space-between" }}>
                <div className="row">
                  <button
                    className="primary"
                    onClick={() => void save(project.slug)}
                    disabled={busy || !draftName.trim()}
                    type="button"
                  >
                    {busy ? <Spinner label="Saving..." /> : "Save"}
                  </button>
                  <button onClick={() => setEditing(null)} disabled={busy} type="button">
                    Cancel
                  </button>
                </div>

                {confirmingDelete === project.slug ? (
                  <div className="row">
                    <span style={{ fontSize: "0.85rem", color: "var(--muted)" }}>
                      Delete its {project.record_count} note
                      {project.record_count === 1 ? "" : "s"} too?
                    </span>
                    <button
                      className="danger"
                      onClick={() => void remove(project, true)}
                      disabled={busy}
                      type="button"
                    >
                      Delete both
                    </button>
                    <button
                      className="quiet"
                      onClick={() => setConfirmingDelete(null)}
                      type="button"
                    >
                      Keep
                    </button>
                  </div>
                ) : (
                  <button
                    className="quiet"
                    onClick={() => void remove(project, false)}
                    disabled={busy}
                    type="button"
                  >
                    Delete
                  </button>
                )}
              </div>
            </div>
          ) : (
            <button key={project.slug} className="item" onClick={() => open(project)}>
              <h3>{project.name}</h3>
              {project.description ? (
                <div className="snippet">{project.description}</div>
              ) : (
                <div className="snippet" style={{ color: "var(--faint)" }}>
                  No description yet
                </div>
              )}
              <div className="meta">
                <span>
                  {project.record_count} note{project.record_count === 1 ? "" : "s"}
                </span>
              </div>
            </button>
          ),
        )}
      </div>
    </section>
  );
}
