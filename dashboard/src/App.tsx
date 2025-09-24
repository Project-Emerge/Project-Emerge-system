import { useState } from "react";
import TopBar from "./components/TopBar";
import RobotScene from "./components/RobotScene";
import ControlPanel from "./components/ControlPanel";
import NeighborLines from "./components/NeighborLines";
import { useMQTT } from "./mqtt/MQTTStore";


function App() {
  const [selectedRobotId, setSelectedRobot] = useState<null | number>(null);
  const [cameraTrigger, triggerCamera] = useState(0);
  const [showNeighbors, setShowNeighbors] = useState(true);

  const onRobotClick = (id: number | null) => {
    setSelectedRobot((selectedRobot) => (selectedRobot === id ? null : id));
  };

  // Get robots from MQTT store for neighbor lines
  const { robots } = useMQTT();

  return (
    <div id="app">
      <TopBar
        onResetCamera={() => triggerCamera(cameraTrigger + 1)}
        showNeighbors={showNeighbors}
        setShowNeighbors={setShowNeighbors}
      />
      <div className="main-content">
        <div className="scene">
          <RobotScene
            showNeighbors={showNeighbors}
            onRobotClick={onRobotClick}
            cameraTrigger={cameraTrigger}
            selectedRobotId={selectedRobotId}
          />
          {showNeighbors && <NeighborLines robots={robots} />}
        </div>
        <div className="sidebar">
          <ControlPanel
            robotId={selectedRobotId}
            selectRobot={onRobotClick}
          />
        </div>
      </div>
    </div>
  );
}

export default App;
