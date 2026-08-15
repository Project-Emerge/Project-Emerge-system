import { ANCHOR_IDS, type AnchorCalibration } from "../../shared/protocol";

export const DISTANCE_KEYS = ["a1a2", "a2a3", "a3a4", "a4a1", "a1a3", "a2a4"] as const;
export type DistanceKey = (typeof DISTANCE_KEYS)[number];
export type DistanceMeasurements = Record<DistanceKey, number>;

export type CalibrationSolution = {
  coordinates: Array<Pick<AnchorCalibration, "anchor_id" | "x" | "y">>;
  residuals: Record<DistanceKey, number>;
  rms: number;
  iterations: number;
};

const PAIRS: Array<{ key: DistanceKey; from: number; to: number }> = [
  { key: "a1a2", from: 0, to: 1 },
  { key: "a2a3", from: 1, to: 2 },
  { key: "a3a4", from: 2, to: 3 },
  { key: "a4a1", from: 3, to: 0 },
  { key: "a1a3", from: 0, to: 2 },
  { key: "a2a4", from: 1, to: 3 },
];

type Point = [number, number];

function pointsFromParameters(parameters: number[]): Point[] {
  return [
    [0, 0],
    [parameters[0], 0],
    [parameters[1], parameters[2]],
    [parameters[3], parameters[4]],
  ];
}

function residualVector(parameters: number[], measurements: DistanceMeasurements): number[] {
  const points = pointsFromParameters(parameters);
  return PAIRS.map(({ key, from, to }) => {
    const dx = points[from][0] - points[to][0];
    const dy = points[from][1] - points[to][1];
    return Math.hypot(dx, dy) - measurements[key];
  });
}

function cost(residuals: number[]): number {
  return residuals.reduce((total, residual) => total + residual * residual, 0);
}

function solveLinearSystem(matrix: number[][], vector: number[]): number[] | null {
  const augmented = matrix.map((row, index) => [...row, vector[index]]);
  const size = vector.length;

  for (let pivot = 0; pivot < size; pivot += 1) {
    let best = pivot;
    for (let row = pivot + 1; row < size; row += 1) {
      if (Math.abs(augmented[row][pivot]) > Math.abs(augmented[best][pivot])) best = row;
    }
    if (Math.abs(augmented[best][pivot]) < 1e-12) return null;
    [augmented[pivot], augmented[best]] = [augmented[best], augmented[pivot]];
    const divisor = augmented[pivot][pivot];
    for (let column = pivot; column <= size; column += 1) augmented[pivot][column] /= divisor;
    for (let row = 0; row < size; row += 1) {
      if (row === pivot) continue;
      const factor = augmented[row][pivot];
      for (let column = pivot; column <= size; column += 1) {
        augmented[row][column] -= factor * augmented[pivot][column];
      }
    }
  }
  return augmented.map((row) => row[size]);
}

function initialParameters(measurements: DistanceMeasurements): number[] {
  const baseline = measurements.a1a2;
  const x3 = (baseline ** 2 + measurements.a1a3 ** 2 - measurements.a2a3 ** 2) / (2 * baseline);
  const y3 = Math.sqrt(Math.max(1e-8, measurements.a1a3 ** 2 - x3 ** 2));
  const x4 = (baseline ** 2 + measurements.a4a1 ** 2 - measurements.a2a4 ** 2) / (2 * baseline);
  const y4 = Math.sqrt(Math.max(1e-8, measurements.a4a1 ** 2 - x4 ** 2));
  return [baseline, x3, y3, x4, y4];
}

export function solveAnchorCoordinates(measurements: DistanceMeasurements): CalibrationSolution {
  for (const key of DISTANCE_KEYS) {
    if (!Number.isFinite(measurements[key]) || measurements[key] <= 0) {
      throw new Error("All distances must be positive values in metres.");
    }
  }

  let parameters = initialParameters(measurements);
  let residuals = residualVector(parameters, measurements);
  let currentCost = cost(residuals);
  let damping = 1e-3;
  let iterations = 0;

  for (; iterations < 80; iterations += 1) {
    const epsilon = 1e-6;
    const jacobian = residuals.map(() => Array<number>(parameters.length).fill(0));
    for (let column = 0; column < parameters.length; column += 1) {
      const shifted = [...parameters];
      shifted[column] += epsilon;
      const shiftedResiduals = residualVector(shifted, measurements);
      for (let row = 0; row < residuals.length; row += 1) {
        jacobian[row][column] = (shiftedResiduals[row] - residuals[row]) / epsilon;
      }
    }

    const normal = Array.from({ length: parameters.length }, () => Array<number>(parameters.length).fill(0));
    const gradient = Array<number>(parameters.length).fill(0);
    for (let row = 0; row < residuals.length; row += 1) {
      for (let column = 0; column < parameters.length; column += 1) {
        gradient[column] -= jacobian[row][column] * residuals[row];
        for (let inner = 0; inner < parameters.length; inner += 1) {
          normal[column][inner] += jacobian[row][column] * jacobian[row][inner];
        }
      }
    }
    for (let index = 0; index < parameters.length; index += 1) normal[index][index] += damping;
    const step = solveLinearSystem(normal, gradient);
    if (!step) break;

    const candidate = parameters.map((value, index) => value + step[index]);
    const candidateResiduals = residualVector(candidate, measurements);
    const candidateCost = cost(candidateResiduals);
    if (candidateCost < currentCost) {
      parameters = candidate;
      residuals = candidateResiduals;
      currentCost = candidateCost;
      damping = Math.max(1e-8, damping * 0.35);
      if (Math.hypot(...step) < 1e-7) break;
    } else {
      damping = Math.min(1e8, damping * 10);
    }
  }

  const points = pointsFromParameters(parameters);
  const residualMap = Object.fromEntries(PAIRS.map(({ key }, index) => [key, residuals[index]])) as Record<DistanceKey, number>;
  return {
    coordinates: points.map(([x, y], index) => ({ anchor_id: ANCHOR_IDS[index], x, y })),
    residuals: residualMap,
    rms: Math.sqrt(currentCost / residuals.length),
    iterations,
  };
}
