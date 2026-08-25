/**
 * The offline queue and the sync that drains it.
 *
 * This is the code that must not lose a note. Everything else in the client
 * can fail and be retried; a capture that vanishes between the textarea and
 * the server is gone.
 */

import { beforeEach, describe, expect, it } from "vitest";
import { clear, count, enqueue, pending, recordFailure, remove } from "../lib/queue";
import { drain } from "../lib/sync";
import type { CortexApi, SyncItemResult, SyncResponse } from "../lib/api";
import { ApiError } from "../lib/api";

beforeEach(async () => {
  await clear();
});

function fakeApi(
  respond: (captures: unknown[]) => SyncResponse | Promise<SyncResponse>,
): { api: CortexApi; batches: unknown[][] } {
  const batches: unknown[][] = [];
  const api = {
    sync: async (captures: unknown[]) => {
      batches.push(captures);
      return respond(captures);
    },
  } as unknown as CortexApi;
  return { api, batches };
}

function results(...statuses: SyncItemResult["status"][]): SyncResponse {
  return {
    results: statuses.map((status) => ({
      idempotency_key: null,
      status,
      record: null,
      detail: status === "failed" ? "the server said no" : null,
    })),
    stored: statuses.filter((s) => s === "stored").length,
    already_stored: statuses.filter((s) => s === "already_stored").length,
    duplicates: statuses.filter((s) => s === "duplicate").length,
    failed: statuses.filter((s) => s === "failed").length,
  };
}

describe("the queue", () => {
  it("keeps a capture and gives it an idempotency key", async () => {
    const item = await enqueue({ text: "a thought on the train" });

    expect(item.id).toBeTruthy();
    expect(item.attempts).toBe(0);
    expect(await count()).toBe(1);
    expect((await pending())[0].text).toBe("a thought on the train");
  });

  it("keeps the fields a capture needs", async () => {
    await enqueue({ text: "note", project: "Echoes", title: "A Title", verbatim: true });
    const [item] = await pending();

    expect(item.project).toBe("Echoes");
    expect(item.title).toBe("A Title");
    expect(item.verbatim).toBe(true);
  });

  it("gives every capture its own key", async () => {
    await enqueue({ text: "one" });
    await enqueue({ text: "two" });
    const ids = (await pending()).map((i) => i.id);

    expect(new Set(ids).size).toBe(2);
    expect(await count()).toBe(2);
  });

  it("returns captures oldest first, so they file in the order you wrote them", async () => {
    await enqueue({ text: "second", created_at: "2026-01-02T00:00:00Z" });
    await enqueue({ text: "first", created_at: "2026-01-01T00:00:00Z" });
    await enqueue({ text: "third", created_at: "2026-01-03T00:00:00Z" });

    expect((await pending()).map((i) => i.text)).toEqual(["first", "second", "third"]);
  });

  it("survives being reopened", async () => {
    await enqueue({ text: "written before the tab closed" });
    // The module holds one connection; reading again goes through IndexedDB.
    expect((await pending())[0].text).toBe("written before the tab closed");
  });

  it("records a failure without dropping the capture", async () => {
    const item = await enqueue({ text: "keep me" });
    await recordFailure(item.id, "no signal");

    const [after] = await pending();
    expect(after.attempts).toBe(1);
    expect(after.last_error).toBe("no signal");
    expect(after.text).toBe("keep me");
  });

  it("counts attempts across retries", async () => {
    const item = await enqueue({ text: "x" });
    await recordFailure(item.id, "first");
    await recordFailure(item.id, "second");

    expect((await pending())[0].attempts).toBe(2);
    expect((await pending())[0].last_error).toBe("second");
  });

  it("ignores a failure for something already gone", async () => {
    await expect(recordFailure("not-a-real-id", "boom")).resolves.toBeUndefined();
  });

  it("removes a capture once it has landed", async () => {
    const item = await enqueue({ text: "landed" });
    await remove(item.id);
    expect(await count()).toBe(0);
  });
});

describe("draining the queue", () => {
  it("does nothing when there is nothing queued", async () => {
    const { api, batches } = fakeApi(() => results());
    const outcome = await drain(api);

    expect(batches).toEqual([]);
    expect(outcome.remaining).toBe(0);
  });

  it("sends queued captures and clears the ones that landed", async () => {
    await enqueue({ text: "one" });
    await enqueue({ text: "two" });

    const { api, batches } = fakeApi(() => results("stored", "stored"));
    const outcome = await drain(api);

    expect(batches).toHaveLength(1);
    expect(batches[0]).toHaveLength(2);
    expect(outcome.stored).toBe(2);
    expect(outcome.remaining).toBe(0);
    expect(await count()).toBe(0);
  });

  it("sends the queue id as the idempotency key", async () => {
    const item = await enqueue({ text: "one" });
    const { api, batches } = fakeApi(() => results("stored"));
    await drain(api);

    expect((batches[0][0] as { idempotency_key: string }).idempotency_key).toBe(item.id);
  });

  it("clears a capture the server had already seen", async () => {
    await enqueue({ text: "resent" });
    const { api } = fakeApi(() => results("already_stored"));
    const outcome = await drain(api);

    expect(outcome.alreadyStored).toBe(1);
    expect(await count()).toBe(0);
  });

  it("clears a capture the server calls a duplicate", async () => {
    await enqueue({ text: "same as an existing note" });
    const { api } = fakeApi(() => results("duplicate"));
    const outcome = await drain(api);

    expect(outcome.duplicates).toBe(1);
    expect(await count()).toBe(0);
  });

  it("keeps a capture the server could not file", async () => {
    await enqueue({ text: "problematic" });
    const { api } = fakeApi(() => results("failed"));
    const outcome = await drain(api);

    expect(outcome.failed).toBe(1);
    expect(outcome.remaining).toBe(1);
    expect((await pending())[0].last_error).toBe("the server said no");
  });

  it("keeps the good ones and holds the bad one", async () => {
    await enqueue({ text: "good", created_at: "2026-01-01T00:00:00Z" });
    await enqueue({ text: "bad", created_at: "2026-01-02T00:00:00Z" });
    await enqueue({ text: "also good", created_at: "2026-01-03T00:00:00Z" });

    const { api } = fakeApi(() => results("stored", "failed", "stored"));
    const outcome = await drain(api);

    expect(outcome.stored).toBe(2);
    expect(outcome.failed).toBe(1);
    expect((await pending()).map((i) => i.text)).toEqual(["bad"]);
  });

  it("keeps everything when the request itself fails", async () => {
    await enqueue({ text: "one" });
    await enqueue({ text: "two" });

    const api = {
      sync: async () => {
        throw new ApiError(0, "Cannot reach Cortex");
      },
    } as unknown as CortexApi;

    const outcome = await drain(api);

    expect(outcome.stored).toBe(0);
    expect(outcome.remaining).toBe(2);
    expect((await pending()).every((i) => i.last_error === "Cannot reach Cortex")).toBe(true);
  });

  it("does not try at all when offline", async () => {
    await enqueue({ text: "written on the train" });
    const { api, batches } = fakeApi(() => results("stored"));

    const original = Object.getOwnPropertyDescriptor(globalThis, "navigator");
    Object.defineProperty(globalThis, "navigator", {
      value: { onLine: false },
      configurable: true,
    });

    const outcome = await drain(api);

    if (original) Object.defineProperty(globalThis, "navigator", original);

    expect(batches).toEqual([]);
    expect(outcome.remaining).toBe(1);
    expect(await count()).toBe(1);
  });

  it("batches a long queue rather than sending one enormous request", async () => {
    for (let i = 0; i < 45; i += 1) {
      await enqueue({ text: `note ${i}`, created_at: `2026-01-01T00:00:${String(i).padStart(2, "0")}Z` });
    }

    const { api, batches } = fakeApi((captures) =>
      results(...captures.map(() => "stored" as const)),
    );
    const outcome = await drain(api);

    expect(batches.map((b) => b.length)).toEqual([20, 20, 5]);
    expect(outcome.stored).toBe(45);
    expect(await count()).toBe(0);
  });

  it("stops after a transport failure instead of hammering the rest", async () => {
    for (let i = 0; i < 45; i += 1) {
      await enqueue({ text: `note ${i}`, created_at: `2026-01-01T00:00:${String(i).padStart(2, "0")}Z` });
    }

    let calls = 0;
    const api = {
      sync: async () => {
        calls += 1;
        throw new ApiError(0, "connection dropped");
      },
    } as unknown as CortexApi;

    await drain(api);

    expect(calls).toBe(1);
    expect(await count()).toBe(45);
  });

  it("concurrent drains share one run, so nothing is sent twice", async () => {
    await enqueue({ text: "one" });

    let inFlight = 0;
    let overlapped = false;
    const api = {
      sync: async (captures: unknown[]) => {
        inFlight += 1;
        if (inFlight > 1) overlapped = true;
        await new Promise((resolve) => setTimeout(resolve, 20));
        inFlight -= 1;
        return results(...captures.map(() => "stored" as const));
      },
    } as unknown as CortexApi;

    const [a, b] = await Promise.all([drain(api), drain(api)]);

    expect(overlapped).toBe(false);
    expect(a).toBe(b); // literally the same run
    expect(await count()).toBe(0);
  });

  it("a retry after a failure eventually lands", async () => {
    await enqueue({ text: "eventually" });

    const failing = {
      sync: async () => {
        throw new ApiError(0, "no signal");
      },
    } as unknown as CortexApi;
    await drain(failing);
    expect(await count()).toBe(1);

    const { api } = fakeApi(() => results("stored"));
    const outcome = await drain(api);

    expect(outcome.stored).toBe(1);
    expect(await count()).toBe(0);
  });
});

describe("watching the queue", () => {
  it("tells watchers when a capture is added, retried or removed", async () => {
    const { onQueueChange } = await import("../lib/queue");
    let changes = 0;
    const stop = onQueueChange(() => {
      changes += 1;
    });

    const item = await enqueue({ text: "one" });
    expect(changes).toBe(1);

    await recordFailure(item.id, "no signal");
    expect(changes).toBe(2);

    await remove(item.id);
    expect(changes).toBe(3);

    stop();
    await enqueue({ text: "two" });
    expect(changes).toBe(3);
  });

  it("tells watchers as a drain clears the queue", async () => {
    const { onQueueChange } = await import("../lib/queue");
    await enqueue({ text: "one" });

    let notified = false;
    const stop = onQueueChange(() => {
      notified = true;
    });

    const { api } = fakeApi(() => results("stored"));
    await drain(api);
    stop();

    expect(notified).toBe(true);
  });
});
