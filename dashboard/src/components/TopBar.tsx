import { useState, useEffect } from "react";
import FormationPopup, { FORMATION_TYPES } from "./FormationPopup";
import Switch from "@mui/material/Switch";

interface TopBarProps {
  onResetCamera: () => void;
  showNeighbors: boolean;
  setShowNeighbors: (show: boolean) => void;
}

function TopBar({ onResetCamera, showNeighbors, setShowNeighbors }: TopBarProps) {
  // Neighborhood logic
  const [neighborhoodType, setNeighborhoodType] = useState<string>("FULL");
  const [radius, setRadius] = useState<number>(1.0);
  const flaskUrl = "http://localhost:5000/neighborhood";

  // Formation state
  const [showFormationPopup, setShowFormationPopup] = useState(false);
  const [selectedFormation, setSelectedFormation] = useState<string>("pointToLeader");
  const [currentFormation, setCurrentFormation] = useState<string>("pointToLeader");

  useEffect(() => {
    // Optionally, fetch the current formation from backend if available
    // For now, just set initial value
    setCurrentFormation(selectedFormation);
  }, []);

  function updateNeighborhood(type: string, radiusValue?: number) {
    const body: any = { type };
    if (type === "RADIUS" && radiusValue !== undefined) {
      body.radius = radiusValue;
    }
    fetch(flaskUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    })
      .then(res => res.json())
      .then(data => {
        setNeighborhoodType(data.type);
        setRadius(data.radius);
      });
  }

  function handleFormationUpdate() {
    setCurrentFormation(selectedFormation);
    setShowFormationPopup(false);
  }

  // Get the label for the current formation
  const currentFormationLabel =
    FORMATION_TYPES.find(f => f.value === currentFormation)?.label || currentFormation;

  return (
    <div className="top-bar" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
      {/* Left: Formation controls */}
      <div style={{ display: "flex", alignItems: "center" }}>
        <button onClick={() => setShowFormationPopup(true)}>
          Update Formation
        </button>
        {showFormationPopup && (
          <FormationPopup
            selectedFormation={selectedFormation}
            setSelectedFormation={setSelectedFormation}
            onConfirm={handleFormationUpdate}
            onCancel={() => setShowFormationPopup(false)}
          />
        )}
        <button onClick={onResetCamera} style={{ marginLeft: "1em" }}>Reset Camera</button>
      </div>
      {/* Center: Current formation name */}
      <div style={{ textAlign: "center", flex: 1, fontWeight: "bold", fontSize: "1.1em" }}>
        Current Formation: {currentFormationLabel}
      </div>
      {/* Right: Neighborhood controls and neighbor lines toggle */}
      <div style={{ display: "flex", alignItems: "center" }}>
        <Switch
          checked={showNeighbors}
          onChange={(_, checked) => setShowNeighbors(checked)}
          color="success"
        />
        {neighborhoodType !== "FULL" && (
          <button onClick={() => updateNeighborhood("FULL")}>
            Set FULL Neighborhood
          </button>
        )}
        {neighborhoodType === "FULL" && (
          <form
            onSubmit={e => {
              e.preventDefault();
              updateNeighborhood("RADIUS", radius);
            }}
            style={{ display: "flex", alignItems: "center", marginLeft: "1em" }}
          >
            <input
              className="input-number"
              type="number"
              step="0.1"
              value={radius}
              onChange={e => setRadius(Number(e.target.value))}
              style={{ width: "80px", marginRight: "8px" }}
            />
            <button type="submit">Set RADIUS Neighborhood</button>
          </form>
        )}
        {neighborhoodType === "RADIUS" && (
          <form
            onSubmit={e => {
              e.preventDefault();
              updateNeighborhood("RADIUS", radius);
            }}
            style={{ display: "flex", alignItems: "center", marginLeft: "1em" }}
          >
            <input
              className="input-number"
              type="number"
              step="0.1"
              value={radius}
              onChange={e => setRadius(Number(e.target.value))}
              style={{ width: "80px", marginRight: "8px" }}
            />
            <button type="submit">Update Radius</button>
          </form>
        )}
      </div>
    </div>
  );
}

export default TopBar;
