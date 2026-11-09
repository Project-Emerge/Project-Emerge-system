import { describe, expect, it } from "vitest";
import { computeSceneBounds, DEFAULT_SCENE_BOUNDS } from "./bounds";

describe("inquadratura della scena in base ai robot posizionati", () => {
  it("usa i bounds di default quando nessun robot ha una posizione", () => {
    expect(computeSceneBounds([])).toEqual(DEFAULT_SCENE_BOUNDS);
  });

  it("centra i bounds sul bounding box dei robot con un margine", () => {
    const bounds = computeSceneBounds([
      { x_m: 0, y_m: 2.8 },
      { x_m: 2, y_m: 4.6 },
    ]);
    expect(bounds.centerX).toBeCloseTo(1, 5);
    expect(bounds.centerY).toBeCloseTo(3.7, 5);
    // span = max(dx=2, dy=1.8, MIN_SPAN=2) + padding(1) = 3
    expect(bounds.span).toBeCloseTo(3, 5);
  });

  it("applica uno span minimo quando i robot sono ammassati", () => {
    const bounds = computeSceneBounds([{ x_m: 5, y_m: 5 }, { x_m: 5.01, y_m: 5.01 }]);
    expect(bounds.centerX).toBeCloseTo(5.005, 5);
    expect(bounds.span).toBeCloseTo(3, 5); // MIN_SPAN(2) + padding(1)
  });
});
