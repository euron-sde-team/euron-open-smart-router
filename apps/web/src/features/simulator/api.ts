"use client";

import { useMutation } from "@tanstack/react-query";

import { api, unwrap } from "@/lib/api/client";
import type { SimulateIn } from "@/lib/api/types";

/** Rank models for a request without calling any. A mutation: every run is a fresh question. */
export function useSimulate() {
  return useMutation({ mutationFn: (body: SimulateIn) => unwrap(api.POST("/api/v1/route/simulate", { body })) });
}
