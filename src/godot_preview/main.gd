extends Node
## Boot scene for the web preview: instantiate the converted scene, loop its
## first animation, publish the animation list to the browser shell, and frame
## the rig. The converter rewrites SCENE below with the actual output name
## before exporting — web builds have no environment to pass it.

const SCENE := "res://__SCENE__.tscn"

var _player: AnimationPlayer
var _scene_root: Node
# NodePath (tracks/…/position|rotation_degrees|scale) -> setup value. Godot
# keeps whatever the last animation wrote; a switch must restore the setup
# pose first or tracks absent from the new animation leak the old pose
# (Spine semantics: missing track = setup pose).
var _setup_pose: Dictionary = {}
var _js_play: JavaScriptObject
var _js_freeze: JavaScriptObject
var _js_cam: JavaScriptObject
var _js_inspect: JavaScriptObject

func _ready() -> void:
	var packed: PackedScene = load(SCENE)
	if packed == null:
		push_error("cannot load converted scene: " + SCENE)
		return
	var instance := packed.instantiate()
	add_child(instance)
	_scene_root = instance
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
		# Unequipped attachments ship as visible=false polygons that stick
		# far outside the drawn rig — framing with them shrinks the rig and
		# shifts the center versus the Spine viewer (which frames only what
		# the runtime draws). Skip them so both panes frame identically.
		if not poly.visible:
			continue
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

	_capture_setup_pose()

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
		_js_inspect = JavaScriptBridge.create_callback(Callable(self, "_web_inspect"))
		win.previewInspect = _js_inspect
		win.previewAnims(",".join(names))

func _web_inspect(_args: Array) -> void:
	# Debug hook: report every Bone2D global pose + rest and every polygon
	# node transform + base vertices, so the browser side can recompute the
	# skinning math (bone_pose * bone_rest⁻¹) and compare against the Spine
	# runtime's attachment worlds.
	var win := JavaScriptBridge.get_interface("window")
	var lines := PackedStringArray()
	var skeleton := _find_skeleton(_scene_root)
	for bone in _collect(_scene_root, Bone2D):
		var xf: Transform2D = bone.get_global_transform()
		lines.append("B %s:%.4f,%.4f,%.4f,%.4f,%.2f,%.2f|REST %s:%.4f,%.4f,%.4f,%.4f,%.2f,%.2f" % [
			bone.name, xf.x.x, xf.x.y, xf.y.x, xf.y.y, xf.origin.x, xf.origin.y,
			bone.name, bone.rest.x.x, bone.rest.x.y, bone.rest.y.x, bone.rest.y.y, bone.rest.origin.x, bone.rest.origin.y])
	for poly in _collect(_scene_root, Polygon2D):
		if not poly.visible:
			continue
		var xf: Transform2D = poly.get_global_transform()
		var pts := PackedStringArray()
		for pt in poly.polygon:
			pts.append("%.2f,%.2f" % [pt.x + poly.offset.x, pt.y + poly.offset.y])
		lines.append("P %s@%s:%s" % [poly.name, String(poly.bones), ",".join(pts)])
	win.previewInspectData("|".join(lines))

func _report_cam(_args: Array) -> void:
	var cam := get_viewport().get_camera_2d()
	var win := JavaScriptBridge.get_interface("window")
	win.previewCamData("viewport=%s|zoom=%s|center=%s" % [
		get_viewport().get_visible_rect().size,
		cam.zoom if cam else Vector2.ONE,
		cam.global_position if cam else Vector2.ZERO])

func _capture_setup_pose() -> void:
	# Snapshot every property any animation track touches, so a switch can
	# restore the setup pose first. Godot keeps the last value each track
	# wrote; without the restore, an animation lacking a track for some bone
	# (Dance has no foot tracks) inherits the pose the previous animation
	# left (Walk moved the feet) — Spine semantics are missing track = setup.
	for anim in _player.get_animation_list():
		var animation := _player.get_animation(anim)
		for t in animation.get_track_count():
			var path := animation.track_get_path(t)
			if _setup_pose.has(path):
				continue
			var prop := String(path.get_subname(0)) if path.get_subname_count() > 0 else ""
			var node := _scene_root.get_node_or_null(NodePath(path.get_concatenated_names()))
			if node == null or prop.is_empty():
				continue
			_setup_pose[path] = node.get(prop)

func _restore_setup_pose() -> void:
	for path in _setup_pose:
		var node := _scene_root.get_node_or_null(
			NodePath(NodePath(path).get_concatenated_names()))
		if node != null:
			node.set(NodePath(path).get_subname(0), _setup_pose[path])

func _web_freeze(args: Array) -> void:
	# The JS→GDScript bridge flattens the call: previewFreeze("Walk", 0.5)
	# arrives as args = ["Walk", 0.5]. Some bridges wrap instead ([["Walk",
	# 0.5]]) — handle both. The old guard read args[0].size() on the NAME
	# STRING ("Walk".size() == 4 ≥ 2) and then indexed it into a CHARACTER
	# ("W"), so has_animation failed and the freeze silently did nothing.
	var name: String
	var time: float
	if args.size() >= 2 and not (args[0] is Array):
		name = str(args[0])
		time = float(args[1])
	elif args.size() >= 1 and args[0] is Array and args[0].size() >= 2:
		name = str(args[0][0])
		time = float(args[0][1])
	else:
		print("PREVIEW_FREEZE= bad args ", args)
		return
	print("PREVIEW_FREEZE=", name, " t=", time)
	if _player == null:
		return
	if _player.has_animation(name):
		# A switch must not inherit the previous animation's pose (see
		# _capture_setup_pose) — restore the setup before sampling.
		if name != _player.current_animation:
			_restore_setup_pose()
		_player.play(name)
		_player.seek(time, true)
	# Pause is the requested effect — it must survive an unknown name (the
	# shell freezes before any track button is active). Freeze in place.
	_player.pause()

func _web_play(args: Array) -> void:
	# JavaScriptBridge.create_callback wraps the JS call into one Array of
	# arguments — the button click arrives as args[0], not as a bare String.
	if args.is_empty():
		return
	var name: String
	if args[0] is Array:
		name = str(args[0][0])
	else:
		name = str(args[0])
	print("PREVIEW_PLAY=", name)
	if _player != null and _player.has_animation(name):
		if name != _player.current_animation:
			_restore_setup_pose()
		_player.play(name)

func _collect(node: Node, klass) -> Array:
	var out := []
	if is_instance_of(node, klass):
		out.append(node)
	for child in node.get_children():
		out.append_array(_collect(child, klass))
	return out

func _find_skeleton(node: Node) -> Skeleton2D:
	if node is Skeleton2D:
		return node
	for child in node.get_children():
		var found := _find_skeleton(child)
		if found != null:
			return found
	return null

func _find_player(node: Node) -> AnimationPlayer:
	if node is AnimationPlayer:
		return node
	for child in node.get_children():
		var found := _find_player(child)
		if found != null:
			return found
	return null