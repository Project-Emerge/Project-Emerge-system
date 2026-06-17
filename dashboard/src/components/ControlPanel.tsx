import { Joystick } from "react-joystick-component";
import { useMQTT } from "../mqtt/MQTTStore";
import { useOffloading } from "../offloading/OffloadingStore";
import type { RobotData } from "../types/RobotData";
import { useRef, useState } from "react";

const joystickSize = 200;
const turnScale = 0.7; // Lower = smoother, less sensitive
const joystickUpdateFrequency = 200; //ms

interface ControlPanelProps {
  robotId: number | null
  selectRobot: (id: number | null) => void
}

function ControlPanel({ robotId, selectRobot }: ControlPanelProps) {
    const [intervalId, setIntervalId] = useState<NodeJS.Timeout | null>(null);
    const { robots, publisher } = useMQTT();
    const { isCentral, hasEdge, setCompute } = useOffloading();
    const joystick = useRef<Joystick | null>(null);
    const [inputRobotId, setInputRobotId] = useState<number>(0);

    let selectedRobot: RobotData | undefined = 
    robotId !== null ? robots.find(robot => robot.id === robotId) : undefined;

    // //IF the robot is not detected, we can still control it like this
    const fakeSelected =  robotId !== null ? {
      id: robotId, 
      position: {x: 0, y: 0}, 
      isLeader: false, 
      neighbors: [], 
      orientation: 0
    } : undefined
    
    if(robotId !== null && !selectedRobot){
        selectedRobot = fakeSelected
    }

    function handleStart(): void {
        if (joystick.current) {
            const id = setInterval(() => {
                const coordinates = joystick.current?.state.coordinates;
                if (coordinates) {
                    publishCoordinates(coordinates.relativeX, coordinates.relativeY, coordinates.distance);
                }
            }, joystickUpdateFrequency);
            setIntervalId(id);

        }
    }

    function handleStop(): void {
        if (joystick.current) {
            if (intervalId) {
                publisher.publishMoveCommand(selectedRobot!.id, { left: 0, right: 0 });
                clearInterval(intervalId);
            }
        }
    }

    function publishCoordinates(relativeX: number, relativeY: number, distance: number): void {
        let x = relativeX / (joystickSize / 2) * (distance / 100);
        const y = - relativeY / (joystickSize / 2) * (distance / 100);

        x = -x //correction for whatever reason


        //this instead fixes the backward turning
        const inverted = y > 0 ? 1 : -1
        x = inverted*x

        let left = y + x * turnScale;
        let right = y - x * turnScale;

        const maxMag = Math.max(1, Math.abs(left), Math.abs(right));
        left /= maxMag;
        right /= maxMag;

        if (selectedRobot) {
            publisher.publishMoveCommand(selectedRobot.id, { left, right });
        }
    }

    return (
        <div className="control-panel">
            <input className="input-number"
                type="number"
                value={inputRobotId}
                onChange={e => setInputRobotId(Number(e.target.value))}
                min={0}
            />
            <button onClick={() => selectRobot(inputRobotId)}>{selectedRobot && selectedRobot.id === inputRobotId ? "De-select" : "Select"} Robot</button>
            {selectedRobot && (
                <>
                    <h2>Robot {selectedRobot.id}</h2>
                    <div>Position:</div>
                    <div style={{ marginLeft: '15px' }}>X: {selectedRobot.position.x.toFixed(3)}</div>
                    <div style={{ marginLeft: '15px' }}>Z: {selectedRobot.position.y.toFixed(3)}</div>
                    <p>Rotation: {orientation2Degrees(selectedRobot.orientation)}</p>
                    <div className="controls">
                        <div id="joystick">
                            <Joystick
                                ref={joystick}
                                size={joystickSize}
                                stickSize={50}
                                baseColor="gray"
                                stickColor="black"
                                start={handleStart}
                                stop={handleStop}
                            />
                        </div>

                    </div>
                    <button onClick={() => publisher.publishLeaderCommand(selectedRobot.id)}>
                        Make Leader
                    </button>
                    {hasEdge(selectedRobot.id) && (
                        <div className="offloading" style={{ marginTop: '12px' }}>
                            <div>
                                Compute:{" "}
                                <b style={{ color: isCentral(selectedRobot.id) ? "#34d399" : "#f5c065" }}>
                                    {isCentral(selectedRobot.id) ? "Central" : "Edge"}
                                </b>
                            </div>
                            <button onClick={() => setCompute(selectedRobot!.id, !isCentral(selectedRobot!.id))}>
                                {isCentral(selectedRobot.id) ? "Switch to Edge" : "Switch to Central"}
                            </button>
                        </div>
                    )}
                </>
            )}
        
        </div>
    );
}


function orientation2Degrees(orientation: number): number {
    return Number((orientation * (180 / Math.PI)).toFixed(1));
}

export default ControlPanel;