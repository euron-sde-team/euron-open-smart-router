import type { Metadata } from "next";
import { Suspense } from "react";

import { EndpointsScreen } from "@/features/models/components/endpoints-screen";

export const metadata: Metadata = { title: "Endpoints" };

export default function Page() {
  return (
    <Suspense>
      <EndpointsScreen />
    </Suspense>
  );
}
