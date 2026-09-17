import { useState } from "react";

/** A live MJPEG feed from the camera worker, in an <img> tag - MJPEG-over-HTTP
 *  needs no JS decoding, no WebRTC signalling, no extra library, and every
 *  browser has rendered it natively for decades. The tag itself IS the
 *  player: each multipart chunk the browser receives just replaces the
 *  decoded image in place.
 *
 *  Shown only when the camera is actually connected (see App.tsx) - an <img>
 *  pointed at a dead stream is a broken-image icon, not useful information
 *  the operator doesn't already have from the "Camera off" status lamp.
 */
export function CameraPreview({ zone }: { zone: string }) {
  const [open, setOpen] = useState(true);

  return (
    <div className="fixed right-4 top-16 z-10 w-[240px] border border-rule bg-panel shadow-lg">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 border-b border-rule px-2 py-1 text-left"
      >
        <span
          className="inline-block size-[6px] rounded-full bg-live lamp-bloom"
          aria-hidden="true"
        />
        <span className="text-[9px] uppercase tracking-[0.14em] text-ink-dim">
          Live · {zone}
        </span>
        <span className="ml-auto text-[10px] text-ink-faint">{open ? "−" : "+"}</span>
      </button>
      {open && (
        <img
          src="/api/video/preview"
          alt={`Live camera feed - ${zone}`}
          className="block aspect-video w-full bg-ground object-cover"
        />
      )}
    </div>
  );
}
