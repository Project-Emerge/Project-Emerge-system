# Aggregate runtime

Runs the aggregate program for the whole fleet: it reads robot poses from the vision system over
MQTT, works out where each robot should go, and drives the motors.

```
/pose/<id>       ->  MqttProvider     ->  AggregateOrchestrator  ->  Actuation
/neighbors/<id>                           (scafi program)             |
/config/formation                                                     v
                                       RobotUpdateMqtt -> HeadingController -> DifferentialDrive
                                                                      |
                                       MotorCommandPublisher  ->  /motors/<id>
```

## Driving the robots

The robots are open loop: no encoders, no on-board heading control. Everything that keeps them on
course is here, closing the loop over the 20 Hz vision poses.

Three pieces do the work, and they are separated so the first two can be tested without a robot:

| | |
|---|---|
| `DifferentialDrive` | Kinematics, and the inverse of the firmware's actuator map. Converts a body twist (m/s, rad/s) into the `left`/`right` numbers on the wire. |
| `HeadingController` | The control law. Pure, dt-aware, one immutable state per robot. Aims first, then drives. |
| `MotorCommandPublisher` | Owns every motor message: republishes at 30 Hz, cancels the firmware's command smoothing, and stops a robot whose pose has gone stale. |

Two properties of the real hardware shape all of this, and both live in `DriveConfig` because they
describe the robot rather than the tuning:

- **The wheel command is not proportional to wheel speed.** The firmware maps any non-zero command
  onto `[0.3, 1.0]` of duty cycle, with a step at zero, to get the motors past their stiction. A
  sender that ignores this has an effective steering gain that varies by about 3x with speed.
  `DifferentialDrive.toWheels` inverts the map, so a requested speed is the speed you get.
- **That stiction floor is one constant for the whole fleet, and the real robots do not all start
  at it.** Commands close to zero are meant to clear it, but in practice they leave some robots
  humming in place. Nothing below `DRIVE_MIN_COMMAND` is ever published: a wheel is either stopped
  or turning fast enough to be well clear of wherever its own threshold sits. The mixer works in
  those terms throughout, so the floor never rounds a command up and bends the path - if a twist is
  too slow to hold, it is driven faster along the same arc rather than nudged off it.
- **The firmware smooths commands once per message received, not per unit time.** Its lag therefore
  depends on how often we speak to it. `MotorCommandPublisher` republishes at 30 Hz and tracks a
  shadow copy of the filter so it can send the value that lands the filtered output on target.

## Configuration

Everything below has a working default; the environment variables exist so the physical constants
can be corrected in the field with a restart rather than a rebuild.

| Variable | Default | Meaning |
|---|---|---|
| `MQTT_URL` | `tcp://localhost:1883` | Broker to connect to |
| `DRIVE_MARKER_YAW_OFFSET_DEG` | `90` | Rotation from the ArUco marker frame to the robot's forward axis |
| `DRIVE_INVERT_WHEELS` | `false` | Set if the published `left` drives the physical right wheel |
| `DRIVE_WHEEL_BASE_M` | `0.10` | Distance between the wheels |
| `DRIVE_MAX_SPEED_MS` | `0.35` | Wheel speed at full duty cycle |
| `DRIVE_MIN_DUTY` | `0.30` | Must match `MotorConfig::min_duty_cycle` in the firmware |
| `DRIVE_MIN_COMMAND` | `0.10` | Smallest command ever published. Raise it if robots still hum instead of moving |
| `DRIVE_FIRMWARE_EMA_ALPHA` | `0.10` | Must match `MotorConfig::ema_filter_alpha`; `1.0` disables lag compensation |

### Calibrating the two conventions

`DRIVE_MARKER_YAW_OFFSET_DEG` depends on which way the ArUco sticker was glued to the robot, and
`DRIVE_INVERT_WHEELS` on how the motors were wired. Neither can be worked out from the source, and
getting either wrong makes a robot turn away from its goal rather than towards it - it will still
arrive, by coming all the way around, which is why it is worth measuring instead of eyeballing.

Give one robot a clear half metre of floor, with the vision system running, and:

```sh
CALIBRATE_ROBOT=A1B2C3 MQTT_URL=tcp://localhost:1883 sbt "runMain it.unibo.demo.CalibrateDrive"
```

It drives the robot forward and then spins it, and prints the two values to set.

## Tests

```sh
sbt test
```

The unit suites need nothing. The end-to-end suite drives the real loop against a fleet of
simulated robots and is skipped unless a broker is named:

```sh
docker run -d -p 1883:1883 eclipse-mosquitto:2.0
EMERGE_TEST_BROKER=tcp://localhost:1883 sbt test
```

`RobotPlant`, in the test sources, models the robot closely enough to be worth trusting: the
firmware's command smoothing and stiction remap, its `Stop` bypassing the filter, and
differential-drive kinematics reported in the marker frame the cameras see. `apps/robot-emulator`
models the same things, so a controller that behaves in simulation has a fair chance of behaving on
the floor.
