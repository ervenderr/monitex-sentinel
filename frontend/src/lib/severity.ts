import type { AlarmRecord, Severity } from "../types";

export const SEVERITY_RANK: Record<Severity, number> = {
  critical: 3,
  warning: 2,
  info: 1,
};

/** Annunciator lamp colours, one row per severity. */
export const SEVERITY_STYLE: Record<
  Severity,
  { lamp: string; text: string; border: string; wash: string; label: string }
> = {
  critical: {
    lamp: "bg-critical",
    text: "text-critical",
    border: "border-critical/45",
    wash: "bg-critical-dim/25",
    label: "Critical",
  },
  warning: {
    lamp: "bg-warning",
    text: "text-warning",
    border: "border-warning/35",
    wash: "bg-warning-dim/20",
    label: "Warning",
  },
  info: {
    lamp: "bg-info",
    text: "text-info",
    border: "border-rule",
    wash: "bg-transparent",
    label: "Info",
  },
};

/** Mirrors AlarmRecord.sort_key on the server: unresolved first, then severity,
 *  then most recent. The server sorts the snapshot; live updates arrive one at a
 *  time, so the client has to hold the same ordering itself. */
export function sortKey(record: AlarmRecord): [number, number, number] {
  return [
    record.status === "resolved" ? 0 : 1,
    SEVERITY_RANK[record.triage.severity],
    new Date(record.event.received_at).getTime(),
  ];
}

export function byRank(a: AlarmRecord, b: AlarmRecord): number {
  const ka = sortKey(a);
  const kb = sortKey(b);
  for (let i = 0; i < ka.length; i += 1) {
    if (ka[i] !== kb[i]) return kb[i] - ka[i];
  }
  return 0;
}

export function eventLabel(type: string): string {
  return type.replace(/_/g, " ");
}
