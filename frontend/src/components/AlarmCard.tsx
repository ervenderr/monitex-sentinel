import { memo } from "react";
import type { AlarmRecord } from "../types";
import { SEVERITY_STYLE, eventLabel } from "../lib/severity";
import { clockTime, confidencePct, sinceNow } from "../lib/format";
import { Badge } from "./Badge";
import { Lamp } from "./Lamp";

interface Props {
  record: AlarmRecord;
  now: number;
  verdictLanded: boolean;
  busy: boolean;
  onAcknowledge: (id: string) => void;
  onResolve: (id: string) => void;
}

/** Which brain produced this verdict, and whether it is still provisional.
 *  Showing this is not debug output: an operator acting on a degraded verdict
 *  should know the AI was down, and one acting on a preliminary verdict should
 *  know a better answer is seconds away. */
function OriginBadge({ record }: { record: AlarmRecord }) {
  const { triage, triage_state } = record;

  if (triage_state === "preliminary") {
    return (
      <Badge tone="info" title="Rule verdict shown now; the AI verdict is still in flight.">
        <span className="inline-block size-[5px] rounded-full bg-info lamp-bloom" />
        Rules · AI pending
      </Badge>
    );
  }
  if (triage.degraded) {
    return (
      <Badge tone="warning" title="The AI provider failed. This is the rule engine's verdict.">
        Fallback · AI unavailable
      </Badge>
    );
  }
  if (triage.origin === "rules") {
    return (
      <Badge title="Unambiguous by type. No AI call was made, and none was needed.">
        Rules only · no AI cost
      </Badge>
    );
  }
  return (
    <Badge tone="live" title={triage.reasoning || undefined}>
      AI {triage.model ?? "model"} · {(triage.latency_ms / 1000).toFixed(1)}s
    </Badge>
  );
}

function AlarmCardBase({
  record,
  now,
  verdictLanded,
  busy,
  onAcknowledge,
  onResolve,
}: Props) {
  const { event, triage, status } = record;
  const style = SEVERITY_STYLE[triage.severity];
  const settled = status !== "active";
  const resolved = status === "resolved";

  return (
    <li
      id={`alarm-${event.event_id}`}
      className={`slide-in flex border ${style.border} ${settled ? "opacity-55" : ""} ${
        resolved ? "opacity-35" : ""
      } bg-panel transition-opacity`}
    >
      <Lamp severity={triage.severity} muted={settled} />

      <div className={`min-w-0 flex-1 ${verdictLanded ? "verdict-land" : ""}`}>
        <div
          className={`flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5 px-3.5 py-1.5 ${style.wash}`}
        >
          <span className={`text-[10px] font-semibold uppercase tracking-[0.16em] ${style.text}`}>
            {style.label}
          </span>
          <span className="text-[14px] font-medium capitalize leading-tight text-ink">
            {eventLabel(event.type)}
          </span>
          <span className="tnum text-[10px] text-ink-faint">
            {event.site_id} / {event.zone}
          </span>
          <span className="tnum ml-auto text-[10px] text-ink-faint">
            {clockTime(event.timestamp)}Z · {sinceNow(event.received_at, now)}
          </span>
        </div>

        {record.escalation && (
          <p className="mx-3.5 mt-1.5 flex items-center gap-1.5 border border-critical/40 bg-critical-dim/30 px-2 py-1 text-[11px] uppercase tracking-wide text-critical">
            <span aria-hidden="true">&#9650;</span>
            Escalated - {record.escalation}
          </p>
        )}
        <p className="px-3.5 pt-1.5 text-[13px] leading-snug text-ink">{triage.summary}</p>

        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3.5 pb-2 pt-1.5">
          <p className="text-[12px] leading-snug text-ink-dim">
            <span aria-hidden="true" className={`mr-1 ${style.text}`}>
              →
            </span>
            {triage.recommended_action}
          </p>

          <div className="flex flex-wrap items-center gap-1.5">
            <OriginBadge record={record} />
            <Badge title="Detector confidence reported with the event.">
              {confidencePct(event.confidence)}
            </Badge>
            {event.source === "camera" && (
              <Badge title="Detected by the camera worker from live video, not a sensor.">
                camera
              </Badge>
            )}
            {triage.overrode_baseline && (
              <Badge
                tone="info"
                title={triage.reasoning || "The model disagreed with the rule baseline."}
              >
                AI overrode rules
              </Badge>
            )}
            {!triage.is_real_threat && (
              <Badge title="Assessed as a likely false positive.">likely false positive</Badge>
            )}
          </div>

          <div className="ml-auto flex shrink-0 items-center gap-1.5">
            {status === "acknowledged" && (
              <span className="text-[10px] uppercase tracking-wider text-ink-faint">
                Ack · {record.acknowledged_by}
              </span>
            )}
            {resolved ? (
              <span className="text-[10px] uppercase tracking-wider text-ink-faint">
                Resolved · {record.resolved_by}
              </span>
            ) : (
              <>
                {status === "active" && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => onAcknowledge(event.event_id)}
                    className="border border-rule-bright px-2.5 py-[3px] text-[10px] uppercase tracking-wider text-ink-dim transition-colors hover:border-ink-dim hover:text-ink disabled:opacity-40"
                  >
                    Acknowledge
                  </button>
                )}
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => onResolve(event.event_id)}
                  className="border border-rule-bright px-2.5 py-[3px] text-[10px] uppercase tracking-wider text-ink-dim transition-colors hover:border-ink-dim hover:text-ink disabled:opacity-40"
                >
                  Resolve
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    </li>
  );
}

export const AlarmCard = memo(AlarmCardBase);
