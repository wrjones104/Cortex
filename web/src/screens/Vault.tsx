import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { errorMessage, type Project, type VaultRecord, type SearchHit } from "../lib/api";
import { useApi } from "../lib/useApi";
import { Empty, Notice, Spinner } from "../components/ui";
import { localTime } from "../lib/time";

const MATCH_LABEL = {
  both: "meaning + keyword",
  meaning: "meaning",
  keyword: "keyword",
} as const;

/**
 * Browse and search. The prototype could only filter by project, which stops
 * being usable somewhere around a hundred notes.
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
        if (mine !== requestId.current) return; // a newer request won
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

  // Debounced: searching runs two model-free queries plus one embedding call,
  // and firing on every keystroke would queue them faster than they return.
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
          <h1>Vault</h1>
          <p>
            {total > 0 && !query ? `${total} note${total === 1 ? "" : "s"}` : "Search by meaning or by keyword"}
          </p>
        </div>
      </div>

      <div className="stack">
        <input
          type="search"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="Search your vault..."
          aria-label="Search"
        />

        <select
          value={project}
          onChange={(event) => setProjectFilter(event.target.value)}
          aria-label="Filter by project"
        >
          <option value="">All projects</option>
          {projects.map((p) => (
            <option key={p.slug} value={p.name}>
              {p.name} ({p.record_count})
            </option>
          ))}
        </select>

        {error && <Notice kind="error">{error}</Notice>}
        {loading && <Spinner label="Loading..." />}

        {!loading && !error && hits !== null && (
          hits.length === 0 ? (
            <Empty
              title="Nothing matched"
              hint="Nothing in the vault is close to that, by meaning or by wording."
            />
          ) : (
            <div className="list">
              {hits.map((hit) => (
                <button
                  key={hit.record.id}
                  className="item"
                  onClick={() => navigate(`/vault/${hit.record.id}`)}
                >
                  <h3>{hit.record.title}</h3>
                  <div className="snippet">{hit.snippet}</div>
                  <div className="meta">
                    <span>{hit.record.project}</span>
                    <span className="dot">|</span>
                    <span className={`chip ${hit.matched_by}`}>{MATCH_LABEL[hit.matched_by]}</span>
                  </div>
                </button>
              ))}
            </div>
          )
        )}

        {!loading && !error && hits === null && (
          records.length === 0 ? (
            <Empty
              title={project ? `Nothing in ${project} yet` : "Your vault is empty"}
              hint="Captured notes show up here."
            />
          ) : (
            <div className="list">
              {records.map((record) => (
                <button
                  key={record.id}
                  className="item"
                  onClick={() => navigate(`/vault/${record.id}`)}
                >
                  <h3>{record.title}</h3>
                  <div className="snippet">{record.body}</div>
                  <div className="meta">
                    <span>{record.project}</span>
                    <span className="dot">|</span>
                    <span>{localTime(record.created_at)}</span>
                    {record.category && (
                      <>
                        <span className="dot">|</span>
                        <span>{record.category}</span>
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
