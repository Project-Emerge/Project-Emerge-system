import { useState } from "react";
import { SceneCanvas, type SceneMode } from "../components/SceneCanvas";
import { RobotDetailsSidebar } from "../components/RobotDetailsSidebar";
import { FormationPanel, getFormationLabel } from "../components/FormationPanel";
import { useDashboardStore } from "../store/dashboard-store";

export function DashboardPage(): React.JSX.Element {
  const [mode, setMode] = useState<SceneMode>("3d");
  const [resetToken, setResetToken] = useState(0);
  const [isFormationOpen, setIsFormationOpen] = useState(false);
  const robotIds = useDashboardStore((state) => state.robotIds);
  const posedRobotIds = useDashboardStore((state) => state.posedRobotIds);
  const activeFormation = useDashboardStore((state) => state.formation);
  const robotsWithoutPose = robotIds.length - posedRobotIds.length;

  return (
    <main className="dashboard-page">
      <RobotDetailsSidebar />
      <div className="dashboard-workspace">
        <section className="scene-toolbar panel">
          <div className="scene-summary">
            <span className="eyebrow">Live arena</span>
            <strong>{robotIds.length} robots detected</strong>
            <span>{posedRobotIds.length} positions available</span>
          </div>
          <div className="toolbar-actions">
            <button
              type="button"
              className="secondary-button formation-trigger"
              onClick={() => setIsFormationOpen(true)}
            >
              <span className={`formation-status-dot ${activeFormation ? "active" : "inactive"}`} />
              Formation: <strong>{activeFormation ? getFormationLabel(activeFormation.program) : "None"}</strong>
            </button>
            <div className="segmented-control" aria-label="View mode">
              <button type="button" className={mode === "2d" ? "active" : ""} onClick={() => setMode("2d")}>2D</button>
              <button type="button" className={mode === "3d" ? "active" : ""} onClick={() => setMode("3d")}>3D</button>
            </div>
            <button type="button" className="secondary-button" onClick={() => setResetToken((token) => token + 1)}>Center arena</button>
          </div>
        </section>
        {isFormationOpen && <FormationPanel onClose={() => setIsFormationOpen(false)} />}
        {robotsWithoutPose > 0 && <div className="scene-notice">{robotsWithoutPose} robot{robotsWithoutPose === 1 ? "" : "s"} without a position. Details appear after the first position update.</div>}
        <section className="scene-panel">
          <SceneCanvas mode={mode} resetToken={resetToken} />
          <div className="scene-hint">Drag to pan · scroll to zoom{mode === "3d" ? " · right-click to orbit" : ""} · trails: 4 s</div>
        </section>
      </div>
    </main>
  );
}
