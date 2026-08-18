import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { motorCommandTopic, type MotorCommand } from "../../shared/protocol";
import { useGatewayClient } from "../services/gateway-context";
import { isRobotStale, useDashboardStore } from "../store/dashboard-store";

type JoystickPosition = { x: number; y: number };
export type ControlMode = "joystick" | "dual";
type ControlSurface = "joystick" | "throttle" | "turn";

const CENTER: JoystickPosition = { x: 0, y: 0 };
const DEAD_ZONE = 0.12;
const COMMAND_INTERVAL_MS = 100;

function rounded(value: number): number {
  return Math.abs(value) < 0.0005 ? 0 : Number(value.toFixed(3));
}

export function differentialDriveCommand(position: JoystickPosition): MotorCommand {
  const magnitude = Math.min(1, Math.hypot(position.x, position.y));
  if (magnitude <= DEAD_ZONE) return "Stop";

  // Remove the dead zone, then soften low-speed input for precise close-range control.
  const shapedMagnitude = ((magnitude - DEAD_ZONE) / (1 - DEAD_ZONE)) ** 1.35;
  const linear = position.y / magnitude * shapedMagnitude;
  // Turning stays strongest in place and becomes calmer as forward speed rises.
  const turnGain = 0.55 + 0.45 * (1 - Math.abs(linear));
  const angular = position.x / magnitude * shapedMagnitude * turnGain;
  const rawLeft = linear + angular;
  const rawRight = linear - angular;
  const normalization = Math.max(1, Math.abs(rawLeft), Math.abs(rawRight));

  return {
    Move: {
      left: rounded(rawLeft / normalization),
      right: rounded(rawRight / normalization),
    },
  };
}

function wheelValues(command: MotorCommand): { left: number; right: number } {
  return command === "Stop" ? { left: 0, right: 0 } : command.Move;
}

export function ManualDriveControl({ now }: { now: number }): React.JSX.Element {
  const gateway = useGatewayClient();
  const connectionStatus = useDashboardStore((state) => state.connectionStatus);
  const selectedRobotId = useDashboardStore((state) => state.selectedRobotId);
  const selectedRobot = useDashboardStore((state) => selectedRobotId ? state.robots[selectedRobotId] : undefined);
  const [position, setPosition] = useState<JoystickPosition>(CENTER);
  const [controlMode, setControlMode] = useState<ControlMode>("joystick");
  const [engaged, setEngaged] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const positionRef = useRef<JoystickPosition>(CENTER);
  const engagedRef = useRef(false);
  const targetRef = useRef<string | null>(null);
  const activePointersRef = useRef<Record<ControlSurface, number | null>>({ joystick: null, throttle: null, turn: null });
  const canControl = connectionStatus === "connected" && Boolean(selectedRobot) && !isRobotStale(selectedRobot!, now);
  const command = differentialDriveCommand(position);
  const wheels = wheelValues(command);

  const publish = useCallback((robotId: string, nextCommand: MotorCommand) => {
    void gateway.publish(motorCommandTopic(robotId), nextCommand).catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : "Motor command failed");
    });
  }, [gateway]);

  const resetInput = useCallback(() => {
    positionRef.current = CENTER;
    engagedRef.current = false;
    targetRef.current = null;
    activePointersRef.current = { joystick: null, throttle: null, turn: null };
    setPosition(CENTER);
    setEngaged(false);
  }, []);

  const stopControl = useCallback((force = false) => {
    const robotId = targetRef.current ?? (force ? selectedRobotId : null);
    const shouldPublish = force || engagedRef.current;
    resetInput();
    if (robotId && shouldPublish) publish(robotId, "Stop");
  }, [publish, resetInput, selectedRobotId]);
  const stopControlRef = useRef(stopControl);

  useEffect(() => {
    stopControlRef.current = stopControl;
  }, [stopControl]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!engagedRef.current || !targetRef.current) return;
      publish(targetRef.current, differentialDriveCommand(positionRef.current));
    }, COMMAND_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [publish]);

  useEffect(() => {
    if (targetRef.current && targetRef.current !== selectedRobotId) stopControl();
  }, [selectedRobotId, stopControl]);

  useEffect(() => {
    if (!canControl && engagedRef.current) stopControl();
  }, [canControl, stopControl]);

  useEffect(() => {
    const stopOnBlur = () => stopControlRef.current();
    const stopWhenHidden = () => {
      if (document.visibilityState === "hidden") stopControlRef.current();
    };
    window.addEventListener("blur", stopOnBlur);
    document.addEventListener("visibilitychange", stopWhenHidden);
    return () => {
      window.removeEventListener("blur", stopOnBlur);
      document.removeEventListener("visibilitychange", stopWhenHidden);
      if (engagedRef.current && targetRef.current) publish(targetRef.current, "Stop");
    };
  }, [publish]);

  const updatePosition = (surface: ControlSurface, event: ReactPointerEvent<HTMLDivElement>): JoystickPosition => {
    const bounds = event.currentTarget.getBoundingClientRect();
    const travel = Math.max(1, Math.min(bounds.width, bounds.height) / 2 - 24);
    const rawX = (event.clientX - (bounds.left + bounds.width / 2)) / travel;
    const rawY = ((bounds.top + bounds.height / 2) - event.clientY) / travel;
    const current = positionRef.current;
    let next: JoystickPosition;
    if (surface === "throttle") {
      next = { x: current.x, y: Math.max(-1, Math.min(1, rawY)) };
    } else if (surface === "turn") {
      next = { x: Math.max(-1, Math.min(1, rawX)), y: current.y };
    } else {
      const scale = Math.max(1, Math.hypot(rawX, rawY));
      next = { x: rawX / scale, y: rawY / scale };
    }
    positionRef.current = next;
    setPosition(next);
    return next;
  };

  const onPointerDown = (surface: ControlSurface, event: ReactPointerEvent<HTMLDivElement>) => {
    if (!canControl || !selectedRobotId || event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    activePointersRef.current[surface] = event.pointerId;
    targetRef.current = selectedRobotId;
    engagedRef.current = true;
    setEngaged(true);
    setError(null);
    publish(selectedRobotId, differentialDriveCommand(updatePosition(surface, event)));
  };

  const onPointerMove = (surface: ControlSurface, event: ReactPointerEvent<HTMLDivElement>) => {
    if (activePointersRef.current[surface] !== event.pointerId) return;
    event.preventDefault();
    updatePosition(surface, event);
  };

  const onPointerEnd = (surface: ControlSurface, event: ReactPointerEvent<HTMLDivElement>) => {
    if (activePointersRef.current[surface] !== event.pointerId) return;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    activePointersRef.current[surface] = null;
    if (surface === "joystick") {
      stopControl();
      return;
    }

    const next = surface === "throttle"
      ? { ...positionRef.current, y: 0 }
      : { ...positionRef.current, x: 0 };
    positionRef.current = next;
    setPosition(next);
    const anotherControlIsActive = activePointersRef.current.throttle !== null || activePointersRef.current.turn !== null;
    if (anotherControlIsActive && targetRef.current) {
      publish(targetRef.current, differentialDriveCommand(next));
    } else {
      stopControl();
    }
  };

  const onModeChange = (nextMode: ControlMode) => {
    if (nextMode === controlMode) return;
    stopControl();
    setControlMode(nextMode);
  };

  const modeHint = controlMode === "joystick"
    ? "Drag up to move; drag sideways to steer."
    : "Use forward/reverse and turn together or independently.";

  const availability = !selectedRobotId
    ? "Select a robot on the map or in the fleet."
    : connectionStatus !== "connected"
      ? "Manual control is unavailable while MQTT is offline."
      : !canControl
        ? "The selected robot is not reachable."
        : modeHint;

  return (
    <section className="manual-drive" aria-label="Manual robot control">
      <div className="manual-drive-heading">
        <div><span className="eyebrow">Manual control</span><strong>{selectedRobotId ?? "No robot selected"}</strong></div>
        <span className="drive-rate">10 Hz</span>
      </div>
      <div className="drive-mode-picker" role="group" aria-label="Control mode">
        <button type="button" aria-pressed={controlMode === "joystick"} onClick={() => onModeChange("joystick")}>Joystick</button>
        <button type="button" aria-pressed={controlMode === "dual"} onClick={() => onModeChange("dual")}>Dual control</button>
      </div>
      {controlMode === "joystick" ? (
        <div
          className={`joystick-base${engaged ? " engaged" : ""}${canControl ? "" : " disabled"}`}
          aria-label="Drive joystick"
          aria-disabled={!canControl}
          onPointerDown={(event) => onPointerDown("joystick", event)}
          onPointerMove={(event) => onPointerMove("joystick", event)}
          onPointerUp={(event) => onPointerEnd("joystick", event)}
          onPointerCancel={(event) => onPointerEnd("joystick", event)}
        >
          <span className="joystick-axis horizontal" />
          <span className="joystick-axis vertical" />
          <span className="joystick-knob" style={{ left: `${50 + position.x * 33}%`, top: `${50 - position.y * 33}%` }} />
        </div>
      ) : (
        <div className="dual-controller-view">
          <div className="dual-controller">
            <span>Forward / reverse</span>
            <div
              className={`axis-controller throttle${activePointersRef.current.throttle !== null ? " engaged" : ""}${canControl ? "" : " disabled"}`}
              aria-label="Forward reverse controller"
              aria-disabled={!canControl}
              onPointerDown={(event) => onPointerDown("throttle", event)}
              onPointerMove={(event) => onPointerMove("throttle", event)}
              onPointerUp={(event) => onPointerEnd("throttle", event)}
              onPointerCancel={(event) => onPointerEnd("throttle", event)}
            >
              <span className="joystick-axis vertical" />
              <span className="joystick-knob" style={{ left: "50%", top: `${50 - position.y * 33}%` }} />
            </div>
          </div>
          <div className="dual-controller">
            <span>Turn</span>
            <div
              className={`axis-controller turn${activePointersRef.current.turn !== null ? " engaged" : ""}${canControl ? "" : " disabled"}`}
              aria-label="Turn controller"
              aria-disabled={!canControl}
              onPointerDown={(event) => onPointerDown("turn", event)}
              onPointerMove={(event) => onPointerMove("turn", event)}
              onPointerUp={(event) => onPointerEnd("turn", event)}
              onPointerCancel={(event) => onPointerEnd("turn", event)}
            >
              <span className="joystick-axis horizontal" />
              <span className="joystick-knob" style={{ left: `${50 + position.x * 33}%`, top: "50%" }} />
            </div>
          </div>
        </div>
      )}
      <div className="wheel-output" aria-label="Wheel command preview">
        <span>Left <strong>{wheels.left.toFixed(2)}</strong></span>
        <span>Right <strong>{wheels.right.toFixed(2)}</strong></span>
      </div>
      <div className="manual-drive-footer">
        <p className={error ? "drive-message error" : "drive-message"}>{error ?? availability}</p>
        <button type="button" className="drive-stop" disabled={!selectedRobotId || connectionStatus !== "connected"} onClick={() => stopControl(true)}>Stop</button>
      </div>
    </section>
  );
}
