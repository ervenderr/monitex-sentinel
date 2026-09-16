import type { ReactNode } from "react";

export function Badge({
  children,
  tone = "neutral",
  title,
}: {
  children: ReactNode;
  tone?: "neutral" | "critical" | "warning" | "info" | "live";
  title?: string;
}) {
  const tones = {
    neutral: "border-rule text-ink-faint",
    critical: "border-critical/50 text-critical",
    warning: "border-warning/50 text-warning",
    info: "border-info/50 text-info",
    live: "border-live/50 text-live",
  } as const;
  return (
    <span
      title={title}
      className={`tnum inline-flex items-center gap-1 border px-1.5 py-[1px] text-[10px] uppercase tracking-wider ${tones[tone]}`}
    >
      {children}
    </span>
  );
}
