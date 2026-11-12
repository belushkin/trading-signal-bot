import { useCallback, useEffect, useState } from "react";
import { get, queryString, send, Signal } from "./api";
import type { Episode, EpisodeReview } from "./episodes";

const PAGE = 100;
const MAX_PAGES = 5;

type Loaded = { signals: Signal[] | null; reviews: EpisodeReview[]; reviewsAvailable: boolean; truncated: boolean };

async function loadSignals(days: number, abort: AbortSignal): Promise<{ items: Signal[]; truncated: boolean }> {
  const items: Signal[] = [];
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const result = await get<{ items?: Signal[]; total?: number }>(`/api/signals${queryString({ days, limit: PAGE, offset: page * PAGE })}`, abort);
    if (!Array.isArray(result.items)) break;
    items.push(...result.items);
    if (result.items.length < PAGE || items.length >= (result.total ?? 0)) return { items, truncated: false };
  }
  return { items, truncated: true };
}

// Loads individual signals for grouping plus the persisted review state.
export function useEpisodeData(days: number, refreshSeconds: number) {
  const [state, setState] = useState<Loaded>({ signals: null, reviews: [], reviewsAvailable: true, truncated: false });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      const [signals, reviews] = await Promise.allSettled([
        loadSignals(days, controller.signal),
        get<{ items?: EpisodeReview[] }>("/api/episode-reviews", controller.signal),
      ]);
      if (controller.signal.aborted) return;
      setState({
        signals: signals.status === "fulfilled" ? signals.value.items : null,
        truncated: signals.status === "fulfilled" && signals.value.truncated,
        reviews: reviews.status === "fulfilled" && Array.isArray(reviews.value.items) ? reviews.value.items : [],
        reviewsAvailable: reviews.status === "fulfilled" && Array.isArray(reviews.value.items),
      });
    };
    void load();
    const timer = window.setInterval(() => void load(), refreshSeconds * 1000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [days, refreshSeconds, tick]);

  const reload = useCallback(() => setTick((value) => value + 1), []);

  const review = useCallback(
    async (episode: Episode, status: "reviewed" | "dismissed") => {
      await send(`/api/episode-reviews/${encodeURIComponent(episode.key)}`, "PUT", {
        status,
        signal_count: episode.signals.length,
        max_severity: episode.maxSeverity,
        last_signal_at: new Date(episode.end).toISOString(),
      });
      reload();
    },
    [reload],
  );

  const reopen = useCallback(
    async (episode: Episode) => {
      await send(`/api/episode-reviews/${encodeURIComponent(episode.key)}`, "DELETE");
      reload();
    },
    [reload],
  );

  return { ...state, review, reopen };
}
