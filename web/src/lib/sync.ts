/**
 * Draining the offline queue.
 *
 * The server side of this was built in M2: POST /api/sync takes a batch, and
 * every item carries an idempotency key so a batch the phone was unsure about
 * can be re-sent safely. One bad item never fails the batch, because a client
 * that cannot tell which notes landed will either lose them or send them all
 * again.
 *
 * Draining happens in the foreground — on app start, when the network comes
 * back, when the tab is shown again, and on demand. True background sync (the
 * service worker draining while the app is closed) is deliberately not here:
 * the connection token lives in localStorage, which a service worker cannot
 * read, so it would mean a second copy of the auth and sync logic that could
 * drift from this one. The window it would cover — between capturing offline
 * and next opening the app — is small, because opening the app is how you
 * capture the next thing anyway.
 */

import type { CortexApi } from "./api";
import { errorMessage } from "./api";
import { pending, recordFailure, remove, type QueuedCapture } from "./queue";

export interface SyncOutcome {
  stored: number;
  alreadyStored: number;
  duplicates: number;
  failed: number;
  remaining: number;
}

/** Item statuses that mean the server has it and we can stop holding a copy. */
const SETTLED = new Set(["stored", "already_stored", "duplicate"]);

const BATCH = 20;

let running: Promise<SyncOutcome> | null = null;

export function isOnline(): boolean {
  return typeof navigator === "undefined" || navigator.onLine !== false;
}

/**
 * Send everything queued. Safe to call often — concurrent calls share one run
 * rather than sending the same notes twice.
 */
export function drain(api: CortexApi): Promise<SyncOutcome> {
  if (running) return running;
  running = drainOnce(api).finally(() => {
    running = null;
  });
  return running;
}

async function drainOnce(api: CortexApi): Promise<SyncOutcome> {
  const outcome: SyncOutcome = {
    stored: 0,
    alreadyStored: 0,
    duplicates: 0,
    failed: 0,
    remaining: 0,
  };

  let queued = await pending();
  if (queued.length === 0) return outcome;

  if (!isOnline()) {
    outcome.remaining = queued.length;
    return outcome;
  }

  for (let start = 0; start < queued.length; start += BATCH) {
    const batch = queued.slice(start, start + BATCH);

    let response;
    try {
      response = await api.sync(batch.map(toCapture));
    } catch (cause) {
      // The whole request failed, so nothing in this batch was seen. Leave it
      // queued and stop: the rest will fail the same way.
      const reason = errorMessage(cause);
      await Promise.all(batch.map((item) => recordFailure(item.id, reason)));
      outcome.failed += batch.length;
      break;
    }

    for (let index = 0; index < response.results.length; index += 1) {
      const result = response.results[index];
      const item = batch[index];
      if (!item) continue;

      if (SETTLED.has(result.status)) {
        await remove(item.id);
        if (result.status === "stored") outcome.stored += 1;
        else if (result.status === "already_stored") outcome.alreadyStored += 1;
        else outcome.duplicates += 1;
      } else {
        await recordFailure(item.id, result.detail ?? "The server could not file it.");
        outcome.failed += 1;
      }
    }
  }

  queued = await pending();
  outcome.remaining = queued.length;
  return outcome;
}

function toCapture(item: QueuedCapture) {
  return {
    text: item.text,
    project: item.project,
    title: item.title,
    verbatim: item.verbatim,
    idempotency_key: item.id,
  };
}
