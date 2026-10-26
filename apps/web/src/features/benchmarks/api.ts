"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "@/lib/api/client";
import type { BenchmarkRun, BenchmarkRunIn } from "@/lib/api/types";
import { qk, STALE } from "@/lib/query-keys";

const active = (runs: BenchmarkRun[] | undefined) => (runs ?? []).some((r) => r.status === "queued" || r.status === "running");

/** Recent runs; polls every 3 s only while one is queued or running. */
export function useBenchmarkRuns() {
  return useQuery({
    queryKey: qk.benchmarkRuns,
    queryFn: () => unwrap(api.GET("/api/v1/benchmarks/runs")),
    staleTime: STALE.config,
    refetchInterval: (q) => (active(q.state.data) ? 3000 : false),
  });
}

export function useBenchmarkRun(id: string | null) {
  return useQuery({
    queryKey: qk.benchmarkRun(id ?? ""),
    queryFn: () => unwrap(api.GET("/api/v1/benchmarks/runs/{run_id}", { params: { path: { run_id: id ?? "" } } })),
    enabled: !!id,
    staleTime: STALE.static,
  });
}

export function useSuite() {
  return useQuery({ queryKey: qk.benchmarkSuite, queryFn: () => unwrap(api.GET("/api/v1/benchmarks/suite")), staleTime: STALE.static });
}

export function useStartBenchmark() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: BenchmarkRunIn) => unwrap(api.POST("/api/v1/benchmarks/runs", { body })),
    onSuccess: () => void client.invalidateQueries({ queryKey: qk.benchmarkRuns }),
  });
}

/** Resolve the per-case results link (presigned S3, or an API path on local storage), then download. */
export function useDownloadCases() {
  return useMutation({
    mutationFn: async (runId: string) => {
      const link = await unwrap(api.GET("/api/v1/benchmarks/runs/{run_id}/cases", { params: { path: { run_id: runId } } }));
      const a = document.createElement("a");
      a.href = link.url;
      a.download = link.filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      return link;
    },
  });
}
