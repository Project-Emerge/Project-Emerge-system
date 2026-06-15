import argparse
import json
import math

import cv2
import numpy as np
import paho.mqtt.client as mqtt
from estimator import ArUcoRobotPoseEstimator


def calibrate_camera():
    """
    Load camera calibration data from calibration.npz file.
    """
    try:
        # Load calibration data
        calib_data = np.load('camera_calibration.npz')
        camera_matrix = calib_data['camera_matrix']
        distortion_coefficients = calib_data['dist_coeffs']
        print(f"Loaded calibration data (RMS Error: {calib_data.get('rms_error', 'N/A'):.4f})")
        return camera_matrix, distortion_coefficients
    except FileNotFoundError:
        print("Warning: No calibration data found! Using default values.")
        print("Run calibration.py first to generate proper calibration data.")
        # Fallback to example values for Full HD (1920x1080)
        camera_matrix = np.array([[1400, 0, 960], [0, 1400, 540], [0, 0, 1]], dtype=np.float32)
        #distortion_coefficients = np.array([0.1, -0.2, 0, 0, 0], dtype=np.float32)
        distortion_coefficients = np.array([0.0, -0.0, 0, 0, 0], dtype=np.float32)
        return camera_matrix, distortion_coefficients


class PoseInfo:
    def __init__(self, marker_id, position, rotation):
        self.marker_id = marker_id
        self.position = position
        self.rotation = rotation
        self.ticks = 0  # Initialize ticks to zero

    def __repr__(self):
        return f"PoseInfo(marker_id={self.marker_id}, position={self.position}, rotation={self.rotation})"

class PosesInfo:
    def __init__(self):
        self.poses = {}

    def update_pose(self, marker_id, position, rotation):
        if marker_id not in self.poses:
            self.poses[marker_id] = PoseInfo(marker_id, position, rotation)
        else:
            self.poses[marker_id].position = position
            self.poses[marker_id].rotation = rotation
        self.poses[marker_id].ticks += 1  # Increment ticks on each update

    def remove_poses_not_in_list(self, valid_ids):
        for marker_id in list(self.poses.keys()):
            if marker_id not in valid_ids:
                del self.poses[marker_id]
    def get_pose(self, marker_id):
        return self.poses.get(marker_id, None)

    def get_all_poses_greater_than_ticks(self, min_ticks):
        return [pose for pose in self.poses.values() if pose.ticks > min_ticks]

    def __repr__(self):
        return f"PosesInfo(poses={self.poses})"

poses_info = PosesInfo()
def main(debug=False, mqtt_url="localhost", width=640, height=480, device=1, fps=30):
    """
    Main function to run the robot pose estimation system.
    """

    # Initialize camera
    cap = cv2.VideoCapture(device) # cv2.VideoCapture(4, cv2.CAP_V4L2)  # Use 0 for default camera
    fourcc = cv2.VideoWriter_fourcc(*"MJPG")
    cap.set(cv2.CAP_PROP_FOURCC, fourcc)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)

    # Check what we actually got
    actual_width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    actual_height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    actual_fps = cap.get(cv2.CAP_PROP_FPS)

    print(f"Camera initialized with resolution: {int(actual_width)}x{int(actual_height)} at {actual_fps} FPS")

    # Get camera calibration parameters
    camera_matrix, distortion_coefficients = calibrate_camera()

    # Initialize pose estimator
    pose_estimator = ArUcoRobotPoseEstimator(camera_matrix, distortion_coefficients, marker_size=0.067, smooting_history=10)

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
            if(debug):
                print(
                    f"\rMarker {pose_info.marker_id}: "
                    f"Pos({pos['x']:.3f}, {pos['y']:.3f}, {pos['z']:.3f}) "
                    f"Rot({rot['roll']:.1f}, {rot['pitch']:.1f}, {rot['yaw']:.1f})"
                )
            poses_info.update_pose(pose_info.marker_id, pos, rot)

        # Draw pose information on frame using cached detection values to maximize FPS
        if debug:
            frame = pose_estimator.draw_last_pose(frame)

        # Remove poses not detected in this frame
        detected_ids = [pose_info.marker_id for pose_info in pose_infos]
        poses_info.remove_poses_not_in_list(detected_ids)
        # Publish all poses with ticks > 30
        for pose in poses_info.get_all_poses_greater_than_ticks(5):
            pos = pose.position
            rot = pose.rotation
            client.publish(
                f"{mqtt_topic}{pose.marker_id}/position",
                json.dumps(
                    {
                        "x": float(pos["x"]) * -1,
                        "y": float(pos["y"]) * -1,
                        "orientation": float(rot["yaw"]) * math.pi / 180.0,
                        "robot_id": int(pose.marker_id),
                    }
                ),
            )
        # Display frame in debug mode
        if debug:
            cv2.imshow("Robot Pose Estimation", frame)

        # Calculate and print FPS
        fps_counter += 1
        if fps_counter % 30 == 0:  # Print FPS every 30 frames
            fps_end_time = cv2.getTickCount()
            fps = 30 / ((fps_end_time - fps_start_time) / cv2.getTickFrequency())
            print(f"\nFPS: {fps:.1f}", end="")
            fps_start_time = fps_end_time

        # Handle key presses
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        if key == ord("s") and pose_infos and len(pose_infos) > 0:
            print(f"\nSaved pose: {pose_infos[0]}")
    client.loop_stop()
    cap.release()
    cv2.destroyAllWindows()
    client.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ArUco Robot Pose Estimator")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode with visual output")
    parser.add_argument("--mqtt-url", default="localhost", help="MQTT broker URL")
    parser.add_argument("--width", type=int, default=640, help="Camera frame width")
    parser.add_argument("--height", type=int, default=480, help="Camera frame height")
    parser.add_argument("--device", type=int, default=1, help="Device id")
    parser.add_argument("--fps", type=int, default=30, help="Current FPS")


    args = parser.parse_args()

    main(debug=args.debug, mqtt_url=args.mqtt_url, width=args.width, height=args.height, device=args.device, fps=args.fps)
