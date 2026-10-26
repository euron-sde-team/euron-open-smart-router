"use client";

import { useId } from "react";

import { cn } from "@/lib/cn";

/** The SmartRouter mark: one request entering, branching into three routes, one chosen. */
export function RouterMark({ className }: { className?: string }) {
  // A unique gradient id per instance: when the first SVG defining a shared id sits in a
  // hidden subtree (the collapsed sidebar on phones), browsers resolve the fill to nothing.
  const gradient = `sr-mark-${useId().replace(/:/g, "")}`;
  return (
    <svg viewBox="0 0 32 32" aria-hidden className={cn("size-7 shrink-0", className)}>
      <defs>
        <linearGradient id={gradient} x1="4" y1="3" x2="28" y2="29" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="var(--series-1)" />
          <stop offset="1" stopColor="var(--primary)" />
        </linearGradient>
      </defs>
      <rect x="2.5" y="2.5" width="27" height="27" rx="8" fill={`url(#${gradient})`} />
      <g fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M7.5 16h5" />
        <path d="M12.5 16c3 0 3.5-6 7-6h3" strokeOpacity="0.55" />
        <path d="M12.5 16h10" />
        <path d="M12.5 16c3 0 3.5 6 7 6h3" strokeOpacity="0.55" />
      </g>
      <circle cx="7.5" cy="16" r="2" fill="white" />
      <circle cx="23.5" cy="16" r="2.4" fill="white" />
      <circle cx="23.5" cy="10" r="1.5" fill="white" fillOpacity="0.55" />
      <circle cx="23.5" cy="22" r="1.5" fill="white" fillOpacity="0.55" />
    </svg>
  );
}

export function RouterLogo({ className, collapsed = false }: { className?: string; collapsed?: boolean }) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <RouterMark />
      {!collapsed && <span className="text-[15px] font-semibold tracking-[-0.02em] text-foreground">SmartRouter</span>}
    </span>
  );
}
