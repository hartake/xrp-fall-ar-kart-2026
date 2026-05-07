extends Node

@export var checkpoints : Array[Vector3]
@export var innerPath : Path3D
@export var startingPoint : Vector3

@export var filePath : String

# adjustable in order to make sure tracking and placements are accurate
@export var scale : float

func Init():
	var scene = load(LevelData.level_path).instantiate()
	add_child(scene)
	print(get_children())
	$Track.scale *= scale
	innerPath = $"Track/Final Path"
	startingPoint = innerPath.curve.get_point_position(0)
	generate_checkpoints()

# all pieces are (currently) only 3 points long, so taking every other point
# gives us the joints between them
func generate_checkpoints():
	var c = innerPath.curve
	for i in c.point_count:
		if i % 2 == 0:
			checkpoints.append(c.get_point_position(i) * scale)
	print(checkpoints)

func request_track() -> void:
	var fileDialog = $FileDialog
	fileDialog.popup_centered_clamped()
	
	var path = await fileDialog.file_selected
	print("finished await1")
	if !path.ends_with(".tscn"):
		pass # deal with later
	
	filePath = path
