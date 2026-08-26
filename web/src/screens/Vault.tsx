import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { errorMessage, type Project, type VaultRecord, type SearchHit } from "../lib/api";
import { useApi } from "../lib/useApi";
import { Empty, Notice, Spinner } from "../components/ui";
import { VaultHero } from "../components/Illustrations";
import { localTime } from "../lib/time";

const MATCH_LABEL = {
  both: "⚡ meaning + keyword",
  meaning: "🧠 meaning",
  keyword: "🔤 keyword",
} as const;

/**
 * Browse and search.
 */
export function Vault() {
  const { api } = useApi();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();

  const query = params.get("q") ?? "";
  const project = params.get("project") ?? "";

  const [input, setInput] = useState(query);
  const [projects, setProjects] = useState<Project[]>([]);
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [records, setRecords] = useState<VaultRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const requestId = useRef(0);

  useEffect(() => {
    api.projects().then(setProjects, () => setProjects([]));
  }, [api]);

  const load = useCallback(async () => {
    const mine = ++requestId.current;
    setLoading(true);
    setError(null);

    try {
      if (query.trim()) {
        const result = await api.search(query, { project: project || undefined, limit: 30 });
        if (mine !== requestId.current) return;
        setHits(result.hits);
      } else {
        const result = await api.records({ project: project || undefined, limit: 50 });
        if (mine !== requestId.current) return;
        setHits(null);
        setRecords(result.records);
        setTotal(result.total);
      }
    } catch (cause) {
      if (mine !== requestId.current) return;
      setError(errorMessage(cause));
    } finally {
      if (mine === requestId.current) setLoading(false);
    }
  }, [api, query, project]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (input === query) return;
    const timer = setTimeout(() => {
      const next = new URLSearchParams(params);
      if (input.trim()) next.set("q", input);
      else next.delete("q");
      setParams(next, { replace: true });
    }, 300);
    return () => clearTimeout(timer);
  }, [input, query, params, setParams]);

  function setProjectFilter(next: string) {
    const updated = new URLSearchParams(params);
    if (next) updated.set("project", next);
    else updated.delete("project");
    setParams(updated, { replace: true });
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Vault 📚</h1>
          <p>
            {total > 0 && !query ? `${total} note${total === 1 ? "" : "s"} in your knowledge base` : "Search deeply by meaning or exact wording"}
          </p>
        </div>
      </div>

      <div className="stack">
        <div className="vault-search-row row" style={{ gap: 10 }}>
          <div style={{ flex: 1, position: "relative" }}>
            <input
              type="search"
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder="🔍 Search your knowledge vault..."
              aria-label="Search"
              className="vault-search-input"
            />
          </div>

          <select
            value={project}
            onChange={(event) => setProjectFilter(event.target.value)}
            aria-label="Filter by project"
            className="vault-project-filter"
            style={{ width: "auto", minWidth: 160 }}
          >
            <option value="">🌐 All projects</option>
            {projects.map((p) => (
              <option key={p.slug} value={p.name}>
                📁 {p.name} ({p.record_count})
              </option>
            ))}
          </select>
        </div>

        {error && <Notice kind="error">{error}</Notice>}
        {loading && <Spinner label="Consulting vault index..." />}

        {!loading && !error && hits !== null && (
          hits.length === 0 ? (
            <Empty
              title="Nothing matched your search"
              hint="Try searching for a different concept, synonym, or keyword."
              illustration={<VaultHero size={88} />}
            />
          ) : (
            <div className="list">
              {hits.map((hit) => (
                <button
                  key={hit.record.id}
                  className="item hit-item bouncy-card"
                  onClick={() => navigate(`/vault/${hit.record.id}`)}
                >
                  <div className="hit-header">
                    <h3>{hit.record.title}</h3>
                    <span className={`chip ${hit.matched_by}`}>{MATCH_LABEL[hit.matched_by]}</span>
                  </div>
                  <div className="snippet">{hit.snippet}</div>
                  <div className="meta">
                    <span className="chip project-chip">📁 {hit.record.project}</span>
                    <span className="dot">•</span>
                    <span>{localTime(hit.record.updated_at)}</span>
                  </div>
                </button>
              ))}
            </div>
          )
        )}

        {!loading && !error && hits === null && (
          records.length === 0 ? (
            <Empty
              title={project ? `No notes in ${project} yet` : "Your vault is empty"}
              hint="Capture your first note and watch your personal library grow!"
              illustration={<VaultHero size={88} />}
            />
          ) : (
            <div className="list">
              {records.map((record) => (
                <button
                  key={record.id}
                  className="item vault-record-item bouncy-card"
                  onClick={() => navigate(`/vault/${record.id}`)}
                >
                  <h3>{record.title}</h3>
                  <div className="snippet">{record.body}</div>
                  <div className="meta">
                    <span className="chip project-chip">📁 {record.project}</span>
                    <span className="dot">•</span>
                    <span>{localTime(record.created_at)}</span>
                    {record.category && (
                      <>
                        <span className="dot">•</span>
                        <span className="chip category-chip">🏷️ {record.category}</span>
                      </>
                    )}
                  </div>
                </button>
              ))}
            </div>
          )
        )}
      </div>
    </>
  );
}
