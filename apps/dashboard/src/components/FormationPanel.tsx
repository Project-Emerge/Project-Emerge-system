import { useEffect, useState } from "react";
import { FormationCommandSchema, formationTopic, type FormationCommand, type FormationProgram } from "../../shared/protocol";
import { useGatewayClient } from "../services/gateway-context";
import { useDashboardStore } from "../store/dashboard-store";

type SaveState = { kind: "idle" | "saving" | "success" | "error"; message?: string };

type FormationParamDefinition = {
  key: string;
  label: string;
  unit?: string;
  min: number;
  max: number;
  step: number;
  defaultValue: number;
};

type FormationDefinition = {
  value: FormationProgram;
  label: string;
  description: string;
  params: FormationParamDefinition[];
};

const COLLISION_AREA: FormationParamDefinition = {
  key: "collisionArea",
  label: "Collision radius",
  unit: "m",
  min: 0.05,
  max: 1,
  step: 0.05,
  defaultValue: 0.3,
};

const STABILITY_THRESHOLD: FormationParamDefinition = {
  key: "stabilityThreshold",
  label: "Stability threshold",
  unit: "m",
  min: 0.01,
  max: 0.5,
  step: 0.01,
  defaultValue: 0.1,
};

const FORMATION_DEFINITIONS: FormationDefinition[] = [
  {
    value: "pointToLeader",
    label: "Point to leader",
    description: "Every robot turns to face the leader.",
    params: [],
  },
  {
    value: "vShape",
    label: "V formation",
    description: "Two trailing arms fan out behind the leader.",
    params: [
      { key: "interDistanceV", label: "Arm spacing", unit: "m", min: 0.1, max: 1.2, step: 0.05, defaultValue: 0.4 },
      { key: "angleV", label: "Arm angle", unit: "rad", min: -Math.PI, max: Math.PI, step: 0.05, defaultValue: -0.79 },
      COLLISION_AREA,
      STABILITY_THRESHOLD,
    ],
  },
  {
    value: "lineShape",
    label: "Line",
    description: "Robots line up side by side behind the leader.",
    params: [
      { key: "interDistanceLine", label: "Robot spacing", unit: "m", min: 0.1, max: 1.2, step: 0.05, defaultValue: 0.4 },
      COLLISION_AREA,
      STABILITY_THRESHOLD,
    ],
  },
  {
    value: "circleShape",
    label: "Circle",
    description: "Robots ring the leader at a fixed radius.",
    params: [
      { key: "radius", label: "Circle radius", unit: "m", min: 0.2, max: 1.5, step: 0.05, defaultValue: 0.6 },
      COLLISION_AREA,
      STABILITY_THRESHOLD,
    ],
  },
  {
    value: "squareShape",
    label: "Square",
    description: "Robots fill a grid around the leader.",
    params: [
      { key: "interDistanceSquare", label: "Grid spacing", unit: "m", min: 0.1, max: 1.2, step: 0.05, defaultValue: 0.4 },
      COLLISION_AREA,
      STABILITY_THRESHOLD,
    ],
  },
  {
    value: "verticalLineShape",
    label: "Vertical line",
    description: "Robots queue directly behind the leader.",
    params: [
      { key: "interDistanceVertical", label: "Robot spacing", unit: "m", min: 0.1, max: 1.2, step: 0.05, defaultValue: 0.4 },
      COLLISION_AREA,
      STABILITY_THRESHOLD,
    ],
  },
  {
    value: "heartShape",
    label: "Heart",
    description: "Robots trace a heart outline around the leader.",
    params: [
      { key: "scaleHeart", label: "Heart size", unit: "m", min: 0.02, max: 0.2, step: 0.01, defaultValue: 0.06 },
      COLLISION_AREA,
      STABILITY_THRESHOLD,
    ],
  },
  {
    value: "stop",
    label: "Stop",
    description: "Every robot holds position.",
    params: [],
  },
];

function definitionFor(program: FormationProgram): FormationDefinition {
  return FORMATION_DEFINITIONS.find((definition) => definition.value === program) ?? FORMATION_DEFINITIONS[0];
}

function defaultParams(definition: FormationDefinition): Record<string, number> {
  return Object.fromEntries(definition.params.map((param) => [param.key, param.defaultValue]));
}

function StatusMessage({ state }: { state: SaveState }): React.JSX.Element | null {
  if (state.kind === "idle" || state.kind === "saving") return null;
  return <p className={`form-message ${state.kind}`}>{state.message}</p>;
}

export function getFormationLabel(program: string): string {
  return FORMATION_DEFINITIONS.find((definition) => definition.value === program)?.label ?? program;
}

export function FormationPanel({ onClose }: { onClose: () => void }): React.JSX.Element {
  const gateway = useGatewayClient();
  const connectionStatus = useDashboardStore((state) => state.connectionStatus);
  const robotIds = useDashboardStore((state) => state.robotIds);
  const activeFormation = useDashboardStore((state) => state.formation);

  const [expanded, setExpanded] = useState(false);
  const [program, setProgram] = useState<FormationProgram>("pointToLeader");
  const [leaderId, setLeaderId] = useState<string | null>(null);
  const [params, setParams] = useState<Record<string, number>>({});
  const [saveState, setSaveState] = useState<SaveState>({ kind: "idle" });

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        onClose();
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  useEffect(() => {
    if (!activeFormation) return;
    setProgram(activeFormation.program);
    setLeaderId(activeFormation.leaderId);
    setParams(activeFormation.params);
    setExpanded(definitionFor(activeFormation.program).params.length > 0);
  }, [activeFormation]);

  const definition = definitionFor(program);
  const needsLeader = program !== "stop";
  const canApply = connectionStatus === "connected" && (!needsLeader || Boolean(leaderId)) && saveState.kind !== "saving";

  function selectProgram(next: FormationProgram): void {
    setProgram(next);
    setParams(defaultParams(definitionFor(next)));
    setExpanded(definitionFor(next).params.length > 0);
  }

  function resetParams(): void {
    setParams(defaultParams(definition));
  }

  function updateParam(key: string, value: number): void {
    setParams((current) => ({ ...current, [key]: value }));
  }

  async function applyFormation(): Promise<void> {
    const command: FormationCommand = { program, leaderId, params };
    const parsed = FormationCommandSchema.safeParse(command);
    if (!parsed.success) {
      setSaveState({ kind: "error", message: parsed.error.issues[0]?.message ?? "Invalid formation command." });
      return;
    }
    setSaveState({ kind: "saving" });
    try {
      await gateway.publish(formationTopic(), parsed.data);
      setSaveState({ kind: "success", message: "Formation applied." });
    } catch (error) {
      setSaveState({ kind: "error", message: error instanceof Error ? error.message : "Apply failed." });
    }
  }

  const activeLabel = activeFormation ? definitionFor(activeFormation.program).label : null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-container formation-panel" onClick={(event) => event.stopPropagation()}>
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close dialog">✕</button>
        <div className="panel-heading">
          <div><span className="eyebrow">Swarm</span><h2>Formation &amp; parameters</h2></div>
          <span className="retained-tag">{activeLabel ? `ACTIVE · ${activeLabel.toUpperCase()}` : "NO FORMATION YET"}</span>
        </div>

      <div className="formation-picker" role="group" aria-label="Formation program">
        {FORMATION_DEFINITIONS.map((option) => (
          <button
            key={option.value}
            type="button"
            className={option.value === program ? "active" : ""}
            aria-pressed={option.value === program}
            onClick={() => selectProgram(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>
      <p className="muted">{definition.description}</p>

      <div className="formation-form-grid">
        <label className="field-label">
          Leader
          <select
            aria-label="Formation leader"
            value={leaderId ?? ""}
            onChange={(event) => setLeaderId(event.target.value || null)}
          >
            <option value="">{robotIds.length === 0 ? "No robots detected" : "Select a robot"}</option>
            {robotIds.map((id) => <option key={id} value={id}>{id}</option>)}
          </select>
        </label>
        <div className="save-row">
          <button type="button" className="primary-button" disabled={!canApply} onClick={applyFormation}>
            {saveState.kind === "saving" ? "Applying…" : "Apply formation"}
          </button>
          <StatusMessage state={saveState} />
        </div>
      </div>
      {needsLeader && !leaderId && <p className="formation-hint">Pick a leader before applying this formation.</p>}

      {definition.params.length > 0 && (
        <div className="formation-params">
          <button type="button" className="formation-params-toggle" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
            {expanded ? "Hide parameters" : "Tune parameters"}
          </button>
          {expanded && (
            <>
              <div className="formation-params-grid">
                {definition.params.map((param) => {
                  const value = params[param.key] ?? param.defaultValue;
                  return (
                    <div className="formation-param-row" key={param.key}>
                      <span className="formation-param-label">{param.label}{param.unit ? ` (${param.unit})` : ""}</span>
                      <input
                        aria-label={`${param.label} slider`}
                        type="range"
                        min={param.min}
                        max={param.max}
                        step={param.step}
                        value={value}
                        onChange={(event) => updateParam(param.key, Number(event.target.value))}
                      />
                      <input
                        aria-label={param.label}
                        className="formation-param-number"
                        type="number"
                        min={param.min}
                        max={param.max}
                        step={param.step}
                        value={value}
                        onChange={(event) => updateParam(param.key, Number(event.target.value))}
                      />
                    </div>
                  );
                })}
              </div>
              <button type="button" className="secondary-button formation-reset" onClick={resetParams}>Reset to defaults</button>
            </>
          )}
        </div>
      )}
    </div>
  </div>
  );
}
