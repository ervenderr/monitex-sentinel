/** Mirrors backend/models.py. Kept hand-written and small rather than generated:
 *  the surface is five types and a generator would be more machinery than it saves. */

export type Severity = "info" | "warning" | "critical";
export type AlarmStatus = "active" | "acknowledged" | "resolved";
export type TriageOrigin = "rules" | "llm" | "fallback";
export type TriageState = "preliminary" | "final";

export interface RawEvent {
  event_id: string;
  site_id: string;
  zone: string;
  type: string;
  source: "sensor" | "camera";
  confidence: number | null;
  timestamp: string;
  snapshot_url: string | null;
  metadata: Record<string, unknown>;
  received_at: string;
}

export interface TriageResult {
  severity: Severity;
  is_real_threat: boolean;
  summary: string;
  recommended_action: string;
  reasoning: string;
  origin: TriageOrigin;
  degraded: boolean;
  model: string | null;
  latency_ms: number;
  cost_usd: number;
  tokens_in: number;
  tokens_out: number;
  cached_tokens: number;
  overrode_baseline: boolean;
}

export interface AlarmRecord {
  event: RawEvent;
  triage: TriageResult;
  triage_state: TriageState;
  status: AlarmStatus;
  escalation: string | null;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
  resolved_at: string | null;
  resolved_by: string | null;
  updated_at: string;
}

export interface Metrics {
  events_ingested: number;
  events_shed: number;
  events_malformed: number;
  events_triaged: number;
  events_per_second: number;
  queue_depth: number;
  queue_capacity: number;
  backpressure_waits: number;
  llm_calls: number;
  llm_skipped_fast_path: number;
  llm_failures: number;
  llm_degraded: number;
  llm_overrides: number;
  llm_tokens_in: number;
  llm_tokens_out: number;
  llm_cached_tokens: number;
  llm_cache_hit_rate: number;
  llm_circuit_state: "closed" | "open" | "half_open";
  triage_latency_p50_ms: number;
  triage_latency_p95_ms: number;
  cost_usd: number;
  stream_connected: boolean;
  stream_reconnects: number;
  video_connected: boolean;
  video_frames_sampled: number;
  video_events_emitted: number;
  escalations: number;
}

export interface Health {
  status: string;
  stream_connected: boolean;
  provider: string;
  subscribers: number;
  video_enabled: boolean;
  video_zone: string;
}

/** Whether the dashboard's own connection to the backend is up - distinct from
 *  whether the backend's connection to the event feed is up. Both can fail. */
export type LinkState = "connecting" | "live" | "down";
