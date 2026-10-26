"use client";

import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { api, unwrap } from "@/lib/api/client";
import type { DecisionFilters } from "@/lib/api/types";
import { qk, STALE } from "@/lib/query-keys";
import { useUiPrefs } from "@/lib/store";

/** The decision log, newest first, keyset-paginated; refreshes while live mode is on. */
export function useDecisions(filters: Omit<DecisionFilters, "cursor">) {
  const autoRefresh = useUiPrefs((s) => s.autoRefresh);
  return useInfiniteQuery({
    queryKey: qk.decisions(filters),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      unwrap(api.GET("/api/v1/decisions", { params: { query: { ...filters, cursor: pageParam ?? undefined, limit: 50 } } })),
    getNextPageParam: (last) => last.next_cursor ?? null,
    staleTime: STALE.live,
    refetchInterval: autoRefresh ? 10_000 : false,
    placeholderData: keepPreviousData,
  });
}

export function useDecision(requestId: string | null) {
  return useQuery({
    queryKey: qk.decision(requestId ?? ""),
    queryFn: () => unwrap(api.GET("/api/v1/decisions/{request_id}", { params: { path: { request_id: requestId ?? "" } } })),
    enabled: !!requestId,
    staleTime: STALE.static,
  });
}
