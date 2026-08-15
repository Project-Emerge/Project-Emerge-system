import { describe, expect, it } from "vitest";
import { solveAnchorCoordinates } from "./calibration";

describe("solver di calibrazione ancore", () => {
  it("ricostruisce un quadrato usando tutte le sei distanze", () => {
    const solution = solveAnchorCoordinates({
      a1a2: 6,
      a2a3: 6,
      a3a4: 6,
      a4a1: 6,
      a1a3: Math.sqrt(72),
      a2a4: Math.sqrt(72),
    });
    expect(solution.rms).toBeLessThan(1e-6);
    expect(solution.coordinates[0]).toMatchObject({ x: 0, y: 0 });
    expect(solution.coordinates[1].x).toBeCloseTo(6, 5);
    expect(solution.coordinates[2]).toMatchObject({ x: expect.closeTo(6, 4), y: expect.closeTo(6, 4) });
    expect(solution.coordinates[3]).toMatchObject({ x: expect.closeTo(0, 4), y: expect.closeTo(6, 4) });
  });

  it("segnala errori di input e quantifica misure incoerenti", () => {
    expect(() => solveAnchorCoordinates({ a1a2: 0, a2a3: 1, a3a4: 1, a4a1: 1, a1a3: 1, a2a4: 1 })).toThrow(/positive/i);
    const solution = solveAnchorCoordinates({ a1a2: 6, a2a3: 6, a3a4: 6, a4a1: 6, a1a3: 20, a2a4: 1 });
    expect(solution.rms).toBeGreaterThan(0.1);
  });
});
