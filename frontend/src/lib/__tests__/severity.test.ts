import { describe, expect, it } from "vitest";
import { byRank, eventLabel, sortKey } from "../severity";
import type { AlarmRecord, AlarmStatus, Severity } from "../../types";

/** The board ordering must match AlarmRecord.sort_key on the server. If these
 *  drift, the snapshot and the live updates disagree and alarms appear to jump
 *  around, so the ordering is pinned on both sides. */
function record(
  id: string,
  severity: Severity,
  status: AlarmStatus = "active",
  receivedAt = "2026-09-17T12:00:00Z",
): AlarmRecord {
  return {
    event: {
      event_id: id,
      site_id: "site-101",
      zone: "lobby",
      type: "motion_detected",
      source: "sensor",
      confidence: 0.8,
      timestamp: receivedAt,
      snapshot_url: null,
      metadata: {},
      received_at: receivedAt,
    },
    triage: {
      severity,
      is_real_threat: true,
      summary: "s",
      recommended_action: "a",
      reasoning: "",
      origin: "llm",
      degraded: false,
      model: "deepseek-flash",
      latency_ms: 900,
      cost_usd: 0,
      tokens_in: 0,
      tokens_out: 0,
      cached_tokens: 0,
      overrode_baseline: false,
    },
    triage_state: "final",
    status,
    escalation: null,
    acknowledged_at: null,
    acknowledged_by: null,
    resolved_at: null,
    resolved_by: null,
    updated_at: receivedAt,
  };
}

describe("board ordering", () => {
  it("puts critical above warning above info", () => {
    const order = [record("i", "info"), record("c", "critical"), record("w", "warning")]
      .sort(byRank)
      .map((r) => r.event.event_id);
    expect(order).toEqual(["c", "w", "i"]);
  });

  it("sinks resolved alarms below everything open", () => {
    const order = [
      record("resolved-critical", "critical", "resolved"),
      record("open-info", "info"),
    ]
      .sort(byRank)
      .map((r) => r.event.event_id);
    expect(order).toEqual(["open-info", "resolved-critical"]);
  });

  it("keeps acknowledged alarms in place, since they are still open", () => {
    const order = [
      record("open-info", "info"),
      record("acked-critical", "critical", "acknowledged"),
    ]
      .sort(byRank)
      .map((r) => r.event.event_id);
    expect(order).toEqual(["acked-critical", "open-info"]);
  });

  it("breaks severity ties with the most recent first", () => {
    const order = [
      record("older", "warning", "active", "2026-09-17T11:00:00Z"),
      record("newer", "warning", "active", "2026-09-17T12:00:00Z"),
    ]
      .sort(byRank)
      .map((r) => r.event.event_id);
    expect(order).toEqual(["newer", "older"]);
  });

  it("produces the same three-part key shape the server uses", () => {
    expect(sortKey(record("x", "critical"))).toHaveLength(3);
    expect(sortKey(record("x", "critical"))[1]).toBe(3);
    expect(sortKey(record("x", "info"))[1]).toBe(1);
    expect(sortKey(record("x", "info", "resolved"))[0]).toBe(0);
  });
});

describe("eventLabel", () => {
  it("renders snake_case event types as readable words", () => {
    expect(eventLabel("perimeter_breach")).toBe("perimeter breach");
    expect(eventLabel("fire_alarm")).toBe("fire alarm");
  });
});
