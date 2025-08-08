import { Canvas, useThree } from '@react-three/fiber';
import { OrbitControls } from '@react-three/drei';
import NeighborLines from './NeighborLines';
import RobotShape from './RobotShape';
import type { RobotData } from '../types/RobotData';
import { useEffect } from 'react';


function CameraController({ trigger }: { trigger: number }) {
  const { camera } = useThree();

  useEffect(() => {
      camera.position.set(0, 30, 0); // Reset to default position
      camera.lookAt(0,0,0);
  }, [trigger]);

  return null;
}

function RobotScene({ robots, trigger, onRobotClick }: { robots: RobotData[]; trigger: number; onRobotClick: (id: number | null) => void }) {
  return (
    <>
      <Canvas 
        onPointerMissed={() => onRobotClick(null)}
        shadows // Enable shadow rendering
      > 
        <axesHelper args={[100]} />
        <CameraController trigger={trigger} /> 
        <OrbitControls enablePan={false} minPolarAngle={0} maxPolarAngle={Math.PI / 2.5} />
        
        {/* Lighting setup for shadows */}
        <ambientLight intensity={0.4} />
        <directionalLight
          position={[10, 20, 10]}
          intensity={1}
          castShadow
          shadow-mapSize={[2048, 2048]}
          shadow-camera-far={50}
          shadow-camera-left={-20}
          shadow-camera-right={20}
          shadow-camera-top={20}
          shadow-camera-bottom={-20}
        />
        
        <NeighborLines robots={robots} />
        <group> 
          {robots.map((robot) => (
            <RobotShape key={robot.id} data={robot} onClick={(id) => {
              onRobotClick(id);
            }} />
          ))}
        </group>
      </Canvas>
    </>
  );
}

export default RobotScene;
