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

export interface Connection {
  baseUrl: string;
  token: string;
}

const STORAGE_KEY = "cortex.connection";

export function loadConnection(): Connection | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Connection;
    return parsed.baseUrl && parsed.token ? parsed : null;
  } catch {
    return null;
  }
}

export function saveConnection(connection: Connection): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(connection));
}

export function clearConnection(): void {
  localStorage.removeItem(STORAGE_KEY);
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

  constructor(connection: Connection) {
    this.connection = connection;
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
    if (!response.ok) throw new ApiError(response.status, await describeFailure(response));
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  status = () => this.request<Status>("/api/status");
  projects = () => this.request<Project[]>("/api/projects");
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
    patch: Partial<Pick<VaultRecord, "project" | "title" | "body" | "category" | "subcategory">>,
  ) =>
    this.request<VaultRecord>(`/api/records/${id}`, { method: "PATCH", body: JSON.stringify(patch) });

  deleteRecord = (id: number) =>
    this.request<void>(`/api/records/${id}`, { method: "DELETE" });

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

/** Probe a server before saving it, so setup fails with a reason. */
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
  if (authed.status === 401) throw new ApiError(401, "That token was rejected. Run `cortex token` to see the right one.");
  if (!authed.ok) throw new ApiError(authed.status, await describeFailure(authed));
}
