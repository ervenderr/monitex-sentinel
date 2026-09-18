import { useState } from "react";
import { pauseVideo, resumeVideo } from "../lib/api";

/** A live MJPEG feed from the camera worker, in an <img> tag - MJPEG-over-HTTP
 *  needs no JS decoding, no WebRTC signalling, no extra library, and every
 *  browser has rendered it natively for decades. The tag itself IS the
 *  player: each multipart chunk the browser receives just replaces the
 *  decoded image in place.
 *
 *  Pause/resume actually stops the backend from holding the camera open -
 *  earlier this panel only had a collapse toggle, which hid the <img> tag
 *  but never told the backend anything, so the camera stayed open and
 *  detection kept running whether or not the panel was visible. This calls
 *  through to /api/video/pause, which releases the capture device for real.
 */
export function CameraPreview({ zone, paused }: { zone: string; paused: boolean }) {
  const [open, setOpen] = useState(true);
  const [busy, setBusy] = useState(false);

  const toggle = async () => {
    setBusy(true);
    try {
      await (paused ? resumeVideo() : pauseVideo());
    } catch {
      // SSE metrics remain the source of truth; a failed click just leaves
      // the button in its current state rather than showing something false.
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed right-4 top-16 z-10 w-[240px] border border-rule bg-panel shadow-lg">
      <div className="flex items-center gap-1.5 border-b border-rule px-2 py-1">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex flex-1 items-center gap-1.5 text-left"
        >
          <span
            className={`inline-block size-[6px] rounded-full ${paused ? "bg-ink-faint" : "bg-live lamp-bloom"}`}
            aria-hidden="true"
          />
          <span className="text-[9px] uppercase tracking-[0.14em] text-ink-dim">
            {paused ? "Paused" : "Live"} · {zone}
          </span>
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={toggle}
          title={paused ? "Resume the camera" : "Pause the camera (releases the device)"}
          className="px-1.5 text-[10px] uppercase tracking-wider text-ink-dim hover:text-ink disabled:opacity-40"
        >
          {paused ? "Resume" : "Pause"}
        </button>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="px-1 text-[10px] text-ink-faint"
        >
          {open ? "−" : "+"}
        </button>
      </div>
      {open &&
        (paused ? (
          <div className="flex aspect-video w-full items-center justify-center bg-ground text-[10px] text-ink-faint">
            Camera paused
          </div>
        ) : (
          <img
            src="/api/video/preview"
            alt={`Live camera feed - ${zone}`}
            className="block aspect-video w-full bg-ground object-cover"
          />
        ))}
    </div>
  );
}
