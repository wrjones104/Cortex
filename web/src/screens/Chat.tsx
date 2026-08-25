import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  errorMessage,
  type ChatMessage,
  type Project,
  type Thread,
  type ThreadDetail,
} from "../lib/api";
import { useApi } from "../lib/useApi";
import { Empty, Notice, Spinner } from "../components/ui";
import { localTime } from "../lib/time";

/**
 * Conversations with the vault.
 *
 * Two panes on a wide screen, one at a time on a phone: the list is the
 * landing view and a conversation replaces it, which is what a thumb expects.
 */
export function Chat() {
  const { api } = useApi();
  const navigate = useNavigate();
  const { id } = useParams<{ id: string }>();
  const threadId = id ? Number(id) : null;

  const [threads, setThreads] = useState<Thread[] | null>(null);
  const [detail, setDetail] = useState<ThreadDetail | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [streaming, setStreaming] = useState("");
  const [pendingSources, setPendingSources] = useState<string[]>([]);

  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement | null>(null);

  const busy = status !== null;

  const refreshThreads = useCallback(async () => {
    try {
      setThreads(await api.threads());
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }, [api]);

  useEffect(() => {
    void refreshThreads();
    api.projects().then(setProjects, () => setProjects([]));
  }, [api, refreshThreads]);

  useEffect(() => {
    if (threadId === null) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setError(null);
    api.thread(threadId).then(
      (loaded) => !cancelled && setDetail(loaded),
      (cause) => !cancelled && setError(errorMessage(cause)),
    );
    return () => {
      cancelled = true;
    };
  }, [api, threadId]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [detail?.messages.length, streaming, status]);

  useEffect(() => () => abort.current?.abort(), []);

  async function startThread() {
    try {
      const thread = await api.createThread({});
      await refreshThreads();
      navigate(`/chat/${thread.id}`);
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }

  async function send() {
    const question = draft.trim();
    if (!question || busy || threadId === null) return;

    setError(null);
    setDraft("");
    setStreaming("");
    setPendingSources([]);
    setStatus("Thinking");

    // Show the question immediately rather than waiting for the round trip.
    setDetail((current) =>
      current
        ? {
            ...current,
            messages: [
              ...current.messages,
              {
                id: -1,
                role: "user",
                content: question,
                sources: [],
                created_at: new Date().toISOString(),
              } as ChatMessage,
            ],
          }
        : current,
    );

    const controller = new AbortController();
    abort.current = controller;

    try {
      await api.ask(
        threadId,
        question,
        {
          onStatus: setStatus,
          onSources: setPendingSources,
          onToken: (text) => setStreaming((current) => current + text),
        },
        controller.signal,
      );
      setDetail(await api.thread(threadId));
      await refreshThreads();
    } catch (cause) {
      if ((cause as Error)?.name !== "AbortError") {
        setError(errorMessage(cause));
        setDraft(question); // give the question back rather than losing it
      }
      if (threadId !== null) setDetail(await api.thread(threadId).catch(() => detail));
    } finally {
      setStatus(null);
      setStreaming("");
      setPendingSources([]);
      abort.current = null;
    }
  }

  async function changeScope(project: string) {
    if (threadId === null) return;
    try {
      await api.updateThread(threadId, project ? { project } : { clear_project: true });
      setDetail(await api.thread(threadId));
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }

  async function removeThread(id: number) {
    try {
      await api.deleteThread(id);
      await refreshThreads();
      if (threadId === id) navigate("/chat");
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }

  const showList = threadId === null;

  return (
    <div className={`chat ${showList ? "chat-list-only" : "chat-open"}`}>
      <aside className="chat-threads">
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 10 }}>
          <h1 style={{ fontSize: "1.25rem" }}>Conversations</h1>
          <button className="primary" onClick={() => void startThread()} type="button">
            New
          </button>
        </div>

        {threads === null && <Spinner label="Loading..." />}
        {threads?.length === 0 && (
          <Empty title="No conversations yet" hint="Ask your vault something." />
        )}

        <div className="list">
          {threads?.map((thread) => (
            <div
              key={thread.id}
              className={`item thread-item ${thread.id === threadId ? "current" : ""}`}
            >
              <button className="thread-open" onClick={() => navigate(`/chat/${thread.id}`)}>
                <h3>{thread.title}</h3>
                <div className="meta">
                  <span>{thread.project ?? "all projects"}</span>
                  <span className="dot">|</span>
                  <span>{thread.message_count} messages</span>
                  {thread.has_summary && (
                    <>
                      <span className="dot">|</span>
                      <span className="chip">summarised</span>
                    </>
                  )}
                  <span className="dot">|</span>
                  <span>{localTime(thread.updated_at)}</span>
                </div>
              </button>
              <button
                className="quiet thread-delete"
                aria-label={`Delete ${thread.title}`}
                onClick={() => void removeThread(thread.id)}
                type="button"
              >
                &times;
              </button>
            </div>
          ))}
        </div>
      </aside>

      <section className="chat-panel">
        {threadId === null ? (
          <div className="chat-empty">
            <Empty title="Pick a conversation" hint="Or start a new one." />
          </div>
        ) : (
          <>
            <div className="chat-head">
              <button className="quiet back" onClick={() => navigate("/chat")} type="button">
                &larr;
              </button>
              <div style={{ flex: 1, minWidth: 0 }}>
                <h2 className="truncate">{detail?.thread.title ?? "..."}</h2>
                {detail && detail.facts.length > 0 && (
                  <details className="facts">
                    <summary>{detail.facts.length} things it remembers</summary>
                    <ul>
                      {detail.facts.map((fact) => (
                        <li key={fact}>{fact}</li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
              <select
                value={detail?.thread.project ?? ""}
                onChange={(event) => void changeScope(event.target.value)}
                aria-label="Search scope"
                className="scope"
                disabled={busy}
              >
                <option value="">All projects</option>
                {projects.map((project) => (
                  <option key={project.slug} value={project.name}>
                    {project.name}
                  </option>
                ))}
              </select>
            </div>

            <div className="transcript">
              {detail?.messages.length === 0 && !busy && (
                <Empty title="Ask anything" hint="Answers come only from your own notes." />
              )}

              {detail?.messages.map((message) =>
                message.role === "marker" ? (
                  <div key={message.id} className="marker">
                    {message.content}
                  </div>
                ) : (
                  <div key={message.id} className={`bubble ${message.role}`}>
                    <div className="body-text">{message.content}</div>
                    {message.sources.length > 0 && (
                      <div className="sources">
                        {message.sources.map((source) => (
                          <span key={source} className="chip">
                            {source}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                ),
              )}

              {streaming && (
                <div className="bubble assistant">
                  <div className="body-text">{streaming}</div>
                  {pendingSources.length > 0 && (
                    <div className="sources">
                      {pendingSources.map((source) => (
                        <span key={source} className="chip">
                          {source}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {busy && !streaming && (
                <div className="bubble assistant thinking">
                  <Spinner label={status ?? "Thinking"} />
                </div>
              )}

              <div ref={bottom} />
            </div>

            {error && <Notice kind="error">{error}</Notice>}

            <div className="composer">
              <textarea
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder="Ask your vault..."
                rows={2}
                aria-label="Message"
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void send();
                  }
                }}
              />
              {busy ? (
                <button className="quiet" onClick={() => abort.current?.abort()} type="button">
                  Stop
                </button>
              ) : (
                <button
                  className="primary"
                  onClick={() => void send()}
                  disabled={!draft.trim()}
                  type="button"
                >
                  Send
                </button>
              )}
            </div>
          </>
        )}
      </section>
    </div>
  );
}
