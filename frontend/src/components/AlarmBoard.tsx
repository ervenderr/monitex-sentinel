import type { AlarmRecord } from "../types";
import { AlarmCard } from "./AlarmCard";

interface Props {
  alarms: AlarmRecord[];
  now: number;
  verdictLanded: Set<string>;
  busy: Set<string>;
  feedUp: boolean;
  totalHeld: number;
  onAcknowledge: (id: string) => void;
  onResolve: (id: string) => void;
}

export function AlarmBoard({
  alarms,
  now,
  verdictLanded,
  busy,
  feedUp,
  totalHeld,
  onAcknowledge,
  onResolve,
}: Props) {
  if (alarms.length === 0) {
    // An empty screen should say what is true and what to do, not just sit blank.
    const message = !feedUp
      ? "The event feed is down. Start the generator with `python stream.py`."
      : totalHeld > 0
        ? "No alarms match the current filters."
        : "No alarms yet. The feed is live and quiet.";
    return (
      <div className="flex flex-1 items-center justify-center px-5 py-16">
        <p className="text-[13px] text-ink-faint">{message}</p>
      </div>
    );
  }

  return (
    <ul className="flex flex-col gap-1.5 px-5 py-3">
      {alarms.map((record) => (
        <AlarmCard
          key={record.event.event_id}
          record={record}
          now={now}
          verdictLanded={verdictLanded.has(record.event.event_id)}
          busy={busy.has(record.event.event_id)}
          onAcknowledge={onAcknowledge}
          onResolve={onResolve}
        />
      ))}
    </ul>
  );
}
