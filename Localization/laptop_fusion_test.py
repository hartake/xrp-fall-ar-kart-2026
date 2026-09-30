"""
laptop_fusion_test.py

Standalone test version of laptop_fusion. Same EKF + AprilTag pipeline,
but no Godot integration — instead:

  - Opens a live OpenCV window showing the RTSP feed with AprilTag
    overlays (bounding box, ID, axes, distance)
  - Prints raw tag pose and fused EKF state side-by-side on every
    sensor packet
  - Optionally logs CSV: time, x_fused, y_fused, theta_fused,
                        x_tag, y_tag, theta_tag
  - No gamepad relay, no TCP video server, no JSON output

Use this for tuning the EKF, calibrating the camera-to-world transform,
verifying AprilTag detection rates, etc., without needing Godot or any
game scene running.

The XRP still needs to be running and broadcasting raw sensors — the
EKF has nothing to predict from otherwise.

Usage:
  python laptop_fusion_test.py [PI_IP]

Press ESC or 'q' in the video window to quit.
"""

import os
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
    "rtsp_transport;tcp"
    "|fflags;nobuffer"
    "|flags;low_delay"
    "|reorder_queue_size;0"
    "|max_delay;0"
    "|buffer_size;65536"
)
os.environ["OPENCV_LOG_LEVEL"] = "ERROR"
os.environ["AV_LOG_FORCE_NOCOLOR"] = "1"

import sys
import time
import math
import csv
import socket
import threading
from contextlib import contextmanager

import numpy as np
import cv2
from pupil_apriltags import Detector

from ackermann_simple_ekf import ackermann_simple_ekf

# ============================================================
# Config
# ============================================================
# PI_IP = sys.argv[1] if len(sys.argv) > 1 else "10.42.0.1"
PI_IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.4.95"
RTSP_URL = f"rtsp://{PI_IP}:8554/cam"

SENSOR_PORT = 5005

# Set to None to disable CSV logging. Otherwise: file path.
CSV_LOG_PATH = "fusion_log.csv"

# Apply camera correction to the EKF? Disable to compare encoder+IMU
# only against the raw tag pose, which is useful for debugging.
APPLY_CAMERA_CORRECTION = True

TAG_FRESHNESS_S = 0.2

# ----- Camera intrinsics -----
Camera_params = [1.26828550e+03, 1.25414697e+03, 3.61108041e+02, 2.97975274e+02]
TAG_SIZE_TAG3 = 0.06339
TAG_SIZE_TAG0 = 0.08584

POSITIVE_Y = 1
X_THRESHOLD = 0.4572
FAR_FROM_ORIGIN = 0

# ============================================================
# Frame transforms (from pi_detect_aprilTag.py)
# ============================================================
_theta_mount = np.radians(0)
s_theta = np.sin(_theta_mount); c_theta = np.cos(_theta_mount)
h1, l1, d = 0.03047, 0.007625, 0.02792
trans_R_C = np.array([
    [0,        1,         0,        0                          ],
    [s_theta,  0,         c_theta,  h1*c_theta + l1*s_theta    ],
    [c_theta,  0,        -s_theta, -d - h1*s_theta + l1*c_theta],
    [0,        0,         0,        1                          ],
])

h3_tag3, l3_tag3 = 0.02483, 0.02155
trans_H_A_tag3 = np.array([
    [1, 0, 0,   0  ], [0, 1, 0, -h3_tag3 ], [0, 0, 1, -l3_tag3 ], [0, 0, 0, 1],
])

h3_tag0, l3_tag0 = -0.0047, 0
trans_H_A_tag0 = np.array([
    [1, 0, 0,   0  ], [0, 1, 0, -h3_tag3 ], [0, 0, 1, -l3_tag3 ], [0, 0, 0, 1],
])

alpha_tag3 = np.radians(7)
c_alpha_tag3, s_alpha_tag3 = np.cos(alpha_tag3), np.sin(alpha_tag3)
l4_tag3 = 0.01895
trans_g_H_tag3 = np.array([
    [1, 0,        0,         0 ],
    [0, c_alpha_tag3, -s_alpha_tag3,    0 ],
    [0, s_alpha_tag3,  c_alpha_tag3,  -l4_tag3],
    [0, 0,        0,          1],
])

alpha_tag0 = np.radians(0)
c_alpha_tag0, s_alpha_tag0 = np.cos(alpha_tag0), np.sin(alpha_tag0)
l4_tag0 = 0.01187
trans_g_H_tag0 = np.array([
    [1, 0,        0,         0 ],
    [0, c_alpha_tag0, -s_alpha_tag0,    0 ],
    [0, s_alpha_tag0,  c_alpha_tag0,  -l4_tag0],
    [0, 0,        0,          1],
])

x1_tag3, y1_tag3, x2_tag3, y2_tag3 = -1, 1.524, 1, 1.524
x3_tag3, y3_tag3 = (x1_tag3+x2_tag3)/2, (y1_tag3+y2_tag3)/2
h4_tag3 = 0.352425
if x1_tag3 == x2_tag3:
    beta_tag3 = np.radians(90) if y1_tag3 > y2_tag3 else np.radians(270)
else:
    beta_tag3 = np.arctan((y1_tag3-y2_tag3)/(x2_tag3-x1_tag3))
    if x1_tag3 <= x2_tag3 and y1_tag3 >= y2_tag3: beta_tag3 = beta_tag3
    elif x1_tag3 > x2_tag3:             beta_tag3 = beta_tag3 + np.radians(180)
    else:                     beta_tag3 = beta_tag3 + np.radians(270)
c_beta_tag3, s_beta_tag3 = np.cos(beta_tag3), np.sin(beta_tag3)
trans_G_g_tag3 = np.array([
    [ c_beta_tag3, 0,  s_beta_tag3, x3_tag3],
    [-s_beta_tag3, 0,  c_beta_tag3, y3_tag3],
    [ 0,     -1,  0,      h4_tag3],
    [ 0,      0,  0,       1],
])
trans_G_A_tag3= trans_G_g_tag3 @ trans_g_H_tag3 @ trans_H_A_tag3
print(f"[Transforms] beta_tag3 = {math.degrees(beta_tag3):.1f}")

x1_tag0, y1_tag0, x2_tag0, y2_tag0 = 1.4144, 0, 0.4144, 0
x3_tag0, y3_tag0 = (x1_tag0+x2_tag0)/2, (y1_tag0+y2_tag0)/2
h4_tag0 = 0.352425
if x1_tag0 == x2_tag0:
    beta_tag0 = np.radians(90) if y1_tag0 > y2_tag0 else np.radians(270)
else:
    beta_tag0 = np.arctan((y1_tag0-y2_tag0)/(x2_tag0-x1_tag0))
    if x1_tag0 <= x2_tag0 and y1_tag0 >= y2_tag0: beta_tag0 = beta_tag0
    elif x1_tag0 > x2_tag0:             beta_tag0 = beta_tag0 + np.radians(180)
    else:                     beta_tag0 = beta_tag0 + np.radians(270)
c_beta_tag0, s_beta_tag0 = np.cos(beta_tag0), np.sin(beta_tag0)
trans_G_g_tag0 = np.array([
    [ c_beta_tag0, 0,  s_beta_tag0, x3_tag0],
    [-s_beta_tag0, 0,  c_beta_tag0, y3_tag0],
    [ 0,     -1,  0,      h4_tag0],
    [ 0,      0,  0,       1],
])
trans_G_A_tag0 = trans_G_g_tag0 @ trans_g_H_tag0 @ trans_H_A_tag0
print(f"[Transforms] beta_tag0 = {math.degrees(beta_tag0):.1f}")

# ============================================================
# EKF
# ============================================================
# WHEEL_RADIUS = 0.026
WHEEL_RADIUS = 0.052
TRACK_WIDTH  = 0.109
r_xy = 0.4
r_th = 5e-2
q_th = math.radians(1.0) ** 2

ekf = None
ekf_lock = threading.Lock()
theta_zero = None

# ============================================================
# Detector
# ============================================================
detector = Detector(families="tag36h11")

@contextmanager
def suppress_stderr():
    devnull = os.open(os.devnull, os.O_WRONLY)
    orig = os.dup(2)
    try:
        os.dup2(devnull, 2); yield
    finally:
        os.dup2(orig, 2); os.close(orig); os.close(devnull)

# ============================================================
# Shared state — between threads and the GUI
# ============================================================
latest_frame = None
latest_frame_id = 0
latest_frame_lock = threading.Lock()

# Detector publishes (annotated_frame, robot_pose_dict) so the GUI thread
# can show the same frame with overlays drawn on it.
latest_display_frame = None
display_frame_lock = threading.Lock()

latest_robot_pose_world = None
latest_robot_pose_lock = threading.Lock()

# Latest fused EKF state for the GUI overlay
latest_fused = None  # {"x": ..., "y": ..., "theta": ...}
latest_fused_lock = threading.Lock()

running = True


# ============================================================
# RTSP grabber thread
# ============================================================
def open_rtsp(url):
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 10000)
    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 10000)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def grabber_thread(rtsp_url):
    global latest_frame, latest_frame_id, running
    while running:
        print(f"[Camera] Connecting to {rtsp_url}")
        cap = open_rtsp(rtsp_url)
        if not cap.isOpened():
            print("[Camera] Failed to open, retrying in 3s...")
            cap.release(); time.sleep(3); continue
        print("[Camera] RTSP connected")
        fail = 0
        while running:
            ret, frame = cap.read()
            if not ret:
                fail += 1
                if fail > 10:
                    print("[Camera] Lost connection, reconnecting...")
                    break
                time.sleep(0.05); continue
            fail = 0
            with latest_frame_lock:
                latest_frame = frame
                latest_frame_id += 1
        cap.release()
        if running: time.sleep(2)


# ============================================================
# AprilTag axes drawing (from pi_detect_aprilTag.py)
# ============================================================
def draw_axes(frame, tag, camera_params, tag_size):
    fx, fy, cx, cy = camera_params
    axis = np.float32([
        [0, 0, 0],
        [tag_size, 0, 0],
        [0, tag_size, 0],
        [0, 0, -tag_size],
    ])
    R = tag.pose_R
    t = tag.pose_t
    pts = []
    for point in axis:
        world = R @ point + t.flatten()
        if abs(world[2]) < 1e-6:
            pts.append((0, 0))
            continue
        x = (world[0] / world[2]) * fx + cx
        y = (world[1] / world[2]) * fy + cy
        pts.append((int(x), int(y)))
    origin = pts[0]
    cv2.line(frame, origin, pts[1], (0, 0, 255), 2)   # X red
    cv2.line(frame, origin, pts[2], (0, 255, 0), 2)   # Y green
    cv2.line(frame, origin, pts[3], (255, 0, 0), 2)   # Z blue


# ============================================================
# Detection thread — draws overlays AND publishes pose
# ============================================================
def detector_thread():
    global latest_robot_pose_world, latest_display_frame, running, POSITIVE_Y, X_THRESHOLD, FAR_FROM_ORIGIN

    trans_C_A_tag3 = np.identity(4)
    trans_C_A_tag0 = np.identity(4)
    last_processed_id = -1
    fps_n = 0
    fps_t = time.time()

    while running:
        with latest_frame_lock:
            if latest_frame is None or latest_frame_id == last_processed_id:
                frame = None
            else:
                frame = latest_frame.copy()    # copy so we can draw on it
                last_processed_id = latest_frame_id

        if frame is None:
            time.sleep(0.005); continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        with suppress_stderr():

            if FAR_FROM_ORIGIN:
                results = detector.detect(
                    gray, estimate_tag_pose=True,
                    camera_params=Camera_params, tag_size=TAG_SIZE_TAG0,
                )
            else:
                results = detector.detect(
                    gray, estimate_tag_pose=True,
                    camera_params=Camera_params, tag_size=TAG_SIZE_TAG3,
                )

            # if POSITIVE_Y:
            #     results = detector.detect(
            #         gray, estimate_tag_pose=True,
            #         camera_params=Camera_params, tag_size=TAG_SIZE_TAG0,
            #     )
            # else:
            #     results = detector.detect(
            #         gray, estimate_tag_pose=True,
            #         camera_params=Camera_params, tag_size=TAG_SIZE_TAG3,
            #     )

        valid = [t for t in results if 0 <= t.tag_id <= 7]

        # Draw all valid detections on the display frame
        for tag in valid:
            corners = tag.corners.astype(int)
            for i in range(4):
                cv2.line(frame, tuple(corners[i]),
                         tuple(corners[(i+1) % 4]), (0, 255, 0), 2)
            center = tuple(tag.center.astype(int))
            cv2.circle(frame, center, 5, (0, 0, 255), -1)
            cv2.putText(frame, f"id {tag.tag_id}",
                        (center[0] + 10, center[1] - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            tz = tag.pose_t[2][0]
            cv2.putText(frame, f"{tz:.2f}m",
                        (center[0] + 10, center[1] + 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 2)
            draw_axes(frame, tag, Camera_params, TAG_SIZE_TAG3)

        # Pick the closest tag for pose update
        if valid:
            tag = min(valid, key=lambda t: abs(t.pose_t[2][0]))
            if tag.tag_id == 3:
                trans_C_A_tag3[:3, :3] = tag.pose_R
                trans_C_A_tag3[0, 3] = tag.pose_t[0][0]
                trans_C_A_tag3[1, 3] = tag.pose_t[1][0]
                trans_C_A_tag3[2, 3] = tag.pose_t[2][0]
                try:
                    trans_A_C_tag3 = np.linalg.inv(trans_C_A_tag3)
                    trans_R_G_tag3 = trans_G_A_tag3 @ trans_A_C_tag3 @ trans_R_C
                    r_x = float(trans_R_G_tag3[0, 3])
                    r_y = float(trans_R_G_tag3[1, 3])
                    r_theta = -(math.atan2(trans_R_G_tag3[1, 0], trans_R_G_tag3[0, 0]) - np.pi/2)
                    with latest_robot_pose_lock:
                        latest_robot_pose_world = {
                            "x": r_x, "y": r_y, "theta": r_theta,
                            "tag_id": tag.tag_id, "time": time.time(),
                        }
                except np.linalg.LinAlgError:
                    pass
            if tag.tag_id == 0:
                trans_C_A_tag0[:3, :3] = tag.pose_R
                trans_C_A_tag0[0, 3] = tag.pose_t[0][0]
                trans_C_A_tag0[1, 3] = tag.pose_t[1][0]
                trans_C_A_tag0[2, 3] = tag.pose_t[2][0]
                try:
                    trans_A_C_tag0 = np.linalg.inv(trans_C_A_tag0)
                    trans_R_G_tag0 = trans_G_A_tag0 @ trans_A_C_tag0 @ trans_R_C
                    r_x = float(trans_R_G_tag0[0, 3])
                    r_y = float(trans_R_G_tag0[1, 3])
                    r_theta = math.atan2(trans_R_G_tag0[1, 0], trans_R_G_tag0[0, 0])
                    with latest_robot_pose_lock:
                        latest_robot_pose_world = {
                            "x": r_x, "y": r_y, "theta": r_theta,
                            "tag_id": tag.tag_id, "time": time.time(),
                        }
                except np.linalg.LinAlgError:
                    pass

        # Publish the annotated frame for the GUI thread to show
        with display_frame_lock:
            latest_display_frame = frame

        fps_n += 1
        now = time.time()
        if now - fps_t >= 1.0:
            tag_str = "no tag"
            if latest_robot_pose_world:
                age = now - latest_robot_pose_world["time"]
                tag_str = f"tag {latest_robot_pose_world['tag_id']} ({'fresh' if age < TAG_FRESHNESS_S else f'STALE {age:.1f}s'})"
            print(f"[Detect] {fps_n} fps | {tag_str}")
            fps_n = 0; fps_t = now


# ============================================================
# GUI thread — shows the live annotated video
# OpenCV imshow MUST run on the main thread on macOS, so we'll call it
# from main() directly. This thread is here for non-mac platforms; on
# macOS we'll just have the main loop pull frames itself.
# ============================================================


# ============================================================
# Sensor loop + EKF (runs in main thread on non-mac, separate on mac)
# ============================================================
def sensor_ekf_thread(csv_writer):
    # global ekf, theta_zero, latest_fused, running, POSITIVE_Y
    global ekf, theta_zero, latest_fused, running, POSITIVE_Y, X_THRESHOLD, FAR_FROM_ORIGIN

    sensor_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sensor_sock.bind(("0.0.0.0", SENSOR_PORT))
    sensor_sock.settimeout(0.5)
    print(f"[Sensor] Listening for XRP raw sensors on UDP {SENSOR_PORT}")

    pkt_count = 0
    last_print = time.time()

    while running:
        try:
            data, addr = sensor_sock.recvfrom(256)
        except socket.timeout:
            continue

        try:
            text = data.decode("utf-8").strip()
        except UnicodeDecodeError:
            continue

        parts = text.split(",")
        if len(parts) != 4 or parts[0] != "RAW":
            continue
        try:
            enc_l = float(parts[1]); enc_r = float(parts[2]); heading = float(parts[3])
        except ValueError:
            continue

        if ekf is None:
            with ekf_lock:
                if ekf is None:
                    ekf = ackermann_simple_ekf(
                        wheel_radius=WHEEL_RADIUS,
                        distance_between_wheels=TRACK_WIDTH,
                        initial_position_uncertainty=r_xy,
                        initial_heading_uncertainty=r_th,
                        sensor_uncertainty=q_th,
                        initial_left_encoder_value=enc_l,
                        initial_right_encoder_value=enc_r,
                        initial_x=0.0, initial_y=0.0, initial_theta=np.pi/2,
                    )
                    theta_zero = heading
                    print(f"[EKF] Initialized at heading={heading:.3f} rad from {addr[0]}")
            continue

        with ekf_lock:
            x_pred, y_pred, th_pred = ekf.predict(enc_l, enc_r)
            z_theta = heading - theta_zero
            x, y, theta = ekf.correct(x_pred, y_pred, th_pred, z_theta)

            # if y >= 0:
            #     POSITIVE_Y = 1
            # else:
            #     POSITIVE_Y = 0

            if x >= X_THRESHOLD:
                FAR_FROM_ORIGIN = 1
            else:
                FAR_FROM_ORIGIN = 0

            with latest_robot_pose_lock:
                pose_w = latest_robot_pose_world
            tag_is_fresh = (
                pose_w is not None
                and (time.time() - pose_w["time"]) < TAG_FRESHNESS_S
            )

            if tag_is_fresh and APPLY_CAMERA_CORRECTION:
                try:
                    x, y, theta = ekf.camera_correction(
                        x_pred, y_pred, th_pred,
                        pose_w["x"], pose_w["y"], pose_w["theta"],
                    )

                    # if y >= 0:
                    #     POSITIVE_Y = 1
                    # else:
                    #     POSITIVE_Y = 0

                    if x >= X_THRESHOLD:
                        FAR_FROM_ORIGIN = 1
                    else:
                        FAR_FROM_ORIGIN = 0

                except Exception as e:
                    print(f"[EKF] camera_correction error: {e}")

        with latest_fused_lock:
            latest_fused = {"x": x, "y": y, "theta": theta}

        # CSV log
        if csv_writer is not None:
            tag_x = pose_w["x"] if (pose_w and tag_is_fresh) else ""
            tag_y = pose_w["y"] if (pose_w and tag_is_fresh) else ""
            tag_th = pose_w["theta"] if (pose_w and tag_is_fresh) else ""
            csv_writer.writerow([
                time.time(), x, y, theta, tag_x, tag_y, tag_th
            ])

        pkt_count += 1
        now = time.time()
        if now - last_print >= 1.0:
            if pose_w is None:
                tag_info = "no tag yet"
            else:
                age = time.time() - pose_w["time"]
                if age < TAG_FRESHNESS_S:
                    tag_info = (f"tag x={pose_w['x']:+.2f} y={pose_w['y']:+.2f} "
                                f"th={math.degrees(pose_w['theta']):+.0f} ({age*1000:.0f}ms)")
                else:
                    tag_info = f"tag stale {age:.1f}s"
            print(f"[EKF] {pkt_count} pkts/s | "
                  f"FUSED x={x:+.2f} y={y:+.2f} th={math.degrees(theta):+.0f} | {tag_info}")
            pkt_count = 0; last_print = now

    sensor_sock.close()


# ============================================================
# Main — handles GUI on the main thread (required on macOS)
# ============================================================
def main():
    global running

    # CSV log
    csv_file = None
    csv_writer = None
    if CSV_LOG_PATH:
        csv_file = open(CSV_LOG_PATH, "w", newline="")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow([
            "time", "x_fused", "y_fused", "theta_fused",
            "x_tag", "y_tag", "theta_tag",
        ])
        print(f"[CSV] Logging to {CSV_LOG_PATH}")

    threading.Thread(target=grabber_thread, args=(RTSP_URL,), daemon=True).start()
    threading.Thread(target=detector_thread, daemon=True).start()
    threading.Thread(target=sensor_ekf_thread, args=(csv_writer,), daemon=True).start()

    print("[GUI] Press ESC or 'q' in the video window to quit")
    cv2.namedWindow("AprilTag Test", cv2.WINDOW_AUTOSIZE)

    try:
        while running:
            with display_frame_lock:
                frame = None if latest_display_frame is None else latest_display_frame.copy()

            if frame is not None:
                # Overlay fused EKF state in the corner
                with latest_fused_lock:
                    fused = latest_fused
                if fused is not None:
                    txt = (f"FUSED  x={fused['x']:+.2f}m  y={fused['y']:+.2f}m  "
                           f"th={math.degrees(fused['theta']):+.0f}")
                    cv2.putText(frame, txt, (10, 25),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                else:
                    cv2.putText(frame, "Waiting for XRP sensors...", (10, 25),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 2)

                with latest_robot_pose_lock:
                    pose_w = latest_robot_pose_world
                if pose_w is not None:
                    age = time.time() - pose_w["time"]
                    color = (0, 255, 0) if age < TAG_FRESHNESS_S else (128, 128, 128)
                    txt = (f"TAG{pose_w['tag_id']}  x={pose_w['x']:+.2f}  "
                           f"y={pose_w['y']:+.2f}  th={math.degrees(pose_w['theta']):+.0f}  "
                           f"({age*1000:.0f}ms)")
                    cv2.putText(frame, txt, (10, 50),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

                cv2.imshow("AprilTag Test", frame)

            key = cv2.waitKey(15) & 0xFF
            if key == 27 or key == ord('q'):
                break

    except KeyboardInterrupt:
        print("\n[Main] Interrupted")
    finally:
        running = False
        time.sleep(0.3)
        cv2.destroyAllWindows()
        if csv_file is not None:
            csv_file.close()
            print(f"[CSV] Saved to {CSV_LOG_PATH}")


if __name__ == "__main__":
    main()
