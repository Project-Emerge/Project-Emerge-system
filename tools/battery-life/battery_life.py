#!/usr/bin/env python3
"""Standalone battery-life test for a single Project Emerge robot.

Drives the wheels through a repeating, realistic motion profile and measures how
long the robot keeps going before its battery is depleted. Not part of the
production stack: it talks to the broker directly and nothing imports it.

    python battery_life.py <ROBOT_ID> <BROKER_IP>

The test ends when the robot reports an empty battery (state of charge or
voltage below the cutoff), when telemetry stops arriving (robot powered off),
or on Ctrl-C. In every case the motors are stopped and the elapsed time is
reported.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import signal
import sys
import threading
import time
from dataclasses import dataclass

import paho.mqtt.client as mqtt

DEVICE_ID_PATTERN = re.compile(r"^[A-F0-9]{6}$")

# One lap of a plausible field duty cycle: cruise, curves, spin, reverse, idle.
# (left, right, seconds); speeds are normalized in [-1, 1] and later scaled.
MOTION_PROFILE: list[tuple[float, float, float]] = [
    (1.0, 1.0, 6.0),    # straight cruise
    (0.8, 0.45, 3.0),   # wide right curve
    (0.45, 0.8, 3.0),   # wide left curve
    (1.0, 1.0, 4.0),    # straight cruise
    (0.7, -0.7, 2.0),   # spin in place
    (0.0, 0.0, 1.5),    # idle / thinking
    (-0.7, -0.7, 2.5),  # reverse
    (0.55, 0.55, 5.0),  # slow cruise
]

COMMAND_PERIOD_S = 0.2  # republish rate, keeps any firmware watchdog fed


@dataclass
class Sample:
    elapsed_s: float
    voltage: float
    current: float
    temperature: float
    state_of_charge: int
    is_charging: bool


class BatteryLifeTest:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.motors_topic = f"/motors/{args.robot_id}"
        self.telemetry_topic = f"/telemetry/{args.robot_id}"

        self.stop_event = threading.Event()
        self.first_telemetry = threading.Event()
        self.lock = threading.Lock()

        self.samples: list[Sample] = []
        self.started_at: float | None = None
        self.ended_at: float | None = None
        self.last_telemetry_at: float | None = None
        self.low_voltage_streak = 0
        self.reason = "interrupted"

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if args.username:
            self.client.username_pw_set(args.username, args.password)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    # --- MQTT ---------------------------------------------------------------

    def _on_connect(self, client, _userdata, _flags, reason_code, _properties=None):
        if reason_code != 0:
            print(f"Connection refused by broker: {reason_code}", file=sys.stderr)
            self.finish("broker refused the connection")
            return
        print(f"Connected to {self.args.broker_ip}:{self.args.port}")
        client.subscribe(self.telemetry_topic, qos=0)

    def _on_message(self, _client, _userdata, message):
        if self.stop_event.is_set():
            return  # the test is over; ignore telemetry from the braking phase
        try:
            battery = json.loads(message.payload)["battery_telemetry"]
        except (ValueError, KeyError, TypeError):
            return

        now = time.monotonic()
        with self.lock:
            if self.started_at is None:
                self.started_at = now
            self.last_telemetry_at = now
            sample = Sample(
                elapsed_s=now - self.started_at,
                voltage=float(battery["voltage"]),
                current=float(battery["current"]),
                temperature=float(battery["temperature"]),
                state_of_charge=int(battery["state_of_charge"]),
                is_charging=bool(battery["is_charging"]),
            )
            self.samples.append(sample)
        self.first_telemetry.set()
        self._report(sample)
        self._check_depletion(sample)

    def _publish_motors(self, left: float, right: float) -> None:
        payload = "Stop" if left == 0.0 and right == 0.0 else {"Move": {"left": left, "right": right}}
        # Motor commands are transient: QoS 0, never retained (as the dashboard does).
        self.client.publish(self.motors_topic, json.dumps(payload), qos=0, retain=False)

    # --- Test logic ---------------------------------------------------------

    def _report(self, sample: Sample) -> None:
        if sample.is_charging:
            print("WARNING: the robot reports it is charging; the measurement is not a discharge test.")
        print(
            f"[{format_duration(sample.elapsed_s)}] "
            f"{sample.state_of_charge:3d}%  {sample.voltage:5.2f} V  "
            f"{sample.current:5.2f} A  {sample.temperature:5.1f} °C"
        )

    def _check_depletion(self, sample: Sample) -> None:
        if sample.state_of_charge <= self.args.cutoff_soc:
            self.finish(f"state of charge reached {sample.state_of_charge}%")
            return
        # Voltage sags under load, so require a sustained reading before calling it.
        if sample.voltage <= self.args.cutoff_voltage:
            self.low_voltage_streak += 1
            if self.low_voltage_streak >= self.args.cutoff_samples:
                self.finish(f"voltage stayed at or below {self.args.cutoff_voltage:.2f} V")
        else:
            self.low_voltage_streak = 0

    def finish(self, reason: str, at: float | None = None) -> None:
        if not self.stop_event.is_set():
            self.reason = reason
            self.ended_at = at if at is not None else time.monotonic()
            self.stop_event.set()

    def _drive(self) -> None:
        """Replay the motion profile until the test ends."""
        scale = self.args.speed
        while not self.stop_event.is_set():
            for left, right, duration in MOTION_PROFILE:
                segment_end = time.monotonic() + duration
                while time.monotonic() < segment_end:
                    if self.stop_event.is_set():
                        return
                    self._publish_motors(round(left * scale, 3), round(right * scale, 3))
                    self.stop_event.wait(COMMAND_PERIOD_S)

    def _watch_silence(self) -> None:
        """A robot that stops publishing has run out of power (or crashed)."""
        while not self.stop_event.wait(1.0):
            with self.lock:
                last = self.last_telemetry_at
            if last is not None and time.monotonic() - last > self.args.silence_timeout:
                # The robot died when it last spoke, not when we noticed.
                self.finish(f"no telemetry for {self.args.silence_timeout:.0f} s (robot went down)", at=last)

    def run(self) -> int:
        try:
            self.client.connect(self.args.broker_ip, self.args.port, keepalive=30)
        except OSError as error:
            print(f"Cannot reach the broker: {error}", file=sys.stderr)
            return 1
        self.client.loop_start()

        print(f"Waiting for telemetry on {self.telemetry_topic} ...")
        if not self.first_telemetry.wait(self.args.startup_timeout):
            print(
                f"No telemetry from {self.args.robot_id} within {self.args.startup_timeout:.0f} s; "
                "is the robot powered on and connected?",
                file=sys.stderr,
            )
            self._shutdown()
            return 1

        print(f"Robot {self.args.robot_id} is alive. Driving until the battery is depleted (Ctrl-C to abort).\n")
        threads = [
            threading.Thread(target=self._drive, daemon=True),
            threading.Thread(target=self._watch_silence, daemon=True),
        ]
        for thread in threads:
            thread.start()
        self.stop_event.wait()
        for thread in threads:
            thread.join(timeout=2.0)

        self._stop_motors()
        self._shutdown()
        self._summary()
        if self.args.csv:
            self._write_csv(self.args.csv)
        return 0

    def _stop_motors(self) -> None:
        # Repeat the stop a few times: it is QoS 0 and the robot must not run away.
        for _ in range(5):
            self._publish_motors(0.0, 0.0)
            time.sleep(0.1)

    def _shutdown(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    def _summary(self) -> None:
        with self.lock:
            samples = list(self.samples)
        if not samples:
            print("\nNo telemetry was collected.")
            return

        first, last = samples[0], samples[-1]
        charge_ah, energy_wh = integrate(samples)
        # The clock stops when the depletion criterion fires, not when the last
        # sample happened to arrive.
        elapsed = (self.ended_at - self.started_at) if self.ended_at else last.elapsed_s
        print("\n" + "=" * 56)
        print(f"Robot            : {self.args.robot_id}")
        print(f"Test ended       : {self.reason}")
        print(f"Battery life     : {format_duration(elapsed)} ({elapsed:.0f} s)")
        print(f"State of charge  : {first.state_of_charge}% -> {last.state_of_charge}%")
        print(f"Voltage          : {first.voltage:.2f} V -> {last.voltage:.2f} V")
        print(f"Average current  : {mean(s.current for s in samples):.2f} A "
              f"(peak {max(s.current for s in samples):.2f} A)")
        print(f"Charge delivered : {charge_ah * 1000:.0f} mAh")
        print(f"Energy delivered : {energy_wh:.2f} Wh")
        print(f"Peak temperature : {max(s.temperature for s in samples):.1f} °C")
        print(f"Telemetry samples: {len(samples)}")
        print("=" * 56)

    def _write_csv(self, path: str) -> None:
        with self.lock:
            samples = list(self.samples)
        with open(path, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["elapsed_s", "voltage", "current", "temperature", "state_of_charge", "is_charging"])
            for s in samples:
                writer.writerow([
                    f"{s.elapsed_s:.3f}", s.voltage, s.current, s.temperature, s.state_of_charge, int(s.is_charging)
                ])
        print(f"Samples written to {path}")


# --- helpers ----------------------------------------------------------------


def mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def integrate(samples: list[Sample]) -> tuple[float, float]:
    """Trapezoidal integration of current and power over time -> (Ah, Wh)."""
    charge_as = 0.0
    energy_ws = 0.0
    for previous, current in zip(samples, samples[1:]):
        dt = current.elapsed_s - previous.elapsed_s
        charge_as += 0.5 * (previous.current + current.current) * dt
        energy_ws += 0.5 * (previous.current * previous.voltage + current.current * current.voltage) * dt
    return charge_as / 3600.0, energy_ws / 3600.0


def format_duration(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def device_id(value: str) -> str:
    normalized = value.strip().upper()
    if not DEVICE_ID_PATTERN.match(normalized):
        raise argparse.ArgumentTypeError("robot ID must be 6 hexadecimal characters, e.g. 4C7A21")
    return normalized


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure how long a robot keeps driving on a full battery.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("robot_id", type=device_id, help="robot ID (6 hex characters)")
    parser.add_argument("broker_ip", help="MQTT broker host or IP")
    parser.add_argument("--port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("--username", help="MQTT username")
    parser.add_argument("--password", help="MQTT password")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="scale applied to the motion profile, in [0, 1]")
    parser.add_argument("--cutoff-soc", type=int, default=3,
                        help="stop when the reported state of charge drops to this percentage")
    parser.add_argument("--cutoff-voltage", type=float, default=6.4,
                        help="stop when the pack voltage stays at or below this value")
    parser.add_argument("--cutoff-samples", type=int, default=5,
                        help="consecutive low-voltage samples required, to ignore load sag")
    parser.add_argument("--silence-timeout", type=float, default=20.0,
                        help="seconds without telemetry after which the robot is considered dead")
    parser.add_argument("--startup-timeout", type=float, default=30.0,
                        help="seconds to wait for the first telemetry message before giving up")
    parser.add_argument("--csv", help="write the collected samples to this CSV file")
    args = parser.parse_args(argv)
    if not 0.0 <= args.speed <= 1.0:
        parser.error("--speed must be between 0 and 1")
    return args


def main(argv: list[str] | None = None) -> int:
    test = BatteryLifeTest(parse_args(argv))
    signal.signal(signal.SIGINT, lambda *_: test.finish("interrupted by the operator"))
    signal.signal(signal.SIGTERM, lambda *_: test.finish("terminated"))
    return test.run()


if __name__ == "__main__":
    sys.exit(main())
