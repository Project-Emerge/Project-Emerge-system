import math

import cv2
import numpy as np


class RobotInformation:
    def __init__(self, marker_id, position, rotation, distance, rotation_vector, transition_vector):
        self.marker_id = marker_id
        self.position = position
        self.rotation = rotation
        self.distance = distance
        self.rotation_vector = rotation_vector
        self.transition_vector = transition_vector

    def to_dict(self):
        return {
            "marker_id": self.marker_id,
            "position": self.position,
            "rotation": self.rotation,
            "distance": self.distance,
            "rotation_vector": self.rotation_vector,
            "transition_vector": self.transition_vector,
        }


class ArUcoRobotPoseEstimator:
    def __init__(self, camera_matrix, distorsion_coefficients, marker_size=0.05, smooting_history=5):
        """
        Initialize the ArUco pose estimator.

        Args:
            camera_matrix: 3x3 camera intrinsic matrix
            dist_coeffs: Camera distortion coefficients
            marker_size: Size of ArUco marker in meters (default: 5cm)
        """
        self.camera_matrix = camera_matrix
        self.dist_coeffs = distorsion_coefficients
        self.marker_size = marker_size

        # Initialize ArUco detector
        self.aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        self.aruco_params = cv2.aruco.DetectorParameters()
        self.detector = cv2.aruco.ArucoDetector(self.aruco_dict, self.aruco_params)

        # For smoothing pose estimates - separate history for each robot
        self.pose_histories = {}  # Dictionary with marker_id as key
        self.max_history = smooting_history

    def detect_markers(self, frame):
        """
        Detect ArUco markers in the frame.

        Args:
            frame: Input camera frame

        Returns:
            corners: Detected marker corners
            ids: Detected marker IDs
            rejected: Rejected marker candidates
        """
        corners, ids, rejected = self.detector.detectMarkers(frame)
        return corners, ids, rejected

    def estimate_pose(self, corners, ids):
        """
        Estimate pose from detected markers.

        Args:
            corners: Detected marker corners
            ids: Detected marker IDs

        Returns:
            poses: List of (rotation_vector, transition_vector) tuples for each marker
        """
        if len(corners) == 0:
            return []

        poses = []

        # Define 3D points of marker corners in marker coordinate system
        marker_3d_points = np.array(
            [
                [-self.marker_size / 2, self.marker_size / 2, 0],
                [self.marker_size / 2, self.marker_size / 2, 0],
                [self.marker_size / 2, -self.marker_size / 2, 0],
                [-self.marker_size / 2, -self.marker_size / 2, 0],
            ],
            dtype=np.float32,
        )

        for i in range(len(ids)):
            # Use solvePnP to estimate pose
            success, rotation_vector, transition_vector = cv2.solvePnP(
                marker_3d_points, corners[i][0], self.camera_matrix, self.dist_coeffs
            )

            if success:
                poses.append((rotation_vector, transition_vector))

        return poses

    def rotation_vector_to_euler(self, rotation_vector):
        """
        Convert rotation vector to Euler angles (roll, pitch, yaw).

        Args:
            rotation_vector: Rotation vector from pose estimation

        Returns:
            roll, pitch, yaw in degrees
        """
        # Convert rotation vector to rotation matrix
        R, _ = cv2.Rodrigues(rotation_vector)

        # Extract Euler angles from rotation matrix
        sy = math.sqrt(R[0, 0] * R[0, 0] + R[1, 0] * R[1, 0])

        singular = sy < 1e-6

        if not singular:
            x = math.atan2(R[2, 1], R[2, 2])
            y = math.atan2(-R[2, 0], sy)
            z = math.atan2(R[1, 0], R[0, 0])
        else:
            x = math.atan2(-R[1, 2], R[1, 1])
            y = math.atan2(-R[2, 0], sy)
            z = 0

        return math.degrees(x), math.degrees(y), math.degrees(z)

    def smooth_pose(self, marker_id, rotation_vector, transition_vector):
        """
        Apply smoothing to pose estimates to reduce noise.

        Args:
            marker_id: ID of the marker
            pose: Current pose (rotation_vector, transition_vector)

        Returns:
            Smoothed pose
        """
        if( marker_id not in self.pose_histories):
            self.pose_histories[marker_id] = []
        history = self.pose_histories[marker_id]
        history.append((rotation_vector, transition_vector))
        poses_history = history[-self.max_history:]
        rotation_history = np.array([pose[0] for pose in poses_history])
        transition_history = np.array([pose[1] for pose in poses_history])
        rotations_history_abs = np.abs(rotation_history)
        rotation_vector = np.mean(rotations_history_abs, axis=0)
        transition_vector = np.mean(transition_history, axis=0)
        return rotation_vector, transition_vector

    def draw_pose_info(self, frame, corners, ids, poses):
        """
        Draw pose information on the frame.

        Args:
            frame: Input frame
            corners: Detected marker corners
            ids: Detected marker IDs
            poses: Estimated poses

        Returns:
            Frame with pose information drawn
        """
        result_frame = frame.copy()

        # Draw detected markers
        cv2.aruco.drawDetectedMarkers(result_frame, corners, ids)

                # Draw pose axes and information
        for rotation_vector, transition_vector in poses:
            # Draw coordinate axes
            cv2.drawFrameAxes(
                result_frame,
                self.camera_matrix,
                self.dist_coeffs,
                rotation_vector,
                transition_vector,
                self.marker_size,
            )

            # Get position and rotation

        return result_frame

    def get_robot_poses(self, frame):
        """
        Main function to get all robot poses from camera frame.

        Args:
            frame: Camera frame

        Returns:
            List of RobotInformation objects
        """
        corners, ids, _ = self.detect_markers(frame)
        robots = []

        if ids is not None and len(ids) > 0:
            poses = self.estimate_pose(corners, ids)

            for i, (rotation_vector, transition_vector) in enumerate(poses):
                # Apply smoothing for this specific marker
                marker_id = ids[i][0]
                smooth_rotation_vector, smooth_transition_vector = self.smooth_pose(marker_id, rotation_vector, transition_vector)
                

                # Convert to readable format
                x, y, z = smooth_transition_vector[0][0], smooth_transition_vector[1][0], smooth_transition_vector[2][0]
                roll, pitch, yaw = self.rotation_vector_to_euler(smooth_rotation_vector)
                robot_info = RobotInformation(
                    marker_id=marker_id,
                    position={"x": x, "y": y, "z": z},
                    rotation={"roll": roll, "pitch": pitch, "yaw": yaw},
                    distance=np.linalg.norm(smooth_transition_vector),
                    rotation_vector=smooth_rotation_vector,
                    transition_vector=smooth_transition_vector,
                )
                robots.append(robot_info)

        return robots
