import { describe, expect, it } from "vitest";
import { clockTime, compactNumber, confidencePct, money, sinceNow } from "../format";

describe("clockTime", () => {
  it("shows UTC clock time to the second", () => {
    expect(clockTime("2026-09-17T03:14:22.511Z")).toBe("03:14:22");
  });

  it("degrades visibly rather than throwing on a bad timestamp", () => {
    expect(clockTime("not-a-date")).toBe("--:--:--");
  });
});

describe("sinceNow", () => {
  const now = Date.parse("2026-09-17T12:00:00Z");

  it.each([
    ["2026-09-17T11:59:55Z", "5s ago"],
    ["2026-09-17T11:57:00Z", "3m ago"],
    ["2026-09-17T09:00:00Z", "3h ago"],
  ])("renders %s as %s", (iso, expected) => {
    expect(sinceNow(iso, now)).toBe(expected);
  });

  it("never shows a negative age when clocks disagree", () => {
    expect(sinceNow("2026-09-17T12:00:30Z", now)).toBe("0s ago");
  });
});

describe("confidencePct", () => {
  it("renders a fraction as a percentage", () => {
    expect(confidencePct(0.87)).toBe("87%");
  });

  it("marks a missing confidence rather than showing 0%", () => {
    expect(confidencePct(null)).toBe("--");
  });
});

describe("money", () => {
  it("keeps sub-cent spend legible instead of rounding it to zero", () => {
    expect(money(0.0087)).toBe("$0.0087");
  });

  it("uses two decimals once spend is material", () => {
    expect(money(1.5)).toBe("$1.50");
  });

  it("shows an exact zero plainly", () => {
    expect(money(0)).toBe("$0.00");
  });
});

describe("compactNumber", () => {
  it.each([
    [42, "42"],
    [1500, "1.5k"],
    [2_400_000, "2.4M"],
  ])("renders %i as %s", (value, expected) => {
    expect(compactNumber(value)).toBe(expected);
  });
});
