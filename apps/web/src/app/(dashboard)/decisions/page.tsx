import type { Metadata } from "next";
import { Suspense } from "react";

import { DecisionsScreen } from "@/features/decisions/components/decisions-screen";

export const metadata: Metadata = { title: "Decisions" };

export default function Page() {
  return (
    <Suspense>
      <DecisionsScreen />
    </Suspense>
  );
}
