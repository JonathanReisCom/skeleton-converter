extends Node
## Boot scene for the web preview: instantiate the converted scene, loop its
## first animation, publish the animation list to the browser shell, and frame
## the rig. The converter rewrites SCENE below with the actual output name
## before exporting — web builds have no environment to pass it.

const SCENE := "res://__SCENE__.tscn"

var _player: AnimationPlayer
var _js_play: JavaScriptObject
var _js_freeze: JavaScriptObject
var _js_cam: JavaScriptObject

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
	# A native scene's AnimationTree overrides whatever the AnimationPlayer
	# plays (the demo drives playback from its own state machine) — silence it
	# so the track buttons and the autoplay actually own the pose.
	for tree in _collect(instance, AnimationTree):
		tree.active = false

	# Camera: same framing recipe as the Spine viewer template — bounds over
	# bones AND attachment vertices, fit both axes, 1.2 margin — so both
	# previews frame the rig identically for side-by-side inspection.
	var cam := Camera2D.new()
	var min_p := Vector2(INF, INF)
	var max_p := Vector2(-INF, -INF)
	var saw_content := false
	for bone in _collect(instance, Bone2D):
		min_p = min_p.min(bone.global_position)
		max_p = max_p.max(bone.global_position)
		saw_content = true
	for poly in _collect(instance, Polygon2D):
		var xform: Transform2D = poly.get_global_transform()
		var offset: Vector2 = poly.offset
		for point in poly.polygon:
			# Native grammar: world = nodeTransform * (vertex + offset) — the
			# offset shifts the drawn mesh and must be part of the framing.
			min_p = min_p.min(xform * (point + offset))
			max_p = max_p.max(xform * (point + offset))
			saw_content = true
	if saw_content:
		var rect := Rect2(min_p, max_p - min_p)
		cam.position = rect.get_center()
		var viewport: Vector2 = get_viewport().get_visible_rect().size
		# Godot's zoom divides the viewport (visible = viewport / zoom); the
		# Spine camera multiplies it — hence the reciprocal of the fit ratio.
		cam.zoom = Vector2.ONE * min(
			viewport.x / max(rect.size.x, 1.0) / 1.2,
			viewport.y / max(rect.size.y, 1.0) / 1.2)
	add_child(cam)
	cam.make_current()

	if names.size() > 0:
		_player.play(names[0])
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
		# Freeze+seek for deterministic side-by-side comparison with the
		# Spine viewer's window.__state.trackTime: (anim, time).
		_js_freeze = JavaScriptBridge.create_callback(Callable(self, "_web_freeze"))
		win.previewFreeze = _js_freeze
		# Camera diagnostics for the side-by-side comparison workflow: the
		# shell receives the framing numbers through a created callback.
		_js_cam = JavaScriptBridge.create_callback(Callable(self, "_report_cam"))
		win.previewCamRequest = _js_cam
		win.previewAnims(",".join(names))

func _report_cam(_args: Array) -> void:
	var cam := get_viewport().get_camera_2d()
	var win := JavaScriptBridge.get_interface("window")
	win.previewCamData("viewport=%s|zoom=%s|center=%s" % [
		get_viewport().get_visible_rect().size,
		cam.zoom if cam else Vector2.ONE,
		cam.global_position if cam else Vector2.ZERO])

func _web_freeze(args: Array) -> void:
	# args = [name: String, time: float] wrapped in one Array by
	# create_callback: args[0][0] is the name, args[0][1] the time.
	if args.is_empty() or args[0].size() < 2:
		return
	var name: String = str(args[0][0])
	var time: float = float(args[0][1])
	if _player != null and _player.has_animation(name):
		_player.play(name)
		_player.seek(time, true)
		_player.pause()

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