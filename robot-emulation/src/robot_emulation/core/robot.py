import math
from typing import Dict
from dataclasses import dataclass


@dataclass
class Position:
    x: float
    y: float
    orientation: float  # in radians

@dataclass
class MotorCommand:
    left: float   # -1 to 1
    right: float  # -1 to 1

class Robot:
    def __init__(self, robot_id: int, initial_position: Position, world_size: float):
        self.id = robot_id
        self.position = initial_position
        self.world_size = world_size
        self.wheel_radius = 0.05  # 5cm wheel radius
        self.wheel_base = 0.1     # 20cm distance between wheels
        self.max_speed = 0.35      # max 1 m/s
        self.left_motor_power = 0.0   # -1 to 1
        self.right_motor_power = 0.0  # -1 to 1
        self.running = False
        
    def process_command(self, command: Dict):
        """Process movement commands with left/right motor values from -1 to 1"""
        print(command)
        try:
            if isinstance(command, dict):
                ## Right left are mirrored.
                self.left_motor_power = max(-1.0, min(1.0, float(command.get('right', 0))))
                self.right_motor_power = max(-1.0, min(1.0, float(command.get('left', 0))))
            else:
                # Fallback for string commands for backward compatibility
                if command == 'l':
                    self.left_motor_power = 1.0
                    self.right_motor_power = 0.0
                elif command == 'r':
                    self.left_motor_power = 0.0
                    self.right_motor_power = 1.0
                elif command == 's':
                    self.left_motor_power = 0.0
                    self.right_motor_power = 0.0
                else:
                    self.left_motor_power = 1.0
                    self.right_motor_power = 1.0
        except (ValueError, TypeError):
            print(f"Invalid command for robot {self.id}: {command}")
    
    def update_position(self, dt: float):
        """Update robot position based on differential drive kinematics"""
        # Calculate wheel velocities from motor power (-1 to 1)
        v_left = self.left_motor_power * self.max_speed
        v_right = self.right_motor_power * self.max_speed

        fx = - math.sin(self.position.orientation)
        fy = math.cos(self.position.orientation)
        if abs(v_left - v_right) < 0.001:  # Moving straight
            velocity = (v_left + v_right) / 2
            self.position.x += velocity * fx * dt
            self.position.y += velocity * fy * dt
        else:  # Turning
            # Calculate instantaneous center of curvature
            if abs(v_right - v_left) > 0.001:  # Avoid division by zero
                omega = (v_left - v_right) / self.wheel_base

                # Update orientation
                self.position.orientation += omega * dt

                # Update forward vector after orientation change
                fx = - math.sin(self.position.orientation)
                fy = math.cos(self.position.orientation)

                # Update position
                velocity = (v_left + v_right) / 2
                self.position.x += velocity * fx * dt
                self.position.y += velocity * fy * dt
        

        # Keep orientation in [-π, π]
        self.position.orientation = (self.position.orientation + math.pi) % (2 * math.pi) - math.pi 

    
    def get_status(self) -> Dict:
        """Get robot status for MQTT publishing"""
        return {
            "robot_id": self.id,
            "x": round(self.position.x, 3),
            "y": round(self.position.y, 3),
            "orientation": self.position.orientation 
        }