// import { Canvas } from '@react-three/fiber';
import { useRef, useEffect, useReducer } from 'react';
import { BufferGeometry, Float32BufferAttribute, LineBasicMaterial, Line as ThreeLine } from 'three';
import type { RobotData } from '../types/RobotData';
// import { color } from 'three/tsl';

/** Internal line cache entry */
interface LineCacheEntry {
  hash: string;
  robotA: number;
  robotB: number;
  positions: Float32Array;
  version: number;
  visible: boolean;
  color: string;
}

function NeighborLines({ robots }: { robots: RobotData[] }) {
  // Cache of all line pairs ever seen (persistent for lifetime of component)
  const lineCacheRef = useRef<Map<string, LineCacheEntry>>(new Map());
  // Dummy reducer to force re-render when cache updated
  const [, forceUpdate] = useReducer((x) => x + 1, 0);

  useEffect(() => {
    const cache = lineCacheRef.current;
    // Mark all lines invisible initially (they may be turned back on below)
    cache.forEach(entry => { entry.visible = false; });

    for (const robot of robots) {
      if (!robot.neighbors)  {continue }
      for (const nId of robot.neighbors) {
        const neighbor = robots.find(rb => rb.id === nId);
        if (!neighbor) { continue }
        const robotA = Math.min(robot.id, nId);
        const robotB = Math.max(robot.id, nId);
        const hash = `${robotA}-${robotB}`;
        let entry = cache.get(hash);
        if (!entry) {
          // Create new persistent entry
            const positions = new Float32Array(6); // two 3D points
            entry = {
              hash,
              robotA: robotA,
              robotB: robotB,
              positions,
              version: 0,
              visible: true,
              color: 'green'
            };
            cache.set(hash, entry);
        }
        // Update coordinates in place
        entry.positions[0] = robot.position.x;
        entry.positions[1] = 0;
        entry.positions[2] = robot.position.y;
        entry.positions[3] = neighbor.position.x;
        entry.positions[4] = 0;
        entry.positions[5] = neighbor.position.y;
        entry.version++;
        entry.visible = true; // ensure visible
      }
    }

    // Force a re-render so visibility / updated positions propagate
    forceUpdate();
  }, [robots]);

  return (
    <>
      {Array.from(lineCacheRef.current.values()).map(entry => (
        <MemoLine
          key={entry.hash}
          positions={entry.positions}
          version={entry.version}
          visible={entry.visible}
          color={entry.color}
        />
      ))}
    </>
  );
}

interface MemoLineProps {
  positions: Float32Array; // length 6
  version: number;
  visible: boolean;
  color: string;
}

const MemoLine = ({ positions, version, visible, color }: MemoLineProps) => {
  const geomRef = useRef<BufferGeometry | null>(null);
  const lineRef = useRef<ThreeLine | null>(null);

  // Initialize geometry & line once
  if (!geomRef.current) {
    const geometry = new BufferGeometry();
    geometry.setAttribute('position', new Float32BufferAttribute(positions.slice(), 3));
    geomRef.current = geometry;
    lineRef.current = new ThreeLine(geometry, new LineBasicMaterial({ color }));
  }

  // Update positions when version changes
  useEffect(() => {
    const geom = geomRef.current;
    if (!geom) { return }
    const attr = geom.getAttribute('position') as Float32BufferAttribute | undefined;
    if (attr) {
      (attr.array as Float32Array).set(positions);
      attr.needsUpdate = true;
      geom.computeBoundingSphere();
    }
  }, [version, positions]);

  // Update material color if it changes
  useEffect(() => {
    if (lineRef.current) {
      const mat = lineRef.current.material as LineBasicMaterial;
      if (mat.color.getStyle() !== color) { mat.color.set(color) }
    }
  }, [color]);

  if (!lineRef.current) { return null }
  return <primitive object={lineRef.current} visible={visible} />;
};

export default NeighborLines;
