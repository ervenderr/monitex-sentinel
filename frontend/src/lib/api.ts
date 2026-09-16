import type { AlarmRecord, Health } from "../types";

const OPERATOR = "operator";

async function post(path: string): Promise<AlarmRecord> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ by: OPERATOR }),
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return (await response.json()) as AlarmRecord;
}

export const acknowledgeAlarm = (id: string) =>
  post(`/api/alarms/${encodeURIComponent(id)}/acknowledge`);

export const resolveAlarm = (id: string) =>
  post(`/api/alarms/${encodeURIComponent(id)}/resolve`);

export async function fetchHealth(): Promise<Health> {
  const response = await fetch("/api/health");
  if (!response.ok) throw new Error(`health ${response.status}`);
  return (await response.json()) as Health;
}
