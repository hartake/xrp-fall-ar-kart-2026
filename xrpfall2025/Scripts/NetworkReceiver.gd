extends Node

# ============================================================
# Combined network receiver — fusion + AR UI.
#
# Launches the bundled laptop_fusion executable on startup. From it:
#   - UDP 6000: fused robot pose JSON {x, y, angle}
#   - TCP 6001: raw RGB video frames
#   - UDP 6002: ar_tags JSON   (NEW — for AR overlays)
#
# Sends to Python:
#   - UDP 4003 (localhost): gamepad packets, Python forwards to XRP
#
# AR mode: at startup, prompts "Enable AR Camera?". If yes, the live
# camera feed becomes the world backdrop and labels float over each
# detected AprilTag. The kart pose / gamepad / fusion logic runs the
# same way regardless.
#
# Variable names xrp_x/y/angle/connected/trail kept for backwards-compat
# with Player.cs.
# ============================================================

# Passed to laptop_fusion, which tries it first and then falls back to
# cornellcup2.local / 10.42.0.1 / 192.168.4.95 on its own. So the kart
# works on the Pi hotspot or CornellCup-Web without changing this.
const PI_IP: String = "cornellcup2.local"

const POSE_PORT: int = 6000
const VIDEO_PORT: int = 6001
const AR_TAGS_PORT: int = 6002
const GAMEPAD_RELAY_PORT: int = 4003
const GAMEPAD_RELAY_HOST: String = "127.0.0.1"

# ---- Fused pose ----
var udp_pose_in: PacketPeerUDP = PacketPeerUDP.new()
var pose_last_received: float = 0.0
const POSE_TIMEOUT: float = 8.0

# ---- Gamepad ----
var udp_gamepad: PacketPeerUDP = PacketPeerUDP.new()
var gamepad_connected: bool = false
const GAMEPAD_SEND_HZ: float = 20.0
var gamepad_send_timer: float = 0.0
const GAMEPAD_HEADER: int = 0x55
const DEADZONE: float = 0.08

# ---- Video TCP ----
var tcp_client: StreamPeerTCP = StreamPeerTCP.new()
var tcp_connected: bool = false
var tcp_buffer: PackedByteArray = PackedByteArray()
var tcp_retry_timer: float = 0.0

# ---- AR tags UDP ----
var udp_ar_tags: PacketPeerUDP = PacketPeerUDP.new()
var ar_frame_w: int = 640
var ar_frame_h: int = 480
const AR_LABEL_PIXEL_SIZE: float = 0.2

# ---- Fusion process ----
var fusion_pid: int = -1

# ---- Latest video frame ----
var video_texture: ImageTexture = null
var _video_confirmed: bool = false

# ---- Robot state (legacy names) ----
var xrp_x: float = 0.0
var xrp_y: float = 0.0
var xrp_angle: float = 0.0
var xrp_connected: bool = false
var xrp_trail: Array[Vector2] = []
const TRAIL_MAX: int = 300

# ---- AR mode ----
var ar_enabled: bool = false
var ar_backdrop_mesh: MeshInstance3D = null
const AR_BACKDROP_W: float = 500.0
const AR_BACKDROP_H: float = 350.0
const AR_BACKDROP_Z: float = -90.0
# Map of tag_id -> Label3D overlay node.
var ar_tag_labels: Dictionary = {}

# ---- Gate event triggering (currently inert; flip ar_events_enabled to wire up) ----
# Set to true once you want detected tags to fire game events.
# Per-tag-ID -> action for _trigger_gate to dispatch.
# Actions, with their States enum ordinal values from Car.cs:
#     States.Inverted = 0, States.Slow = 1, States.Fast = 2, States.Regular = 3
# Use ApplyItemEffect(state_int) to give the player an effect, or
# StoreItem(state_int) to put one in their item slot.
const AR_EVENTS_ENABLED: bool = false
const TAG_EVENTS: Dictionary = {
	# Example mappings — uncomment or edit when you turn AR_EVENTS_ENABLED on:
	# 0: "checkpoint",   # finish line — increment lap
	# 1: "speed_boost",  # ApplyItemEffect(Fast)
	# 2: "store_item",   # StoreItem(random)
	# 3: "hazard",       # ApplyItemEffect(Slow)
}
var _last_gate_fired_at: Dictionary = {}      # tag_id -> last trigger time
const GATE_COOLDOWN_S: float = 2.0
var _player_node: Node = null

# ---- Debug ----
var debug_timer: float = 0.0
var _frames_dropped_this_period: int = 0
var _frames_shown_this_period: int = 0


# ============================================================
# Setup
# ============================================================

func _get_base_dir() -> String:
	if OS.has_feature("editor"):
		return ProjectSettings.globalize_path("res://")
	else:
		return OS.get_executable_path().get_base_dir()


func _get_fusion_path() -> String:
	var base = _get_base_dir()
	if OS.get_name() == "Windows":
		return base.path_join("laptop_fusion.exe")
	else:
		return base.path_join("laptop_fusion")


func _ready() -> void:
	# Pose listener
	var err_pose = udp_pose_in.bind(POSE_PORT, "0.0.0.0")
	if err_pose != OK:
		push_error("[NetworkReceiver] Failed to bind pose port %d (error %d)" % [POSE_PORT, err_pose])
	else:
		print("[NetworkReceiver] Listening for fused pose on UDP %d" % POSE_PORT)

	# AR tags listener
	var err_ar = udp_ar_tags.bind(AR_TAGS_PORT, "0.0.0.0")
	if err_ar != OK:
		push_error("[NetworkReceiver] Failed to bind AR tags port %d (error %d)" % [AR_TAGS_PORT, err_ar])
	else:
		print("[NetworkReceiver] Listening for AR tags on UDP %d" % AR_TAGS_PORT)

	# Gamepad output
	udp_gamepad.set_dest_address(GAMEPAD_RELAY_HOST, GAMEPAD_RELAY_PORT)
	print("[NetworkReceiver] Gamepad relayed via %s:%d at %d Hz" % [
		GAMEPAD_RELAY_HOST, GAMEPAD_RELAY_PORT, int(GAMEPAD_SEND_HZ)])

	Input.joy_connection_changed.connect(_on_joy_connection_changed)
	var pads = Input.get_connected_joypads()
	if not pads.is_empty():
		print("[NetworkReceiver] Controller already connected: %s" % Input.get_joy_name(0))

	_start_fusion()
	await get_tree().create_timer(4.0).timeout
	_connect_video_tcp()

	# Show the AR popup after fusion + video are wired up
	call_deferred("_show_ar_dialog")


func _process(_delta: float) -> void:
	_process_tcp_status()
	_process_video()
	_process_pose()
	_process_ar_tags()
	_process_gamepad_send(_delta)

	if not tcp_connected:
		tcp_retry_timer += _delta
		if tcp_retry_timer > 3.0:
			tcp_retry_timer = 0.0
			tcp_client.disconnect_from_host()
			tcp_client = StreamPeerTCP.new()
			var err = tcp_client.connect_to_host("127.0.0.1", VIDEO_PORT)
			if err != OK:
				print("[NetworkReceiver] TCP connect attempt failed (error %d)" % err)
			else:
				print("[NetworkReceiver] Retrying TCP connection to %d..." % VIDEO_PORT)

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
		print("[NetworkReceiver] Status - TCP: %s | Pose: %s | Gamepad: %s | Video: %s | AR: %s | Frames: %d/%d" % [
			tcp_status_name,
			"active" if xrp_connected else "waiting",
			"connected" if gamepad_connected else "none",
			"yes" if _video_confirmed else "no",
			"on" if ar_enabled else "off",
			_frames_shown_this_period,
			_frames_dropped_this_period,
		])
		_frames_shown_this_period = 0
		_frames_dropped_this_period = 0


# ============================================================
# Fusion process launcher
# ============================================================

func _start_fusion() -> void:
	_kill_old_fusion()
	await get_tree().create_timer(1.0).timeout
	var fusion_path = _get_fusion_path()
	if not FileAccess.file_exists(fusion_path):
		push_error("[NetworkReceiver] Fusion binary not found: %s" % fusion_path)
		return
	if OS.get_name() == "macOS":
		OS.execute("xattr", ["-rd", "com.apple.quarantine", fusion_path])
		OS.execute("chmod", ["+x", fusion_path])
	fusion_pid = OS.create_process(fusion_path, [PI_IP])
	if fusion_pid > 0:
		print("[NetworkReceiver] Fusion started (PID: %d)" % fusion_pid)
	else:
		push_error("[NetworkReceiver] Failed to start fusion binary")


func _kill_old_fusion() -> void:
	if OS.get_name() == "Windows":
		OS.execute("taskkill", ["/F", "/IM", "laptop_fusion.exe"])
	else:
		OS.execute("pkill", ["-f", "laptop_fusion"])


func _notification(what: int) -> void:
	if what == NOTIFICATION_WM_CLOSE_REQUEST:
		_cleanup()
		get_tree().quit()


func _cleanup() -> void:
	if fusion_pid > 0:
		OS.kill(fusion_pid)
		print("[NetworkReceiver] Fusion stopped (PID: %d)" % fusion_pid)
		fusion_pid = -1
	_kill_old_fusion()
	tcp_client.disconnect_from_host()
	tcp_connected = false
	udp_pose_in.close()
	udp_ar_tags.close()
	udp_gamepad.close()


# ============================================================
# AR mode setup
# ============================================================

func _show_ar_dialog() -> void:
	var dialog = ConfirmationDialog.new()
	dialog.title = "AR Camera"
	dialog.dialog_text = "Enable AR Camera?\n\nThis will use the kart's live camera as the game background\nwith floating labels over detected tags."
	dialog.ok_button_text = "Yes, use camera"
	dialog.cancel_button_text = "No, normal mode"
	dialog.size = Vector2i(420, 170)
	dialog.initial_position = Window.WINDOW_INITIAL_POSITION_CENTER_MAIN_WINDOW_SCREEN
	dialog.confirmed.connect(_on_ar_yes)
	dialog.canceled.connect(_on_ar_no)

	var canvas = CanvasLayer.new()
	canvas.layer = 100
	canvas.name = "ARDialogLayer"
	add_child(canvas)
	canvas.add_child(dialog)
	dialog.popup_centered()


func _on_ar_yes() -> void:
	ar_enabled = true
	var dl = get_node_or_null("ARDialogLayer")
	if dl: dl.queue_free()
	_setup_camera_backdrop()
	_hide_world()
	print("[AR] AR mode active")


func _on_ar_no() -> void:
	ar_enabled = false
	var dl = get_node_or_null("ARDialogLayer")
	if dl: dl.queue_free()
	print("[AR] Normal mode")


func _setup_camera_backdrop() -> void:
	# The backdrop attaches to whatever Camera3D the scene already has.
	# Adjust the find-by-name if your camera node has a different name.
	var cam = _find_node_by_name(get_tree().root, "fpsCamera") as Camera3D
	if cam == null:
		# Fallback: any Camera3D
		cam = _find_first_camera3d(get_tree().root)
	if cam == null:
		print("[AR] No Camera3D found; AR backdrop disabled")
		ar_enabled = false
		return

	var mesh = QuadMesh.new()
	mesh.size = Vector2(AR_BACKDROP_W, AR_BACKDROP_H)

	# Use the existing live video texture if it's been created already,
	# or a placeholder if not (will get updated on first frame).
	if video_texture == null:
		var img = Image.create(ar_frame_w, ar_frame_h, false, Image.FORMAT_RGB8)
		img.fill(Color(0.15, 0.15, 0.25))
		video_texture = ImageTexture.create_from_image(img)

	var mat = StandardMaterial3D.new()
	mat.albedo_texture = video_texture
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.cull_mode = BaseMaterial3D.CULL_DISABLED

	ar_backdrop_mesh = MeshInstance3D.new()
	ar_backdrop_mesh.mesh = mesh
	ar_backdrop_mesh.material_override = mat
	ar_backdrop_mesh.name = "ARCameraBackdrop"
	cam.add_child(ar_backdrop_mesh)
	ar_backdrop_mesh.position = Vector3(0, 0, AR_BACKDROP_Z)


func _hide_world() -> void:
	var root = get_tree().root
	var kp = _find_node_by_name(root, "killPlane")
	if kp: _hide_visual_children(kp)


func _hide_visual_children(node: Node) -> void:
	for child in node.get_children():
		if child is VisualInstance3D: child.visible = false
		if child is Light3D: child.visible = false
		_hide_visual_children(child)


# ============================================================
# AR overlay updates from incoming tag packets
# ============================================================

func _process_ar_tags() -> void:
	while udp_ar_tags.get_available_packet_count() > 0:
		var pkt = udp_ar_tags.get_packet()
		var msg = pkt.get_string_from_utf8().strip_edges()
		var json = JSON.new()
		var err = json.parse(msg)
		if err != OK:
			continue
		var data = json.data
		if not (data is Dictionary): continue
		if str(data.get("type", "")) != "ar_tags": continue

		ar_frame_w = int(data.get("frame_w", ar_frame_w))
		ar_frame_h = int(data.get("frame_h", ar_frame_h))

		var tags: Array = data.get("tags", [])
		_update_ar_labels(tags)


func _update_ar_labels(tags: Array) -> void:
	if not ar_enabled or ar_backdrop_mesh == null:
		# Even if AR isn't active visually, still consume the packets
		return

	# Track which IDs are present this frame so we can hide stale ones.
	var seen_ids := {}

	for tag in tags:
		if not (tag is Dictionary): continue
		var id := int(tag.get("id", -1))
		if id < 0: continue
		seen_ids[id] = true

		var corners: Array = tag.get("corners", [])
		var center: Array = tag.get("center", [0.0, 0.0])
		var name: String = str(tag.get("name", "TAG %d" % id))

		var label: Label3D = ar_tag_labels.get(id, null)
		if label == null:
			label = _create_tag_label(name)
			ar_tag_labels[id] = label

		_position_tag_label(label, center, corners)
		label.text = name
		label.visible = true

		# Optional: dispatch a game event for this tag ID.
		# Inert by default; flip AR_EVENTS_ENABLED when ready.
		if AR_EVENTS_ENABLED:
			_trigger_gate(id)

	# Hide labels for tags we didn't see this frame
	for existing_id in ar_tag_labels.keys():
		if not seen_ids.has(existing_id):
			(ar_tag_labels[existing_id] as Label3D).visible = false


# ============================================================
# Game event dispatch — called from _update_ar_labels when a tag is
# seen. Currently inert (AR_EVENTS_ENABLED = false). When you turn it
# on, fill out TAG_EVENTS with which IDs do what.
#
# Calls into Player.cs / Car.cs:
#   - ApplyItemEffect was originally private. To call it from GDScript,
#     change it to `public void ApplyItemEffect(States item)` in Car.cs.
#   - StoreItem(States) is already public.
#   - States enum: Inverted=0, Slow=1, Fast=2, Regular=3.
# ============================================================
func _trigger_gate(tag_id: int) -> void:
	if not TAG_EVENTS.has(tag_id):
		return

	# Cooldown so a tag held in view doesn't fire every frame
	var now = Time.get_ticks_msec() / 1000.0
	var last = float(_last_gate_fired_at.get(tag_id, -999.0))
	if now - last < GATE_COOLDOWN_S:
		return
	_last_gate_fired_at[tag_id] = now

	if _player_node == null:
		_find_player()
	if _player_node == null:
		print("[AR] gate fired but no player found")
		return

	var event = str(TAG_EVENTS[tag_id])
	match event:
		"checkpoint":
			# Finish-line / lap behavior is normally handled by physical
			# checkpoint trigger volumes in the game world. If you want a
			# tag to also act as one, hook it up here. For now: just log.
			print("[AR] tag %d: checkpoint" % tag_id)
		"speed_boost":
			# States.Fast = 2
			_player_node.call("ApplyItemEffect", 2)
			print("[AR] tag %d: SPEED BOOST" % tag_id)
		"store_item":
			# Random one of Inverted/Slow/Fast (0/1/2). Skip Regular.
			_player_node.call("StoreItem", randi() % 3)
			print("[AR] tag %d: ITEM stored" % tag_id)
		"hazard":
			# States.Slow = 1
			_player_node.call("ApplyItemEffect", 1)
			print("[AR] tag %d: HAZARD" % tag_id)
		_:
			print("[AR] tag %d: unknown event '%s'" % [tag_id, event])


func _find_player() -> void:
	# Try common locations first, then fall back to a tree search
	_player_node = get_node_or_null("/root/CarManager/Player")
	if _player_node == null:
		_player_node = _find_node_by_name(get_tree().root, "Player")


func _create_tag_label(text: String) -> Label3D:
	var label = Label3D.new()
	label.text = text
	label.font_size = 64
	label.modulate = Color(1, 0.9, 0.2)
	label.outline_modulate = Color(0.3, 0.0, 0.0)
	label.outline_size = 8
	label.billboard = BaseMaterial3D.BILLBOARD_DISABLED
	label.no_depth_test = true
	label.pixel_size = AR_LABEL_PIXEL_SIZE
	# Parented to the backdrop so positions are in backdrop-local coords
	ar_backdrop_mesh.add_child(label)
	return label


func _pixel_to_backdrop(px: float, py: float) -> Vector3:
	# Convert 2D pixel coordinates (0..frame_w, 0..frame_h) into
	# 3D positions on the backdrop plane, in the backdrop's local frame.
	var x = (px / float(ar_frame_w) - 0.5) * AR_BACKDROP_W
	var y = (0.5 - py / float(ar_frame_h)) * AR_BACKDROP_H
	# Tiny z offset so the label sits in front of the backdrop, not behind
	return Vector3(x, y, 0.5)


func _position_tag_label(label: Label3D, center: Array, corners: Array) -> void:
	var cx = float(center[0]) if center.size() > 0 else 0.0
	var cy = float(center[1]) if center.size() > 1 else 0.0
	var pos = _pixel_to_backdrop(cx, cy)

	# Lift the label above the tag by a fraction of its height so it
	# doesn't cover the marker
	var tag_h_px := 0.0
	if corners.size() == 4:
		var top_y = min(float(corners[0][1]), float(corners[1][1]))
		var bot_y = max(float(corners[2][1]), float(corners[3][1]))
		tag_h_px = bot_y - top_y
	var lift = (tag_h_px / float(ar_frame_h)) * AR_BACKDROP_H * 0.6 + 8.0

	label.position = Vector3(pos.x, pos.y + lift, pos.z)


# ============================================================
# TCP video
# ============================================================

func _connect_video_tcp() -> void:
	tcp_client.disconnect_from_host()
	tcp_client = StreamPeerTCP.new()
	var err = tcp_client.connect_to_host("127.0.0.1", VIDEO_PORT)
	if err != OK:
		push_error("[NetworkReceiver] Failed to initiate TCP connection (error %d)" % err)
	else:
		print("[NetworkReceiver] Connecting to video TCP %d..." % VIDEO_PORT)


func _process_tcp_status() -> void:
	tcp_client.poll()
	var status = tcp_client.get_status()
	if status == StreamPeerTCP.STATUS_CONNECTED and not tcp_connected:
		tcp_connected = true
		tcp_buffer.clear()
		tcp_client.set_no_delay(true)
	elif (status == StreamPeerTCP.STATUS_ERROR or status == StreamPeerTCP.STATUS_NONE) and tcp_connected:
		tcp_connected = false
		tcp_buffer.clear()
		_video_confirmed = false
		print("[NetworkReceiver] Video TCP disconnected")


func _read_uint32_be(data: PackedByteArray, offset: int) -> int:
	return (data[offset] << 24) | (data[offset + 1] << 16) | (data[offset + 2] << 8) | data[offset + 3]


func _process_video() -> void:
	if not tcp_connected:
		return

	var available = tcp_client.get_available_bytes()
	if available > 0:
		var result = tcp_client.get_data(available)
		if result[0] == OK:
			tcp_buffer.append_array(result[1])

	if tcp_buffer.size() > 32 * 1024 * 1024:
		print("[NetworkReceiver] TCP buffer overflow, clearing")
		tcp_buffer.clear()
		return

	var latest_w: int = 0
	var latest_h: int = 0
	var latest_data: PackedByteArray = PackedByteArray()
	var frames_in_this_batch: int = 0

	const HEADER_SIZE = 12

	while tcp_buffer.size() >= HEADER_SIZE:
		var w = _read_uint32_be(tcp_buffer, 0)
		var h = _read_uint32_be(tcp_buffer, 4)
		var frame_len = _read_uint32_be(tcp_buffer, 8)

		if w <= 0 or h <= 0 or w > 7680 or h > 4320:
			print("[NetworkReceiver] Bad frame dimensions: %dx%d, clearing buffer" % [w, h])
			tcp_buffer.clear()
			break

		var expected_len = w * h * 3
		if frame_len != expected_len:
			print("[NetworkReceiver] Frame length mismatch: got %d, expected %d, clearing" % [frame_len, expected_len])
			tcp_buffer.clear()
			break

		if tcp_buffer.size() < HEADER_SIZE + frame_len:
			break

		latest_w = w
		latest_h = h
		latest_data = tcp_buffer.slice(HEADER_SIZE, HEADER_SIZE + frame_len)
		tcp_buffer = tcp_buffer.slice(HEADER_SIZE + frame_len)
		frames_in_this_batch += 1

	if frames_in_this_batch == 0:
		return

	if frames_in_this_batch > 1:
		_frames_dropped_this_period += frames_in_this_batch - 1

	var img = Image.create_from_data(latest_w, latest_h, false, Image.FORMAT_RGB8, latest_data)
	if img == null:
		return

	if video_texture == null:
		video_texture = ImageTexture.create_from_image(img)
	else:
		video_texture.update(img)

	# Keep the AR side's frame size up to date so pixel->backdrop math is correct
	ar_frame_w = latest_w
	ar_frame_h = latest_h

	_frames_shown_this_period += 1

	if not _video_confirmed:
		_video_confirmed = true
		print("[NetworkReceiver] Video stream active! (%dx%d RGB)" % [latest_w, latest_h])


# ============================================================
# Gamepad sending
# ============================================================

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

	gamepad_send_timer += delta
	if gamepad_send_timer < 1.0 / GAMEPAD_SEND_HZ:
		return
	gamepad_send_timer = 0.0

	var ly = _apply_deadzone(Input.get_joy_axis(0, JOY_AXIS_LEFT_Y))
	var rx = _apply_deadzone(Input.get_joy_axis(0, JOY_AXIS_RIGHT_X))

	var packet := PackedByteArray()
	packet.append(GAMEPAD_HEADER)
	packet.append(4)
	packet.append(1); packet.append(_encode_axis(ly))
	packet.append(2); packet.append(_encode_axis(rx))

	udp_gamepad.put_packet(packet)


# ============================================================
# Fused pose receiver
# ============================================================

func _process_pose() -> void:
	while udp_pose_in.get_available_packet_count() > 0:
		var pkt = udp_pose_in.get_packet()
		var msg = pkt.get_string_from_utf8().strip_edges()
		var json = JSON.new()
		var err = json.parse(msg)
		if err != OK:
			print("[NetworkReceiver] Pose parse error: ", msg)
			continue
		var data = json.data
		if data is Dictionary:
			if not xrp_connected:
				xrp_connected = true
				print("[NetworkReceiver] Robot pose active!")
			pose_last_received = Time.get_ticks_msec() / 1000.0
			xrp_x = float(data.get("x", 0.0))
			xrp_y = float(data.get("y", 0.0))
			xrp_angle = float(data.get("angle", 0.0))
			xrp_trail.append(Vector2(xrp_x, xrp_y))
			if xrp_trail.size() > TRAIL_MAX:
				xrp_trail.remove_at(0)

	if xrp_connected:
		var now = Time.get_ticks_msec() / 1000.0
		if now - pose_last_received > POSE_TIMEOUT:
			xrp_connected = false
			print("[NetworkReceiver] Pose stream timed out")


func _on_joy_connection_changed(device_id: int, connected: bool) -> void:
	if connected:
		print("[NetworkReceiver] Controller %d connected: %s" % [device_id, Input.get_joy_name(device_id)])
	else:
		print("[NetworkReceiver] Controller %d disconnected" % device_id)
		if device_id == 0:
			gamepad_connected = false


# ============================================================
# Helpers
# ============================================================

func _find_node_by_name(node: Node, target: String) -> Node:
	if node.name == target:
		return node
	for child in node.get_children():
		var r = _find_node_by_name(child, target)
		if r:
			return r
	return null


func _find_first_camera3d(node: Node) -> Camera3D:
	if node is Camera3D:
		return node
	for child in node.get_children():
		var c = _find_first_camera3d(child)
		if c != null:
			return c
	return null
