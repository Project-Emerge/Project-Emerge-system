import type { RobotData } from '../types/RobotData';
import type { CommandPublisher } from '../utils/CommandPublisherInterface';
import { Joystick } from 'react-joystick-component';
import { useState, useEffect } from 'react';
import type { Vector2D } from '../types/Vector2D';


interface ControlPanelProps {
  commandPublisher: CommandPublisher;
  robot: RobotData; // Ensure robot prop is required
}

interface IJoystickUpdateEvent {
  type: 'move' | 'stop' | 'start';
  x: number | null;
  y: number | null;
  direction: 'FORWARD' | 'RIGHT' | 'LEFT' | 'BACKWARD' | null;
  distance: number | null; // Percentile 0-100% of joystick
}

function ControlPanel({ commandPublisher, robot }: ControlPanelProps) {
  const [joystickPosition, setJoystickPosition] = useState<Vector2D>({ x: 0, y: 0 });
  const [intervalId, setIntervalId] = useState<NodeJS.Timeout | null>(null);
  const [counter, setCounter] = useState(0);

  //TODO is this needed? Maybe it's ok if i simply send the direction once and then I dont need to update it continuously, for now let's keep it

  const handleMove = (event: IJoystickUpdateEvent) => {
    if (event.x !== null && event.y !== null) {
      setJoystickPosition({ x: event.x, y: event.y });
    
    }
  };

  const handleStart = () => {
    setIntervalId(setInterval(() => {
      setCounter(prev => prev + 1);
    }, 300));
  }

  const handleStop = () => {
    if (intervalId) {
      clearInterval(intervalId);
      setIntervalId(null);
      const command: Vector2D = {
        x: 0,
        y: 0,
      };
      commandPublisher.publishMoveCommand(robot.id, command);
    }
  };

  useEffect(() => {
    if (intervalId) {
      const command: Vector2D = {
        x: joystickPosition.x,
        y: joystickPosition.y
      };
      commandPublisher.publishMoveCommand(robot.id, command);
    }
  }, [joystickPosition, counter, commandPublisher, robot.id, intervalId]);

  return (
    <div className="control-panel">
      <h3>Robot {robot.id}</h3>

      <div className="robot-data"> {/* Display robot data */}
          <p>X={robot.position.x}</p>
          <p>Z={robot.position.y}</p>
        <p>Orientation: {robot.orientation}°</p>
        <p>Leader: {robot.isLeader ? 'Yes' : 'No'}</p>
      </div>
      
      <Joystick
        size={100}
        baseColor="gray"
        stickColor="black"
        move={handleMove}
        start={handleStart}
        stop={handleStop}
        throttle={100}
      />

      <button onClick={() => commandPublisher.publishLeaderCommand(robot.id, true)}>Make Leader</button> {/* Add button to make the robot the leader */}
    </div>
  );
}

export default ControlPanel;
