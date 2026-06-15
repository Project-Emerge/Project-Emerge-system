import React, { useState, useEffect } from "react";
import {useMQTT} from "../mqtt/MQTTStore.ts";

export interface FormationType {
  value: string;
  label: string;
  defaultParams: Record<string, number>;
}

export const FORMATION_TYPES: FormationType[] = [
  { value: "pointToLeader", label: "Point to Leader", defaultParams: {} },
  { value: "vShape", label: "V Shape", defaultParams: { interDistanceV: 0.4, angleV: -Math.PI / 4, collisionArea: 0.3, stabilityThreshold: 0.1 } },
  { value: "lineShape", label: "Line Shape", defaultParams: { interDistanceLine: 0.4, collisionArea: 0.3, stabilityThreshold: 0.1 } },
  { value: "circleShape", label: "Circle Shape", defaultParams: { radius: 0.6, collisionArea: 0.3, stabilityThreshold: 0.1 } },
  { value: "squareShape", label: "Square Shape", defaultParams: { interDistanceSquare: 0.4, collisionArea: 0.3, stabilityThreshold: 0.1 } },
  { value: "verticalLineShape", label: "Vertical Line Shape", defaultParams: { interDistanceVertical: 0.4, collisionArea: 0.3, stabilityThreshold: 0.1 } },
  { value: "heartShape", label: "Heart Shape", defaultParams: { scaleHeart: 0.06, collisionArea: 0.3, stabilityThreshold: 0.1 } },
  { value: "stop", label: "Stop", defaultParams: {} }
];

// Store the original defaults for reset functionality
const ORIGINAL_DEFAULTS: Record<string, Record<string, number>> = {};
FORMATION_TYPES.forEach(f => {
  ORIGINAL_DEFAULTS[f.value] = { ...f.defaultParams };
});

interface FormationPopupProps {
  selectedFormation: string;
  setSelectedFormation: (value: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
}

const FormationPopup: React.FC<FormationPopupProps> = ({
  selectedFormation,
  setSelectedFormation,
  onConfirm,
  onCancel
}) => {

    const { publisher } = useMQTT();
  // Find the selected formation type object
  const formationObj = FORMATION_TYPES.find(f => f.value === selectedFormation);

  // State for parameter values
  const [params, setParams] = useState<Record<string, number>>({});

  // Reset params when formation changes
  useEffect(() => {
    if (formationObj && formationObj.defaultParams) {
      // Only include keys with defined number values
      const filteredParams: Record<string, number> = {};
      Object.entries(formationObj.defaultParams).forEach(([key, value]) => {
        if (typeof value === "number") { filteredParams[key] = value; }
      });
      setParams(filteredParams);
    } else {
      setParams({});
    }
  }, [selectedFormation]);

  // Handle input change (local state only)
  function handleParamChange(key: string, value: string) {
    setParams(prev => ({
      ...prev,
      [key]: Number(value),
    }));
  }

  // Confirm: update FORMATION_TYPES defaultParams and call onConfirm
  function handleConfirm() {
    if (formationObj) {
      Object.keys(params).forEach(key => {
        formationObj.defaultParams[key] = params[key];
      });
    }
    onConfirm();
    publisher.publishProgramCommand({
        "program": formationObj?.value,
        ...formationObj?.defaultParams
    })
  }

  // Reset to original defaults
  function handleResetDefaults() {
    if (formationObj) {
      const original = ORIGINAL_DEFAULTS[formationObj.value];
      setParams({ ...original });
    }
  }

  return (
    <div
      style={{
        position: "absolute",
        top: "20%",
        left: "50%",
        transform: "translateX(-50%)",
        background: "#0710138f",
        border: "1px solid #ccc",
        borderRadius: "8px",
        padding: "2em",
        zIndex: 1000,
        boxShadow: "0 2px 8px rgba(0,0,0,0.15)"
      }}
    >
      <h3>Select Formation</h3>
      <select
        value={selectedFormation}
        onChange={e => setSelectedFormation(e.target.value)}
        style={{ marginBottom: "1em", width: "100%" }}
      >
        {FORMATION_TYPES.map(f => (
          <option key={f.value} value={f.value}>{f.label}</option>
        ))}
      </select>
      {/* Render input fields for parameters if any */}
      {formationObj && Object.keys(formationObj.defaultParams).length > 0 && (
        <div style={{ marginBottom: "1em" }}>
          <h4>Parameters</h4>
          {Object.entries(formationObj.defaultParams).map(([key, _]) => (
            <div key={key} style={{ marginBottom: "0.5em", display: "flex", alignItems: "center" }}>
              <label style={{ marginRight: "0.5em" }}>{key}:</label>
              <input
                className="input-number"
                type="number"
                value={params[key]}
                onChange={e => handleParamChange(key, e.target.value)}
                step={0.1}
                style={{ width: "100px", marginLeft: "auto" }} // align input to the right
              />
            </div>
          ))}
          <button type="button" onClick={handleResetDefaults} style={{ marginTop: "0.5em", width: "100%" }}>
            Reset to Default Parameters
          </button>
        </div>
      )}
      <div style={{ display: "flex", justifyContent: "space-between", gap: "8px" }}>
        <button onClick={onCancel}>Cancel</button>
        <button onClick={handleConfirm}>Confirm</button>
      </div>
    </div>
  );
};

export default FormationPopup;
