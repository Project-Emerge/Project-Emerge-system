import threading
import json
import os
from flask import Flask, request, jsonify
from flask_cors import CORS
import paho.mqtt.client as mqtt
import time

app = Flask(__name__)
CORS(app)


# Global state
neighborhood_type = 'FULL'  # Default type "FULL" or "RADIUS"
radius_value = 1.0            # Default radius
robot_positions = {}
last_neighbors_sent = {}
update_counter = {}

# Read MQTT broker URL and port from environment variables
MQTT_BROKER = os.environ.get('MQTT_BROKER', 'localhost')
MQTT_PORT = int(os.environ.get('MQTT_PORT', '1883'))
POSITION_TOPIC = '/pose/+'
NEIGHBORS_TOPIC = '/neighbors/{}'

STATE_FILE = "neighborhood_state.json"

# --- MQTT Logic ---
def on_connect(client, userdata, flags, rc, *args):
    conn_code = rc if isinstance(rc, int) else (rc.value if hasattr(rc, 'value') else 0)
    if conn_code == 0:
        print('Connected to MQTT broker')
        client.subscribe(POSITION_TOPIC)
    else:
        print(f'Failed to connect to MQTT broker, return code: {conn_code}')

def on_message(client, userdata, msg):
    topic = msg.topic
    payload = msg.payload.decode()
    # Extract robot id from topic
    try:
        robot_id = topic.split('/')[-1]
        position = json.loads(payload)
        robot_positions[robot_id] = position
        # Track update count
        update_counter[robot_id] = update_counter.get(robot_id, 0) + 1
        compute_and_publish_neighbors(client, robot_id)
    except Exception as e:
        print(f'Error processing message: {e}')

def compute_and_publish_neighbors(client, robot_id):
    global neighborhood_type, radius_value
    pos1 = robot_positions.get(robot_id)
    if pos1 is None:
        return
    neighbors = []
    if neighborhood_type == 'FULL':
        neighbors = [id2 for id2 in robot_positions if id2 != robot_id]
    elif neighborhood_type == 'RADIUS':
        for id2, pos2 in robot_positions.items():
            if robot_id == id2:
                continue
            x1 = pos1.get('x_m') or pos1.get('x', 0)
            y1 = pos1.get('y_m') or pos1.get('y', 0)
            x2 = pos2.get('x_m') or pos2.get('x', 0)
            y2 = pos2.get('y_m') or pos2.get('y', 0)
            dist = ((x1-x2)**2 + (y1-y2)**2)**0.5
            if dist <= radius_value:
                neighbors.append(id2)
    # Only publish if changed or after 10 updates
    neighbors_sorted = sorted(neighbors)
    last_sent = last_neighbors_sent.get(robot_id)
    count = update_counter.get(robot_id, 0)
    if neighbors_sorted != last_sent or count >= 10:
        topic = NEIGHBORS_TOPIC.format(robot_id)
        client.publish(topic, json.dumps(neighbors_sorted))
        last_neighbors_sent[robot_id] = neighbors_sorted
        update_counter[robot_id] = 0

try:
    mqtt_client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
except AttributeError:
    mqtt_client = mqtt.Client()

mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

def mqtt_thread():
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
    mqtt_client.loop_forever()

# --- Flask HTTP Endpoint ---

# Set neighborhood type and radius via HTTP
@app.route('/neighborhood', methods=['GET', 'POST'])
def neighborhood():
    global neighborhood_type, radius_value
    if request.method == 'POST':
        data = request.get_json()
        if not data or 'type' not in data:
            return jsonify({'error': 'Missing type'}), 400
        neighborhood_type = data['type']
        if 'radius' in data:
            try:
                radius_value = float(data['radius'])
            except Exception:
                return jsonify({'error': 'Invalid radius value'}), 400
        save_state()
        return jsonify({'status': 'ok', 'type': neighborhood_type, 'radius': radius_value})
    else:  # GET
        return jsonify({'type': neighborhood_type, 'radius': radius_value})


# Default page: show current neighborhood type and radius
@app.route('/', methods=['GET', 'POST'])
def index():
    global neighborhood_type, radius_value
    message = ""
    if request.method == 'POST':
        ntype = request.form.get('type')
        radius = request.form.get('radius')
        if ntype:
            neighborhood_type = ntype
            if ntype == "RADIUS" and radius:
                try:
                    radius_value = float(radius)
                except Exception:
                    message = "Invalid radius value!"
            message = f"Neighborhood set to {neighborhood_type} (radius={radius_value})"
            save_state()
        elif radius is not None and neighborhood_type == "RADIUS":
            try:
                radius_value = float(radius)
                message = f"Radius updated to {radius_value}"
                save_state()
            except Exception:
                message = "Invalid radius value!"

    # Conditional rendering of buttons/inputs
    form_html = ""
    if neighborhood_type != "FULL":
        # Show button to switch to FULL
        form_html += """
        <form method="post">
            <button name="type" value="FULL" type="submit">Set FULL Neighborhood</button>
        </form>
        """
    if neighborhood_type == "FULL":
        # Show input and button to switch to RADIUS
        form_html += f"""
        <form method="post">
            <input type="number" step="any" name="radius" value="{radius_value}" />
            <button name="type" value="RADIUS" type="submit">Set RADIUS Neighborhood</button>
        </form>
        """
    if neighborhood_type == "RADIUS":
        # Show input and button to update radius only
        form_html += f"""
        <form method="post">
            <input type="number" step="any" name="radius" value="{radius_value}" />
            <button type="submit">Update Radius</button>
        </form>
        """

    html = f"""
    <h2>Neighborhood System</h2>
    <p>Type: <b>{neighborhood_type}</b></p>
    <p>Radius: <b>{radius_value}</b></p>
    {form_html}
    <p style="color: red;">{message}</p>
    """
    return html, 200

def clean_up():
    while True:
        # Avoid cleaning globals as in main branch implementation (local assignment only)
        robot_positions = {}
        last_neighbors_sent = {}
        update_counter = {}
        time.sleep(1)

def save_state():
    with open(STATE_FILE, "w") as f:
        json.dump({"type": neighborhood_type, "radius": radius_value}, f)

def load_state():
    global neighborhood_type, radius_value
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
            neighborhood_type = data.get("type", "FULL")
            radius_value = data.get("radius", 1.0)
    except Exception:
        pass

# Call load_state() at startup
load_state()

if __name__ == '__main__':
    threading.Thread(target=mqtt_thread, daemon=True).start()
    threading.Thread(target=clean_up, daemon=True).start()
    app.run(host='0.0.0.0', port=5000)
