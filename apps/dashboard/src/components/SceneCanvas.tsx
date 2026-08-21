import { Html, OrbitControls } from "@react-three/drei";
import { Canvas, type ThreeEvent, useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { computeSceneBounds, DEFAULT_SCENE_BOUNDS, type SceneBounds } from "../domain/bounds";
import { neighborLinks } from "../domain/neighborhood";
import { useTheme, type ResolvedTheme } from "../services/theme-context";
import { useDashboardStore } from "../store/dashboard-store";

export type SceneMode = "2d" | "3d";
const ROBOT_DIAMETER_M = 0.11;
const ROBOT_RADIUS_M = ROBOT_DIAMETER_M / 2;
const ROBOT_HEIGHT_M = 0.04;
const TRAIL_DURATION_MS = 4_000;
const MAX_TRAIL_POINTS = 96;
const LINK_HEIGHT_M = 0.03;

// Links read the rendered meshes instead of the raw store poses so they stay glued to the
// robots while RobotMesh eases each body towards its latest position.
const robotNodes = new Map<string, THREE.Object3D>();

type TrailSample = { x: number; y: number; receivedAt: number };

type SceneCanvasProps = {
  mode: SceneMode;
  resetToken: number;
};

type Bounds = SceneBounds;

function CameraControls({ mode, bounds, resetToken }: { mode: SceneMode; bounds: Bounds; resetToken: number }): React.JSX.Element {
  const { camera, invalidate } = useThree();
  useEffect(() => {
    const target = new THREE.Vector3(bounds.centerX, 0, bounds.centerY);
    if (mode === "2d") {
      camera.position.set(bounds.centerX, Math.max(12, bounds.span * 2.2), bounds.centerY);
      if (camera instanceof THREE.OrthographicCamera) {
        camera.zoom = Math.max(0.65, 11 / bounds.span);
        camera.updateProjectionMatrix();
      }
    } else {
      camera.position.set(bounds.centerX + bounds.span * 0.9, bounds.span * 0.95, bounds.centerY + bounds.span * 0.9);
    }
    camera.lookAt(target);
    invalidate();
  }, [bounds, camera, invalidate, mode, resetToken]);

  return (
    <OrbitControls
      key={`${mode}-${resetToken}`}
      target={[bounds.centerX, 0, bounds.centerY]}
      enableDamping
      dampingFactor={0.08}
      enableRotate={mode === "3d"}
      enablePan
      mouseButtons={{ LEFT: THREE.MOUSE.PAN, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.ROTATE }}
      minDistance={1}
      maxDistance={60}
      minPolarAngle={mode === "2d" ? 0 : Math.PI / 7}
      maxPolarAngle={mode === "2d" ? 0 : Math.PI / 2.05}
    />
  );
}

function leaderColor(theme: ResolvedTheme): string {
  return theme === "dark" ? "#e3b25f" : "#b57f22";
}

function NeighborhoodLinks({ theme }: { theme: ResolvedTheme }): React.JSX.Element | null {
  const neighbors = useDashboardStore((state) => state.neighbors);
  const leaderId = useDashboardStore((state) => state.formation?.leaderId ?? null);
  const links = useMemo(() => neighborLinks(neighbors), [neighbors]);
  const capacity = Math.max(links.length, 1);

  const geometry = useMemo(() => {
    const next = new THREE.BufferGeometry();
    next.setAttribute("position", new THREE.BufferAttribute(new Float32Array(capacity * 6), 3));
    next.setAttribute("color", new THREE.BufferAttribute(new Float32Array(capacity * 6), 3));
    next.setDrawRange(0, 0);
    return next;
  }, [capacity]);

  const segments = useMemo(() => {
    const material = new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.62, depthWrite: false });
    const next = new THREE.LineSegments(geometry, material);
    next.frustumCulled = false;
    return next;
  }, [geometry]);

  const linkTone = useMemo(() => new THREE.Color(theme === "dark" ? "#5f7f95" : "#93a6b6"), [theme]);
  const leaderTone = useMemo(() => new THREE.Color(leaderColor(theme)), [theme]);

  useEffect(() => () => {
    geometry.dispose();
    segments.material.dispose();
  }, [geometry, segments]);

  useFrame(() => {
    const positions = geometry.getAttribute("position") as THREE.BufferAttribute;
    const colors = geometry.getAttribute("color") as THREE.BufferAttribute;
    let vertex = 0;
    for (const [from, to] of links) {
      const first = robotNodes.get(from);
      const second = robotNodes.get(to);
      // A neighbor without a pose has no mesh to anchor to yet.
      if (!first || !second) continue;
      const tone = leaderId === from || leaderId === to ? leaderTone : linkTone;
      positions.setXYZ(vertex, first.position.x, LINK_HEIGHT_M, first.position.z);
      colors.setXYZ(vertex, tone.r, tone.g, tone.b);
      vertex += 1;
      positions.setXYZ(vertex, second.position.x, LINK_HEIGHT_M, second.position.z);
      colors.setXYZ(vertex, tone.r, tone.g, tone.b);
      vertex += 1;
    }
    positions.needsUpdate = true;
    colors.needsUpdate = true;
    geometry.setDrawRange(0, vertex);
  });

  if (links.length === 0) return null;
  return <primitive object={segments} />;
}

function LeaderMarker({ theme }: { theme: ResolvedTheme }): React.JSX.Element {
  const ring = useRef<THREE.Mesh>(null);
  const beacon = useRef<THREE.Mesh>(null);
  const tone = useMemo(() => leaderColor(theme), [theme]);

  useFrame((state) => {
    const pulse = (Math.sin(state.clock.elapsedTime * 2.4) + 1) / 2;
    if (ring.current) {
      const scale = 1 + pulse * 0.16;
      ring.current.scale.set(scale, scale, 1);
      (ring.current.material as THREE.MeshBasicMaterial).opacity = 0.88 - pulse * 0.44;
    }
    if (beacon.current) {
      beacon.current.position.y = 0.098 + pulse * 0.014;
      beacon.current.rotation.y = state.clock.elapsedTime * 1.2;
    }
  });

  return (
    <>
      <mesh ref={ring} rotation={[-Math.PI / 2, 0, 0]} position={[0, -ROBOT_RADIUS_M + 0.003, 0]}>
        <ringGeometry args={[0.094, 0.108, 40]} />
        <meshBasicMaterial color={tone} transparent opacity={0.88} side={THREE.DoubleSide} depthWrite={false} />
      </mesh>
      <mesh ref={beacon} position={[0, 0.098, 0]} rotation={[Math.PI, 0, 0]}>
        <coneGeometry args={[0.02, 0.042, 4]} />
        <meshStandardMaterial color={tone} emissive={tone} emissiveIntensity={0.7} roughness={0.34} />
      </mesh>
    </>
  );
}

function trailColor(id: string): THREE.Color {
  const seed = [...id].reduce((total, character) => total + character.charCodeAt(0), 0);
  return new THREE.Color().setHSL(0.42 + (seed % 18) / 100, 0.52, 0.56);
}

function RobotTrail({ id, theme }: { id: string; theme: ResolvedTheme }): React.JSX.Element {
  const geometry = useMemo(() => {
    const next = new THREE.BufferGeometry();
    next.setAttribute("position", new THREE.BufferAttribute(new Float32Array(MAX_TRAIL_POINTS * 3), 3));
    next.setAttribute("color", new THREE.BufferAttribute(new Float32Array(MAX_TRAIL_POINTS * 3), 3));
    next.setDrawRange(0, 0);
    return next;
  }, []);
  const samples = useRef<TrailSample[]>([]);
  const dirty = useRef(false);
  const trailTone = useMemo(() => trailColor(id), [id]);
  const fadedTone = useMemo(() => new THREE.Color(theme === "dark" ? "#26313d" : "#cbd3dc"), [theme]);
  const line = useMemo(() => {
    const material = new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.86, depthWrite: false });
    const next = new THREE.Line(geometry, material);
    next.frustumCulled = false;
    next.renderOrder = 1;
    return next;
  }, [geometry]);

  useEffect(() => {
    const unsubscribe = useDashboardStore.subscribe(
      (state) => state.robots[id]?.pose,
      (pose) => {
        if (!pose) return;
        const receivedAt = Date.now();
        const last = samples.current.at(-1);
        const distance = last ? Math.hypot(last.x - pose.x_m, last.y - pose.y_m) : Infinity;
        if (last && distance < 0.003 && receivedAt - last.receivedAt < 150) return;
        samples.current.push({ x: pose.x_m, y: pose.y_m, receivedAt });
        if (samples.current.length > MAX_TRAIL_POINTS) samples.current.shift();
        dirty.current = true;
      },
      { equalityFn: (left, right) => left?.timestamp_us === right?.timestamp_us },
    );
    return unsubscribe;
  }, [id]);

  useEffect(() => () => {
    geometry.dispose();
    line.material.dispose();
  }, [geometry, line]);

  useFrame(() => {
    const now = Date.now();
    const cutoff = now - TRAIL_DURATION_MS;
    const before = samples.current.length;
    while (samples.current[0]?.receivedAt < cutoff) samples.current.shift();
    if (before !== samples.current.length) dirty.current = true;
    if (!dirty.current) return;

    const positions = geometry.getAttribute("position") as THREE.BufferAttribute;
    const colors = geometry.getAttribute("color") as THREE.BufferAttribute;
    const sampleCount = samples.current.length;
    samples.current.forEach((sample, index) => {
      const visibility = Math.max(0, Math.min(1, (sample.receivedAt - cutoff) / TRAIL_DURATION_MS));
      const color = fadedTone.clone().lerp(trailTone, visibility ** 1.8);
      positions.setXYZ(index, sample.x, 0.006, sample.y);
      colors.setXYZ(index, color.r, color.g, color.b);
    });
    positions.needsUpdate = true;
    colors.needsUpdate = true;
    geometry.setDrawRange(0, sampleCount);
    dirty.current = false;
  });

  return <primitive object={line} />;
}

function RobotMesh({ id, theme }: { id: string; theme: ResolvedTheme }): React.JSX.Element | null {
  const group = useRef<THREE.Group>(null);
  const bodyMaterial = useRef<THREE.MeshStandardMaterial>(null);
  const statusMaterial = useRef<THREE.MeshStandardMaterial>(null);
  const target = useRef(useDashboardStore.getState().robots[id]?.pose);
  const selected = useDashboardStore((state) => state.selectedRobotId === id);
  const isLeader = useDashboardStore((state) => state.formation?.leaderId === id);
  const hasPose = useDashboardStore((state) => Boolean(state.robots[id]?.pose));

  useEffect(() => {
    const node = group.current;
    if (!node) return;
    robotNodes.set(id, node);
    return () => { robotNodes.delete(id); };
  }, [id, hasPose]);

  useEffect(() => useDashboardStore.subscribe(
    (state) => state.robots[id]?.pose,
    (pose) => { target.current = pose; },
    { equalityFn: (left, right) => left?.timestamp_us === right?.timestamp_us },
  ), [id]);

  useFrame((_state, delta) => {
    const robot = useDashboardStore.getState().robots[id];
    const pose = target.current ?? robot?.pose;
    if (!group.current || !pose) return;
    const targetPosition = new THREE.Vector3(pose.x_m, ROBOT_RADIUS_M, pose.y_m);
    group.current.position.lerp(targetPosition, 1 - Math.exp(-delta * 17));
    const heading = -pose.heading_rad;
    group.current.rotation.y += THREE.MathUtils.euclideanModulo(heading - group.current.rotation.y + Math.PI, Math.PI * 2) - Math.PI;
    const stale = !robot || Date.now() - robot.lastSeenAt > 3_000;
    const uncertain = pose.position_variance_m2 > 0.1;
    const color = stale
      ? theme === "dark" ? "#647184" : "#8994a3"
      : uncertain ? "#d39a4a" : "#45a487";
    bodyMaterial.current?.color.set(color);
    statusMaterial.current?.emissive.set(color);
  });

  const handleClick = (event: ThreeEvent<MouseEvent>) => {
    event.stopPropagation();
    useDashboardStore.getState().toggleSelectedRobot(id);
  };

  if (!hasPose) return null;

  return (
    <group ref={group} onClick={handleClick}>
      <mesh castShadow receiveShadow position={[0, -ROBOT_RADIUS_M + ROBOT_HEIGHT_M / 2, 0]} rotation={[0, -Math.PI / 2, 0]}>
        <cylinderGeometry args={[ROBOT_RADIUS_M, ROBOT_RADIUS_M, ROBOT_HEIGHT_M, 3]} />
        <meshStandardMaterial ref={bodyMaterial} color="#45a487" roughness={0.48} metalness={0.12} />
      </mesh>
      <mesh position={[0, -ROBOT_RADIUS_M + ROBOT_HEIGHT_M + 0.001, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <circleGeometry args={[0.026, 3]} />
        <meshBasicMaterial color="#f8fcff" side={THREE.DoubleSide} />
      </mesh>
      <mesh position={[-0.018, -ROBOT_RADIUS_M + ROBOT_HEIGHT_M + 0.004, 0.018]}>
        <sphereGeometry args={[0.008, 12, 12]} />
        <meshStandardMaterial ref={statusMaterial} color="#45a487" emissive="#45a487" emissiveIntensity={1.8} />
      </mesh>
      {selected && (
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -ROBOT_RADIUS_M + 0.002, 0]}>
          <ringGeometry args={[0.075, 0.085, 32]} />
          <meshBasicMaterial color={theme === "dark" ? "#e7e9ed" : "#3b4350"} transparent opacity={0.86} />
        </mesh>
      )}
      {isLeader && <LeaderMarker theme={theme} />}
      <Html position={[0, isLeader ? 0.198 : 0.15, 0]} center distanceFactor={11} zIndexRange={[10, 0]} style={{ pointerEvents: "none" }}>
        <span
          className={isLeader ? "scene-label robot-label leader" : "scene-label robot-label"}
          style={{ padding: "1px 3px", fontSize: "5px", opacity: 0.9 }}
        >
          {isLeader ? `\u2605 ${id}` : id}
        </span>
      </Html>
    </group>
  );
}

function SceneContents({ mode, bounds, resetToken, theme }: { mode: SceneMode; bounds: Bounds; resetToken: number; theme: ResolvedTheme }): React.JSX.Element {
  const robotIds = useDashboardStore((state) => state.robotIds);
  const sceneBackground = theme === "dark" ? "#111820" : "#edf0f4";
  return (
    <>
      <color attach="background" args={[sceneBackground]} />
      <fog attach="fog" args={[sceneBackground, 10, 45]} />
      <ambientLight intensity={0.7} />
      <directionalLight position={[5, 8, 3]} intensity={1.8} castShadow />
      <gridHelper args={[
        Math.max(12, bounds.span * 2.2),
        Math.max(12, Math.ceil(bounds.span * 2.2)),
        theme === "dark" ? "#3f5262" : "#aab4c0",
        theme === "dark" ? "#252f39" : "#d6dce3",
      ]} />
      <axesHelper args={[1]} />
      <CameraControls mode={mode} bounds={bounds} resetToken={resetToken} />
      <NeighborhoodLinks theme={theme} />
      {robotIds.map((id) => <RobotTrail key={`${id}-trail`} id={id} theme={theme} />)}
      {robotIds.map((id) => <RobotMesh key={id} id={id} theme={theme} />)}
    </>
  );
}

/**
 * Frames the arena around the robots that actually have a position, recomputed on mount,
 * the first time any pose arrives, and whenever "Center arena" bumps resetToken. Deliberately
 * not reactive to every pose update, or the view would fight the user's pan/zoom as robots move.
 */
function useFramingBounds(resetToken: number): Bounds {
  const posedRobotIds = useDashboardStore((state) => state.posedRobotIds);
  const hasFramed = useRef(false);
  const [bounds, setBounds] = useState<Bounds>(DEFAULT_SCENE_BOUNDS);

  useEffect(() => {
    if (posedRobotIds.length === 0 && !hasFramed.current) return;
    hasFramed.current = true;
    const state = useDashboardStore.getState();
    const poses = posedRobotIds
      .map((id) => state.robots[id]?.pose)
      .filter((pose): pose is NonNullable<typeof pose> => Boolean(pose));
    setBounds(computeSceneBounds(poses));
    // Re-frame on an explicit reset or the fleet's first-ever pose, not on every pose update.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetToken, posedRobotIds.length > 0]);

  return bounds;
}

export function SceneCanvas({ mode, resetToken }: SceneCanvasProps): React.JSX.Element {
  const bounds = useFramingBounds(resetToken);
  const { resolvedTheme } = useTheme();
  if (mode === "2d") {
    return <Canvas orthographic camera={{ position: [bounds.centerX, 18, bounds.centerY], zoom: 1 }} shadows dpr={[1, 2]} gl={{ antialias: true }}>
      <SceneContents mode={mode} bounds={bounds} resetToken={resetToken} theme={resolvedTheme} />
    </Canvas>;
  }
  return <Canvas camera={{ position: [bounds.centerX + 6, 6, bounds.centerY + 6], fov: 45 }} shadows dpr={[1, 2]} gl={{ antialias: true }}>
    <SceneContents mode={mode} bounds={bounds} resetToken={resetToken} theme={resolvedTheme} />
  </Canvas>;
}
