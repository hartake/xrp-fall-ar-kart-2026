import time, math, network, socket, json
from XRPLib.defaults import *
from ackermann_simple_ekf import ackermann_simple_ekf

# -------- UDP / Network Configuration --------

WIFI_SSID = "RedRover"
WIFI_PASSWORD = ""

# Laptop IP address (same network as Pico)
LAPTOP_IP = "10.49.19.31"

# UDP ports (must match laptop pygame joystick script)
PICO_UDP_PORT = 5005      # Pico listens here for JOY,x,y
LAPTOP_UDP_PORT = 5006    # Laptop listens here for TEL,x,y,theta

# -------- Joystick -> Robot Mapping --------

# Map joystick Y in [-1,1] to wheel speed in rpm (same for left & right)
# MAX_DRIVE_RPM = 90.0
MAX_DRIVE_RPM = 200
JOYSTICK_DEADBAND = 0.05

# Map joystick X in [-1,1] to steering servo angle in degrees
# Servo supports roughly [0, 200]; treat 100 as center.
STEER_CENTER_DEG = 100.0
STEER_RANGE_DEG = 60.0   # +-60 degrees from center


def connect_wifi(ssid: str, password: str, timeout_s: int = 20):
    """
    Connect to the given WiFi network.
    Returns the active WLAN interface on success, or None on failure.
    """
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if not wlan.isconnected():
        print("Connecting to WiFi...")
        wlan.connect(ssid, password)

        start = time.time()
        while not wlan.isconnected():
            if time.time() - start > timeout_s:
                print("Failed to connect to WiFi (timeout).")
                return None
            print(".", end="")
            time.sleep(0.5)
        print()  # newline after dots

    print("Connected to WiFi")
    print("IP address:", wlan.ifconfig()[0])
    return wlan
    
def create_udp_socket(target_ip: str, target_port: int):
    """
    Create and return a UDP socket (bound for receive) and target address tuple.
    """
    addr = (target_ip, target_port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # Bind so we can receive joystick packets from the laptop
    sock.bind(("0.0.0.0", PICO_UDP_PORT))
    sock.settimeout(0.01)  # small timeout for non-blocking-style loop
    return sock, addr

# -------- Helpers --------
def wrap_pi(a):
    return (a + math.pi) % (2.0 * math.pi) - math.pi

def clamp(v, lo, hi):
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v

# -------- Robot Physical Constants --------
radius = 0.026      # wheel radius in meters
B = 0.109           # track width in meters

# -------- EKF Tuning --------
# r_xy = 5e-3
# r_th = 2e-4
r_xy = 5e-2
r_th = 5e-2

q_xy = 0
q_th = math.radians(1.0) ** 2 # very large sensor uncertainty

# Describes Model Uncertainty
R = [
    [r_xy, 0.0,  0.0],
    [0.0,  r_xy, 0.0],
    [0.0,  0.0,  r_th],
]

# Describes Sensor Uncertainty (we are assuming that we have complete no sensor uncertainty on x and y because we "can't" observe them)
#                                 this may be a bad idea
Q = q_th

# Assuming that IMU can give us theta, and provides no new information on x and y
C = [0, 0, 1]
    
# -------- Anti-jump Guards --------
DT_MAX = 0.12
MAX_ROT_STEP = 0.5
MAX_GYRO_DPS = 300.0
MAX_INNOV = math.radians(30)
MAX_XY_CORR = 0.002

# -------- Gyro Config --------
AXIS = "y"
RATE_EPS = 0.2
bias_beta = 0.002
HEADING_OFFSET = 0.0
HEADING_SIGN = 1.0

def gyro_rate_dps():
    if AXIS == "x": return imu.get_gyro_x_rate() / 1000.0
    if AXIS == "y": return imu.get_gyro_y_rate() / 1000.0
    return imu.get_gyro_z_rate() / 1000.0

def calibrate_bias(seconds=3.0):
    print("Calibrating gyro bias...")
    t0 = time.ticks_ms()
    s = 0.0
    n = 0
    while time.ticks_diff(time.ticks_ms(), t0) < int(seconds * 1000):
        s += gyro_rate_dps()
        n += 1
        time.sleep(0.01)
    b = s / max(n, 1)
    print("gyro_bias =", b, "deg/s")
    return b

def imu_heading_rad():
    roll_deg = imu.get_roll()
    return wrap_pi(HEADING_SIGN * math.radians(roll_deg) + HEADING_OFFSET)

# -------- Init --------
print("starting default calibration")
imu.calibrate(3, 0)
print("ending default calibration")
gyro_bias = calibrate_bias(3.0)

x, y, theta = 0.0, 0.0, 0.0

# Initialize theta from IMU
theta = imu_heading_rad()
theta_0 = theta

theta = theta - theta_0

motor_three.reset_encoder_position()
motor_four.reset_encoder_position()
last_l = motor_three.get_position()
last_r = motor_four.get_position()

motor_three.reset_encoder_position()
motor_four.reset_encoder_position()

last_t = time.ticks_ms()
time.sleep(0.05)

myEKF = ackermann_simple_ekf(radius, B, r_xy, r_th, q_th, last_l, last_r, x, y, theta)

print("Streaming EKF data with joystick UDP I/O... (Ctrl+C to stop)")
 
def main():
    
    global last_t, last_l, last_r
    global gyro_bias
    global x, y, theta
    global Q
    global C
    global R

    wlan = connect_wifi(WIFI_SSID, WIFI_PASSWORD)
    if wlan is None:
        print("Cannot continue without WiFi. Reset the board to retry.")
        return

    # Single UDP socket used both to receive joystick data and send EKF state
    sock, laptop_addr = create_udp_socket(LAPTOP_IP, LAPTOP_UDP_PORT)

    print("\nUDP EKF loop ready.")
    print("  Listening for JOY,x,y on port", PICO_UDP_PORT)
    print("  Sending TEL,x,y,theta to {}:{}\n".format(LAPTOP_IP, LAPTOP_UDP_PORT))

    # Last joystick command (hold previous value between packets)
    joy_x = 0.0
    joy_y = 0.0

    while True:
        try:
            # ---- Receive joystick data (if any) ----
            try:
                raw, from_addr = sock.recvfrom(128)
            except OSError:
                raw = None

            if raw:
                print("Data sent")
                try:
                    text = raw.decode("utf-8").strip()
                except UnicodeDecodeError:
                    text = ""
                parts = text.split(",")
                if len(parts) == 3 and parts[0] == "JOY":
                    try:
                        joy_x = -float(parts[1])
                        joy_y = float(parts[2])
                        print("JOY from {}: X={:+.2f}, Y={:+.2f}".format(from_addr[0], joy_x, joy_y))
                    except ValueError:
                        print("Received malformed JOY packet:", text)
                else:
                    # Ignore unrelated packets
                    pass
            
            # ---- Update dt ----
            now = time.ticks_ms()
            dt = time.ticks_diff(now, last_t) / 1000.0
            last_t = now
        
            # Guard against stalls
            if dt <= 0 or dt > DT_MAX:
                continue
            
            # ---- Gyro bias tracking ----
            rate = clamp(gyro_rate_dps() - gyro_bias, -MAX_GYRO_DPS, MAX_GYRO_DPS)
            if abs(rate - gyro_bias) < RATE_EPS:
                gyro_bias = (1.0 - bias_beta) * gyro_bias + bias_beta * rate
        
        
            # ---- PREDICTION STEP ----
            l = motor_three.get_position() + last_l
            r = motor_four.get_position() + last_r
            motor_three.reset_encoder_position()
            motor_four.reset_encoder_position()
            last_l = l
            last_r = r
            
            print("Last L: " + str(last_l))
            print("Last R: " + str(last_r))

            x_pred, y_pred, theta_pred = myEKF.predict(l, r)
            
            # ---- CORRECTION STEP ----
            
            z_theta = imu_heading_rad() - theta_0
            x, y, theta = myEKF.correct(x_pred, y_pred, theta_pred, z_theta)
            
            Epsilon = myEKF.get_uncertainty()
            
            # print("x: " + str(x))
            # print("y: " + str(y))
            # print("theta: " + str(theta))
            
            # ---- Map joystick to motors and steering (Ackermann control) ----
            # Forward/reverse speed from joystick Y
    
            if abs(joy_y) < JOYSTICK_DEADBAND:
                motor_three.set_speed(0.0)
                motor_four.set_speed(0.0)
            else:
                target_rpm = clamp(joy_y, -1.0, 1.0) * MAX_DRIVE_RPM
                motor_three.set_speed(target_rpm)
                motor_four.set_speed(target_rpm)

            # Steering from joystick X
            steer_norm = clamp(joy_x, -1.0, 1.0)
            steer_angle = STEER_CENTER_DEG + steer_norm * STEER_RANGE_DEG
            steer_angle = clamp(steer_angle, 0.0, 200.0) # need to update this range
            servo_three.set_angle(steer_angle)
            
            # ---- Send EKF state back to laptop over UDP ----
            # msg = "TEL,{:.3f},{:.3f},{:.3f}".format(x, y, theta)
            msg = "TEL,{:.3f},{:.3f},{:.3f},{:.3f},{:.3f},{:.3f}".format(x, y, theta, Epsilon[0][0], Epsilon[1][1], Epsilon[2][2])
            try:
                sock.sendto(msg.encode("utf-8"), laptop_addr)
            except OSError:
                # Ignore transient send errors
                pass

            time.sleep(0.01)
        except KeyboardInterrupt:
            print("\nStopping UDP EKF loop.")
            break
        except Exception as e:
            # Keep running on transient errors
            print("Error in EKF loop:", e)

    sock.close()
    
if __name__ == "__main__":
    main()
