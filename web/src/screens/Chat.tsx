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
import { Empty, Notice, Spinner, TypingIndicator } from "../components/ui";
import { Markdown } from "../components/Markdown";
import { ChatBotAvatar, UserAvatar, ChatHero } from "../components/Illustrations";
import { localTime } from "../lib/time";

const STARTER_PROMPTS = [
  "What projects are in my vault right now?",
  "Summarize the most recent notes I've captured",
  "What ideas or brainstorms do I have stored?",
  "Connect any related notes across all my projects",
];

/**
 * Conversations with the vault.
 *
 * Two panes on a wide screen, one at a time on a phone: the list is the
 * landing view and a conversation replaces it, which is what a thumb expects.
 */
export function Chat() {
  const { api, account } = useApi();
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
  const [copiedId, setCopiedId] = useState<number | null>(null);

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

  async function send(textToSend?: string) {
    const question = (textToSend ?? draft).trim();
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

  function copyMessageText(msgId: number, text: string) {
    void navigator.clipboard.writeText(text).then(() => {
      setCopiedId(msgId);
      setTimeout(() => setCopiedId(null), 2000);
    });
  }

  const showList = threadId === null;

  return (
    <div className={`chat ${showList ? "chat-list-only" : "chat-open"}`}>
      <aside className="chat-threads">
        <div className="page-head row" style={{ justifyContent: "space-between", alignItems: "flex-start", marginBottom: 16 }}>
          <div>
            <h1>Conversations 💬</h1>
            <p>Talk with your vault and Librarian.</p>
          </div>
          <button className="primary bouncy-btn" onClick={() => void startThread()} type="button" style={{ flexShrink: 0, marginTop: 2 }}>
            + New
          </button>
        </div>

        {threads === null && <Spinner label="Loading..." />}
        {threads?.length === 0 && (
          <Empty
            title="No conversations yet"
            hint="Ask your vault something."
            illustration={<ChatHero size={54} />}
          />
        )}

        <div className="list">
          {threads?.map((thread) => (
            <div
              key={thread.id}
              className={`item thread-card ${thread.id === threadId ? "current" : ""}`}
            >
              <button className="card-main-btn" onClick={() => navigate(`/chat/${thread.id}`)}>
                <div className="card-top-row">
                  <h3 className="card-title">{thread.title}</h3>
                </div>
                <div className="card-bottom-row">
                  <div className="card-tags">
                    <span className="chip project-chip">{thread.project ?? "all projects"}</span>
                    {thread.has_summary && (
                      <span className="chip summary-chip">✨ summarized</span>
                    )}
                  </div>
                  <div className="card-stats">
                    <span>{thread.message_count} msgs</span>
                    <span className="dot">·</span>
                    <span>{localTime(thread.updated_at)}</span>
                  </div>
                </div>
              </button>
              <button
                className="quiet card-delete-btn"
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
            <Empty
              title="Pick a conversation"
              hint="Or start a new one to talk with your Librarian."
              illustration={<ChatHero size={88} />}
            />
            <button
              className="primary bouncy-btn"
              onClick={() => void startThread()}
              type="button"
              style={{ marginTop: 16 }}
            >
              Start New Chat
            </button>
          </div>
        ) : (
          <>
            <div className="chat-head">
              <button className="quiet back bouncy-btn" onClick={() => navigate("/chat")} type="button">
                &larr; Back
              </button>
              <div style={{ flex: 1, minWidth: 0 }}>
                <h2 className="truncate">{detail?.thread.title ?? "..."}</h2>
                {detail && detail.facts.length > 0 && (
                  <details className="facts">
                    <summary>🧠 {detail.facts.length} things remembered</summary>
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
                <option value="">🌐 All projects</option>
                {projects.map((project) => (
                  <option key={project.slug} value={project.name}>
                    📁 {project.name}
                  </option>
                ))}
              </select>
            </div>

            <div className="transcript">
              {detail?.messages.length === 0 && !busy && (
                <div className="chat-welcome stack" style={{ alignItems: "center", margin: "24px 0" }}>
                  <Empty
                    title="Ask the Librarian anything!"
                    hint="Answers are grounded directly in your notes and thoughts."
                    illustration={<ChatHero size={72} />}
                  />
                  <div className="starter-prompts">
                    <span className="starter-title">Try asking:</span>
                    <div className="starter-grid">
                      {STARTER_PROMPTS.map((prompt) => (
                        <button
                          key={prompt}
                          type="button"
                          className="starter-chip"
                          onClick={() => {
                            setDraft(prompt);
                          }}
                        >
                          💡 {prompt}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {detail?.messages.map((message) =>
                message.role === "marker" ? (
                  <div key={message.id} className="marker">
                    <span>✨ {message.content}</span>
                  </div>
                ) : (
                  <div key={message.id} className={`message-row ${message.role}`}>
                    <div className="message-avatar">
                      {message.role === "assistant" ? (
                        <ChatBotAvatar size={34} />
                      ) : (
                        <UserAvatar size={34} label={account?.displayName || account?.username || "You"} />
                      )}
                    </div>

                    <div className={`bubble ${message.role}`}>
                      <div className="bubble-header">
                        <span className="bubble-author">
                          {message.role === "assistant" ? "Librarian" : account?.displayName || "You"}
                        </span>
                        <span className="bubble-time">{localTime(message.created_at)}</span>
                        {message.role === "assistant" && (
                          <button
                            type="button"
                            className="msg-copy-btn"
                            onClick={() => copyMessageText(message.id, message.content)}
                            aria-label="Copy response"
                          >
                            {copiedId === message.id ? "Copied! ✨" : "Copy"}
                          </button>
                        )}
                      </div>

                      {message.role === "assistant" ? (
                        <Markdown content={message.content} />
                      ) : (
                        <div className="body-text">{message.content}</div>
                      )}

                      {message.sources.length > 0 && (
                        <div className="sources">
                          <span className="sources-label">📚 Sources:</span>
                          {message.sources.map((source) => (
                            <span key={source} className="chip source-chip">
                              {source}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                ),
              )}

              {streaming && (
                <div className="message-row assistant">
                  <div className="message-avatar">
                    <ChatBotAvatar size={34} />
                  </div>
                  <div className="bubble assistant">
                    <div className="bubble-header">
                      <span className="bubble-author">Librarian</span>
                      <span className="bubble-time">typing...</span>
                    </div>
                    <Markdown content={streaming} />
                    {pendingSources.length > 0 && (
                      <div className="sources">
                        <span className="sources-label">📚 Sources:</span>
                        {pendingSources.map((source) => (
                          <span key={source} className="chip source-chip">
                            {source}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {busy && !streaming && (
                <div className="message-row assistant">
                  <div className="message-avatar">
                    <ChatBotAvatar size={34} />
                  </div>
                  <div className="bubble assistant thinking">
                    <div className="bubble-header">
                      <span className="bubble-author">Librarian</span>
                    </div>
                    <TypingIndicator label={status ?? "Consulting notes..."} />
                  </div>
                </div>
              )}

              <div ref={bottom} />
            </div>

            {error && <Notice kind="error">{error}</Notice>}

            <div className="composer">
              <textarea
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder="Ask your vault anything... (Enter to send, Shift+Enter for newline)"
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
                <button className="quiet stop-btn bouncy-btn" onClick={() => abort.current?.abort()} type="button">
                  ⏹ Stop
                </button>
              ) : (
                <button
                  className="primary send-btn bouncy-btn"
                  onClick={() => void send()}
                  disabled={!draft.trim()}
                  type="button"
                >
                  <span>Send 🚀</span>
                </button>
              )}
            </div>
          </>
        )}
      </section>
    </div>
  );
}
