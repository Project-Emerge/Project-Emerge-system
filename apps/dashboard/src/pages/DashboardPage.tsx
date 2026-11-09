import { useState } from "react";
import { SceneCanvas, type SceneMode } from "../components/SceneCanvas";
import { RobotDetailsSidebar } from "../components/RobotDetailsSidebar";
import { FormationPanel } from "../components/FormationPanel";
import { ChatPanel } from "../components/ChatPanel";
import { getFormationLabel } from "../../shared/formations";
import { useLocale } from "../services/locale-context";
import { useDashboardStore } from "../store/dashboard-store";

export function DashboardPage(): React.JSX.Element {
  const { t } = useLocale();
  const [mode, setMode] = useState<SceneMode>("3d");
  const [resetToken, setResetToken] = useState(0);
  const [isFormationOpen, setIsFormationOpen] = useState(false);
  const [isChatOpen, setIsChatOpen] = useState(false);
  const robotIds = useDashboardStore((state) => state.robotIds);
  const posedRobotIds = useDashboardStore((state) => state.posedRobotIds);
  const activeFormation = useDashboardStore((state) => state.formation);
  const hasNeighborhood = useDashboardStore((state) => Object.keys(state.neighbors).length > 0);
  const robotsWithoutPose = robotIds.length - posedRobotIds.length;

  const activeFormationLabel = activeFormation
    ? (t.formationModal.programs[activeFormation.program]?.label ?? getFormationLabel(activeFormation.program))
    : t.dashboard.formationNone;

  return (
    <main className="dashboard-page">
      <RobotDetailsSidebar />
      <div className="dashboard-workspace">
        <section className="scene-toolbar panel">
          <div className="scene-summary">
            <span className="eyebrow">{t.dashboard.liveArena}</span>
            <strong>{t.dashboard.robotsDetected(robotIds.length)}</strong>
            <span>{t.dashboard.positionsAvailable(posedRobotIds.length)}</span>
          </div>
          <div className="toolbar-actions">
            <button
              type="button"
              className="secondary-button formation-trigger"
              onClick={() => setIsFormationOpen(true)}
            >
              <span className={`formation-status-dot ${activeFormation ? "active" : "inactive"}`} />
              {t.dashboard.formationLabel}: <strong>{activeFormationLabel}</strong>
              {activeFormation?.leaderId
                ? <span className="formation-leader-chip">★ {activeFormation.leaderId}</span>
                : activeFormation?.anchor === "auto"
                  ? <span className="formation-leader-chip">★ AUTO</span>
                  : null}
            </button>
            <button
              type="button"
              className="secondary-button chat-trigger"
              aria-pressed={isChatOpen}
              onClick={() => setIsChatOpen((open) => !open)}
            >
              {t.dashboard.askTheSwarm}
            </button>
            <div className="segmented-control" aria-label={t.dashboard.viewModeAria}>
              <button type="button" className={mode === "2d" ? "active" : ""} onClick={() => setMode("2d")}>2D</button>
              <button type="button" className={mode === "3d" ? "active" : ""} onClick={() => setMode("3d")}>3D</button>
            </div>
            <button type="button" className="secondary-button" onClick={() => setResetToken((token) => token + 1)}>{t.dashboard.centerArena}</button>
          </div>
        </section>
        {isFormationOpen && <FormationPanel onClose={() => setIsFormationOpen(false)} />}
        {isChatOpen && <ChatPanel onClose={() => setIsChatOpen(false)} />}
        {robotsWithoutPose > 0 && <div className="scene-notice">{t.dashboard.robotsWithoutPose(robotsWithoutPose)}</div>}
        <section className="scene-panel">
          <SceneCanvas mode={mode} resetToken={resetToken} />
          <div className="scene-hint">{t.dashboard.sceneHint(mode === "3d", hasNeighborhood)}</div>
        </section>
      </div>
    </main>
  );
}
