import { useState } from "react";
import { ArucoMapSchema, DEVICE_ID_PATTERN, arucoMapTopic } from "../../shared/protocol";
import { useGatewayClient } from "../services/gateway-context";
import { useDashboardStore } from "../store/dashboard-store";

type SaveState = { kind: "idle" | "saving" | "success" | "error"; message?: string };

function StatusMessage({ state }: { state: SaveState }): React.JSX.Element | null {
  if (state.kind === "idle" || state.kind === "saving") return null;
  return <p className={`form-message ${state.kind}`}>{state.message}</p>;
}

function sortedEntries(map: Record<string, string>): [string, string][] {
  return Object.entries(map).sort(([a], [b]) => Number(a) - Number(b));
}

export function ArucoMapPanel(): React.JSX.Element {
  const gateway = useGatewayClient();
  const arucoMap = useDashboardStore((state) => state.arucoMap);
  const robotIds = useDashboardStore((state) => state.robotIds);
  const [markerIdInput, setMarkerIdInput] = useState("");
  const [robotIdInput, setRobotIdInput] = useState("");
  const [saveState, setSaveState] = useState<SaveState>({ kind: "idle" });

  const trimmedMarkerId = markerIdInput.trim();
  const markerIdValue = Number(trimmedMarkerId);
  const isMarkerIdValid = trimmedMarkerId !== "" && Number.isInteger(markerIdValue) && markerIdValue >= 0 && markerIdValue <= 49;
  const markerKey = isMarkerIdValid ? String(markerIdValue) : null;
  const normalizedRobotId = robotIdInput.trim().toUpperCase();
  const isRobotIdValid = DEVICE_ID_PATTERN.test(normalizedRobotId);
  const conflictingMarkerId = isRobotIdValid
    ? sortedEntries(arucoMap).find(([markerId, robotId]) => robotId === normalizedRobotId && markerId !== markerKey)?.[0]
    : undefined;
  const existingRobotForMarker = markerKey ? arucoMap[markerKey] : undefined;
  const willReassign = Boolean(!conflictingMarkerId && isRobotIdValid && existingRobotForMarker && existingRobotForMarker !== normalizedRobotId);
  const canAdd = isMarkerIdValid && isRobotIdValid && !conflictingMarkerId;

  async function publishMap(nextMap: Record<string, string>): Promise<void> {
    const parsed = ArucoMapSchema.safeParse(nextMap);
    if (!parsed.success) {
      setSaveState({ kind: "error", message: parsed.error.issues[0]?.message ?? "Invalid mapping." });
      return;
    }
    setSaveState({ kind: "saving" });
    try {
      await gateway.publish(arucoMapTopic(), parsed.data);
      setSaveState({ kind: "success", message: "Mapping saved." });
    } catch (error) {
      setSaveState({ kind: "error", message: error instanceof Error ? error.message : "Save failed." });
    }
  }

  async function addMapping(): Promise<void> {
    if (!canAdd || !markerKey) return;
    await publishMap({ ...arucoMap, [markerKey]: normalizedRobotId });
    setMarkerIdInput("");
    setRobotIdInput("");
  }

  async function removeMapping(markerId: string): Promise<void> {
    const nextMap = { ...arucoMap };
    delete nextMap[markerId];
    await publishMap(nextMap);
  }

  return (
    <section className="panel aruco-map-panel">
      <div className="panel-heading">
        <div><span className="eyebrow">3 · Computer vision</span><h2>Marker mapping</h2></div>
        <span className="retained-tag">RETAINED</span>
      </div>
      <p className="muted">Map each ArUco marker ID (0-49) to the robot it is taped on, so the vision system can publish positions keyed by robot ID instead of the raw marker ID.</p>

      {sortedEntries(arucoMap).length === 0 ? (
        <p className="empty-message">No mappings yet. Add one below.</p>
      ) : (
        <div className="aruco-map-list">
          {sortedEntries(arucoMap).map(([markerId, robotId]) => (
            <div className="aruco-map-row" key={markerId}>
              <span className="aruco-map-row-marker">Marker {markerId}</span>
              <span className="aruco-map-row-robot">{robotId}</span>
              <button
                type="button"
                className="secondary-button"
                aria-label={`Remove mapping for marker ${markerId}`}
                disabled={saveState.kind === "saving"}
                onClick={() => removeMapping(markerId)}
              >
                Remove
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="aruco-map-form-grid">
        <label className="field-label">
          Marker ID
          <input
            aria-label="ArUco marker ID"
            type="number"
            min={0}
            max={49}
            step={1}
            value={markerIdInput}
            onChange={(event) => setMarkerIdInput(event.target.value)}
          />
        </label>
        <label className="field-label">
          Robot ID
          <input
            aria-label="Robot ID"
            type="text"
            list="known-robot-ids"
            placeholder="A1B2C3"
            value={robotIdInput}
            onChange={(event) => setRobotIdInput(event.target.value.toUpperCase())}
          />
        </label>
        <datalist id="known-robot-ids">
          {robotIds.map((id) => <option key={id} value={id} />)}
        </datalist>
        <div className="save-row">
          <button type="button" className="primary-button" disabled={!canAdd || saveState.kind === "saving"} onClick={addMapping}>
            {saveState.kind === "saving" ? "Saving…" : "Add mapping"}
          </button>
          <StatusMessage state={saveState} />
        </div>
      </div>
      {conflictingMarkerId && (
        <p className="aruco-map-collision-hint">Robot {normalizedRobotId} is already mapped to marker {conflictingMarkerId}. Remove that mapping first.</p>
      )}
      {willReassign && (
        <p className="aruco-map-collision-hint">Marker {markerKey} is currently mapped to {existingRobotForMarker} — saving will reassign it to {normalizedRobotId}.</p>
      )}
    </section>
  );
}
