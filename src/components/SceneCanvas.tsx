import { Html, OrbitControls } from "@react-three/drei";
import { Canvas, type ThreeEvent, useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { useDashboardStore } from "../store/dashboard-store";

export type SceneMode = "2d" | "3d";
const ROBOT_DIAMETER_M = 0.11;
const ROBOT_RADIUS_M = ROBOT_DIAMETER_M / 2;
const TRAIL_DURATION_MS = 4_000;
const MAX_TRAIL_POINTS = 96;

type TrailSample = { x: number; y: number; receivedAt: number };

type SceneCanvasProps = {
  mode: SceneMode;
  resetToken: number;
};

type Bounds = { centerX: number; centerY: number; span: number };
const DEFAULT_BOUNDS: Bounds = { centerX: 3, centerY: 3, span: 7 };

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

function trailColor(id: string): THREE.Color {
  const seed = [...id].reduce((total, character) => total + character.charCodeAt(0), 0);
  return new THREE.Color().setHSL(0.42 + (seed % 18) / 100, 0.72, 0.57);
}

function RobotTrail({ id }: { id: string }): React.JSX.Element {
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
  const fadedTone = useMemo(() => new THREE.Color("#0b2637"), []);
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

function RobotMesh({ id }: { id: string }): React.JSX.Element | null {
  const group = useRef<THREE.Group>(null);
  const bodyMaterial = useRef<THREE.MeshStandardMaterial>(null);
  const statusMaterial = useRef<THREE.MeshStandardMaterial>(null);
  const target = useRef(useDashboardStore.getState().robots[id]?.pose);
  const selected = useDashboardStore((state) => state.selectedRobotId === id);
  const hasPose = useDashboardStore((state) => Boolean(state.robots[id]?.pose));

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
    const color = stale ? "#64748b" : uncertain ? "#f7b955" : "#42dfb0";
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
      <mesh castShadow receiveShadow>
        <sphereGeometry args={[ROBOT_RADIUS_M, 20, 16]} />
        <meshStandardMaterial ref={bodyMaterial} color="#42dfb0" roughness={0.42} metalness={0.2} />
      </mesh>
      <mesh position={[0, ROBOT_RADIUS_M + 0.003, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <circleGeometry args={[0.026, 3]} />
        <meshBasicMaterial color="#f8fcff" side={THREE.DoubleSide} />
      </mesh>
      <mesh position={[-0.018, 0.028, 0.018]}>
        <sphereGeometry args={[0.008, 12, 12]} />
        <meshStandardMaterial ref={statusMaterial} color="#42dfb0" emissive="#42dfb0" emissiveIntensity={2.5} />
      </mesh>
      {selected && (
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -ROBOT_RADIUS_M + 0.002, 0]}>
          <ringGeometry args={[0.075, 0.085, 32]} />
          <meshBasicMaterial color="#ffffff" transparent opacity={0.9} />
        </mesh>
      )}
      <Html position={[0, 0.15, 0]} center distanceFactor={11} style={{ pointerEvents: "none" }}>
        <span className="scene-label robot-label" style={{ padding: "2px 4px", fontSize: "8px", opacity: 0.9 }}>{id}</span>
      </Html>
    </group>
  );
}

function SceneContents({ mode, bounds, resetToken }: { mode: SceneMode; bounds: Bounds; resetToken: number }): React.JSX.Element {
  const robotIds = useDashboardStore((state) => state.robotIds);
  return (
    <>
      <color attach="background" args={["#07111f"]} />
      <fog attach="fog" args={["#07111f", 10, 45]} />
      <ambientLight intensity={0.7} />
      <directionalLight position={[5, 8, 3]} intensity={1.8} castShadow />
      <gridHelper args={[Math.max(12, bounds.span * 2.2), Math.max(12, Math.ceil(bounds.span * 2.2)), "#1f4a5a", "#122a3b"]} />
      <axesHelper args={[1]} />
      <CameraControls mode={mode} bounds={bounds} resetToken={resetToken} />
      {robotIds.map((id) => <RobotTrail key={`${id}-trail`} id={id} />)}
      {robotIds.map((id) => <RobotMesh key={id} id={id} />)}
    </>
  );
}

export function SceneCanvas({ mode, resetToken }: SceneCanvasProps): React.JSX.Element {
  const bounds = DEFAULT_BOUNDS;
  if (mode === "2d") {
    return <Canvas orthographic camera={{ position: [bounds.centerX, 18, bounds.centerY], zoom: 1 }} shadows dpr={[1, 2]} gl={{ antialias: true }}>
      <SceneContents mode={mode} bounds={bounds} resetToken={resetToken} />
    </Canvas>;
  }
  return <Canvas camera={{ position: [bounds.centerX + 6, 6, bounds.centerY + 6], fov: 45 }} shadows dpr={[1, 2]} gl={{ antialias: true }}>
    <SceneContents mode={mode} bounds={bounds} resetToken={resetToken} />
  </Canvas>;
}
