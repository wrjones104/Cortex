/**
 * Typed client for the Cortex API.
 *
 * Connection details live in localStorage rather than a build-time constant:
 * the same built app is opened from a desktop browser on localhost and from a
 * phone on a Tailscale address, and neither should need a rebuild.
 */

export interface VaultRecord {
  id: number;
  project: string;
  title: string;
  body: string;
  category: string;
  subcategory: string;
  source: string;
  created_at: string;
  updated_at: string;
}

export interface RecordList {
  records: VaultRecord[];
  total: number;
  limit: number;
  offset: number;
}

export interface SearchHit {
  record: VaultRecord;
  score: number;
  snippet: string;
  matched_by: "meaning" | "keyword" | "both";
}

export interface Project {
  id: number;
  name: string;
  slug: string;
  description: string;
  record_count: number;
}

export interface ModelInfo {
  name: string;
  parameter_size: string | null;
  capabilities: string[];
  can_chat: boolean;
  can_embed: boolean;
  can_think: boolean;
}

export interface Settings {
  librarian_model: string;
  creative_model: string;
  embed_model: string;
  embed_model_locked: boolean;
}

export interface Status {
  version: string;
  records: number;
  projects: number;
  vault_path: string;
  integrity: Integrity;
  ollama_reachable: boolean;
  ollama_detail: string | null;
  models: { role: string; name: string; installed: boolean }[];
}

export interface Integrity {
  orphan_chunks: number;
  chunks_without_vectors: number;
  vectors_without_chunks: number;
  records_without_chunks: number;
}

export interface CaptureInput {
  text: string;
  project?: string | null;
  title?: string | null;
  verbatim?: boolean;
  allow_duplicate?: boolean;
  idempotency_key?: string | null;
}

export interface CaptureResult {
  record: VaultRecord;
  chunks: number;
  warnings: string[];
}

export interface Thread {
  id: number;
  title: string;
  project: string | null;
  message_count: number;
  has_summary: boolean;
  fact_count: number;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant" | "marker";
  content: string;
  sources: string[];
  created_at: string;
}

export interface ThreadDetail {
  thread: Thread;
  messages: ChatMessage[];
  facts: string[];
}

export interface AnswerDone {
  sources: string[];
  standalone_query: string;
  compacted: boolean;
  prompt_tokens: number;
  estimated_tokens: number;
}

export interface AnswerHandlers {
  onStatus?: (message: string) => void;
  onSources?: (sources: string[]) => void;
  onToken?: (text: string) => void;
}

export interface Idea {
  ordinal: number;
  title: string;
  pitch: string;
  detail: string;
  banked: boolean;
  banked_record_id: number | null;
}

export interface Generation {
  id: number;
  prompt: string;
  project: string | null;
  model: string;
  mode: "options" | "freeform";
  output: string;
  created_at: string;
  ideas: Idea[];
}

export interface GenerateDone {
  generation_id: number;
  ideas: number;
  mode: string;
}

export interface BankResult {
  banked: VaultRecord[];
  skipped: { ordinal: number; reason: string }[];
}

export interface SyncItemResult {
  idempotency_key: string | null;
  status: "stored" | "already_stored" | "duplicate" | "failed";
  record: VaultRecord | null;
  detail: string | null;
}

export interface SyncResponse {
  results: SyncItemResult[];
  stored: number;
  already_stored: number;
  duplicates: number;
  failed: number;
}

export interface Account {
  id: number;
  username: string;
  display_name: string;
  is_owner: boolean;
  created_at: string;
}

export interface Me {
  user: Account;
  sessions: number;
  needs_account: boolean;
}

export interface AuthState {
  configured: boolean;
  adopting_existing_vault: boolean;
  requires_token: boolean;
}

export interface SessionResponse {
  token: string;
  expires_at: string;
  user: Account;
}

/** A signed-in session: where the server is, and what proves who you are. */
export interface Connection {
  baseUrl: string;
  token: string;
  username?: string;
  displayName?: string;
  isOwner?: boolean;
  expiresAt?: string;
}

const STORAGE_KEY = "cortex.session";

/**
 * The key this used to be stored under, when the app asked for a token.
 *
 * Read once and carried across so that updating Cortex does not sign anybody
 * out of a client they already had working. The token in there is the machine
 * token, which still authenticates - as the owner.
 */
const LEGACY_STORAGE_KEY = "cortex.connection";

export function loadConnection(): Connection | null {
  for (const key of [STORAGE_KEY, LEGACY_STORAGE_KEY]) {
    try {
      const raw = localStorage.getItem(key);
      if (!raw) continue;
      const parsed = JSON.parse(raw) as Connection;
      if (parsed.baseUrl && parsed.token) return parsed;
    } catch {
      /* try the next one */
    }
  }
  return null;
}

export function saveConnection(connection: Connection): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(connection));
  localStorage.removeItem(LEGACY_STORAGE_KEY);
}

export function clearConnection(): void {
  localStorage.removeItem(STORAGE_KEY);
  localStorage.removeItem(LEGACY_STORAGE_KEY);
}

/**
 * Where to look for the server, best guess first.
 *
 * Cortex serves this app itself, so the page's own origin is nearly always
 * right - and when it is, nobody has to type an address at all. The second
 * guess covers development, where Vite serves the client on its own port and
 * the API is on 8765 beside it.
 */
export function candidateBaseUrls(): string[] {
  const origin = trimUrl(window.location.origin);
  const guesses = [origin];
  const beside = `${window.location.protocol}//${window.location.hostname}:8765`;
  if (beside !== origin) guesses.push(beside);
  return guesses;
}

/** The first candidate that answers as Cortex, or null if none do. */
export async function discoverBaseUrl(): Promise<string | null> {
  for (const candidate of candidateBaseUrls()) {
    try {
      const response = await fetch(`${candidate}/health`, {
        signal: AbortSignal.timeout(4000),
      });
      if (!response.ok) continue;
      const body = await response.json();
      if (body?.service === "cortex") return candidate;
    } catch {
      /* try the next */
    }
  }
  return null;
}

async function post<T>(baseUrl: string, path: string, body: unknown, token?: string): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;

  let response: Response;
  try {
    response = await fetch(trimUrl(baseUrl) + path, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, `Cannot reach Cortex at ${trimUrl(baseUrl)}. Is it running?`);
  }
  if (!response.ok) throw new ApiError(response.status, await describeFailure(response));
  return (await response.json()) as T;
}

/** Whether this Cortex has an owner yet, and what claiming it would take. */
export async function fetchAuthState(baseUrl: string): Promise<AuthState> {
  let response: Response;
  try {
    response = await fetch(`${trimUrl(baseUrl)}/api/auth/state`);
  } catch {
    throw new ApiError(
      0,
      `Nothing answered at ${trimUrl(baseUrl)}. Check the address and that Cortex is running.`,
    );
  }
  if (!response.ok) throw new ApiError(response.status, await describeFailure(response));
  return (await response.json()) as AuthState;
}

export function signIn(
  baseUrl: string,
  username: string,
  password: string,
  device: string,
): Promise<SessionResponse> {
  return post<SessionResponse>(baseUrl, "/api/auth/login", { username, password, device });
}

/** Create the owner account. `token` is only needed to claim a vault with notes in it. */
export function createOwner(
  baseUrl: string,
  body: { username: string; password: string; display_name?: string; device?: string },
  token?: string,
): Promise<SessionResponse> {
  return post<SessionResponse>(baseUrl, "/api/auth/setup", body, token);
}

export function connectionOf(baseUrl: string, session: SessionResponse): Connection {
  return {
    baseUrl: trimUrl(baseUrl),
    token: session.token,
    username: session.user.username,
    displayName: session.user.display_name,
    isOwner: session.user.is_owner,
    expiresAt: session.expires_at,
  };
}

/** What to call this device in the signed-in list. A hint, not an identity. */
export function deviceLabel(): string {
  const agent = navigator.userAgent;
  if (/iPhone|iPad|iPod/i.test(agent)) return "iPhone or iPad";
  if (/Android/i.test(agent)) return "Android";
  if (/Macintosh/i.test(agent)) return "Mac";
  if (/Windows/i.test(agent)) return "Windows";
  if (/Linux/i.test(agent)) return "Linux";
  return "A browser";
}

/** An API error carrying the status, so callers can react to 401 or 409. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * Narrow an unknown throw to an ApiError.
 *
 * `instanceof` alone is not reliable: dev-time module reloading (and any
 * bundling that duplicates the module) creates a second class object, and an
 * error thrown by one copy fails `instanceof` against the other. The symptom
 * is a clean message being replaced by a stringified stack.
 */
export function isApiError(value: unknown): value is ApiError {
  return (
    value instanceof ApiError ||
    (typeof value === "object" && value !== null && (value as Error).name === "ApiError")
  );
}

/** The message to show a person for any thrown value. */
export function errorMessage(value: unknown): string {
  if (isApiError(value)) return value.message;
  if (value instanceof Error) return value.message;
  return String(value);
}

function trimUrl(url: string): string {
  return url.replace(/\/+$/, "");
}

async function describeFailure(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") return body.detail;
    // FastAPI validation errors arrive as a list of field problems.
    if (Array.isArray(body?.detail)) {
      return body.detail.map((d: { msg?: string }) => d.msg ?? "invalid").join("; ");
    }
  } catch {
    /* fall through to the status text */
  }
  return response.statusText || `Request failed (${response.status})`;
}

export class CortexApi {
  private readonly connection: Connection;

  /**
   * Called when the server stops accepting this session.
   *
   * A session can end while the app is open - it expires, another device
   * revokes it, the password changes. Without this the app would keep
   * rendering a shell around requests that all fail; with it, it returns to
   * the sign-in screen the moment the server says who you are is no longer
   * true.
   */
  private readonly onUnauthorized?: () => void;

  constructor(connection: Connection, onUnauthorized?: () => void) {
    this.connection = connection;
    this.onUnauthorized = onUnauthorized;
  }

  private get headers(): HeadersInit {
    return {
      Authorization: `Bearer ${this.connection.token}`,
      "Content-Type": "application/json",
    };
  }

  private url(path: string, params?: Record<string, string | number | undefined>): string {
    const url = new URL(trimUrl(this.connection.baseUrl) + path);
    for (const [key, value] of Object.entries(params ?? {})) {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, String(value));
      }
    }
    return url.toString();
  }

  private async request<T>(path: string, init?: RequestInit & { params?: Record<string, string | number | undefined> }): Promise<T> {
    const { params, ...rest } = init ?? {};
    let response: Response;
    try {
      response = await fetch(this.url(path, params), { ...rest, headers: this.headers });
    } catch {
      throw new ApiError(0, `Cannot reach Cortex at ${this.connection.baseUrl}. Is it running?`);
    }
    if (!response.ok) {
      if (response.status === 401) this.onUnauthorized?.();
      throw new ApiError(response.status, await describeFailure(response));
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  // --- account ------------------------------------------------------------

  me = () => this.request<Me>("/api/auth/me");

  logout = () => this.request<void>("/api/auth/logout", { method: "POST" });

  revokeSessions = () =>
    this.request<void>("/api/auth/sessions/revoke", { method: "POST" });

  setDisplayName = (display_name: string) =>
    this.request<Me>("/api/auth/me", {
      method: "PATCH",
      body: JSON.stringify({ display_name }),
    });

  /** Change your own password. Every device signs out; the reply re-signs this one in. */
  changePassword = (current_password: string, new_password: string) =>
    this.request<SessionResponse>("/api/auth/password", {
      method: "POST",
      body: JSON.stringify({ current_password, new_password }),
    });

  accounts = () => this.request<Account[]>("/api/users");

  createAccount = (body: { username: string; password: string; display_name?: string }) =>
    this.request<Account>("/api/users", { method: "POST", body: JSON.stringify(body) });

  deleteAccount = (id: number, purge = false) =>
    this.request<void>(`/api/users/${id}`, {
      method: "DELETE",
      params: { purge: purge ? "true" : undefined },
    });

  status = () => this.request<Status>("/api/status");
  projects = () => this.request<Project[]>("/api/projects");

  updateProject = (slug: string, patch: { name?: string; description?: string }) =>
    this.request<Project>(`/api/projects/${slug}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    });

  deleteProject = (slug: string, force = false) =>
    this.request<void>(`/api/projects/${slug}`, {
      method: "DELETE",
      params: { force: force ? "true" : undefined },
    });
  models = () => this.request<ModelInfo[]>("/api/models");
  settings = () => this.request<Settings>("/api/settings");

  updateSettings = (patch: Partial<Pick<Settings, "librarian_model" | "creative_model">>) =>
    this.request<Settings>("/api/settings", { method: "PATCH", body: JSON.stringify(patch) });

  records = (params: { project?: string; limit?: number; offset?: number } = {}) =>
    this.request<RecordList>("/api/records", { params });

  record = (id: number) => this.request<VaultRecord>(`/api/records/${id}`);

  search = (q: string, params: { project?: string; limit?: number } = {}) =>
    this.request<{ query: string; hits: SearchHit[] }>("/api/search", { params: { q, ...params } });

  capture = (input: CaptureInput) =>
    this.request<CaptureResult>("/api/records", { method: "POST", body: JSON.stringify(input) });

  updateRecord = (
    id: number,
    patch: Partial<
      Pick<VaultRecord, "project" | "title" | "body" | "category" | "subcategory">
    > & { expected_updated_at?: string },
  ) =>
    this.request<VaultRecord>(`/api/records/${id}`, { method: "PATCH", body: JSON.stringify(patch) });

  deleteRecord = (id: number) =>
    this.request<void>(`/api/records/${id}`, { method: "DELETE" });

  /** Drain a batch of captures queued offline. */
  sync = (captures: CaptureInput[]) =>
    this.request<SyncResponse>("/api/sync", {
      method: "POST",
      body: JSON.stringify({ captures }),
    });

  generations = () => this.request<Generation[]>("/api/generations");

  generation = (id: number) => this.request<Generation>(`/api/generations/${id}`);

  deleteGeneration = (id: number) =>
    this.request<void>(`/api/generations/${id}`, { method: "DELETE" });

  splitGeneration = (id: number) =>
    this.request<Generation>(`/api/generations/${id}/split`, { method: "POST" });

  bankIdeas = (
    id: number,
    body: { ordinals: number[]; project?: string | null; verbatim?: boolean },
  ) =>
    this.request<BankResult>(`/api/generations/${id}/bank`, {
      method: "POST",
      body: JSON.stringify(body),
    });

  /** Brainstorm, streaming progress while the model works. */
  async brainstorm(
    body: {
      prompt: string;
      mode: "options" | "freeform";
      count?: number;
      project?: string | null;
      use_context?: boolean;
    },
    handlers: { onStatus?: (m: string) => void; onToken?: (t: string) => void } = {},
    signal?: AbortSignal,
  ): Promise<GenerateDone> {
    return this.readEvents(
      "/api/generations",
      body,
      {
        status: (p) => handlers.onStatus?.(String(p)),
        token: (p) => handlers.onToken?.(String(p)),
      },
      signal,
    );
  }

  threads = () => this.request<Thread[]>("/api/threads");

  thread = (id: number) => this.request<ThreadDetail>(`/api/threads/${id}`);

  createThread = (body: { title?: string; project?: string | null }) =>
    this.request<Thread>("/api/threads", { method: "POST", body: JSON.stringify(body) });

  updateThread = (
    id: number,
    patch: { title?: string; project?: string | null; clear_project?: boolean },
  ) => this.request<Thread>(`/api/threads/${id}`, { method: "PATCH", body: JSON.stringify(patch) });

  deleteThread = (id: number) =>
    this.request<void>(`/api/threads/${id}`, { method: "DELETE" });

  /** Ask a question in a thread, streaming the answer as it is produced. */
  async ask(
    threadId: number,
    message: string,
    handlers: AnswerHandlers = {},
    signal?: AbortSignal,
  ): Promise<AnswerDone> {
    return this.readEvents(
      `/api/threads/${threadId}/messages`,
      { message },
      {
        status: (p) => handlers.onStatus?.(p.message ?? String(p)),
        sources: (p) => handlers.onSources?.(p as string[]),
        token: (p) => handlers.onToken?.(String(p)),
      },
      signal,
    );
  }

  /**
   * Read a server-sent event stream from a POST.
   *
   * EventSource cannot POST, so the body is parsed off fetch directly. The
   * stream must end with exactly one terminal event; one that ends without
   * one is a failure, not a silent success.
   */
  private async readEvents<T>(
    path: string,
    body: unknown,
    handlers: Record<string, (payload: any) => void>,
    signal?: AbortSignal,
  ): Promise<T> {
    let response: Response;
    try {
      response = await fetch(this.url(path), {
        method: "POST",
        headers: this.headers,
        body: JSON.stringify(body),
        signal,
      });
    } catch (cause) {
      if ((cause as Error)?.name === "AbortError") throw cause;
      throw new ApiError(0, `Cannot reach Cortex at ${this.connection.baseUrl}. Is it running?`);
    }

    if (!response.ok) throw new ApiError(response.status, await describeFailure(response));
    if (!response.body) throw new ApiError(0, "The server sent an empty response.");

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let done: T | null = null;

    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });

      let split: number;
      while ((split = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, split);
        buffer = buffer.slice(split + 2);

        let event = "message";
        let data = "";
        for (const line of frame.split("\n")) {
          if (line.startsWith("event: ")) event = line.slice(7).trim();
          else if (line.startsWith("data: ")) data += line.slice(6);
        }
        if (!data) continue;
        const payload = JSON.parse(data);

        if (event === "error") throw new ApiError(payload.status ?? 500, payload.detail);
        if (event === "done") done = payload as T;
        else handlers[event]?.(payload);
      }
    }

    if (!done) throw new ApiError(0, "The answer ended without completing.");
    return done;
  }

  /**
   * Capture with progress. A local 14B model takes ten to twenty seconds, so
   * the stream reports each stage as it begins.
   *
   * EventSource cannot POST, so this reads the SSE body off fetch directly.
   * Exactly one terminal event ends the stream; a stream that ends without
   * one is treated as a failure rather than silently succeeding.
   */
  async captureStreaming(
    input: CaptureInput,
    onProgress: (stage: string, message: string) => void,
    signal?: AbortSignal,
  ): Promise<CaptureResult> {
    let response: Response;
    try {
      response = await fetch(this.url("/api/records/stream"), {
        method: "POST",
        headers: this.headers,
        body: JSON.stringify(input),
        signal,
      });
    } catch (cause) {
      if ((cause as Error)?.name === "AbortError") throw cause;
      throw new ApiError(0, `Cannot reach Cortex at ${this.connection.baseUrl}. Is it running?`);
    }

    if (!response.ok) throw new ApiError(response.status, await describeFailure(response));
    if (!response.body) throw new ApiError(0, "The server sent an empty response.");

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let result: CaptureResult | null = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE frames are separated by a blank line.
      let split: number;
      while ((split = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, split);
        buffer = buffer.slice(split + 2);

        let event = "message";
        let data = "";
        for (const line of frame.split("\n")) {
          if (line.startsWith("event: ")) event = line.slice(7).trim();
          else if (line.startsWith("data: ")) data += line.slice(6);
        }
        if (!data) continue;
        const payload = JSON.parse(data);

        if (event === "progress") onProgress(payload.stage, payload.message);
        else if (event === "error") throw new ApiError(payload.status ?? 500, payload.detail);
        else if (event === "record") result = payload as CaptureResult;
      }
    }

    if (!result) throw new ApiError(0, "The capture ended without confirming it was saved.");
    return result;
  }
}

/** Probe a server before trying to sign in, so a bad address fails with a reason. */
export async function verifyConnection(connection: Connection): Promise<void> {
  const base = trimUrl(connection.baseUrl);
  let health: Response;
  try {
    health = await fetch(`${base}/health`);
  } catch {
    throw new ApiError(0, `Nothing answered at ${base}. Check the address and that Cortex is running.`);
  }
  if (!health.ok) throw new ApiError(health.status, `${base} answered, but not like Cortex.`);

  const authed = await fetch(`${base}/api/status`, {
    headers: { Authorization: `Bearer ${connection.token}` },
  });
  if (authed.status === 401) throw new ApiError(401, "That sign-in is no longer valid.");
  if (!authed.ok) throw new ApiError(authed.status, await describeFailure(authed));
}
