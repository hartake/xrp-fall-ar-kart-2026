import cv2
import numpy as np

# ---------------- CAMERA / BALL CONSTANTS ----------------

BALL_DIAMETER_MM = 67.89

# camera_matrix = np.array([
#     [1.41722835e+03, 0.00000000e+00, 9.48431225e+02],  # [fx,  0, cx]  (Example values for a 640x480 frame)
#     [0.00000000e+00, 1.42951113e+03, 5.13535423e+02],  # [ 0, fy, cy]
#     [0.00000000e+00, 0.00000000e+00, 1.00000000e+00]   # [ 0,  0,  1]
# ])

camera_matrix = np.array([
    [1.37839461e+03,   0.0, 9.87902678e+02],  # [fx,  0, cx]  (Example values for a 640x480 frame)
    [  0.0, 1.37803661e+03, 4.54808963e+02],  # [ 0, fy, cy]
    [  0.0,   0.0,   1.0]                     # [ 0,  0,  1]
])

distortion_matrix = np.array([[-0.3525383, 0.08318022, 0.00181599, -0.00182542, 0.02471334]])

fx = camera_matrix[0, 0]
fy = camera_matrix[1, 1]
cx = camera_matrix[0, 2]
cy = camera_matrix[1, 2]


# ---------------- CAMERA GLOBAL POSE ----------------

# Replace these with your actual camera pose
#R_camera_to_world = np.eye(3)
R_camera_to_world = np.array([
    [1,  0,  0],
    [0, -1,  0],
    [0,  0, -1]
])

T_camera_to_world = np.array([
    [0.0],
    [-609.6],
    [-2451.1]
])


class PositionSmoother:
    """Lightweight constant-velocity Kalman filter for a 2D (X, Y) point."""

    def __init__(self, process_var=25.0, meas_var=9.0):
        self.kf = cv2.KalmanFilter(4, 2)
        self.kf.transitionMatrix = np.array([
            [1, 0, 1, 0],
            [0, 1, 0, 1],
            [0, 0, 1, 0],
            [0, 0, 0, 1]
        ], dtype=np.float32)
        self.kf.measurementMatrix = np.array([
            [1, 0, 0, 0],
            [0, 1, 0, 0]
        ], dtype=np.float32)
        self.kf.processNoiseCov = np.eye(4, dtype=np.float32) * process_var
        self.kf.measurementNoiseCov = np.eye(2, dtype=np.float32) * meas_var
        self.initialized = False

    def update(self, x, y):
        measurement = np.array([[np.float32(x)], [np.float32(y)]])
        if not self.initialized:
            self.kf.statePre = np.array([[x], [y], [0], [0]], dtype=np.float32)
            self.kf.statePost = np.array([[x], [y], [0], [0]], dtype=np.float32)
            self.initialized = True
        self.kf.predict()
        corrected = self.kf.correct(measurement)
        return float(corrected[0, 0]), float(corrected[1, 0])


smoother = PositionSmoother()

# T_camera_to_world = np.array([
#     [0.0],
#     [609.6],
#     [-2281.1]
# ])

# T_camera_to_world = np.array([
#     [0.0],
#     [609.6],
#     [-0]
# ])


# ---------------- COLOR DETECTION ----------------

# lower_color = np.array([29, 86, 6])
# upper_color = np.array([64, 255, 255])

#better color range

# # green
# lower_color = np.array([25, 30, 120])
# upper_color = np.array([50, 220, 255])

# green
lower_color = np.array([160, 40, 20])
upper_color = np.array([179, 255, 255])

cap = cv2.VideoCapture(0)


while True:

    ret, frame = cap.read()

    if not ret:
        break

    h, w = frame.shape[:2]

    new_camera_matrix, roi = cv2.getOptimalNewCameraMatrix(camera_matrix, distortion_matrix, (w, h), 1, (w, h))

    # Undistort using the new matrix
    undistorted = cv2.undistort(frame, camera_matrix, distortion_matrix, None, new_camera_matrix)

    # Optional: Crop the image based on the Region of Interest (ROI)
    x, y, w, h = roi
    undistorted = undistorted[y:y+h, x:x+w]

    # undistorted = cv2.undistort(frame, camera_matrix, distortion_matrix)


    # Blur image
    blurred = cv2.GaussianBlur(frame, (11, 11), 0)

    # # Blur image
    # blurred = cv2.GaussianBlur(undistorted, (11, 11), 0)

    # Convert to HSV
    hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)

    # Isolate ball color
    mask = cv2.inRange(
        hsv,
        lower_color,
        upper_color
    )

    # Remove noise
    mask = cv2.erode(mask, None, iterations=2)
    mask = cv2.dilate(mask, None, iterations=2)

    # Find contours
    contours, _ = cv2.findContours(
        mask.copy(),
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE
    )


    if len(contours) > 0:

        largest_contour = max(contours, key=cv2.contourArea)

        ((x, y), radius) = cv2.minEnclosingCircle(largest_contour)

        M = cv2.moments(largest_contour)

        # print("Radius: " + str(radius))


        if radius > 10 and M["m00"] != 0:

            # --------------------------------
            # BALL LOCATION IN IMAGE
            # --------------------------------

            pixel_center_u = x
            pixel_center_v = y

            pixel_diameter = radius * 2

            # print("Pixel diameter: " + str(pixel_diameter))


            # --------------------------------
            # ESTIMATE CAMERA-RELATIVE POSITION
            # --------------------------------

            # r = BALL_DIAMETER_MM / 2
            # p = pixel_diameter / 2

            # Z_c = np.sqrt((fx * r / p)**2 + r**2)

            Z_c = (
                BALL_DIAMETER_MM * fx
            ) / pixel_diameter

            # Z_c = -2451.1 - 116.56

            # Z_c = BALL_DIAMETER_MM/2

            X_c = (
                (pixel_center_u - cx)
                * Z_c
            ) / fx

            Y_c = (
                (pixel_center_v - cy)
                * Z_c
            ) / fy


            camera_coords = np.array([
                [X_c],
                [Y_c],
                [Z_c]
            ])

            # print(camera_coords)

            # --------------------------------
            # CONVERT TO GLOBAL COORDINATES
            # --------------------------------

            global_coords = (
                R_camera_to_world @ camera_coords
                + T_camera_to_world
            )

            print(
                f"Camera Ball Position: "
                f"X={X_c:.1f} m, "
                f"Y={Y_c:.1f} m, "
                f"Z={Z_c:.1f} m"
            )

            # global_coords = (camera_coords)

            # print("Camera Z: " + str(global_coords[2, 0]))

            X_global = global_coords[0, 0]
            Y_global = global_coords[1, 0]
            Z_global = global_coords[2, 0]

            # print(
            #     f"Global Ball Position: "
            #     f"X={X_global:.1f} mm, "
            #     f"Y={Y_global:.1f} mm, "
            #     f"Z={Z_global:.1f} mm"
            # )

            # X_global_in = X_global/25.4
            # Y_global_in = Y_global/25.4
            # Z_global_in = Z_global/25.4

            # Smooth the noisy per-frame estimate.
            X_smooth, Y_smooth = smoother.update(X_global, Y_global)

            X_global_in = X_smooth / 25.4
            Y_global_in = Y_smooth / 25.4
            Z_global_in = Z_global / 25.4

            # print(
            #     f"Global Ball Position: "
            #     f"X={X_global_in:.1f} in, "
            #     f"Y={Y_global_in:.1f} in, "
            #     f"Z={Z_global_in:.1f} in"
            # )

            # print(
            #     f"Camera Z={Z_global_in:.1f} in, "
            #     f"Pixel Diameter={pixel_diameter:.1f} pixels"
            # )

            # --------------------------------
            # DRAW DETECTION
            # --------------------------------

            center = (
                int(pixel_center_u),
                int(pixel_center_v)
            )

            cv2.circle(
                frame,
                center,
                int(radius),
                (0, 255, 255),
                2
            )

            cv2.circle(
                frame,
                center,
                5,
                (0, 0, 255),
                -1
            )

            # Show global coordinates on image
            text = (
                f"X:{X_global:.0f} "
                f"Y:{Y_global:.0f} "
                f"Z:{Z_global:.0f} mm"
            )

            cv2.putText(
                frame,
                text,
                (20, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 255),
                2
            )

    cv2.imshow("Ball Tracking", frame)

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()