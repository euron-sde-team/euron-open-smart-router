"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "@/lib/api/client";
import type { PolicyIn, PolicyUpdate } from "@/lib/api/types";
import { qk, STALE } from "@/lib/query-keys";

export function usePolicies() {
  return useQuery({ queryKey: qk.policies, queryFn: () => unwrap(api.GET("/api/v1/policies")), staleTime: STALE.config });
}

export function useCreatePolicy() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: PolicyIn) => unwrap(api.POST("/api/v1/policies", { body })),
    onSuccess: () => void client.invalidateQueries({ queryKey: qk.policies }),
  });
}

export function useUpdatePolicy() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: PolicyUpdate }) =>
      unwrap(api.PATCH("/api/v1/policies/{policy_id}", { params: { path: { policy_id: id } }, body })),
    onSuccess: () => void client.invalidateQueries({ queryKey: qk.policies }),
  });
}

export function useDeletePolicy() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => unwrap(api.DELETE("/api/v1/policies/{policy_id}", { params: { path: { policy_id: id } } })),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: qk.policies });
      void client.invalidateQueries({ queryKey: qk.projectsAll });
    },
  });
}
