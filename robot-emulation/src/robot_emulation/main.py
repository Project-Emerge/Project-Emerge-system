import time
import json
import argparse
import sys
from core.world import RobotWorld

import paho.mqtt.client as mqtt

# Command line interface and main function
def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description='Robot Emulation System')
    parser.add_argument('--robots', '-r', type=int, required=True,
                        help='Number of robots to simulate')
    parser.add_argument('--mqtt', '-m', type=str, required=True,
                        help='MQTT broker URL (e.g., mqtt://localhost:1883 or localhost:1883)')
    parser.add_argument('--world-size', '-w', type=float, default=10.0,
                        help='World size in meters (default: 10.0)')
    parser.add_argument('--neighborhood-range', '-n', type=float, default=20.0,
                        help='Neighborhood range in meters (default: 20.0)')
    parser.add_argument('--send-neighbors', type=bool, default=False,
                        help='Send neighbors information (default: False)')
    return parser.parse_args()

def main():
    """Main function with command line interface"""
    args = parse_arguments()
    
    print(f"Starting Robot Emulation System...")
    print(f"Robots: {args.robots}")
    print(f"MQTT URL: {args.mqtt}")
    print(f"World Size: {args.world_size}m x {args.world_size}m")
    print(f"Neighborhood Range: {args.neighborhood_range}m")
    print(f"Send Neighbors: {args.send_neighbors}")
    # Create world
    world = RobotWorld(num_robots=args.robots, mqtt_url=args.mqtt, world_size=args.world_size, neighborhood_range=args.neighborhood_range, send_neighbors=args.send_neighbors)
    
    # Start simulation
    world.start()
    
    try:
        print("\nSimulation started. Robot status will be published every second.")
        print("Send motor commands to robots via MQTT:")
        print("Topic: robots/{robot_id}/command")
        print("Message format: {\"left\": -1.0, \"right\": 1.0}")
        print("  - left/right values: -1.0 (full backward) to 1.0 (full forward)")
        print("  - 0.0 = stop")
        print("\nExample commands:")
        print("  Forward: {\"left\": 1.0, \"right\": 1.0}")
        print("  Backward: {\"left\": -1.0, \"right\": -1.0}")
        print("  Turn left: {\"left\": -0.5, \"right\": 0.5}")
        print("  Turn right: {\"left\": 0.5, \"right\": -0.5}")
        print("  Stop: {\"left\": 0.0, \"right\": 0.0}")
        print("\nPress Ctrl+C to stop simulation\n")
        
        # Keep running and show status periodically
        while True:
            time.sleep(10)
            world.print_status()
            
    except KeyboardInterrupt:
        print("\nStopping simulation...")
        world.stop()
        print("Simulation stopped successfully.")


if __name__ == "__main__":
    main()