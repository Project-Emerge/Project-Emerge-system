import { Canvas, /*invalidate*/ } from "@react-three/fiber";
import Ground from "./Ground";
import { useMQTT } from "../mqtt/MQTTStore";
import ResettableCamera from "./ResettableCamera";
// import { Selection, Select, EffectComposer, Outline } from "@react-three/postprocessing";

import Robot from "./Robot";
import NeighborLines from "./NeighborLines";


interface RobotSceneProps {
    showNeighbors: boolean;
    onRobotClick: (id: number | null) => void;
    cameraTrigger: number;
    selectedRobotId: number | null;
}

function RobotScene({ showNeighbors, onRobotClick, cameraTrigger, selectedRobotId }: RobotSceneProps) {

    const { robots } = useMQTT();

    // useEffect(() => {
    //     console.log("Robots updated:", robots);
    // }, [robots]);

    return (
    <Canvas
        shadows
        frameloop="demand"
        onPointerMissed={() => onRobotClick(null)}
        camera={{ position: [3, 5, -3], fov: 60, far: 3000}}
    >
        <Ground />
        {/* <axesHelper args={[500]} /> */}
        <directionalLight position={[10, 20, 10]} intensity={1} />
        <ambientLight intensity={0.2} />
        <ResettableCamera trigger={cameraTrigger} />

        {/* Don't know why but using the border highligh effect drops performances significantly
        <Selection>
        <EffectComposer multisampling={8} autoClear={false}>
          <Outline visibleEdgeColor={0xffff00} />
        </EffectComposer> */}

        {showNeighbors && <NeighborLines robots={robots}/>}

        {robots.map((robot) => (
            <Robot key={robot.id} robot={robot} selected={robot.id === selectedRobotId} onClick={() => onRobotClick(robot.id)} />
        ))}
        {/* </Selection> */}

    </Canvas>
    )
}

export default RobotScene;