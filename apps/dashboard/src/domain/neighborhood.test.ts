import { describe, expect, it } from "vitest";
import { neighborLinks } from "./neighborhood";

describe("derivazione dei collegamenti di vicinato", () => {
  it("collassa i link reciproci in un solo arco", () => {
    expect(neighborLinks({ A1B2C3: ["D4E5F6"], D4E5F6: ["A1B2C3"] })).toEqual([["A1B2C3", "D4E5F6"]]);
  });

  it("mantiene i link dichiarati da un solo robot", () => {
    expect(neighborLinks({ D4E5F6: ["A1B2C3"] })).toEqual([["A1B2C3", "D4E5F6"]]);
  });

  it("scarta gli auto-riferimenti e le liste vuote", () => {
    expect(neighborLinks({ A1B2C3: ["A1B2C3"], D4E5F6: [] })).toEqual([]);
  });

  it("produce un arco per ogni coppia di una topologia completa", () => {
    const links = neighborLinks({
      A1B2C3: ["D4E5F6", "AABBCC"],
      D4E5F6: ["A1B2C3", "AABBCC"],
      AABBCC: ["A1B2C3", "D4E5F6"],
    });
    expect(links).toHaveLength(3);
    expect(links.map((link) => link.join("|")).sort()).toEqual([
      "A1B2C3|AABBCC",
      "A1B2C3|D4E5F6",
      "AABBCC|D4E5F6",
    ]);
  });
});
