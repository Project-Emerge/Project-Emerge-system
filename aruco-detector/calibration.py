import argparse
import os
import cv2
import numpy as np

def main():
    parser = argparse.ArgumentParser(description="Camera Calibration Script")
    parser.add_argument("--device", type=int, default=1, help="Camera device ID (default: 1)")
    parser.add_argument("--width", type=int, default=640, help="Camera frame width (default: 640)")
    parser.add_argument("--height", type=int, default=480, help="Camera frame height (default: 480)")
    parser.add_argument("--rows", type=int, default=6, help="Number of inner corners in chessboard rows (default: 6)")
    parser.add_argument("--cols", type=int, default=9, help="Number of inner corners in chessboard columns (default: 9)")
    parser.add_argument("--square-size", type=float, default=0.025, help="Size of a chessboard square in meters (default: 0.025)")
    
    args = parser.parse_args()

    # Termination criteria for subpixel corner refinement
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)

    # Prepare object points (0,0,0), (1,0,0), (2,0,0) ...., (cols-1, rows-1, 0)
    objp = np.zeros((args.rows * args.cols, 3), np.float32)
    objp[:, :2] = np.mgrid[0:args.cols, 0:args.rows].T.reshape(-1, 2)
    objp *= args.square_size

    # Arrays to store object points and image points from all the images.
    objpoints = []  # 3d point in real world space
    imgpoints = []  # 2d points in image plane.

    cap = cv2.VideoCapture(args.device)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)

    # Check actual resolution
    actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"Camera opened with resolution: {actual_width}x{actual_height}")
    print("\n=== Camera Calibration Instructions ===")
    print(f"1. Print or display a chessboard with {args.cols}x{args.rows} inner corners.")
    print("2. Move the chessboard in front of the camera, showing it from different angles, distances, and positions (especially edges).")
    print("3. Press 'c' or SPACE to capture a frame (it will highlight green if detected).")
    print("4. Aim for at least 15-20 successful captures.")
    print("5. Press 'g' to run the calibration and save the results.")
    print("6. Press 'q' to quit at any time without saving.\n")

    captured_count = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Failed to grab frame!")
            break

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        # Display clone to draw overlays on
        display_frame = frame.copy()

        # Find the chess board corners
        ret_corners, corners = cv2.findChessboardCorners(gray, (args.cols, args.rows), None)

        # If found, draw them
        if ret_corners:
            cv2.drawChessboardCorners(display_frame, (args.cols, args.rows), corners, ret_corners)
            cv2.putText(display_frame, "Chessboard Detected! Press 'c' to capture.", (20, 30), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        else:
            cv2.putText(display_frame, "Searching for Chessboard...", (20, 30), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

        # Draw capture stats
        cv2.putText(display_frame, f"Captures: {captured_count}/15+", (20, args.height - 20), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)

        cv2.imshow('Calibration Script', display_frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            print("Exiting without saving.")
            break
        elif (key == ord('c') or key == ord(' ')) and ret_corners:
            # Add object points and image points after refining corner locations
            objpoints.append(objp)
            
            # Subpixel refinement
            corners2 = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)
            imgpoints.append(corners2)
            
            captured_count += 1
            print(f"[{captured_count}] Captured successful chessboard frame!")
            
            # Brief visual confirmation flash
            flash = display_frame.copy()
            cv2.rectangle(flash, (0, 0), (args.width, args.height), (0, 255, 0), 10)
            cv2.imshow('Calibration Script', flash)
            cv2.waitKey(100)
            
        elif key == ord('g'):
            if captured_count < 10:
                print(f"Warning: Only {captured_count} frames captured. It is highly recommended to capture at least 15 frames for good results.")
                print("Are you sure you want to calibrate? Press 'g' again to force, or 'c' to keep capturing.")
                # Wait for next key press
                while True:
                    next_key = cv2.waitKey(0) & 0xFF
                    if next_key == ord('g') or next_key == ord('q') or next_key == ord('c') or next_key == ord(' '):
                        key = next_key
                        break
                if key == ord('q'):
                    print("Exiting.")
                    break
                elif key == ord('c') or key == ord(' '):
                    print("Continuing capture...")
                    continue
            
            print("\nCalculating camera calibration parameters... Please wait...")
            # Calibrate camera
            ret_val, camera_matrix, dist_coeffs, rvecs, tvecs = cv2.calibrateCamera(
                objpoints, imgpoints, gray.shape[::-1], None, None
            )
            
            if ret_val:
                print("\n=== Calibration Successful! ===")
                print(f"Reprojection RMS Error: {ret_val:.4f} pixels (lower is better, ideally < 0.5)")
                print("\nCamera Matrix:")
                print(camera_matrix)
                print("\nDistortion Coefficients:")
                print(dist_coeffs.ravel())
                
                # Save calibration parameters
                filename = 'camera_calibration.npz'
                np.savez(filename, 
                         camera_matrix=camera_matrix, 
                         dist_coeffs=dist_coeffs, 
                         rms_error=ret_val,
                         resolution=(actual_width, actual_height))
                print(f"\nCalibration data saved to: {os.path.abspath(filename)}")
                print("You can now run main.py and it will automatically load this file!")
            else:
                print("Error: Calibration failed!")
            break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
