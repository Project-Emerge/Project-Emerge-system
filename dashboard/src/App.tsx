import { useEffect, useState } from 'react';
import './App.css';
import RobotScene from './components/RobotScene';
import ControlPanel from './components/ControlPanel';
import TopBar from './components/TopBar';
import type { EventStream } from './utils/EventStreamInterface';
import type { RobotData } from './types/RobotData';
import type { CommandPublisher } from './utils/CommandPublisherInterface';
import { FakeEventStream } from './utils/FakeEventStream';
import { MQTTEventStream } from './utils/MQTTEventStream';
import { FakeCommandPublisher } from './utils/FakeCommandPublisher';
import { MQTTCommandPublisher } from './utils/MQTTCommandPublisher';

const MQTT_BROKER_URL = 'ws://localhost:8083/mqtt'; // Replace with actual broker
const USE_FAKE_DATA = false; // set to false to enable MQTT

const eventStream: EventStream = USE_FAKE_DATA
  ? new FakeEventStream(10)
  : new MQTTEventStream(MQTT_BROKER_URL);

const commandPublisher: CommandPublisher = USE_FAKE_DATA
  ? new FakeCommandPublisher()
  : new MQTTCommandPublisher(MQTT_BROKER_URL);

function App() {
  const [robots, setRobots] = useState<RobotData[]>([]); // Initialize robots state
  const [resetCameraTrigger, setResetCameraTrigger] = useState(0); // Use a counter for reset triggers
  const [selectedRobot, setSelectedRobot] = useState<number | null>(null); // Track selected robot

  useEffect(() => {
    eventStream.subscribe((updatedRobots: RobotData[]) => {
      setRobots(updatedRobots); 
    });
    return () => eventStream.cleanup();
  }, []);

  useEffect(() => {
    // Reset camera at the beginning of the app
    setResetCameraTrigger((prev) => prev + 1);
  }, []);

  const handleResetCamera = () => {
    setResetCameraTrigger((prev) => prev + 1);
  };

  const handleRobotClick = (id: number | null) => {
    console.log(`Robot clicked: ${id}`);
    console.log(`Selected robot: ${robots}`)
    console.log(`Selected robot ID: ${id}`);
    setSelectedRobot((prevSelectedRobot) => (prevSelectedRobot === id ? null : id));
  };

  return (
    <div className="app-layout"> 
      <TopBar onResetCamera={handleResetCamera} />
      <div className="main-content">
        <div className="map-container">
          <RobotScene robots={robots} trigger={resetCameraTrigger} onRobotClick={handleRobotClick} /> 
        </div>
        {selectedRobot !== null && robots.find(r => r.id == selectedRobot) && (
          <ControlPanel commandPublisher={commandPublisher} robot={robots.find(r => r.id == selectedRobot)!} />
        )}
      </div>
    </div>
  );
}

export default App;
