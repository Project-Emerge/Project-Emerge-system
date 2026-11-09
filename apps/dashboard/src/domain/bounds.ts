export type SceneBounds = { centerX: number; centerY: number; span: number };

export const DEFAULT_SCENE_BOUNDS: SceneBounds = { centerX: 3, centerY: 3, span: 7 };

const MIN_SPAN_M = 2;
const PADDING_M = 1;

/**
 * Frames the scene around the robots that actually have a position, instead of a fixed
 * guess at the arena size. Falls back to DEFAULT_SCENE_BOUNDS when nothing has reported yet.
 */
export function computeSceneBounds(points: { x_m: number; y_m: number }[]): SceneBounds {
  if (points.length === 0) return DEFAULT_SCENE_BOUNDS;

  let minX = points[0].x_m;
  let maxX = points[0].x_m;
  let minY = points[0].y_m;
  let maxY = points[0].y_m;
  for (const { x_m, y_m } of points) {
    if (x_m < minX) minX = x_m;
    if (x_m > maxX) maxX = x_m;
    if (y_m < minY) minY = y_m;
    if (y_m > maxY) maxY = y_m;
  }

  return {
    centerX: (minX + maxX) / 2,
    centerY: (minY + maxY) / 2,
    span: Math.max(maxX - minX, maxY - minY, MIN_SPAN_M) + PADDING_M,
  };
}
