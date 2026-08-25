import { useCallback, useEffect, useState } from "react";
import type { CortexApi } from "./api";
import { count as queuedCount, onQueueChange } from "./queue";
import { drain, isOnline } from "./sync";

/**
 * Keeps the pending count fresh and drains the queue at the moments it can
 * plausibly succeed: on mount, when the network returns, and when the tab is
 * shown again after being backgrounded.
 */
export function useSync(api: CortexApi) {
  const [pendingCount, setPendingCount] = useState(0);
  const [online, setOnline] = useState(isOnline());
  const [syncing, setSyncing] = useState(false);

  const refresh = useCallback(async () => {
    setPendingCount(await queuedCount());
  }, []);

  const run = useCallback(async () => {
    setSyncing(true);
    try {
      const outcome = await drain(api);
      setPendingCount(outcome.remaining);
      return outcome;
    } finally {
      setSyncing(false);
    }
  }, [api]);

  // Any part of the app can add to or drain the queue, so follow the queue
  // itself rather than only this component's own actions.
  useEffect(() => onQueueChange(() => void refresh()), [refresh]);

  useEffect(() => {
    void refresh();
    void run();

    const onOnline = () => {
      setOnline(true);
      void run();
    };
    const onOffline = () => setOnline(false);
    const onVisible = () => {
      if (document.visibilityState === "visible") void run();
    };

    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [refresh, run]);

  return { pendingCount, online, syncing, refresh, sync: run };
}
