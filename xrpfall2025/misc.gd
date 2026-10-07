extends Node

# CONFIG
const PI_IP: String = "10.42.0.1"

# UDP for AprilTag pose
var udp_pose: PacketPeerUDP = PacketPeerUDP.new()

# UDP and timeout for XRP sensor data
var udp_xrp: PacketPeerUDP = PacketPeerUDP.new()
var xrp_last_received: float = 0.0
const XRP_TIMEOUT: float = 8.0

# UDP for gamepad commands to XRP
var udp_gamepad: PacketPeerUDP = PacketPeerUDP.new()
var gamepad_connected: bool = false
const GAMEPAD_SEND_HZ: float = 50.0
var gamepad_send_timer: float = 0.0
const GAMEPAD_HEADER: int = 0x55
const DEADZONE: float = 0.08
var xrp_ip: String = ""

# TCP for video
var tcp_client: StreamPeerTCP = StreamPeerTCP.new()
var tcp_connected: bool = false
var tcp_buffer: PackedByteArray = PackedByteArray()
var tcp_retry_timer: float = 0.0

# Detector process
var detector_pid: int = -1

# Latest video frame
var video_texture: ImageTexture = null
var _video_confirmed: bool = false

# XRP state
var xrp_x: float = 0.0
var xrp_y: float = 0.0
var xrp_angle: float = 0.0
var xrp_connected: bool = false
var xrp_trail: Array[Vector2] = []
const TRAIL_MAX: int = 300

# AprilTag poses: tag_id -> {tx, ty, tz, roll, pitch, yaw}
var tag_poses: Dictionary = {}

# Debug timer
var debug_timer: float = 0.0


func _get_base_dir() -> String:
	if OS.has_feature("editor"):
		return ProjectSettings.globalize_path("res://")
	else:
		return OS.get_executable_path().get_base_dir()


func _get_detector_path() -> String:
	var base = _get_base_dir()
	if OS.get_name() == "Windows":
		return base.path_join("apriltag_detector.exe")
	else:
		return base.path_join("apriltag_detector")


func _ready() -> void:
	var err_pose = udp_pose.bind(6000, "0.0.0.0")
	if err_pose != OK:
		push_error("[NetworkReceiver] Failed to bind pose port 6000 (error %d)" % err_pose)
	else:
		print("[NetworkReceiver] Listening for pose on UDP 6000")

	var err_xrp = udp_xrp.bind(4001, "0.0.0.0")
	if err_xrp != OK:
		push_error("[NetworkReceiver] Failed to bind XRP port 4001 (error %d)" % err_xrp)
	else:
		print("[NetworkReceiver] Listening for XRP data on UDP 4001")

	print("[NetworkReceiver] Gamepad will send to XRP once discovered")

	Input.joy_connection_changed.connect(_on_joy_connection_changed)
	var pads = Input.get_connected_joypads()
	if not pads.is_empty():
		print("[NetworkReceiver] Controller already connected: %s" % Input.get_joy_name(0))

	_start_detector()

	await get_tree().create_timer(4.0).timeout
	_connect_video_tcp()


func _process(_delta: float) -> void:
	_process_tcp_status()
	_process_video()
	_process_pose()
	_process_xrp()
	_process_gamepad_send(_delta)

	# Retry TCP connection if not connected
	if not tcp_connected:
		tcp_retry_timer += _delta
		if tcp_retry_timer > 3.0:
			tcp_retry_timer = 0.0
			tcp_client.disconnect_from_host()
			tcp_client = StreamPeerTCP.new()
			var err = tcp_client.connect_to_host("127.0.0.1", 6001)
			if err != OK:
				print("[NetworkReceiver] TCP connect attempt failed (error %d)" % err)
			else:
				print("[NetworkReceiver] Retrying TCP connection to 6001...")

	# Print debug status every 5 seconds
	debug_timer += _delta
	if debug_timer > 5.0:
		debug_timer = 0.0
		var tcp_status = tcp_client.get_status()
		var tcp_status_name = "NONE"
		match tcp_status:
			StreamPeerTCP.STATUS_NONE: tcp_status_name = "NONE"
			StreamPeerTCP.STATUS_CONNECTING: tcp_status_name = "CONNECTING"
			StreamPeerTCP.STATUS_CONNECTED: tcp_status_name = "CONNECTED"
			StreamPeerTCP.STATUS_ERROR: tcp_status_name = "ERROR"
		print("[NetworkReceiver] Status - TCP: %s | XRP: %s (%s) | Gamepad: %s | Video: %s | Tags: %d" % [
			tcp_status_name,
			"connected" if xrp_connected else "waiting",
			xrp_ip if xrp_ip != "" else "no IP",
			"connected" if gamepad_connected else "none",
			"yes" if _video_confirmed else "no",
			tag_poses.size()
		])


# -------- Detector launcher --------

func _start_detector() -> void:
	_kill_old_detector()
	await get_tree().create_timer(1.0).timeout

	var detector_path = _get_detector_path()

	if not FileAccess.file_exists(detector_path):
		push_error("[NetworkReceiver] Detector not found: %s" % detector_path)
		return

	if OS.get_name() == "macOS":
		OS.execute("xattr", ["-rd", "com.apple.quarantine", detector_path])
		OS.execute("chmod", ["+x", detector_path])

	detector_pid = OS.create_process(detector_path, [PI_IP])
	if detector_pid > 0:
		print("[NetworkReceiver] Detector started (PID: %d)" % detector_pid)
	else:
		push_error("[NetworkReceiver] Failed to start detector")


func _kill_old_detector() -> void:
	if OS.get_name() == "Windows":
		OS.execute("taskkill", ["/F", "/IM", "apriltag_detector.exe"])
	else:
		OS.execute("pkill", ["-f", "apriltag_detector"])


func _notification(what: int) -> void:
	if what == NOTIFICATION_WM_CLOSE_REQUEST:
		_cleanup()
		get_tree().quit()


func _cleanup() -> void:
	if detector_pid > 0:
		OS.kill(detector_pid)
		print("[NetworkReceiver] Detector stopped (PID: %d)" % detector_pid)
		detector_pid = -1
	_kill_old_detector()

	tcp_client.disconnect_from_host()
	tcp_connected = false

	udp_pose.close()
	udp_xrp.close()
	udp_gamepad.close()


# -------- TCP video --------

func _connect_video_tcp() -> void:
	tcp_client.disconnect_from_host()
	tcp_client = StreamPeerTCP.new()
	var err = tcp_client.connect_to_host("127.0.0.1", 6001)
	if err != OK:
		push_error("[NetworkReceiver] Failed to initiate TCP connection (error %d)" % err)
	else:
		print("[NetworkReceiver] Connecting to video TCP 6001...")


func _process_tcp_status() -> void:
	tcp_client.poll()
	var status = tcp_client.get_status()
	if status == StreamPeerTCP.STATUS_CONNECTED and not tcp_connected:
		tcp_connected = true
		tcp_buffer.clear()
		# Don't print connected yet — wait for first frame
	elif (status == StreamPeerTCP.STATUS_ERROR or status == StreamPeerTCP.STATUS_NONE) and tcp_connected:
		tcp_connected = false
		tcp_buffer.clear()
		_video_confirmed = false
		video_texture = null
		print("[NetworkReceiver] Video TCP disconnected")


func _process_video() -> void:
	if not tcp_connected:
		return

	var available = tcp_client.get_available_bytes()
	if available > 0:
		var result = tcp_client.get_data(available)
		if result[0] == OK:
			tcp_buffer.append_array(result[1])

	if tcp_buffer.size() > 5 * 1024 * 1024:
		print("[NetworkReceiver] TCP buffer overflow, clearing")
		tcp_buffer.clear()
		return

	while tcp_buffer.size() >= 4:
		var frame_len = (tcp_buffer[0] << 24) | (tcp_buffer[1] << 16) | (tcp_buffer[2] << 8) | tcp_buffer[3]

		if frame_len <= 0 or frame_len > 2 * 1024 * 1024:
			print("[NetworkReceiver] Invalid frame size: %d, clearing buffer" % frame_len)
			tcp_buffer.clear()
			break

		if tcp_buffer.size() < 4 + frame_len:
			break

		var jpg_data = tcp_buffer.slice(4, 4 + frame_len)
		tcp_buffer = tcp_buffer.slice(4 + frame_len)

		var img = Image.new()
		var err = img.load_jpg_from_buffer(jpg_data)
		if err != OK:
			continue
		video_texture = ImageTexture.create_from_image(img)
		if not _video_confirmed:
			_video_confirmed = true
			print("[NetworkReceiver] Video stream active!")


func _encode_axis(value: float) -> int:
	return clampi(int((value + 1.0) * 127.5), 0, 255)

func _apply_deadzone(value: float) -> float:
	if absf(value) < DEADZONE:
		return 0.0
	return value

func _process_gamepad_send(delta: float) -> void:
	if Input.get_connected_joypads().is_empty():
		if gamepad_connected:
			gamepad_connected = false
			print("[NetworkReceiver] Gamepad disconnected")
		return

	if not gamepad_connected:
		gamepad_connected = true
		var pad_name = Input.get_joy_name(0)
		print("[NetworkReceiver] Gamepad connected: %s" % pad_name)

	if xrp_ip == "":
		return

	if not xrp_connected:
		return

	gamepad_send_timer += delta
	if gamepad_send_timer < 1.0 / GAMEPAD_SEND_HZ:
		return
	gamepad_send_timer = 0.0

	var lx = _apply_deadzone(Input.get_joy_axis(0, JOY_AXIS_LEFT_X))
	var ly = _apply_deadzone(Input.get_joy_axis(0, JOY_AXIS_LEFT_Y))
	var rx = _apply_deadzone(Input.get_joy_axis(0, JOY_AXIS_RIGHT_X))
	var ry = _apply_deadzone(Input.get_joy_axis(0, JOY_AXIS_RIGHT_Y))
	var btn_a = Input.is_joy_button_pressed(0, JOY_BUTTON_A)
	var btn_b = Input.is_joy_button_pressed(0, JOY_BUTTON_B)
	var btn_x = Input.is_joy_button_pressed(0, JOY_BUTTON_X)
	var btn_y = Input.is_joy_button_pressed(0, JOY_BUTTON_Y)
	var bumper_l = Input.is_joy_button_pressed(0, JOY_BUTTON_LEFT_SHOULDER)
	var bumper_r = Input.is_joy_button_pressed(0, JOY_BUTTON_RIGHT_SHOULDER)
	var trigger_l = Input.get_joy_axis(0, JOY_AXIS_TRIGGER_LEFT)
	var trigger_r = Input.get_joy_axis(0, JOY_AXIS_TRIGGER_RIGHT)
	var dpad_up = Input.is_joy_button_pressed(0, JOY_BUTTON_DPAD_UP)
	var dpad_dn = Input.is_joy_button_pressed(0, JOY_BUTTON_DPAD_DOWN)
	var dpad_l = Input.is_joy_button_pressed(0, JOY_BUTTON_DPAD_LEFT)
	var dpad_r = Input.is_joy_button_pressed(0, JOY_BUTTON_DPAD_RIGHT)
	var back = Input.is_joy_button_pressed(0, JOY_BUTTON_BACK)
	var start = Input.is_joy_button_pressed(0, JOY_BUTTON_START)

	var pairs := PackedByteArray()
	pairs.append(0);  pairs.append(_encode_axis(lx))
	pairs.append(1);  pairs.append(_encode_axis(ly))
	pairs.append(2);  pairs.append(_encode_axis(rx))
	pairs.append(3);  pairs.append(_encode_axis(ry))
	pairs.append(4);  pairs.append(255 if btn_a else 0)
	pairs.append(5);  pairs.append(255 if btn_b else 0)
	pairs.append(6);  pairs.append(255 if btn_x else 0)
	pairs.append(7);  pairs.append(255 if btn_y else 0)
	pairs.append(8);  pairs.append(255 if bumper_l else 0)
	pairs.append(9);  pairs.append(255 if bumper_r else 0)
	pairs.append(10); pairs.append(_encode_axis(trigger_l))
	pairs.append(11); pairs.append(_encode_axis(trigger_r))
	pairs.append(12); pairs.append(255 if back else 0)
	pairs.append(13); pairs.append(255 if start else 0)
	pairs.append(14); pairs.append(255 if dpad_up else 0)
	pairs.append(15); pairs.append(255 if dpad_dn else 0)
	pairs.append(16); pairs.append(255 if dpad_l else 0)
	pairs.append(17); pairs.append(255 if dpad_r else 0)

	var packet := PackedByteArray()
	packet.append(GAMEPAD_HEADER)
	packet.append(pairs.size())
	packet.append_array(pairs)

	udp_gamepad.put_packet(packet)


# -------- UDP pose --------

func _process_pose() -> void:
	while udp_pose.get_available_packet_count() > 0:
		var pkt = udp_pose.get_packet()
		var msg = pkt.get_string_from_utf8().strip_edges()
		var parts = msg.split(",")
		if parts.size() < 7:
			continue
		var tag_id = int(parts[0])
		tag_poses[tag_id] = {
			"tx": float(parts[1]),
			"ty": float(parts[2]),
			"tz": float(parts[3]),
			"roll": float(parts[4]),
			"pitch": float(parts[5]),
			"yaw": float(parts[6]),
		}


# -------- UDP XRP --------

func _process_xrp() -> void:
	while udp_xrp.get_available_packet_count() > 0:
		var pkt = udp_xrp.get_packet()

		var sender_ip = udp_xrp.get_packet_ip()
		if xrp_ip == "" or xrp_ip != sender_ip:
			xrp_ip = sender_ip
			udp_gamepad.set_dest_address(xrp_ip, 4002)
			print("[NetworkReceiver] XRP IP discovered: %s" % xrp_ip)

		var msg = pkt.get_string_from_utf8().strip_edges()
		var json = JSON.new()
		var err = json.parse(msg)
		if err != OK:
			print("[NetworkReceiver] XRP parse error: ", msg)
			continue
		var data = json.data
		if data is Dictionary:
			if not xrp_connected:
				xrp_connected = true
				print("[NetworkReceiver] XRP connected!")
			xrp_last_received = Time.get_ticks_msec() / 1000.0
			xrp_x = float(data.get("x", 0.0))
			xrp_y = float(data.get("y", 0.0))
			xrp_angle = float(data.get("angle", 0.0))
			xrp_trail.append(Vector2(xrp_x, xrp_y))
			if xrp_trail.size() > TRAIL_MAX:
				xrp_trail.remove_at(0)

	if xrp_connected:
		var now = Time.get_ticks_msec() / 1000.0
		if now - xrp_last_received > XRP_TIMEOUT:
			xrp_connected = false
			print("[NetworkReceiver] XRP timed out, switching to keyboard")


func _on_joy_connection_changed(device_id: int, connected: bool) -> void:
	if connected:
		print("[NetworkReceiver] Controller %d connected: %s" % [device_id, Input.get_joy_name(device_id)])
	else:
		print("[NetworkReceiver] Controller %d disconnected" % device_id)
		if device_id == 0:
			gamepad_connected = false
