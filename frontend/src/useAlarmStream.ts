import { useCallback, useEffect, useRef, useState } from "react";
import type { AlarmRecord, LinkState, Metrics } from "./types";
import { playCriticalTone } from "./lib/tone";

interface StreamState {
  alarms: Map<string, AlarmRecord>;
  metrics: Metrics | null;
  link: LinkState;
  /** Alarms whose AI verdict has just replaced the rule verdict. Drives the
   *  one-shot flare that makes the two-stage triage visible. */
  verdictLanded: Set<string>;
}

const EMPTY: StreamState = {
  alarms: new Map(),
  metrics: null,
  link: "connecting",
  verdictLanded: new Set(),
};

export function useAlarmStream(soundEnabled: boolean) {
  const [state, setState] = useState<StreamState>(EMPTY);

  // Refs, not state: these feed decisions inside the SSE handler and must not
  // re-subscribe the stream when they change.
  const announcedRef = useRef<Set<string>>(new Set());
  const soundRef = useRef(soundEnabled);
  soundRef.current = soundEnabled;

  const announce = useCallback((record: AlarmRecord) => {
    const id = record.event.event_id;
    const isNewCritical =
      record.triage.severity === "critical" &&
      record.status === "active" &&
      !announcedRef.current.has(id);
    if (!isNewCritical) return;
    announcedRef.current.add(id);
    if (soundRef.current) playCriticalTone();
  }, []);

  useEffect(() => {
    const source = new EventSource("/api/stream");

    source.addEventListener("snapshot", (event) => {
      const payload = JSON.parse((event as MessageEvent).data) as { alarms: AlarmRecord[] };
      const alarms = new Map<string, AlarmRecord>();
      for (const record of payload.alarms) {
        alarms.set(record.event.event_id, record);
        // Seed, do not announce: a page refresh must not replay every alarm.
        if (record.triage.severity === "critical") {
          announcedRef.current.add(record.event.event_id);
        }
      }
      setState((prev) => ({ ...prev, alarms, link: "live" }));
    });

    source.addEventListener("alarm", (event) => {
      const payload = JSON.parse((event as MessageEvent).data) as { record: AlarmRecord };
      const record = payload.record;
      const id = record.event.event_id;
      announce(record);

      setState((prev) => {
        const previous = prev.alarms.get(id);
        const landed =
          previous?.triage_state === "preliminary" && record.triage_state === "final";

        const alarms = new Map(prev.alarms);
        alarms.set(id, record);

        if (!landed) return { ...prev, alarms, link: "live" };
        const verdictLanded = new Set(prev.verdictLanded);
        verdictLanded.add(id);
        return { ...prev, alarms, link: "live", verdictLanded };
      });
    });

    source.addEventListener("metrics", (event) => {
      const metrics = JSON.parse((event as MessageEvent).data) as Metrics;
      setState((prev) => ({ ...prev, metrics, link: "live" }));
    });

    source.onopen = () => setState((prev) => ({ ...prev, link: "live" }));
    source.onerror = () =>
      // EventSource retries on its own; reflect the gap rather than hiding it.
      setState((prev) => ({ ...prev, link: prev.link === "live" ? "down" : "connecting" }));

    return () => source.close();
  }, [announce]);

  /** Apply a server response immediately instead of waiting for it to echo back
   *  over SSE, so a click feels instant. The SSE update is idempotent. */
  const applyLocal = useCallback((record: AlarmRecord) => {
    setState((prev) => {
      const alarms = new Map(prev.alarms);
      alarms.set(record.event.event_id, record);
      return { ...prev, alarms };
    });
  }, []);

  return { ...state, applyLocal };
}
