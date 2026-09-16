import type { Severity } from "../types";

export type StatusFilter = "open" | "all";

interface Props {
  severities: Set<Severity>;
  onToggleSeverity: (severity: Severity) => void;
  statusFilter: StatusFilter;
  onStatusChange: (value: StatusFilter) => void;
  shown: number;
  total: number;
}

const ORDER: Severity[] = ["critical", "warning", "info"];
const TONE: Record<Severity, string> = {
  critical: "border-critical text-critical",
  warning: "border-warning text-warning",
  info: "border-info text-info",
};

export function FilterBar({
  severities,
  onToggleSeverity,
  statusFilter,
  onStatusChange,
  shown,
  total,
}: Props) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-rule bg-ground px-5 py-2">
      <span className="text-[9px] uppercase tracking-[0.16em] text-ink-faint">Show</span>

      <div className="flex gap-1.5">
        {ORDER.map((severity) => {
          const on = severities.has(severity);
          return (
            <button
              key={severity}
              type="button"
              aria-pressed={on}
              onClick={() => onToggleSeverity(severity)}
              className={`border px-2 py-[3px] text-[10px] uppercase tracking-wider transition-colors ${
                on ? TONE[severity] : "border-rule text-ink-faint hover:border-rule-bright"
              }`}
            >
              {severity}
            </button>
          );
        })}
      </div>

      <div className="flex gap-1.5">
        {(["open", "all"] as const).map((value) => (
          <button
            key={value}
            type="button"
            aria-pressed={statusFilter === value}
            onClick={() => onStatusChange(value)}
            className={`border px-2 py-[3px] text-[10px] uppercase tracking-wider transition-colors ${
              statusFilter === value
                ? "border-ink-dim text-ink"
                : "border-rule text-ink-faint hover:border-rule-bright"
            }`}
          >
            {value === "open" ? "Open only" : "Include resolved"}
          </button>
        ))}
      </div>

      <span className="tnum ml-auto text-[11px] text-ink-faint">
        {shown} of {total} alarms
      </span>
    </div>
  );
}
