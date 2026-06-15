import math

import cv2
import numpy as np
import time


class OneEuroFilter:
    """One Euro Filter implementation for real-time smoothing with low lag.
    Reference: Casiez et al. 2012.
    """
    def __init__(self, min_cutoff=1.0, beta=0.0, d_cutoff=1.0):
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self.x_prev = None
        self.dx_prev = 0.0
        self.t_prev = None

    def _alpha(self, dt, cutoff):
        if cutoff <= 0.0:
            return 1.0
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt) if dt > 0 else 1.0

    def __call__(self, x, t=None):
        if t is None:
            t = time.time()
        if self.x_prev is None:
            # First sample initializes state
            self.x_prev = x
            self.t_prev = t
            return x
        dt = t - self.t_prev
        if dt <= 0:
            dt = 1e-6
        # Derivative of the signal
        dx = (x - self.x_prev) / dt
        # Smooth derivative
        alpha_d = self._alpha(dt, self.d_cutoff)
        dx_hat = alpha_d * dx + (1 - alpha_d) * self.dx_prev
        # Dynamic cutoff
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        # Smooth signal
        alpha = self._alpha(dt, cutoff)
        x_hat = alpha * x + (1 - alpha) * self.x_prev
        # Update state
        self.x_prev = x_hat
        self.dx_prev = dx_hat
        self.t_prev = t
        return x_hat


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
    def __init__(self, camera_matrix, distorsion_coefficients, marker_size=0.05, smooting_history=5,
                 position_min_cutoff=0.5, position_beta=0.01, position_d_cutoff=5.0,
                 yaw_min_cutoff=0.5, yaw_beta=0.01, yaw_d_cutoff=5.0):
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
        # Enable subpixel refinement for much better corner precision and pose stability
        self.aruco_params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        self.detector = cv2.aruco.ArucoDetector(self.aruco_dict, self.aruco_params)

        # For smoothing pose estimates - separate history for each robot
        self.pose_histories = {}  # Dictionary with marker_id as key
        self.yaw_histories = {}
        self.max_history = smooting_history
        # One Euro filter parameter storage
        self.position_filter_params = (position_min_cutoff, position_beta, position_d_cutoff)
        self.yaw_filter_params = (yaw_min_cutoff, yaw_beta, yaw_d_cutoff)
        # Per-marker filters
        self.position_filters = {}  # marker_id -> [fx, fy, fz]
        self.yaw_filters = {}       # marker_id -> filter for unwrapped yaw
        self._yaw_last_unwrapped = {}  # marker_id -> last unwrapped yaw (deg)

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

    def rotation_vector_to_yaw(self, rotation_vector):
        """
        Extract only yaw (rotation around the Z axis) from a Rodrigues rotation vector.
        Returns yaw in degrees.
        """
        R, _ = cv2.Rodrigues(rotation_vector)
        yaw = math.degrees(math.atan2(R[1, 0], R[0, 0]))  # rotation around Z
        return yaw

    def _wrap_angle(self, angle):
        """Wrap angle to [-180, 180)."""
        return ((angle + 180.0) % 360.0) - 180.0

    def smooth_yaw(self, marker_id, yaw, timestamp=None):
        """One Euro filter based yaw smoothing with angle unwrapping.
        Steps:
          1. Unwrap new yaw vs previous to maintain continuity.
          2. Apply One Euro filter on unwrapped yaw.
          3. Wrap back to [-180,180) for output.
        """
        if marker_id not in self.yaw_filters:
            min_c, beta, d_c = self.yaw_filter_params
            self.yaw_filters[marker_id] = OneEuroFilter(min_cutoff=min_c, beta=beta, d_cutoff=d_c)
            self._yaw_last_unwrapped[marker_id] = yaw
            # First sample just returns wrapped yaw
            return self._wrap_angle(yaw)
        # Unwrap
        prev_unwrapped = self._yaw_last_unwrapped[marker_id]
        raw_wrapped = self._wrap_angle(yaw)
        prev_wrapped = self._wrap_angle(prev_unwrapped)
        delta = raw_wrapped - prev_wrapped
        if delta > 180:
            delta -= 360
        elif delta < -180:
            delta += 360
        unwrapped = prev_unwrapped + delta
        # Filter
        filtered_unwrapped = self.yaw_filters[marker_id](unwrapped, timestamp)
        self._yaw_last_unwrapped[marker_id] = unwrapped
        return self._wrap_angle(filtered_unwrapped)

    def smooth_pose(self, marker_id, translation_vector, timestamp=None):
        """Apply One Euro filter to translation vector (x,y,z)."""
        # translation_vector shape (3,1) or (3,) expected
        vec = translation_vector.reshape(3)
        if marker_id not in self.position_filters:
            min_c, beta, d_c = self.position_filter_params
            self.position_filters[marker_id] = [
                OneEuroFilter(min_cutoff=min_c, beta=beta, d_cutoff=d_c),
                OneEuroFilter(min_cutoff=min_c, beta=beta, d_cutoff=d_c),
                OneEuroFilter(min_cutoff=min_c, beta=beta, d_cutoff=d_c),
            ]
        filters = self.position_filters[marker_id]
        t = timestamp if timestamp is not None else None
        smoothed = np.array([
            filters[0](float(vec[0]), t),
            filters[1](float(vec[1]), t),
            filters[2](float(vec[2]), t),
        ], dtype=np.float32).reshape(3, 1)
        return smoothed

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

        return result_frame

    def draw_last_pose(self, frame):
        """
        Draw the most recently detected poses on the frame without re-detecting them.
        """
        if hasattr(self, 'last_corners') and self.last_ids is not None and len(self.last_ids) > 0:
            return self.draw_pose_info(frame, self.last_corners, self.last_ids, getattr(self, 'last_poses', []))
        return frame

    def get_robot_poses(self, frame):
        """
        Main function to get all robot poses from camera frame.

        Args:
            frame: Camera frame

        Returns:
            List of RobotInformation objects
        """
        corners, ids, _ = self.detect_markers(frame)
        
        # Store for debug drawing to avoid redundant detection
        self.last_corners = corners
        self.last_ids = ids
        self.last_poses = []
        
        robots = []
        if ids is not None and len(ids) > 0:
            poses = self.estimate_pose(corners, ids)
            self.last_poses = poses
            current_time = time.time()
            for i, (rotation_vector, transition_vector) in enumerate(poses):
                marker_id = ids[i][0]
                smooth_transition_vector = self.smooth_pose(marker_id, transition_vector, current_time)
                x, y, z = smooth_transition_vector[0][0], smooth_transition_vector[1][0], smooth_transition_vector[2][0]
                yaw = self.rotation_vector_to_yaw(rotation_vector)
                yaw = self.smooth_yaw(marker_id, yaw, current_time)
                robot_info = RobotInformation(
                    marker_id=marker_id,
                    position={"x": x, "y": y, "z": z},
                    rotation={"roll": 0.0, "pitch": 0.0, "yaw": yaw},
                    distance=np.linalg.norm(smooth_transition_vector),
                    rotation_vector=rotation_vector,
                    transition_vector=smooth_transition_vector,
                )
                robots.append(robot_info)
        return robots
