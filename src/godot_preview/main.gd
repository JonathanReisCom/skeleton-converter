extends Node
## Boot scene for the web preview: instantiate the converted scene, loop its
## first animation, publish the animation list to the browser shell, and frame
## the rig. The converter rewrites SCENE below with the actual output name
## before exporting — web builds have no environment to pass it.

const SCENE := "res://__SCENE__.tscn"

var _player: AnimationPlayer
var _js_play: JavaScriptObject

func _ready() -> void:
	var packed: PackedScene = load(SCENE)
	if packed == null:
		push_error("cannot load converted scene: " + SCENE)
		return
	var instance := packed.instantiate()
	add_child(instance)
	_player = _find_player(instance)
	if _player == null:
		return
	var names: PackedStringArray = _player.get_animation_list()
	print("PREVIEW_ANIMS=", ",".join(names))
	# Native scenes mark animations non-looping (the demo loops them from its
	# own script) — a one-shot animation freezes at its last frame in seconds
	# and the preview looks static forever. Loop everything.
	for anim_name in names:
		_player.get_animation(anim_name).loop_mode = Animation.LOOP_LINEAR
	if names.size() > 0:
		_player.play(names[0])
	# A native scene's AnimationTree overrides whatever the AnimationPlayer
	# plays (the demo drives playback from its own state machine) — silence it
	# so the track buttons and the autoplay actually own the pose.
	for tree in _collect(instance, AnimationTree):
		tree.active = false
	# Gameplay scripts (the demo's player.gd applies gravity every frame) walk
	# the character out of the camera in seconds — the preview animates, it
	# does not play. Freeze physics so the rig stays in frame.
	instance.set_physics_process(false)
	instance.set_process(false)

	# Web preview: publish the animation list to the shell (which renders the
	# track buttons) and accept play commands from them.
	if OS.has_feature("web"):
		var win := JavaScriptBridge.get_interface("window")
		# A Callable assigned to a JS interface property does not become
		# callable from JS — create_callback produces a real JS function.
		# The callback object must be kept referenced from Godot — a created
		# callback without a Godot-side reference is garbage-collected and the
		# JS function becomes dead (button clicks dispatch into nothing).
		_js_play = JavaScriptBridge.create_callback(Callable(self, "_web_play"))
		win.previewPlay = _js_play
		win.previewAnims(",".join(names))

	# Camera: frame the rig from the bones themselves — Polygon2D bounds are
	# unreliable here (get_global_rect returned null on the hero), bone
	# positions are not.
	var cam := Camera2D.new()
	var min_p := Vector2(INF, INF)
	var max_p := Vector2(-INF, -INF)
	var bones := 0
	for bone in _collect(instance, Bone2D):
		var p: Vector2 = bone.global_position
		min_p = min_p.min(p)
		max_p = max_p.max(p)
		bones += 1
	if bones > 0:
		var rect := Rect2(min_p, max_p - min_p)
		cam.position = rect.get_center()
		var viewport: Vector2 = get_viewport().get_visible_rect().size
		# Uniform zoom: a non-uniform one skews the rig; fit the larger span.
		cam.zoom = Vector2.ONE * min(viewport.x / max(rect.size.x, 1.0),
		                             viewport.y / max(rect.size.y, 1.0)) * 0.8
	add_child(cam)
	cam.make_current()

func _web_play(args: Array) -> void:
	# JavaScriptBridge.create_callback wraps the JS call into one Array of
	# arguments — the button click arrives as args[0], not as a bare String.
	if args.is_empty():
		return
	var name: String = str(args[0])
	print("PREVIEW_PLAY=", name)
	if _player != null and _player.has_animation(name):
		_player.play(name)

func _collect(node: Node, klass) -> Array:
	var out := []
	if is_instance_of(node, klass):
		out.append(node)
	for child in node.get_children():
		out.append_array(_collect(child, klass))
	return out

func _find_player(node: Node) -> AnimationPlayer:
	if node is AnimationPlayer:
		return node
	for child in node.get_children():
		var found := _find_player(child)
		if found != null:
			return found
	return null