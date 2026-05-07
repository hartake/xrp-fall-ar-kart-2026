extends Node3D

@export var base_rotation : Vector3

@export var next_piece : Node = null
@export var previous_piece : Node = null

func _ready() -> void:
	base_rotation = rotation # point to reset to when not hovering on snap point

func _process(delta: float)  -> void:
	pass

func disable_points():
	$"Snap Point 1".process_mode = Node.PROCESS_MODE_DISABLED
	$"Snap Point 2".process_mode = Node.PROCESS_MODE_DISABLED

func enable_points():
	$"Snap Point 1".process_mode = Node.PROCESS_MODE_INHERIT
	$"Snap Point 2".process_mode = Node.PROCESS_MODE_INHERIT
