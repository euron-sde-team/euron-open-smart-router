"use client";

import { PlugZapIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { CodeBlock } from "@/components/code-block";
import { Surface } from "@/components/surface";
import { buttonVariants } from "@/components/ui/button";
import { curlSnippet, gatewayBaseUrl, nodeSnippet, pythonSnippet } from "@/lib/snippets";

function useOrigin(): string {
  const [origin, setOrigin] = React.useState("http://localhost:4210");
  React.useEffect(() => setOrigin(window.location.origin), []);
  return origin;
}

/** First-run guidance: how to point an existing OpenAI client at the gateway. */
export function ConnectCard() {
  const origin = useOrigin();
  const key = "tsr_your_key_here";
  return (
    <Surface className="overflow-hidden">
      <div className="grid gap-6 p-5 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)] lg:p-6">
        <div>
          <div className="mb-3 flex size-10 items-center justify-center rounded-xl bg-primary-soft text-primary">
            <PlugZapIcon className="size-5" />
          </div>
          <h2 className="text-[16px] font-semibold tracking-[-0.01em]">Connect your first app</h2>
          <p className="mt-1.5 text-[13px] leading-relaxed text-muted-foreground">
            No traffic has reached the gateway in this range. Keep your OpenAI SDK and change two settings: the base URL becomes{" "}
            <code className="rounded bg-muted px-1 py-0.5 font-mono text-[12px] text-foreground">{gatewayBaseUrl(origin)}</code> and the API
            key becomes a SmartRouter key. Send <code className="rounded bg-muted px-1 py-0.5 font-mono text-[12px] text-foreground">model=&quot;auto&quot;</code> and each
            request goes to the cheapest model that is good enough for it, with every decision logged.
          </p>
          <ol className="mt-4 space-y-2 text-[13px]">
            <li className="flex gap-2.5">
              <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-muted text-[11px] font-semibold">1</span>
              Create a project and an API key for it.
            </li>
            <li className="flex gap-2.5">
              <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-muted text-[11px] font-semibold">2</span>
              Swap the base URL and key in your client.
            </li>
            <li className="flex gap-2.5">
              <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-muted text-[11px] font-semibold">3</span>
              Watch decisions and savings land in the log.
            </li>
          </ol>
          <div className="mt-5 flex flex-wrap gap-2">
            <Link href="/keys" className={buttonVariants({ size: "sm" })}>
              Create an API key
            </Link>
            <Link href="/simulator" className={buttonVariants({ variant: "ghost", size: "sm" })}>
              Try the Simulator
            </Link>
          </div>
        </div>
        <CodeBlock
          samples={[
            { id: "python", label: "Python", code: pythonSnippet(origin, key) },
            { id: "curl", label: "curl", code: curlSnippet(origin, key) },
            { id: "node", label: "Node", code: nodeSnippet(origin, key) },
          ]}
        />
      </div>
    </Surface>
  );
}
