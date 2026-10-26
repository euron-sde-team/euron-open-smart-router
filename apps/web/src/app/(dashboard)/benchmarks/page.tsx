import type { Metadata } from "next";
import { Suspense } from "react";

import { BenchmarksScreen } from "@/features/benchmarks/components/benchmarks-screen";

export const metadata: Metadata = { title: "Benchmarks" };

export default function Page() {
  return (
    <Suspense>
      <BenchmarksScreen />
    </Suspense>
  );
}
