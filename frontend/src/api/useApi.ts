import { useCallback, useEffect, useRef, useState } from "react";

interface ApiState<T> {
  data: T | null;
  error: string | null;
  failure: unknown; // the original error, for ApiErrorBanner
  loading: boolean;
  reload: () => Promise<void>;
  setData: (data: T) => void;
}

/** Fetch on mount, optionally poll, and expose reload(). Polling pauses while the tab is hidden (nobody is looking) and
 *  refreshes at once when it becomes visible again. reload() always fetches. */
export function useApi<T>(fetcher: () => Promise<T>, pollMs?: number): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [failure, setFailure] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const reload = useCallback(async () => {
    try {
      setData(await fetcherRef.current());
      setFailure(null);
    } catch (e) {
      setFailure(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
    if (!pollMs) return;
    const id = window.setInterval(() => { if (!document.hidden) void reload(); }, pollMs);
    const vis = () => { if (!document.hidden) void reload(); };
    document.addEventListener("visibilitychange", vis);
    return () => { window.clearInterval(id); document.removeEventListener("visibilitychange", vis); };
  }, [reload, pollMs]);

  const error = failure ? (failure instanceof Error ? failure.message : String(failure)) : null;
  return { data, error, failure, loading, reload, setData };
}
