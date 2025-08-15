import { useRef, useMemo } from 'react';
import { useFrame, useThree } from '@react-three/fiber';
import * as THREE from 'three';
import { Text, useGLTF } from '@react-three/drei';
import type { RobotData } from '../types/RobotData';

// const normalizeAngle = (angle: number): number => {
//   return ((angle + Math.PI) % (2 * Math.PI)) - Math.PI;
// };

// Helper: check and fix diagonal orientation
// const fixOrientation = (angle: number): number => {
//   const normalized = normalizeAngle(angle);
//   const tol = 0.01;

//   // Check for diagonals: 45°, 135°, -45°, -135°
//   const diagonals = [Math.PI / 4, 3 * Math.PI / 4, -Math.PI / 4, -3 * Math.PI / 4];

//   const isDiagonal = diagonals.some(
//     (diag) => Math.abs(normalized - diag) < tol
//   );

//   if (isDiagonal) {
//     // Rotate by -90 degrees (–π/2)
//     return normalized - Math.PI / 2;
//   }

//   return normalized;
// };

function RobotShape({ data, onClick }: { data: RobotData; onClick?: (id : number) => void }) {
  const { position, orientation, isLeader, id } = data;
  const meshRef = useRef<THREE.Group>(null);
  const textRef = useRef<THREE.Mesh>(null);
  const { camera } = useThree();
  
  // Load the GLB model
  const { scene } = useGLTF('/src/assets/base.glb');
  
  // Use the original model with leader/follower coloring
  const robotModel = useMemo(() => {
    const clonedScene = scene.clone();
    const color = isLeader ? 'gold' : 'skyblue';
    
    // Ensure all meshes in the model cast shadows and apply color
    clonedScene.traverse((child) => {
      if (child instanceof THREE.Mesh) {
        child.castShadow = true;
        child.receiveShadow = true;
        if (child.material instanceof THREE.Material) {
          child.material = child.material.clone();
          if ('color' in child.material) {
            (child.material as THREE.MeshStandardMaterial).color = new THREE.Color(color);
          }
        }
      }
    });
    
    return clonedScene;
  }, [scene, isLeader]);

  useFrame(() => {
    if (meshRef.current) {
      meshRef.current.position.set(position.x, 0, position.y);
      // const correctedOrientation = fixOrientation(orientation);
      // Apply orientation (already in radians from -pi to pi) with a -180 degree offset to align with X-axis
      meshRef.current.rotation.set(0, -orientation , 0);
      meshRef.current.userData = { id }; // Set userData with robot ID
    }
    if (textRef.current) {
      textRef.current.lookAt(camera.position); // Make the text face the camera
    }
  });

  return (
    <>
      <group 
        ref={meshRef} 
        castShadow 
        receiveShadow 
        onClick={(event) => {
          event.stopPropagation(); // Prevent event bubbling
          if (onClick) {onClick(data.id);}
        }}
      >
        {/* cube as a mesh */}
        <mesh 
          geometry={new THREE.BoxGeometry(1, 1, 1)} 
          material={new THREE.MeshStandardMaterial({ color: isLeader ? 'gold' : 'skyblue' })} 
          position={[0, 0.5, 0]} // Position the cube above the ground
          scale={[11, 7, 17]} // Scale
        />
        {/* <primitive 
          object={robotModel} 
          scale={[0.01, 0.01, 0.01]}
        /> */}
      </group>
      <Text
        ref={textRef}
        position={[position.x, 1.5, position.y]} // Position text above the mesh
        fontSize={0.5}
        color="black"
        anchorX="center"
        anchorY="middle"
        font="https://fonts.gstatic.com/s/roboto/v20/KFOmCnqEu92Fr1Mu4mxP.ttf" // Use a compatible font
      >
        {id}
      </Text>
    </>
  );
}

// Preload the GLB model
useGLTF.preload('/src/assets/base.glb');

export default RobotShape;
