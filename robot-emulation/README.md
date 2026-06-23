# Project Emerge Experiment Startup Guide

This repository setup is used to launch the Project Emerge experiment environment, including the system services, dashboard, and robot emulation.

The default scenario is **Point to Leader**, where all robots orient themselves toward a selected leader robot.
But you can change the formation scenario from the dashboard to see different behaviors---see the [Using the Dashboard](#3-using-the-dashboard) section for details.

---

## Repository Setup

Clone the required repositories:

```bash
git clone https://github.com/Project-Emerge/Project-Emerge-system
git clone https://github.com/Project-Emerge/robot-emulation
```

You should now have the following folders:

```text
Project-Emerge-system/
robot-emulation/
```

---

## Requirements

Before starting, make sure you have the following tools installed:

- Docker
- Docker Compose
- Python
- Poetry

---

## 1. Start the Project Emerge System

Move into the `Project-Emerge-system` repository:

```bash
cd Project-Emerge-system
```

Start the required services:

```bash
docker compose up -d --build mqtt-broker dashboard neighborhood-system aggregate-runtime
```

This command starts:

- `mqtt-broker`
- `dashboard`
- `neighborhood-system`
- `aggregate-runtime`

Once the services are running, open the dashboard in your browser:

```text
http://localhost:5173/
```

---

## 2. Start the Robot Emulation

Open a new terminal and move into the `robot-emulation` repository:

```bash
cd robot-emulation
```

Install the project dependencies with Poetry:

```bash
poetry install
```

Launch the robot emulation:

```bash
poetry run python src/robot_emulation/main.py --robots 12 --mqtt mqtt://localhost:1883 --world-size 5.0
```

This starts an emulation with:

- `12` robots
- MQTT broker running at `mqtt://localhost:1883`
- world size set to `5.0`

---

## 3. Using the Dashboard

Open the dashboard at:

```text
http://localhost:5173/
```

This is what you should see
![Dashboard Screenshot](./static/localhost.png)

By default, the selected scenario is `Point to Leader`, where all robots orient themselves toward a selected leader robot.


Since no leader is selected by default, the robots should keep rotating indefinitely.

To select a leader:

1. Click on a robot in the dashboard.
2. Open the contextual menu.
3. Select **Make Leader**.

See this video for a demonstration of the expected behavior:

<img width="1914" height="963" alt="point-to-leader" src="https://github.com/user-attachments/assets/fe3a75e2-635e-468a-9115-57fca26cb035" />


After selecting a leader, the other robots should start orienting themselves toward it.

---

## 4. Change Formation Scenario

In the top-left corner of the dashboard, you can select a different formation scenario.

Available formations may include:

- Circle
- V-shape
- Other available formations from the scenario selector

Select a formation from the dropdown menu to change the behavior of the robot swarm.

See this video for a demonstration of changing the formation:

<img width="957" height="482" alt="smaller" src="https://github.com/user-attachments/assets/93f2c4f1-9164-4eba-bbe9-f202ee6bb18f" />


---

## Useful Commands

### Start system services

```bash
cd Project-Emerge-system
docker compose up -d --build mqtt-broker dashboard neighborhood-system aggregate-runtime
```

### Start robot emulation

```bash
cd robot-emulation
poetry install
poetry run python src/robot_emulation/main.py --robots 12 --mqtt mqtt://localhost:1883 --world-size 5.0
```

### Stop system services

From inside the `Project-Emerge-system` repository:

```bash
docker compose down
```

---

## Troubleshooting

### The dashboard does not open

Make sure the dashboard service is running:

```bash
docker compose ps
```

Then open:

```text
http://localhost:5173/
```

### Robots are not visible or not moving

Check that the robot emulation is running and connected to the MQTT broker:

```bash
poetry run python src/robot_emulation/main.py --robots 12 --mqtt mqtt://localhost:1883 --world-size 5.0
```

Also make sure the system services were started before launching the emulation.

### Robots keep spinning

This is expected in the default **Point to Leader** scenario when no leader has been selected.

Click on a robot and choose **Make Leader** to assign a leader.

---

## Expected Startup Flow

1. Clone both repositories.
2. Start the system services with Docker Compose.
3. Open the dashboard at `http://localhost:5173/`.
4. Install and start the robot emulation.
5. Select a robot and click **Make Leader**.
6. Optionally change the formation from the top-left selector.

---

## Notes

Keep both terminals open while running the experiment:

- One terminal for the Docker Compose services.
- One terminal for the robot emulation.

Stopping either one may interrupt the experiment.
