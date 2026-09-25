import { describe, expect, it } from "vitest";
import { cornerCount, inspectDesign } from "./design-check.js";

type Point = [number, number];

const star: Point[] = Array.from({ length: 10 }, (_, k) => {
  const angle = (2 * Math.PI * k) / 10;
  const radius = k % 2 === 0 ? 0.9 : 0.45;
  return [Math.sin(angle) * radius, Math.cos(angle) * radius];
});

const triangle: Point[] = [[0, 0.9], [0.8, -0.5], [-0.8, -0.5]];

describe("controllo di un disegno prima della pubblicazione", () => {
  it("lascia passare un triangolo ben dimensionato", () => {
    expect(inspectDesign({ kind: "points", points: triangle, closed: true }, 8, {})).toEqual([]);
  });

  it("segnala piu angoli che robot", () => {
    const issues = inspectDesign({ kind: "points", points: star, closed: true }, 8, {});
    expect(issues).toHaveLength(1);
    expect(issues[0]).toContain("10 corners");
  });

  it("segnala una forma troppo piccola, tenendo conto della scala", () => {
    const small: Point[] = triangle.map(([x, y]) => [x / 3, y / 3]);
    expect(inspectDesign({ kind: "points", points: small, closed: true }, 8, {})[0]).toContain("too small");
    expect(inspectDesign({ kind: "points", points: small, closed: true }, 8, { customScale: 3 })).toEqual([]);
  });

  it("segnala punti oltre il tetto di raggio", () => {
    const issues = inspectDesign({ kind: "points", points: triangle, closed: true }, 8, { customMaxRadius: 0.5 });
    expect(issues[0]).toContain("cap");
  });

  it("segnala un vertice sopra l'ancora, non un lato che le passa vicino", () => {
    const letterV: Point[] = [[-0.6, 0.6], [0, 0], [0.6, 0.6]];
    const issues = inspectDesign({ kind: "points", points: letterV, closed: false }, 8, {});
    expect(issues).toHaveLength(1);
    expect(issues[0]).toContain("anchor");
    // A T's stem runs through the origin, where the anchor completes it.
    const letterT: Point[] = [[-0.6, 0.6], [0.6, 0.6], [0, 0.6], [0, -0.6]];
    expect(inspectDesign({ kind: "points", points: letterT, closed: false }, 8, {})).toEqual([]);
  });

  it("non giudica le formule", () => {
    expect(inspectDesign({ kind: "polar", r: "0.01", theta: "0" }, 8, {})).toEqual([]);
  });

  it("conta gli angoli come il runtime", () => {
    const circle: Point[] = Array.from({ length: 16 }, (_, k) => [Math.sin(k * Math.PI / 8), Math.cos(k * Math.PI / 8)]);
    expect(cornerCount(circle, true)).toBe(0);
    expect(cornerCount(star, true)).toBe(10);
    // A closing duplicate is not an extra corner, and an open path's two ends always are.
    expect(cornerCount([...triangle, triangle[0]], true)).toBe(3);
    expect(cornerCount([[0, 0], [0.5, 0], [1, 0]], false)).toBe(2);
  });
});
