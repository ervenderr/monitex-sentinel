import type { AlarmRecord } from "../types";
import { eventLabel } from "../lib/severity";

/** The brief asks for critical situations to be impossible to miss. This is
 *  that guarantee: it sits above the board, it counts what is outstanding, and
 *  it names the most recent one so the operator knows where to look first. */
export function CriticalBanner({
  alarms,
  onJump,
}: {
  alarms: AlarmRecord[];
  onJump: (id: string) => void;
}) {
  if (alarms.length === 0) return null;
  const newest = alarms[0];

  return (
    <div
      role="alert"
      aria-live="assertive"
      className="banner-pulse flex flex-wrap items-center gap-x-4 gap-y-1 border-y-2 border-critical bg-critical-dim/50 px-5 py-2.5"
    >
      <span className="text-[13px] font-semibold uppercase tracking-[0.14em] text-critical">
        {alarms.length} critical {alarms.length === 1 ? "alarm" : "alarms"} unacknowledged
      </span>
      <button
        type="button"
        onClick={() => onJump(newest.event.event_id)}
        className="text-left text-[13px] text-ink underline decoration-critical/50 underline-offset-4 hover:decoration-critical"
      >
        Latest: {eventLabel(newest.event.type)} · {newest.event.site_id} /{" "}
        {newest.event.zone}
      </button>
    </div>
  );
}
