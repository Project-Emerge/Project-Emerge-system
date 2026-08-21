import math
import time
import json
import threading
import random
from typing import Tuple, Dict, List
from urllib.parse import urlparse
from robot_emulator.core.robot import Robot, Position
import paho.mqtt.client as mqtt

class RobotWorld:
    def __init__(self, num_robots: int, mqtt_url: str, world_size: float = 10.0, neighborhood_range: float = 20.0, send_neighbors: bool = False):
        self.world_size = world_size
        self.neighborhood_range = neighborhood_range
        self.send_neighbors = send_neighbors
        self.robots: List[Robot] = []
        self.running = False
        self.update_thread = None
        self.mqtt_client = None
        self.mqtt_url = mqtt_url
        # Initialize robots in a square formation
        self._initialize_robots(num_robots)
        # Setup MQTT
        self._setup_mqtt()
    
    def _initialize_robots(self, num_robots: int):
        """Initialize robots in a square formation"""
        if num_robots <= 0:
            raise ValueError("Number of robots must be positive")
        
        # Calculate grid size for square formation
        grid_size = math.ceil(math.sqrt(num_robots))
        spacing = self.world_size / (grid_size + 1)
        
        for i in range(num_robots):
            row = i // grid_size
            col = i % grid_size
            
            # Calculate position in the square
            x = spacing * (col + 1)
            y = spacing * (row + 1)
            
            # Random initial orientation
            orientation = (random.uniform(0, 2 * math.pi) - math.pi)
            
            initial_pos = Position(x, y, orientation)
            robot = Robot(i, initial_pos, self.world_size)
            self.robots.append(robot)
    
    def _get_neighbors(self, robot: Robot) -> List[str]:
        """Get all robots within neighborhood range of the given robot (hex IDs)"""
        neighbors = []
        robot_status = robot.get_status()
        robot_x, robot_y = robot_status["x"], robot_status["y"]
        
        for other_robot in self.robots:
            if other_robot.id == robot.id:
                continue
                
            other_status = other_robot.get_status()
            other_x, other_y = other_status["x"], other_status["y"]
            
            # Calculate distance between robots
            distance = math.sqrt((robot_x - other_x)**2 + (robot_y - other_y)**2)
            
            if distance <= self.neighborhood_range:
                neighbors.append(other_robot.hex_id)
        
        return neighbors
    
    def _parse_mqtt_url(self, url: str) -> Tuple[str, int]:
        """Parse MQTT URL to extract host and port"""
        try:
            parsed = urlparse(url if url.startswith(('mqtt://', 'tcp://')) else f'mqtt://{url}')
            host = parsed.hostname or 'localhost'
            port = parsed.port or 1883
            return host, port
        except Exception:
            print(f"Invalid MQTT URL: {url}, using localhost:1883")
            return 'localhost', 1883
    
    def _setup_mqtt(self):
        """Setup MQTT client and callbacks"""
        # Support both old and new paho-mqtt client interfaces
        try:
            self.mqtt_client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        except AttributeError:
            self.mqtt_client = mqtt.Client()
            
        self.mqtt_client.on_connect = self._on_mqtt_connect
        self.mqtt_client.on_message = self._on_mqtt_message
        
        try:
            # Parse MQTT URL
            host, port = self._parse_mqtt_url(self.mqtt_url)
            print(f"Connecting to MQTT broker at {host}:{port}")
            
            # Connect to MQTT broker
            self.mqtt_client.connect(host, port)
            self.mqtt_client.loop_start()
        except Exception as e:
            print(f"Failed to connect to MQTT broker: {e}")
            print("Continuing without MQTT...")
    
    def _on_mqtt_connect(self, client, userdata, flags, rc, *args):
        """MQTT connection callback"""
        # Support both older on_connect signatures and CallbackAPIVersion.VERSION2
        conn_code = rc if isinstance(rc, int) else (rc.value if hasattr(rc, 'value') else 0)
        
        if conn_code == 0:
            print("Connected to MQTT broker")
            # Subscribe to the motor commands topic for all robots
            topic = "/motors/+"
            client.subscribe(topic)
            print(f"Subscribed to {topic}")
        else:
            print(f"Failed to connect to MQTT broker, return code: {conn_code}")
    
    def _on_mqtt_message(self, client, userdata, msg):
        """MQTT message callback"""
        try:
            topic_parts = msg.topic.split('/')
            # Expected topic: /motors/{deviceId} -> ['', 'motors', '{deviceId}']
            if len(topic_parts) >= 3 and topic_parts[1] == "motors":
                device_id = topic_parts[2]
                payload_str = msg.payload.decode('utf-8')
                
                # Try parsing payload as JSON
                try:
                    command = json.loads(payload_str)
                except json.JSONDecodeError:
                    command = payload_str
                
                # Find robot and process command
                for robot in self.robots:
                    if robot.hex_id == device_id:
                        robot.process_command(command)
                        print(f"Robot {device_id} received command: {command}")
                        break
        except Exception as e:
            print(f"Error processing MQTT message: {e}")
            
    def _generate_fake_telemetry(self, robot: Robot, dt: float) -> Dict:
        """Construct high-fidelity faked telemetry payload matching TelemetrySchema"""
        # Battery telemetry: voltage around 7.8V, drains slowly or fluctuates slightly
        voltage = 7.4 + random.uniform(0.3, 0.6)
        current = 0.1 + 0.4 * (abs(robot.left_motor_power) + abs(robot.right_motor_power))
        state_of_charge = 85
        is_charging = False
        
        # Calculate actual angular velocity (omega) in rad/s based on wheel speeds
        v_left = robot.left_motor_power * robot.max_speed
        v_right = robot.right_motor_power * robot.max_speed
        omega = (v_left - v_right) / robot.wheel_base
        
        timestamp_us = int(time.time() * 1000000)
        
        # IMU sensors faking
        # Gravity (9.81 m/s^2) on Z-axis, with minor noise
        accel = [random.uniform(-0.02, 0.02), random.uniform(-0.02, 0.02), 9.81 + random.uniform(-0.05, 0.05)]
        gyro = [random.uniform(-0.005, 0.005), random.uniform(-0.005, 0.005), omega + random.uniform(-0.01, 0.01)]
        mag = [math.cos(robot.position.orientation), math.sin(robot.position.orientation), 0.0]
        
        # Heading normalized to [0, 360) degrees
        heading_deg = math.degrees(robot.position.orientation) % 360.0
        
        # 2D orientation quaternion [x, y, z, w]
        half_angle = robot.position.orientation / 2.0
        quaternion = [0.0, 0.0, math.sin(half_angle), math.cos(half_angle)]
        
        is_stationary = (abs(v_left) < 0.001 and abs(v_right) < 0.001)
        
        imu = {
            "timestamp_us": timestamp_us,
            "raw": {
                "accelerometer": accel,
                "gyroscope": gyro,
                "magnetometer": mag,
                "temperature": 25.0 + random.uniform(-0.2, 0.2)
            },
            "filtered": {
                "accelerometer": accel,
                "gyroscope": gyro,
                "magnetometer": mag,
                "linear_acceleration": [0.0, 0.0, 0.0],
                "quaternion": quaternion,
                "roll": 0.0,
                "pitch": 0.0,
                "heading": heading_deg,
                "is_stationary": is_stationary
            }
        }
        
        motor_telemetry = "Stopped" if is_stationary else {
            "Motoring": {
                "left": robot.left_motor_power,
                "right": robot.right_motor_power
            }
        }
        
        return {
            "motor_telemetry": motor_telemetry,
            "battery_telemetry": {
                "voltage": voltage,
                "current": current,
                "temperature": 26.0 + random.uniform(-0.2, 0.2),
                "is_charging": is_charging,
                "state_of_charge": state_of_charge
            },
            "imu_telemetry": imu,
            "network_telemetry": {
                "rssi": random.randint(-60, -45),
                "ip_address": f"192.168.8.{10 + robot.id}"
            }
        }
    
    def _publish_robot_status(self, robot: Robot, dt: float):
        """Publish robot status, pose and telemetry to MQTT"""
        if self.mqtt_client and self.mqtt_client.is_connected():
            try:
                # 1. Pose publishing
                v_left = robot.left_motor_power * robot.max_speed
                v_right = robot.right_motor_power * robot.max_speed
                speed_m_s = (v_left + v_right) / 2.0
                
                pose_payload = {
                    "x_m": round(robot.position.x, 3),
                    "y_m": round(robot.position.y, 3),
                    "heading_rad": robot.position.orientation,
                    "speed_m_s": round(speed_m_s, 3),
                    "position_variance_m2": 0.01,
                    "timestamp_us": int(time.time() * 1000000)
                }
                
                pose_topic = f"/pose/{robot.hex_id}"
                self.mqtt_client.publish(pose_topic, json.dumps(pose_payload))
                
                # 2. Telemetry and IMU publishing
                telemetry_payload = self._generate_fake_telemetry(robot, dt)
                
                telemetry_topic = f"/telemetry/{robot.hex_id}"
                self.mqtt_client.publish(telemetry_topic, json.dumps(telemetry_payload))
                
                imu_topic = f"/imu/{robot.hex_id}"
                self.mqtt_client.publish(imu_topic, json.dumps(telemetry_payload["imu_telemetry"]))
                
            except Exception as e:
                print(f"Failed to publish status for robot {robot.hex_id}: {e}")
    
    def _update_loop(self):
        """Main update loop for the simulation"""
        last_time = time.time()
        
        while self.running:
            current_time = time.time()
            dt = current_time - last_time
            last_time = current_time
            
            # Update all robots
            for robot in self.robots:
                robot.update_position(dt)
                self._publish_robot_status(robot, dt)
            
            # Sleep for approximately 1/60th of a second
            time.sleep(0.016)
    
    def start(self):
        """Start the robot simulation"""
        if self.running:
            return
        
        self.running = True
        self.update_thread = threading.Thread(target=self._update_loop)
        self.update_thread.daemon = True
        self.update_thread.start()
        print(f"Started simulation with {len(self.robots)} robots (neighborhood range: {self.neighborhood_range}m)")
    
    def stop(self):
        """Stop the robot simulation"""
        self.running = False
        if self.update_thread:
            self.update_thread.join()
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
        print("Simulation stopped")
    
    def get_world_status(self) -> Dict:
        """Get status of all robots"""
        return {
            "world_size": self.world_size,
            "neighborhood_range": self.neighborhood_range,
            "num_robots": len(self.robots),
            "robots": [robot.get_status() for robot in self.robots]
        }
    
    def print_status(self):
        """Print current status of all robots"""
        print("\n" + "="*50)
        print(f"ROBOT WORLD STATUS - World Size: {self.world_size}m x {self.world_size}m")
        print(f"Neighborhood Range: {self.neighborhood_range}m")
        print("="*50)
        
        for robot in self.robots:
            status = robot.get_status()
            neighbors = self._get_neighbors(robot)
            neighbor_count = len(neighbors)
            print(f"Robot {robot.hex_id}: "
                  f"Pos({status['x']:.2f}, {status['y']:.2f}) "
                  f"Angle: {math.degrees(status['orientation']):.1f}° "
                  f"Neighbors: {neighbor_count}")
