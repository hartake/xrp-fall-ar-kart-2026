extends Node3D

@export var camera : Camera3D

var pan = false
var pan_start : Vector2

var dragging = false
var drag_object : Node3D = null

var currently_snapped = false
var snapped_to : Node3D = null

var currently_saving = false

func _input(event: InputEvent) -> void:
	if event is InputEventKey:
		# flip piece
		if event.keycode == KEY_F and event.pressed and dragging:
			drag_object.rotation.z += PI # flip over
			# did try and use scale * -1 but it interacted weirdly with rotations
		# delete, but only if not root part
		elif event.keycode == KEY_D and event.pressed and dragging:
			if drag_object != %RootPart: # potentially redundant
				drag_object.queue_free()
				drag_object = null
				dragging = false
		# save track
		elif event.keycode == KEY_S and event.pressed and !currently_saving:
			currently_saving = true
			save_track()
			currently_saving = false
		# generate path (for debug)
		elif event.keycode == KEY_G and event.pressed:
			generate_path()
		elif event.keycode == KEY_Q and event.pressed:
			get_tree().change_scene_to_file("res://Scenes/GameplayScenes/title_screen.tscn")
			pass
	elif event is InputEventMouseButton:
		# pan camera
		# note: is limited by the $Backdrop plane, as it finds location by casting to that
		if event.button_index == MOUSE_BUTTON_RIGHT:
			# this might have an issue if right click is held while initializing
			if event.pressed == true and pan == false: # just pressed
				pan = true
				var pos =  mouse_raycast_on_layer(2).get("position", Vector3.ZERO)
				pan_start = Vector2(pos.x, pos.z) 
			elif event.pressed == true and pan == true: # being held
				pass
			else: # just released or not held
				pan = false
		# dragging piece
		if event.button_index == MOUSE_BUTTON_LEFT:
			if event.pressed == true and dragging == false: # just pressed
				var result = mouse_raycast_on_layer(8)
				if result == null:
					pass
				var collider = result.get("collider",null)
				if collider == null:
					return
				drag_object = collider.get_parent()
				if drag_object == %RootPart:
					drag_object = null
					return
				dragging = true
				drag_object.disable_points()
			elif event.pressed == true and dragging == true: # being held
				pass
			elif event.pressed == false and dragging == true: # just released
				dragging = false
				drag_object.enable_points()
				generate_path()
			else: # not being held
				pass
	elif event is InputEventMouseMotion:
		if pan:
			# calculate offset between current mouse location and where pan started
			# and translate camera accordingly
			var target = mouse_raycast_on_layer(2).get("position", Vector3.ZERO)
			var diff = Vector2(target.x, target.z) - pan_start
			camera.position.x -= diff.x
			camera.position.z -= diff.y
		elif dragging:
			var result = mouse_raycast_on_layer(4) # bitmasks baybee
			if result != {} and snapped_to == null: # just went over a snap point
				#print(result["collider"])
				drag_object.global_position = result["collider"].global_position
				drag_object.global_rotation.y = result["collider"].global_rotation.y# + drag_object.rotation
				snapped_to = result["collider"].get_parent()
				
				if snapped_to.next_piece == null: # don't replace a piece that is in place
					snapped_to.next_piece = drag_object
					drag_object.previous_piece = snapped_to
			elif result == {} and snapped_to != null: # just dragged off a point
				drag_object.previous_piece = null
				if snapped_to.next_piece == drag_object:
					snapped_to.next_piece = null
				
				snapped_to = null
			elif result == {}: # not over a snap point, was not on one
				drag_object.position = mouse_raycast_on_layer(2).get("position", Vector3.ZERO)
			else:
				pass

# take in a layer mask to cast onto, and return result
func mouse_raycast_on_layer(layer_mask : int) -> Dictionary:
	var mouse_position = get_viewport().get_mouse_position()
	var origin = camera.project_ray_origin(mouse_position)
	var direction = camera.project_ray_normal(mouse_position)
	var end = origin + direction * 10
	var space_state = get_world_3d().direct_space_state
	var query = PhysicsRayQueryParameters3D.create(origin,end, layer_mask)
	return space_state.intersect_ray(query)

# instantiate given scene, and manually start drag
# if you wish to add new parts, add new buttons and create new scenes that must have the
# "track builder piece.gd" script, and assign points and paths appropriately, look at existing
# piece scenes for reference (also note how the CSGPolygon interacts with the path for generating
# driving collision)
func spawn_part(part : PackedScene) -> void:
	var spawned = part.instantiate()
	$Track.add_child(spawned)
	var pos = mouse_raycast_on_layer(2).get("position", Vector3.ZERO)
	spawned.position = pos
	drag_object = spawned
	dragging = true
	spawned.disable_points()

# compile a packed scene, stitch path together, and serialize
func save_track():
	generate_path()
	var saveScene = PackedScene.new()
	var track = $Track
	track.owner = track
	for child in track.get_children():
		child.owner = track
	saveScene.pack(track)
	
	var fileDialog = $FileDialog
	fileDialog.popup_centered_clamped()
	
	var path = await fileDialog.file_selected
	
	if !path.ends_with(".tscn"):
		path += ".tscn"
	
	var err = await ResourceSaver.save(saveScene, path)

# stitch together the Path3D from each track piece (starting at root part)
# and assign to Final Path node
func generate_path():
	var path = Curve3D.new()
	path.closed = true # makes end connect to start
	var piece = %RootPart
	while piece != null:
		var cur_path : Curve3D = piece.get_child(-1).curve # path3D is at the end for now, might need to be smarter
		for i in cur_path.point_count:
			var cur_point = cur_path.get_point_position(i)
			var pos_global = piece.to_global(cur_point)
			if path.point_count != 0: # always add points from root
				# skip any points that are too close (usually overlapping start/end of pieces)
				var dis_from_prev = (pos_global - path.get_point_position(path.point_count-1)).length()
				if dis_from_prev > 0.1:
					path.add_point(pos_global)
			else:
				path.add_point(pos_global)
		piece = piece.next_piece
	$"Track/Final Path".curve = path
