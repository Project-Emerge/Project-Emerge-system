# Battery life test

Standalone utility that drives one robot through a repeating, realistic motion
profile (cruise, curves, spin, reverse, idle) until its battery is depleted, and
reports the elapsed time.

It is a bench tool: it is not part of the production stack, nothing imports it,
and it is not wired into `compose.yaml`, the `Makefile`, or the npm workspace.

## Requirements

`paho-mqtt`. The repository virtual environment already provides it:

```bash
.venv/bin/python tools/battery-life/battery_life.py <ROBOT_ID> <BROKER_IP>
```

Otherwise `pip install paho-mqtt` in any Python 3.9+ environment.

## Usage

```bash
.venv/bin/python tools/battery-life/battery_life.py 4C7A21 192.168.8.1
```

Useful options:

| Option | Meaning |
| --- | --- |
| `--port` | Broker port (default `1883`) |
| `--username` / `--password` | Broker credentials |
| `--speed` | Scales the whole motion profile, in `[0, 1]` (default `1.0`) |
| `--cutoff-soc` | Stop at this reported state of charge (default `3`%) |
| `--cutoff-voltage` | Stop at this pack voltage (default `6.4` V) |
| `--cutoff-samples` | Consecutive low-voltage samples required, to ignore load sag |
| `--silence-timeout` | Seconds without telemetry before the robot is declared dead |
| `--csv` | Write every telemetry sample to a CSV file |

The maximum wheel speed itself is the fleet-wide `/config/motors` setting; this
tool does not change it, so run the test with the configuration you want to
characterise.

## What it does

1. Subscribes to `/telemetry/<ROBOT_ID>` and waits for the first message, so the
   clock starts only once the robot is actually alive.
2. Publishes `/motors/<ROBOT_ID>` commands at 5 Hz (QoS 0, not retained, exactly
   like the dashboard), cycling through the motion profile.
3. Ends when the state of charge or the voltage falls below the cutoff, when
   telemetry goes silent, or on Ctrl-C — always sending `Stop` first.
4. Prints the battery life plus voltage and state-of-charge endpoints, average
   and peak current, and the delivered charge (mAh) and energy (Wh), integrated
   from telemetry.

## Before running

- Stop anything else that commands this robot — the aggregate runtime and the
  dashboard publish to the same `/motors/<ID>` topic and will fight this tool
  for control (`docker compose stop aggregate-runtime`).
- Give the robot clear floor space: it drives forward, reverses, and spins for
  the whole discharge.
