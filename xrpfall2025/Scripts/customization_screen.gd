extends Control

@onready var car_button_group: ButtonGroup = $CarOptions/Car1.button_group
@onready var map_button_group: ButtonGroup = $MapOptions/Map2.button_group

var selected_color: Color = Color.AQUA

func _ready():
	$CarOptions/Car1.pressed.connect(_on_car_pressed.bind(Color.AQUA))
	$CarOptions/Car2.pressed.connect(_on_car_pressed.bind(Color.RED))
	$CarOptions/Car3.pressed.connect(_on_car_pressed.bind(Color.GREEN))

func _on_back_button_pressed() -> void:
	get_tree().change_scene_to_file("res://Scenes/GameplayScenes/title_screen.tscn")
	pass # Replace with function body.

func _on_confirm_button_pressed() -> void:
	get_tree().change_scene_to_file("res://Scenes/GameplayScenes/main.tscn")
	pass # Replace with function body.
	
func _process(_delta):
	var car = car_button_group.get_pressed_button()
	var map = map_button_group.get_pressed_button()
	#if car:
		#print("Selected:", car.name)
	#if map:
		#print("Selected:", map.name)
		
# Works for all the buttons being pressed instead of one at a time
func _on_car_pressed(color: Color) -> void:
#	selected_color = color
#	print("Selected color:", selected_color)
#	save_customization()
	pass
		
		# Maybe the logic will be an enum of states in player for the cars, 
		# and car.name is compared to the state name then sets the car
		# I have to figure out when it will be reset, if it even needs to be reset
		

func _on_file_select_pressed() -> void:
	var fileDialog = $MapOptions/FileDialog
	fileDialog.popup_centered_clamped()
	
	var path = await fileDialog.file_selected
	

# Trying to save the selected choice into a file so it can be read in player
func save_customization():
	var save_data = { "car_color": selected_color.to_html() } # e.g. "#00ffff"
	var file = FileAccess.open("user://customization.json", FileAccess.WRITE)
	file.store_string(JSON.stringify(save_data))
	file.close()
