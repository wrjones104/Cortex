/**
 * The offline capture queue.
 *
 * Everything captured on the phone goes here first and the UI returns
 * immediately. A note is only removed once the server has confirmed it. That
 * ordering is the whole point: capture must never wait for a round trip, and
 * must never depend on one succeeding.
 *
 * IndexedDB rather than localStorage because localStorage is synchronous,
 * capped at a few megabytes, and stringly-typed — none of which suits a
 * write-ahead log for things you cannot afford to lose.
 */

const DB_NAME = "cortex";
const DB_VERSION = 1;
const STORE = "outbox";

export interface QueuedCapture {
  /** Client-generated, and sent as the idempotency key, so a retry that the
   *  server already saw returns the original record instead of a duplicate. */
  id: string;
  text: string;
  project: string | null;
  title: string | null;
  verbatim: boolean;
  created_at: string;
  attempts: number;
  last_error: string | null;
}

/**
 * Watchers of the queue's size.
 *
 * The nav badge and the capture screen each want to know how many notes are
 * waiting, and either can be the one that changes it. Without a notification
 * the badge stays stale until something else happens to refresh it - which is
 * exactly when you most want to see that a note is queued.
 */
const watchers = new Set<() => void>();

export function onQueueChange(listener: () => void): () => void {
  watchers.add(listener);
  return () => {
    watchers.delete(listener);
  };
}

function announce(): void {
  for (const listener of watchers) listener();
}

let opening: Promise<IDBDatabase> | null = null;

function open(): Promise<IDBDatabase> {
  if (opening) return opening;
  opening = new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE)) {
        const store = db.createObjectStore(STORE, { keyPath: "id" });
        store.createIndex("created_at", "created_at");
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  return opening;
}

async function withStore<T>(
  mode: IDBTransactionMode,
  work: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  const db = await open();
  return new Promise<T>((resolve, reject) => {
    const transaction = db.transaction(STORE, mode);
    const request = work(transaction.objectStore(STORE));
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
    transaction.onabort = () => reject(transaction.error);
  });
}

function newId(): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  // Older WebViews have crypto but not randomUUID.
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function enqueue(
  capture: Pick<QueuedCapture, "text"> & Partial<QueuedCapture>,
): Promise<QueuedCapture> {
  const item: QueuedCapture = {
    id: capture.id ?? newId(),
    text: capture.text,
    project: capture.project ?? null,
    title: capture.title ?? null,
    verbatim: capture.verbatim ?? false,
    created_at: capture.created_at ?? new Date().toISOString(),
    attempts: capture.attempts ?? 0,
    last_error: capture.last_error ?? null,
  };
  await withStore("readwrite", (store) => store.put(item));
  announce();
  return item;
}

export async function pending(): Promise<QueuedCapture[]> {
  const all = await withStore<QueuedCapture[]>("readonly", (store) => store.getAll());
  return all.sort((a, b) => a.created_at.localeCompare(b.created_at));
}

export async function count(): Promise<number> {
  return withStore<number>("readonly", (store) => store.count());
}

export async function remove(id: string): Promise<void> {
  await withStore("readwrite", (store) => store.delete(id));
  announce();
}

export async function recordFailure(id: string, reason: string): Promise<void> {
  const existing = await withStore<QueuedCapture | undefined>("readonly", (store) =>
    store.get(id),
  );
  if (!existing) return;
  await withStore("readwrite", (store) =>
    store.put({ ...existing, attempts: existing.attempts + 1, last_error: reason }),
  );
  announce();
}

/** Only for tests and for a deliberate "discard everything" action. */
export async function clear(): Promise<void> {
  await withStore("readwrite", (store) => store.clear());
  announce();
}
