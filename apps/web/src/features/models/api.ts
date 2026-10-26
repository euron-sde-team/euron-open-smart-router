"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "@/lib/api/client";
import type { ModelIn, ModelUpdate, ProviderIn, ProviderUpdate, QualityOverride } from "@/lib/api/types";
import { qk, STALE } from "@/lib/query-keys";

export function useModels() {
  return useQuery({ queryKey: qk.models, queryFn: () => unwrap(api.GET("/api/v1/models")), staleTime: STALE.config });
}

/** Live circuit state and latency per model; refreshed while the page is open. */
export function useModelHealth() {
  return useQuery({
    queryKey: qk.health,
    queryFn: () => unwrap(api.GET("/api/v1/health/models")),
    staleTime: STALE.live,
    refetchInterval: 10_000,
  });
}

export function useResetCircuit() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (alias: string) =>
      unwrap(api.POST("/api/v1/health/models/{alias}/reset", { params: { path: { alias } } })),
    onSuccess: () => void client.invalidateQueries({ queryKey: qk.health }),
  });
}

export function useQuality() {
  return useQuery({ queryKey: qk.quality, queryFn: () => unwrap(api.GET("/api/v1/quality")), staleTime: STALE.config });
}

export function useSetQuality() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: QualityOverride) => unwrap(api.PUT("/api/v1/quality", { body })),
    onSuccess: () => void client.invalidateQueries({ queryKey: qk.quality }),
  });
}

export function useCreateModel() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: ModelIn) => unwrap(api.POST("/api/v1/models", { body })),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: qk.models });
      void client.invalidateQueries({ queryKey: qk.providers });
      void client.invalidateQueries({ queryKey: qk.health });
    },
  });
}

export function useUpdateModel() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ModelUpdate }) =>
      unwrap(api.PATCH("/api/v1/models/{model_id}", { params: { path: { model_id: id } }, body })),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: qk.models });
      void client.invalidateQueries({ queryKey: qk.health });
    },
  });
}

export function useProviders() {
  return useQuery({ queryKey: qk.providers, queryFn: () => unwrap(api.GET("/api/v1/providers")), staleTime: STALE.config });
}

export function useCreateProvider() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: ProviderIn) => unwrap(api.POST("/api/v1/providers", { body })),
    onSuccess: () => void client.invalidateQueries({ queryKey: qk.providers }),
  });
}

export function useUpdateProvider() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ProviderUpdate }) =>
      unwrap(api.PATCH("/api/v1/providers/{provider_id}", { params: { path: { provider_id: id } }, body })),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: qk.providers });
      void client.invalidateQueries({ queryKey: qk.models });
    },
  });
}

export function useTestProvider() {
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(api.POST("/api/v1/providers/{provider_id}/test", { params: { path: { provider_id: id } } })),
  });
}
