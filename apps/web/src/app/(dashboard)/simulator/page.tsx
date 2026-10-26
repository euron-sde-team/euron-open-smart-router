import type { Metadata } from "next";
import { Suspense } from "react";

import { SimulatorScreen } from "@/features/simulator/components/simulator-screen";

export const metadata: Metadata = { title: "Simulator" };

export default function Page() {
  return (
    <Suspense>
      <SimulatorScreen />
    </Suspense>
  );
}
