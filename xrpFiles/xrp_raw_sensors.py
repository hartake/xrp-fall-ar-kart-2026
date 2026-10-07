import time, math, network, socket, json
from XRPLib.defaults import *

def wrap360(deg): 
    if deg < 0:
        return deg + 360.0
    return deg % 360.0

def ang_err(target, current):
    return (target - current + 180.0) % 360.0 - 180.0

# WiFi Config
WIFI_SSID = "XRP-Robot"
WIFI_PASSWORD = "xrpbot123"

GODOT_IP = "10.42.0.23"
GODOT_PORT = 4001
GAMEPAD_PORT = 4002
SEND_RATE_HZ = 50

# Robot Physical Constants
radius = 0.033
L = 0.12

# Gyro Config
AXIS = "y"
RATE_EPS = 0.2
bias_beta = 0.002
k_corr = 0.0
gyro_scale = 1.0

# Ackermann Steering Config
SERVO_CENTER = 125
SERVO_RANGE = 50
THROTTLE_SCALE = 0.8
DIFF_FACTOR = 0.3

# Gamepad State
joy_data = [0.0] * 18
gamepad_active = False
gamepad_last_recv = 0

def gamepad_axis(index):
    return -joy_data[index]

def gamepad_button(index):
    return joy_data[index] > 0

def parse_gamepad_packet(data):
    global joy_data, gamepad_active, gamepad_last_recv
    if len(data) < 2:
        return False
    if data[0] != 0x55:
        return False
    if len(data) != data[1] + 2:
        return False
    for i in range(2, data[1] + 2, 2):
        idx = data[i]
        if idx < len(joy_data):
            joy_data[idx] = round(data[i + 1] / 127.5 - 1, 2)
    gamepad_active = True
    gamepad_last_recv = time.ticks_ms()
    return True

wlan = network.WLAN(network.STA_IF)

def stop_motors():
    """Emergency stop all motors and center steering."""
    motor_three.set_effort(0)
    motor_four.set_effort(0)
    servo_three.set_angle(SERVO_CENTER)

def connect_wifi():
    """Connect to WiFi with retry loop — waits for Pi hotspot to boot."""
    wlan.active(True)
    if wlan.isconnected():
        print("Already connected, IP:", wlan.ifconfig()[0])
        return wlan.ifconfig()[0]
    
    print(f"Waiting for {WIFI_SSID} hotspot...")
    time.sleep(2)
    
    for attempt in range(20):
        wlan.disconnect()
        time.sleep(1)
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        
        start = time.time()
        while not wlan.isconnected():
            if time.time() - start > 5:
                break
            time.sleep(0.5)
        
        if wlan.isconnected():
            ip = wlan.ifconfig()[0]
            print(f"\nConnected! IP: {ip} (attempt {attempt + 1})")
            print(f"Sending to: {GODOT_IP}:{GODOT_PORT}")
            return ip
        
        print(".", end="")
        time.sleep(3)
    
    print("\nERROR: WiFi connection failed after all attempts!")
    return None

def check_wifi():
    """Check WiFi and reconnect if needed.
    Returns True if was already connected.
    Returns 'reconnected' if had to reconnect.
    Returns False if reconnect failed."""
    if wlan.isconnected():
        return True
    print("WiFi lost! Stopping motors...")
    stop_motors()
    board.led_on()
    ip = connect_wifi()
    board.led_off()
    if ip:
        print("WiFi reconnected at", ip)
        return "reconnected"
    print("WiFi reconnect failed")
    return False

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

gyro_bias = calibrate_bias(3.0)

# Initial State
angle_est = float(imu.get_roll())
x, y = 0.0, 0.0

motor_three.reset_encoder_position()
motor_four.reset_encoder_position()
last_l = motor_three.get_position()
last_r = motor_four.get_position()

last_t = time.ticks_ms()
time.sleep(0.05)

def create_sockets():
    """Create fresh send and receive sockets."""
    sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock_recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock_recv.bind(("0.0.0.0", GAMEPAD_PORT))
    sock_recv.setblocking(False)
    return sock_send, sock_recv

def main():
    global last_t, angle_est, gyro_bias, last_l, last_r, x, y
    global gamepad_active

    ip = connect_wifi()
    if ip is None:
        return

    # Blink LED to confirm connection
    for i in range(3):
        board.led_on()
        time.sleep(0.15)
        board.led_off()
        time.sleep(0.15)

    # Create sockets
    sock_send, sock_recv = create_sockets()
    dest = (GODOT_IP, GODOT_PORT)
    print(f"Listening for gamepad on UDP {GAMEPAD_PORT}")

    send_interval_ms = 1000 // SEND_RATE_HZ
    last_send = time.ticks_ms()
    last_wifi_check = time.ticks_ms()
    
    send_count = 0
    error_count = 0

    print("Streaming sensor data... (Ctrl+C to stop)")

    while True:
        now = time.ticks_ms()
        dt = time.ticks_diff(now, last_t) / 1000.0
        last_t = now
        if dt <= 0 or dt > 0.5:
            dt = 0.1

        # Check WiFi every 2 seconds
        if time.ticks_diff(now, last_wifi_check) > 2000:
            last_wifi_check = now
            wifi_status = check_wifi()
            if not wifi_status:
                stop_motors()
                time.sleep(2)
                continue
            if wifi_status == "reconnected":
                # Recreate sockets after reconnect
                try:
                    sock_send.close()
                except:
                    pass
                try:
                    sock_recv.close()
                except:
                    pass
                sock_send, sock_recv = create_sockets()
                print("Sockets recreated after reconnect")

        # Receive gamepad packets
        try:
            while True:
                data, addr = sock_recv.recvfrom(64)
                parse_gamepad_packet(data)
        except OSError:
            pass

        # Gamepad timeout (500ms with no packets = stop)
        if gamepad_active and time.ticks_diff(now, gamepad_last_recv) > 500:
            gamepad_active = False
            for i in range(len(joy_data)):
                joy_data[i] = 0.0
            stop_motors()
            print("Gamepad timed out — motors stopped, steering centered")

        # Apply gamepad to kart
        if gamepad_active:
            throttle = gamepad_axis(1) * THROTTLE_SCALE
            steer = -joy_data[2]

            steer_angle = SERVO_CENTER + (steer * SERVO_RANGE)
            steer_angle = max(SERVO_CENTER - SERVO_RANGE, min(SERVO_CENTER + SERVO_RANGE, steer_angle))
            servo_three.set_angle(steer_angle)

            diff = abs(steer) * DIFF_FACTOR
            if steer > 0:
                motor_three.set_effort(throttle)
                motor_four.set_effort(throttle * (1.0 - diff))
            elif steer < 0:
                motor_three.set_effort(throttle * (1.0 - diff))
                motor_four.set_effort(throttle)
            else:
                motor_three.set_effort(throttle)
                motor_four.set_effort(throttle)

        # Gyro heading
        rate = gyro_rate_dps()

        if abs(rate - gyro_bias) < RATE_EPS:
            gyro_bias = (1.0 - bias_beta) * gyro_bias + bias_beta * rate

        angle_est += (rate - gyro_bias) * dt * gyro_scale

        if k_corr > 0:
            roll_meas = wrap360(imu.get_roll())
            err = ang_err(roll_meas, wrap360(angle_est))
            angle_est += k_corr * err

        display = wrap360(angle_est)

        # Encoder odometry
        l = motor_three.get_position()
        r = motor_four.get_position()
        dl = l - last_l
        dr = r - last_r
        last_l, last_r = l, r

        ds_l = dl * 2 * radius * math.pi
        ds_r = dr * 2 * radius * math.pi
        ds = 0.5 * (ds_l + ds_r)
        v = ds / dt

        x = x + dt * v * math.cos(math.radians(angle_est + 90))
        y = y + dt * v * math.sin(math.radians(angle_est + 90))

        # Send to Godot
        now = time.ticks_ms()
        if time.ticks_diff(now, last_send) >= send_interval_ms:
            last_send = now

            try:
                sensor_data = json.dumps({
                    "x": -(x * 100),
                    "y": y * 100,
                    "angle": display,
                })
                sock_send.sendto(sensor_data.encode(), dest)
                send_count += 1
                
                if send_count % 500 == 0:
                    gp_status = "active" if gamepad_active else "none"
                    print(f"Sent {send_count} | errors: {error_count} | gamepad: {gp_status}")
                    
            except Exception as e:
                error_count += 1
                print(f"Send error ({error_count}): {e}")
                if error_count % 5 == 0:
                    wifi_status = check_wifi()
                    if wifi_status == "reconnected" or not wifi_status:
                        try:
                            sock_send.close()
                        except:
                            pass
                        try:
                            sock_recv.close()
                        except:
                            pass
                        sock_send, sock_recv = create_sockets()

        time.sleep(0.005)

main()
