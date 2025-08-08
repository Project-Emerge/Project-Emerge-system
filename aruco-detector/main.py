import cv2
import numpy as np
import math
import argparse
from estimator import ArUcoRobotPoseEstimator
import paho.mqtt.client as mqtt
import json

def calibrate_camera():
    """
    Example camera calibration function.
    You should replace this with your actual camera calibration data.
    """
    # Example camera matrix (you need to calibrate your specific camera)
    camera_matrix = np.array([
        [800, 0, 320],
        [0, 800, 240],
        [0, 0, 1]
    ], dtype=np.float32)
    
    # Example distortion coefficients
    dist_coeffs = np.array([0.1, -0.2, 0, 0, 0], dtype=np.float32)
    
    return camera_matrix, dist_coeffs

def main(debug=False, mqtt_url="localhost", width=640, height=480):
    """
    Main function to run the robot pose estimation system.
    """
    
    # Initialize camera
    cap = cv2.VideoCapture(0, cv2.CAP_V4L2)  # Use 0 for default camera
    fourcc = cv2.VideoWriter_fourcc(*'MJPG')
    cap.set(cv2.CAP_PROP_FOURCC, fourcc)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, 144)

    # Check what we actually got
    actual_width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    actual_height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    actual_fps = cap.get(cv2.CAP_PROP_FPS)

    print(f"Camera initialized with resolution: {int(actual_width)}x{int(actual_height)} at {actual_fps} FPS")

    # Get camera calibration parameters
    camera_matrix, dist_coeffs = calibrate_camera()
    
    # Initialize pose estimator
    pose_estimator = ArUcoRobotPoseEstimator(camera_matrix, dist_coeffs, marker_size=0.05)
    
    # Initialize MQTT client
    print(f"Connecting to MQTT broker... {mqtt_url}")
    client = mqtt.Client()
    client.connect(mqtt_url, 1883)
    
    print("Robot Pose Estimation System Started")
    print(f"Connected to MQTT broker at {mqtt_url}")
    print("Press 'q' to quit")
    print("Press 's' to save current pose")
    mqtt_topic = "robots/"
    client.loop_start()
    # FPS calculation variables
    fps_counter = 0
    fps_start_time = cv2.getTickCount()
    
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        
        # Get robot pose
        pose_infos = pose_estimator.get_robot_poses(frame)
        for pose_info in pose_infos:
                # Print pose information
            pos = pose_info.position
            rot = pose_info.rotation
            print(f"\rMarker {pose_info.marker_id}: "
                f"Pos({pos['x']:.3f}, {pos['y']:.3f}, {pos['z']:.3f}) "
                f"Rot({rot['roll']:.1f}, {rot['pitch']:.1f}, {rot['yaw']:.1f})")
    
            client.publish(f"{mqtt_topic}{pose_info.marker_id}/position", json.dumps({
                "x": float(pos['x']),
                "y": float(pos['y']),
                "orientation": float(rot['yaw']) * math.pi / 180.0, 
                "robot_id": int(pose_info.marker_id)
            }))
            # Draw pose information on frame
            if debug:
                corners, ids, _ = pose_estimator.detect_markers(frame)
                poses = pose_estimator.estimate_pose(corners, ids)
                frame = pose_estimator.draw_pose_info(frame, corners, ids, poses)
            
        # Display frame in debug mode
        if debug:
            cv2.imshow('Robot Pose Estimation', frame)
        
        # Calculate and print FPS
        fps_counter += 1
        if fps_counter % 30 == 0:  # Print FPS every 30 frames
            fps_end_time = cv2.getTickCount()
            fps = 30 / ((fps_end_time - fps_start_time) / cv2.getTickFrequency())
            print(f"\nFPS: {fps:.1f}", end='')
            fps_start_time = fps_end_time
        
        # Handle key presses
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('s') and pose_infos and len(pose_infos) > 0:
            print(f"\nSaved pose: {pose_infos[0]}")
    client.loop_stop()
    cap.release()
    cv2.destroyAllWindows()
    client.disconnect()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='ArUco Robot Pose Estimator')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode with visual output')
    parser.add_argument('--mqtt-url', default='localhost', help='MQTT broker URL')
    parser.add_argument('--width', type=int, default=640, help='Camera frame width')
    parser.add_argument('--height', type=int, default=480, help='Camera frame height')
    
    args = parser.parse_args()
    
    main(debug=args.debug, mqtt_url=args.mqtt_url, width=args.width, height=args.height)