/** A two-pitch annunciator chirp, synthesised rather than shipped as a file.
 *
 *  Real alarm panels buzz when something needs a human. This is the software
 *  equivalent: short, unmistakable, and only ever for a new critical alarm -
 *  an alert that fires for everything trains the operator to ignore it.
 */
let context: AudioContext | null = null;

function ensureContext(): AudioContext | null {
  if (typeof window === "undefined") return null;
  if (!context) {
    const Ctor = window.AudioContext ?? (window as never as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return null;
    context = new Ctor();
  }
  // Browsers suspend audio until a user gesture; resuming is a no-op if running.
  if (context.state === "suspended") void context.resume();
  return context;
}

function blip(ctx: AudioContext, frequency: number, startAt: number, duration: number): void {
  const oscillator = ctx.createOscillator();
  const gain = ctx.createGain();
  oscillator.type = "square";
  oscillator.frequency.value = frequency;
  // Soft edges: a raw square wave gate clicks unpleasantly over a long shift.
  gain.gain.setValueAtTime(0.0001, startAt);
  gain.gain.exponentialRampToValueAtTime(0.16, startAt + 0.012);
  gain.gain.exponentialRampToValueAtTime(0.0001, startAt + duration);
  oscillator.connect(gain).connect(ctx.destination);
  oscillator.start(startAt);
  oscillator.stop(startAt + duration + 0.02);
}

export function playCriticalTone(): void {
  const ctx = ensureContext();
  if (!ctx) return;
  const now = ctx.currentTime;
  blip(ctx, 880, now, 0.1);
  blip(ctx, 1170, now + 0.13, 0.14);
}

/** Called from a click handler so the browser unlocks audio for later alerts. */
export function primeAudio(): void {
  ensureContext();
}
