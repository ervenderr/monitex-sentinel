import type { Metrics } from "../types";
import { compactNumber, money } from "../lib/format";

/** Operational readouts. Every number here answers a question an operator or an
 *  on-call engineer would actually ask: am I keeping up, is the AI healthy,
 *  what is this costing. Nothing is here because it was easy to collect. */
function Readout({
  label,
  value,
  tone = "normal",
  title,
}: {
  label: string;
  value: string;
  tone?: "normal" | "warn" | "bad";
  title?: string;
}) {
  const tones = {
    normal: "text-ink",
    warn: "text-warning",
    bad: "text-critical",
  } as const;
  return (
    <div title={title} className="flex flex-col gap-0.5">
      <span className="text-[9px] uppercase tracking-[0.16em] text-ink-faint">{label}</span>
      <span className={`tnum text-[13px] leading-none ${tones[tone]}`}>{value}</span>
    </div>
  );
}

export function InstrumentStrip({ metrics }: { metrics: Metrics | null }) {
  if (!metrics) {
    return (
      <div className="border-b border-rule bg-ground px-5 py-2.5 text-[11px] text-ink-faint">
        Waiting for the first metrics tick…
      </div>
    );
  }

  const queueLoad = metrics.queue_capacity
    ? metrics.queue_depth / metrics.queue_capacity
    : 0;

  return (
    <div className="flex flex-wrap items-center gap-x-7 gap-y-3 border-b border-rule bg-ground px-5 py-2.5">
      <Readout label="Ingest" value={`${metrics.events_per_second.toFixed(1)}/s`} />
      <Readout
        label="Queue"
        value={`${metrics.queue_depth}/${metrics.queue_capacity}`}
        tone={queueLoad > 0.8 ? "bad" : queueLoad > 0.4 ? "warn" : "normal"}
        title="Depth of the triage backlog. Rising means the AI layer is behind the stream."
      />
      <Readout
        label="Shed"
        value={compactNumber(metrics.events_shed)}
        tone={metrics.events_shed > 0 ? "warn" : "normal"}
        title="Events dropped under sustained overload. Critical alarms are never shed."
      />
      <Readout
        label="Triage p50 / p95"
        value={`${metrics.triage_latency_p50_ms.toFixed(0)} / ${metrics.triage_latency_p95_ms.toFixed(0)}ms`}
      />
      <Readout
        label="Degraded"
        value={compactNumber(metrics.llm_degraded)}
        tone={metrics.llm_degraded > 0 ? "warn" : "normal"}
        title="Alarms that fell back to rule verdicts because the AI provider failed."
      />
      <Readout
        label="Overrides"
        value={
          metrics.llm_calls
            ? `${Math.round((metrics.llm_overrides / metrics.llm_calls) * 100)}%`
            : "—"
        }
        title="How often the model disagreed with the rule baseline. Near 0% means it is not earning its cost."
      />
      <Readout
        label="Cache hit"
        value={`${Math.round(metrics.llm_cache_hit_rate * 100)}%`}
        title="Share of prompt tokens served from the provider's cache, billed at a fraction of the normal rate."
      />
      <Readout
        label="Spend"
        value={money(metrics.cost_usd)}
        title="Estimated, from published token rates. Fast-path alarms cost nothing."
      />
      <Readout label="Skipped AI" value={compactNumber(metrics.llm_skipped_fast_path)} title="Unambiguous alarms resolved by rules alone, at no cost." />
      <Readout
        label="Escalated"
        value={compactNumber(metrics.escalations)}
        tone={metrics.escalations > 0 ? "warn" : "normal"}
        title="Alarms bumped to critical by a repeated or combined pattern at one site."
      />
    </div>
  );
}
