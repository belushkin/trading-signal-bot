import type { Signal } from "./api";

export type DirectionAssessment = {
  conclusion: "mixed" | "none";
  summary: string;
  evidence: string;
};

// Price signals reflect observations, not buyer/seller pressure or proven predictive power.
export function assessDirection(signals: Signal[]): DirectionAssessment {
  const changes = signals
    .filter((signal) => signal.category === "price_movement")
    .map((signal) => signal.observed_facts?.observed_percent)
    .filter((value): value is string | number => typeof value === "number" || typeof value === "string")
    .map((value) => typeof value === "string" && value.trim() === "" ? NaN : Number(value))
    .filter((value) => Number.isFinite(value));
  if (changes.some((value) => value > 0) && changes.some((value) => value < 0)) {
    return {
      conclusion: "mixed",
      summary: "Mixed price evidence — no directional trade recommendation.",
      evidence: "Price-movement signals in this episode point in different directions. Time windows may differ.",
    };
  }
  return {
    conclusion: "none",
    summary: "No directional conclusion — no trade recommendation.",
    evidence: changes.length
      ? "A recorded price move does not establish continuation or buying or selling pressure."
      : "Volume, trade size and trade orientation alone do not establish market direction.",
  };
}
