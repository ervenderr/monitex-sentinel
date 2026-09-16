import type { Severity } from "../types";
import { SEVERITY_STYLE } from "../lib/severity";

/** The legend lamp from an annunciator panel: the one element that carries
 *  severity. Critical blooms because a critical alarm on a real panel flashes. */
export function Lamp({ severity, muted }: { severity: Severity; muted: boolean }) {
  const style = SEVERITY_STYLE[severity];
  return (
    <div className="relative w-[3px] shrink-0 self-stretch" aria-hidden="true">
      <div className={`absolute inset-0 ${style.lamp} ${muted ? "opacity-25" : ""}`} />
      {severity === "critical" && !muted && (
        <div className={`absolute -inset-x-[7px] inset-y-0 ${style.lamp} blur-[6px] lamp-bloom`} />
      )}
    </div>
  );
}
