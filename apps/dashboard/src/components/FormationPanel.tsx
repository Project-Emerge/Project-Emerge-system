import { useEffect, useState } from "react";
import {
  FormationCommandSchema,
  formationTopic,
  type CustomFormationSpec,
  type FormationAnchor,
  type FormationCommand,
  type FormationProgram,
} from "../../shared/protocol";
import {
  ANCHOR_DESCRIPTIONS,
  ANCHOR_LABELS,
  FORMATION_DEFINITIONS,
  GROUP_LABELS,
  GROUP_ORDER,
  defaultParams,
  definitionFor,
  resolveAnchor,
} from "../../shared/formations";
import { useGatewayClient } from "../services/gateway-context";
import { useLocale } from "../services/locale-context";
import { useDashboardStore } from "../store/dashboard-store";

type SaveState = { kind: "idle" | "saving" | "success" | "error"; message?: string };

function StatusMessage({ state }: { state: SaveState }): React.JSX.Element | null {
  if (state.kind === "idle" || state.kind === "saving") return null;
  return <p className={`form-message ${state.kind}`}>{state.message}</p>;
}

export function FormationPanel({ onClose }: { onClose: () => void }): React.JSX.Element {
  const gateway = useGatewayClient();
  const { t } = useLocale();
  const connectionStatus = useDashboardStore((state) => state.connectionStatus);
  const robotIds = useDashboardStore((state) => state.robotIds);
  const activeFormation = useDashboardStore((state) => state.formation);

  const [expanded, setExpanded] = useState(false);
  const [program, setProgram] = useState<FormationProgram>("pointToLeader");
  const [anchor, setAnchor] = useState<FormationAnchor>("leader");
  const [leaderId, setLeaderId] = useState<string | null>(null);
  const [params, setParams] = useState<Record<string, number>>({});
  const [customSpec, setCustomSpec] = useState<CustomFormationSpec | null>(null);
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
    setAnchor(activeFormation.anchor);
    setLeaderId(activeFormation.leaderId);
    setParams(activeFormation.params);
    setCustomSpec(activeFormation.custom ?? null);
    setExpanded(definitionFor(activeFormation.program).params.length > 0);
  }, [activeFormation]);

  const definition = definitionFor(program);
  const effectiveAnchor = resolveAnchor(definition, anchor);
  const anchorApplies = definition.anchors.length > 0;
  const needsLeader = anchorApplies && effectiveAnchor === "leader";
  const hasGeometry = program !== "custom" || customSpec !== null;
  const canApply = connectionStatus === "connected"
    && (!needsLeader || Boolean(leaderId))
    && hasGeometry
    && saveState.kind !== "saving";

  function selectProgram(next: FormationProgram): void {
    const nextDefinition = definitionFor(next);
    setProgram(next);
    setAnchor(resolveAnchor(nextDefinition, anchor));
    setParams(defaultParams(nextDefinition));
    if (next !== "custom") setCustomSpec(null);
    setExpanded(nextDefinition.params.length > 0);
  }

  function resetParams(): void {
    setParams(defaultParams(definition));
  }

  function updateParam(key: string, value: number): void {
    setParams((current) => ({ ...current, [key]: value }));
  }

  async function applyFormation(): Promise<void> {
    const command: FormationCommand = {
      program,
      // Anything but an operator-chosen leader must clear the id, otherwise the runtime
      // keeps rooting the formation on the previous robot.
      leaderId: needsLeader ? leaderId : null,
      anchor: effectiveAnchor,
      params,
      custom: program === "custom" ? customSpec : null,
    };
    const parsed = FormationCommandSchema.safeParse(command);
    if (!parsed.success) {
      setSaveState({ kind: "error", message: parsed.error.issues[0]?.message ?? t.formationModal.invalidCommand });
      return;
    }
    setSaveState({ kind: "saving" });
    try {
      await gateway.publish(formationTopic(), parsed.data);
      setSaveState({ kind: "success", message: t.formationModal.appliedSuccess });
    } catch (error) {
      setSaveState({ kind: "error", message: error instanceof Error ? error.message : t.formationModal.applyFailed });
    }
  }

  const activeLabel = activeFormation
    ? (t.formationModal.programs[activeFormation.program]?.label ?? definitionFor(activeFormation.program).label)
    : null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-container formation-panel" onClick={(event) => event.stopPropagation()}>
        <button type="button" className="modal-close" onClick={onClose} aria-label={t.formationModal.closeDialog}>✕</button>
        <div className="panel-heading">
          <div><span className="eyebrow">{t.formationModal.fleet}</span><h2>{t.formationModal.title}</h2></div>
          <span className="retained-tag">{activeLabel ? t.formationModal.activeStatus(activeLabel) : t.formationModal.noFormationStatus}</span>
        </div>

      {GROUP_ORDER.map((group) => {
        const groupLabel = t.formationModal.groups[group] ?? GROUP_LABELS[group];
        return (
          <div className="formation-group" key={group}>
            <span className="formation-group-label">{groupLabel}</span>
            <div className="formation-picker" role="group" aria-label={groupLabel}>
              {FORMATION_DEFINITIONS.filter((option) => option.group === group).map((option) => {
                const programLabel = t.formationModal.programs[option.value]?.label ?? option.label;
                return (
                  <button
                    key={option.value}
                    type="button"
                    className={option.value === program ? "active" : ""}
                    aria-pressed={option.value === program}
                    onClick={() => selectProgram(option.value)}
                  >
                    {programLabel}
                  </button>
                );
              })}
            </div>
          </div>
        );
      })}
      <p className="muted">{t.formationModal.programs[program]?.description ?? definition.description}</p>

      {anchorApplies && (
        <div className="formation-group">
          <span className="formation-group-label">{t.formationModal.builtAround}</span>
          <div className="segmented-control" aria-label="Formation anchor">
            {definition.anchors.map((option) => (
              <button
                key={option}
                type="button"
                className={option === effectiveAnchor ? "active" : ""}
                aria-pressed={option === effectiveAnchor}
                onClick={() => setAnchor(option)}
              >
                {t.formationModal.anchors[option]?.label ?? ANCHOR_LABELS[option]}
              </button>
            ))}
          </div>
          <p className="muted">{t.formationModal.anchors[effectiveAnchor]?.description ?? ANCHOR_DESCRIPTIONS[effectiveAnchor]}</p>
        </div>
      )}

      <div className="formation-form-grid">
        <label className="field-label">
          {t.formationModal.leaderLabel}
          <select
            aria-label="Formation leader"
            value={needsLeader ? leaderId ?? "" : ""}
            disabled={!needsLeader}
            onChange={(event) => setLeaderId(event.target.value || null)}
          >
            <option value="">
              {!anchorApplies
                ? t.formationModal.leaderUnused
                : effectiveAnchor === "auto"
                  ? t.formationModal.leaderElected
                  : robotIds.length === 0
                    ? t.formationModal.leaderNoneDetected
                    : t.formationModal.leaderSelectPrompt}
            </option>
            {needsLeader && robotIds.map((id) => <option key={id} value={id}>{id}</option>)}
          </select>
        </label>
        <div className="save-row">
          <button type="button" className="primary-button" disabled={!canApply} onClick={applyFormation}>
            {saveState.kind === "saving" ? t.formationModal.applyingButton : t.formationModal.applyButton}
          </button>
          <StatusMessage state={saveState} />
        </div>
      </div>
      {needsLeader && !leaderId && <p className="formation-hint">{t.formationModal.pickLeaderHint}</p>}
      {program === "custom" && (
        <p className="formation-hint">
          {customSpec
            ? t.formationModal.customDesignedInChat(customSpec.label)
            : t.formationModal.customAskPrompt}
        </p>
      )}

      {definition.params.length > 0 && (
        <div className="formation-params">
          <button type="button" className="formation-params-toggle" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
            {expanded ? t.formationModal.hideParams : t.formationModal.tuneParams}
          </button>
          {expanded && (
            <>
              <div className="formation-params-grid">
                {definition.params.map((param) => {
                  const paramLabel = t.formationModal.params[param.key] ?? param.label;
                  const value = params[param.key] ?? param.defaultValue;
                  return (
                    <div className="formation-param-row" key={param.key}>
                      <span className="formation-param-label">{paramLabel}{param.unit ? ` (${param.unit})` : ""}</span>
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
              <button type="button" className="secondary-button formation-reset" onClick={resetParams}>{t.formationModal.resetDefaults}</button>
            </>
          )}
        </div>
      )}
    </div>
  </div>
  );
}
