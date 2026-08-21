import os
import time
import argparse
import sys
from robot_emulator.core.world import RobotWorld

def parse_arguments():
    """Parse command line arguments with environment variable fallbacks"""
    # Build default MQTT URL from MQTT_BROKER and MQTT_PORT if available, otherwise fallback to MQTT env var or localhost
    mqtt_broker = os.environ.get('MQTT_BROKER', '')
    mqtt_port = os.environ.get('MQTT_PORT', '')
    if mqtt_broker:
        port_suffix = f":{mqtt_port}" if mqtt_port else ""
        default_mqtt = f"mqtt://{mqtt_broker}{port_suffix}"
    else:
        default_mqtt = os.environ.get('MQTT', 'mqtt://localhost:1883')

    parser = argparse.ArgumentParser(description='Robot Emulation System')
    parser.add_argument('--robots', '-r', type=int,
                        default=int(os.environ.get('ROBOTS', 12)),
                        help='Number of robots to simulate (default: 12)')
    parser.add_argument('--mqtt', '-m', type=str,
                        default=default_mqtt,
                        help=f'MQTT broker URL (default: {default_mqtt})')
    parser.add_argument('--world-size', '-w', type=float,
                        default=float(os.environ.get('WORLD_SIZE', 10.0)),
                        help='World size in meters (default: 10.0)')
    parser.add_argument('--neighborhood-range', '-n', type=float,
                        default=float(os.environ.get('NEIGHBORHOOD_RANGE', 20.0)),
                        help='Neighborhood range in meters (default: 20.0)')
    parser.add_argument('--send-neighbors', type=bool,
                        default=os.environ.get('SEND_NEIGHBORS', 'False').lower() in ('true', '1', 't'),
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
    world = RobotWorld(
        num_robots=args.robots, 
        mqtt_url=args.mqtt, 
        world_size=args.world_size, 
        neighborhood_range=args.neighborhood_range, 
        send_neighbors=args.send_neighbors
    )
    
    # Start simulation
    world.start()
    
    try:
        print("\nSimulation started. Robot status will be published every 16ms.")
        print("Send motor commands to robots via MQTT:")
        print("Topic: /motors/{deviceId}")
        print("Message format: {\"Move\": {\"left\": -1.0, \"right\": 1.0}}")
        print("  - left/right values: -1.0 (full backward) to 1.0 (full forward)")
        print("  - \"Stop\" = stop")
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
