import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { errorMessage, type Project, type VaultRecord } from "../lib/api";
import { useApi } from "../lib/useApi";
import { Notice, ProjectPicker, Spinner } from "../components/ui";
import { localTime } from "../lib/time";

export function RecordDetail() {
  const { api } = useApi();
  const navigate = useNavigate();
  const { id } = useParams<{ id: string }>();
  const recordId = Number(id);

  const [record, setRecord] = useState<VaultRecord | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Partial<VaultRecord>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    api.record(recordId).then(
      (loaded) => {
        if (cancelled) return;
        setRecord(loaded);
        setDraft(loaded);
      },
      (cause) => !cancelled && setError(errorMessage(cause)),
    );
    api.projects().then(setProjects, () => setProjects([]));
    return () => {
      cancelled = true;
    };
  }, [api, recordId]);

  async function save() {
    if (!record) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await api.updateRecord(record.id, {
        title: draft.title,
        body: draft.body,
        project: draft.project,
        category: draft.category,
        subcategory: draft.subcategory,
      });
      setRecord(updated);
      setDraft(updated);
      setEditing(false);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!record) return;
    setBusy(true);
    try {
      await api.deleteRecord(record.id);
      navigate("/vault", { replace: true });
    } catch (cause) {
      setError(errorMessage(cause));
      setBusy(false);
    }
  }

  if (error && !record) {
    return (
      <div className="stack">
        <button className="quiet" onClick={() => navigate("/vault")} type="button">
          &larr; Back to vault
        </button>
        <Notice kind="error">{error}</Notice>
      </div>
    );
  }

  if (!record) return <Spinner label="Loading..." />;

  return (
    <div className="stack">
      <button className="quiet" onClick={() => navigate(-1)} type="button" style={{ alignSelf: "start" }}>
        &larr; Back
      </button>

      {error && <Notice kind="error">{error}</Notice>}

      {editing ? (
        <div className="stack">
          <div>
            <label htmlFor="title">Title</label>
            <input
              id="title"
              type="text"
              value={draft.title ?? ""}
              onChange={(event) => setDraft({ ...draft, title: event.target.value })}
            />
          </div>

          <div>
            <label htmlFor="project">Project</label>
            <ProjectPicker
              value={draft.project ?? ""}
              onChange={(next) => setDraft({ ...draft, project: next })}
              projects={projects.map((p) => p.name)}
              allLabel="Keep current project"
            />
          </div>

          <div className="row" style={{ gap: 10, flexWrap: "nowrap" }}>
            <div style={{ flex: 1 }}>
              <label htmlFor="category">Category</label>
              <input
                id="category"
                type="text"
                value={draft.category ?? ""}
                onChange={(event) => setDraft({ ...draft, category: event.target.value })}
              />
            </div>
            <div style={{ flex: 1 }}>
              <label htmlFor="subcategory">Subcategory</label>
              <input
                id="subcategory"
                type="text"
                value={draft.subcategory ?? ""}
                onChange={(event) => setDraft({ ...draft, subcategory: event.target.value })}
              />
            </div>
          </div>

          <div>
            <label htmlFor="body">Note</label>
            <textarea
              id="body"
              value={draft.body ?? ""}
              onChange={(event) => setDraft({ ...draft, body: event.target.value })}
              style={{ minHeight: 280 }}
            />
          </div>

          <div className="row">
            <button className="primary" onClick={() => void save()} disabled={busy} type="button">
              {busy ? <Spinner label="Saving..." /> : "Save changes"}
            </button>
            <button
              onClick={() => {
                setDraft(record);
                setEditing(false);
              }}
              disabled={busy}
              type="button"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <>
          <div>
            <h1>{record.title}</h1>
            <div className="meta" style={{ color: "var(--faint)", fontSize: "0.83rem", marginTop: 6 }}>
              {record.project}
              {record.category && ` | ${record.category}`}
              {record.subcategory && ` / ${record.subcategory}`}
              {` | ${localTime(record.created_at)}`}
              {record.updated_at !== record.created_at && ` | edited ${localTime(record.updated_at)}`}
            </div>
          </div>

          <div className="card body-text">{record.body}</div>

          <div className="row" style={{ justifyContent: "space-between" }}>
            <button onClick={() => setEditing(true)} type="button">
              Edit
            </button>

            {confirmingDelete ? (
              <div className="row">
                <span style={{ fontSize: "0.88rem", color: "var(--muted)" }}>Delete permanently?</span>
                <button className="danger" onClick={() => void remove()} disabled={busy} type="button">
                  {busy ? <Spinner /> : "Delete"}
                </button>
                <button className="quiet" onClick={() => setConfirmingDelete(false)} type="button">
                  Keep
                </button>
              </div>
            ) : (
              <button className="quiet" onClick={() => setConfirmingDelete(true)} type="button">
                Delete
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}
