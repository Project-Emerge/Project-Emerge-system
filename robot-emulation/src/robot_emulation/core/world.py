import math
import time
import json
import threading
import random
from typing import Tuple, Dict, List
from urllib.parse import urlparse
from core.robot import Robot
from core.robot import Position
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
        self.send_neighbors = send_neighbors
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
    
    def _get_neighbors(self, robot: Robot) -> List[int]:
        """Get all robots within neighborhood range of the given robot"""
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
                neighbors.append(other_robot.id)
        
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
    
    def _on_mqtt_connect(self, client, userdata, flags, rc):
        """MQTT connection callback"""
        if rc == 0:
            print("Connected to MQTT broker")
            # Subscribe to command topics for all robots
            for robot in self.robots:
                topic = f"robots/{robot.id}/move"
                client.subscribe(topic)
                print(f"Subscribed to {topic}")
        else:
            print(f"Failed to connect to MQTT broker: {rc}")
    
    def _on_mqtt_message(self, client, userdata, msg):
        """MQTT message callback"""
        try:
            # Extract robot ID from topic
            topic_parts = msg.topic.split('/')
            print(f"Received MQTT message on topic: {msg.topic}")
            if len(topic_parts) >= 3 and topic_parts[0] == "robots":
                robot_id = int(topic_parts[1])
                # Parse command - expecting JSON format
                try:
                    command = json.loads(msg.payload.decode('utf-8'))
                except json.JSONDecodeError:
                    # Fallback to string command for backward compatibility
                    command = msg.payload.decode('utf-8')
                
                # Find robot and process command
                for robot in self.robots:
                    if robot.id == robot_id:
                        robot.process_command(command)
                        print(f"Robot {robot_id} received command: {command}")
                        break
        except Exception as e:
            print(f"Error processing MQTT message: {e}")
    
    def _publish_robot_status(self, robot: Robot):
        """Publish robot status to MQTT"""
        if self.mqtt_client:
            # Publish position
            position_topic = f"robots/{robot.id}/position"
            position_message = json.dumps(robot.get_status())
            self.mqtt_client.publish(position_topic, position_message)
            
            # Publish neighbors information only if enabled
            # TODO For some reason this is not working as intended, removing it now 
            # if self.send_neighbors:
            #     neighbors_topic = f"robots/{robot.id}/neighbors"
            #     neighbors = self._get_neighbors(robot)
            #     neighbors_message = json.dumps(neighbors)
            #     self.mqtt_client.publish(neighbors_topic, neighbors_message)
    
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
                self._publish_robot_status(robot)
            
            # Sleep for approximately 1 second
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
            print(f"Robot {robot.id}: "
                  f"Pos({status['x']:.2f}, {status['y']:.2f}) "
                  f"Angle: {status['orientation']:.1f}° "
                  f"Neighbors: {neighbor_count}")
