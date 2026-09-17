import { useCallback, useEffect, useMemo, useState } from "react";
import type { AlarmRecord, Health, Severity } from "./types";
import { useAlarmStream } from "./useAlarmStream";
import { acknowledgeAlarm, fetchHealth, resolveAlarm } from "./lib/api";
import { byRank } from "./lib/severity";
import { primeAudio } from "./lib/tone";
import { StatusRail } from "./components/StatusRail";
import { CriticalBanner } from "./components/CriticalBanner";
import { InstrumentStrip } from "./components/InstrumentStrip";
import { FilterBar, type StatusFilter } from "./components/FilterBar";
import { AlarmBoard } from "./components/AlarmBoard";
import { CameraPreview } from "./components/CameraPreview";

const ALL_SEVERITIES: Severity[] = ["critical", "warning", "info"];
const SOUND_KEY = "sentinel.sound";

export default function App() {
  const [soundEnabled, setSoundEnabled] = useState(
    () => localStorage.getItem(SOUND_KEY) !== "off",
  );
  const { alarms, metrics, link, verdictLanded, applyLocal } = useAlarmStream(soundEnabled);

  const [health, setHealth] = useState<Health | null>(null);
  const [severities, setSeverities] = useState<Set<Severity>>(new Set(ALL_SEVERITIES));
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("open");
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    void fetchHealth().then(setHealth).catch(() => setHealth(null));
  }, []);

  // One timer for every relative timestamp on the board, rather than one each.
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const toggleSound = useCallback(() => {
    setSoundEnabled((previous) => {
      const next = !previous;
      localStorage.setItem(SOUND_KEY, next ? "on" : "off");
      // Unlock the audio context while we are inside a user gesture.
      if (next) primeAudio();
      return next;
    });
  }, []);

  const toggleSeverity = useCallback((severity: Severity) => {
    setSeverities((previous) => {
      const next = new Set(previous);
      if (next.has(severity)) {
        // Never let the operator filter the board down to nothing by accident.
        if (next.size > 1) next.delete(severity);
      } else {
        next.add(severity);
      }
      return next;
    });
  }, []);

  const act = useCallback(
    async (id: string, action: (id: string) => Promise<AlarmRecord>) => {
      setBusy((previous) => new Set(previous).add(id));
      try {
        applyLocal(await action(id));
      } catch {
        // The SSE stream remains the source of truth; a failed click just
        // leaves the alarm as it was rather than showing a false state.
      } finally {
        setBusy((previous) => {
          const next = new Set(previous);
          next.delete(id);
          return next;
        });
      }
    },
    [applyLocal],
  );

  const onAcknowledge = useCallback((id: string) => void act(id, acknowledgeAlarm), [act]);
  const onResolve = useCallback((id: string) => void act(id, resolveAlarm), [act]);

  const all = useMemo(() => [...alarms.values()].sort(byRank), [alarms]);

  const visible = useMemo(
    () =>
      all.filter(
        (record) =>
          severities.has(record.triage.severity) &&
          (statusFilter === "all" || record.status !== "resolved"),
      ),
    [all, severities, statusFilter],
  );

  // Unacknowledged criticals drive the banner regardless of the active filters:
  // a filter is a convenience, and it must not be able to hide an emergency.
  const outstandingCritical = useMemo(
    () => all.filter((r) => r.triage.severity === "critical" && r.status === "active"),
    [all],
  );

  const jumpTo = useCallback((id: string) => {
    const node = document.getElementById(`alarm-${id}`);
    node?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, []);

  return (
    <div className="flex h-full flex-col bg-ground">
      <StatusRail
        link={link}
        metrics={metrics}
        health={health}
        soundEnabled={soundEnabled}
        onToggleSound={toggleSound}
      />
      <CriticalBanner alarms={outstandingCritical} onJump={jumpTo} />
      {metrics?.video_connected && health?.video_zone && (
        <CameraPreview zone={health.video_zone} />
      )}
      <InstrumentStrip metrics={metrics} />
      <FilterBar
        severities={severities}
        onToggleSeverity={toggleSeverity}
        statusFilter={statusFilter}
        onStatusChange={setStatusFilter}
        shown={visible.length}
        total={all.length}
      />
      <main className="flex flex-1 flex-col overflow-y-auto">
        <AlarmBoard
          alarms={visible}
          now={now}
          verdictLanded={verdictLanded}
          busy={busy}
          feedUp={metrics?.stream_connected ?? true}
          totalHeld={all.length}
          onAcknowledge={onAcknowledge}
          onResolve={onResolve}
        />
      </main>
    </div>
  );
}
