extends SceneTree

# Samples one pose of a Godot Skeleton2D scene and prints bone world positions,
# so the same pose can be compared against the Spine runtime's numbers.
#
#   godot --headless --path <project> --script res://sample_pose.gd -- \
#         <scene.tscn> <animation> <time> [scale]
#
# The optional scale divides out a parent Sprite2D scale (Godot scenes often
# carry one) so the numbers land in skeleton space, which is what the Spine
# runtime reports.

func _init():
	var args := OS.get_cmdline_user_args()
	var scene_path: String = args[0] if args.size() > 0 else "res://player/player.tscn"
	var animation: String = args[1] if args.size() > 1 else "walk"
	var sample_time: float = float(args[2]) if args.size() > 2 else 0.3
	var scale: float = float(args[3]) if args.size() > 3 else 1.0

	var scene: Node = (load(scene_path) as PackedScene).instantiate()
	# Silence any AnimationTree BEFORE adding the scene to the tree: the tree
	# would otherwise apply the demo's state machine during the first process
	# frame (before our seek), leaving residual rotations on the bones.
	for tree in _find_all(scene, AnimationTree):
		tree.active = false
		# The demo's player.gd re-enables the tree in its own _ready (which
		# runs inside add_child, after our flag) — disabling PROCESSING also
		# keeps it inert whatever the script does.
		tree.process_mode = Node.PROCESS_MODE_DISABLED
	root.add_child(scene)
	await process_frame
	var player: AnimationPlayer = scene.get_node("AnimationPlayer")
	player.play(animation)
	player.seek(sample_time, true)
	player.pause()  # freeze so the sampled time is exactly sample_time

	var skeleton: Skeleton2D = scene.get_node("Sprite2D/Skeleton2D")
	var skeleton_origin: Vector2 = skeleton.get_global_transform().origin
	for bone in _all_bones(skeleton):
		var transform: Transform2D = bone.get_global_transform()
		# Report in skeleton space, un-scaled, Y still down (the comparator flips).
		var position := (transform.origin - skeleton_origin) / scale
		print("POSE ", bone.name, " ", position.x, " ", position.y)
		print("ROT ", bone.name, " ", rad_to_deg(bone.get_global_transform().get_rotation()),
				" local=", rad_to_deg(bone.rotation))
		# Scale-track diagnostic: the LOCAL scale an AnimationPlayer track
		# wrote (not the accumulated one), beside the global accumulated one.
		print("SCALE ", bone.name, " ", bone.scale.x, " ", bone.scale.y,
				" global=", transform.get_scale().x, ":", transform.get_scale().y)
	# Attachment-timeline diagnostic: which Polygon2D of each slot is drawn.
	# The slot rides as metadata (see README, "Every skin entry is carried");
	# scenes from other sources fall back to the node name as the slot.
	for poly in _find_all(scene, Polygon2D):
		var slot: String = str(poly.get_meta("slot", poly.name))
		print("VIS ", slot, " ", poly.name, " ", 1 if poly.visible else 0)
	quit()

func _all_bones(node: Node) -> Array:
	var out := []
	for child in node.get_children():
		if child is Bone2D:
			out.append(child)
			out.append_array(_all_bones(child))
	return out

func _find_all(node: Node, klass) -> Array:
	var out: Array = []
	if is_instance_of(node, klass):
		out.append(node)
	for child in node.get_children():
		out.append_array(_find_all(child, klass))
	return out
