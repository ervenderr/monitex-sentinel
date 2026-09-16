import type { Health, LinkState, Metrics } from "../types";

interface Props {
  link: LinkState;
  metrics: Metrics | null;
  health: Health | null;
  soundEnabled: boolean;
  onToggleSound: () => void;
}

function Lamp({ on, tone }: { on: boolean; tone: string }) {
  return (
    <span
      aria-hidden="true"
      className={`inline-block size-[7px] rounded-full ${on ? tone : "bg-ink-faint/40"}`}
    />
  );
}

export function StatusRail({ link, metrics, health, soundEnabled, onToggleSound }: Props) {
  const feedUp = metrics?.stream_connected ?? health?.stream_connected ?? false;
  const circuit = metrics?.llm_circuit_state ?? "closed";
  const provider = health?.provider ?? "—";

  return (
    <header className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-rule bg-panel px-5 py-3">
      <div className="flex items-baseline gap-2.5">
        <span className="text-[15px] font-semibold tracking-[0.2em] text-ink">SENTINEL</span>
        <span className="text-[10px] uppercase tracking-[0.18em] text-ink-faint">
          Alarm monitoring
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 text-[11px]">
        <span className="flex items-center gap-1.5">
          <Lamp on={link === "live"} tone="bg-live" />
          <span className="text-ink-dim">
            Dashboard {link === "live" ? "linked" : link === "down" ? "reconnecting" : "connecting"}
          </span>
        </span>

        <span className="flex items-center gap-1.5">
          <Lamp on={feedUp} tone="bg-live" />
          <span className={feedUp ? "text-ink-dim" : "text-critical"}>
            {feedUp ? "Event feed live" : "Event feed down"}
          </span>
        </span>

        <span className="flex items-center gap-1.5">
          <Lamp on={circuit === "closed"} tone="bg-live" />
          <span className={circuit === "closed" ? "text-ink-dim" : "text-warning"}>
            AI {provider}
            {circuit !== "closed" && ` · circuit ${circuit.replace("_", "-")}`}
          </span>
        </span>
      </div>

      <button
        type="button"
        onClick={onToggleSound}
        aria-pressed={soundEnabled}
        className="ml-auto border border-rule px-2.5 py-1 text-[10px] uppercase tracking-wider text-ink-dim transition-colors hover:border-rule-bright hover:text-ink"
      >
        {soundEnabled ? "Alert tone on" : "Alert tone off"}
      </button>
    </header>
  );
}
