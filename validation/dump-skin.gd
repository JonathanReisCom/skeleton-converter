extends SceneTree

# Dumps the SKINNED polygon vertices of a Godot Skeleton2D scene for one pose,
# in skeleton-local space, so they can be compared against the Spine runtime's
# `computeWorldVertices` for the same time.
#
#   godot --headless --path <project> --script res://dump-skin.gd -- \
#         <scene.tscn> <animation> <time>
#
# Godot does the skinning here on purpose. The comparison target is "does the
# Godot scene draw what the Spine runtime draws", and every earlier attempt to
# redo Godot's matrices in Python lost a round to a convention (row-major vs
# column-major `Transform2D`, the skeleton-relative `get_skeleton_rest()` vs the
# global one). The engine applying its own formula removes that whole class of
# mistake.
#
# Every accessor is defensive: an error anywhere in `_init` aborts the script
# before `quit()`, and headless Godot then idles forever instead of failing.
#
# Output, one line per item:
#   BONE <name> <x.x> <x.y> <y.x> <y.y> <ox> <oy>   skeleton-local pose
#   REST <name> <x.x> <x.y> <y.x> <y.y> <ox> <oy>   skeleton-local rest
#   POLY <node> <slot> <attachment> <n> <x0> <y0> ...  skinned, local
#   NOTE <text>                                     something was missing
#
# Space: skeleton-local, Y still down (the comparator negates Y once to reach
# Spine space, exactly like the polygon conversion does).

func _init():
	var args := OS.get_cmdline_user_args()
	var scene_path: String = args[0]
	var animation: String = args[1]
	var sample_time: float = float(args[2])

	var scene: Node = (load(scene_path) as PackedScene).instantiate()
	for tree in _find_all(scene, AnimationTree):
		tree.active = false
		tree.process_mode = Node.PROCESS_MODE_DISABLED
	root.add_child(scene)
	await process_frame
	var player: AnimationPlayer = scene.get_node_or_null("AnimationPlayer")
	if player == null:
		print("NOTE no AnimationPlayer")
	else:
		player.play(animation)
		player.seek(sample_time, true)
		player.pause()  # freeze so the sampled time is exactly sample_time

	var skeleton: Skeleton2D = scene.get_node_or_null("Sprite2D/Skeleton2D")
	if skeleton == null:
		for candidate in _find_all(scene, Skeleton2D):
			skeleton = candidate
			break
	if skeleton == null:
		print("NOTE no Skeleton2D")
		quit()
		return
	var skel_inv: Transform2D = skeleton.get_global_transform().affine_inverse()
	for bone in _all_bones(skeleton):
		_line("BONE", bone.name, skel_inv * bone.get_global_transform())
		_line("REST", bone.name, bone.get_skeleton_rest())

	for poly in _find_all(scene, Polygon2D):
		# Godot 4 packs the bindings into ONE `bones` property, alternating a
		# skeleton-relative path with that bone's weights-per-vertex array:
		#   bones = ["root/hip/body/head", PackedFloat32Array(1, 1, 1, ...)]
		# There is no `weights` property at all (touching it aborts the
		# script, and headless Godot then idles instead of failing).
		var binding = poly.get("bones")
		if binding == null or binding.size() == 0:
			print("NOTE ", poly.name, " has no bone bindings")
		var bone_paths := PackedStringArray()
		var per_bone_weights := []
		if binding != null:
			var entry := 0
			while entry + 1 < binding.size():
				bone_paths.append(str(binding[entry]))
				per_bone_weights.append(binding[entry + 1])
				entry += 2
		var xforms: Array = []
		for b_index in bone_paths.size():
			var bone: Bone2D = skeleton.get_node_or_null(NodePath(bone_paths[b_index]))
			if bone == null:
				print("NOTE ", poly.name, " references missing bone ", bone_paths[b_index])
				xforms.append(Transform2D.IDENTITY)
				continue
			xforms.append(bone.get_global_transform() * bone.get_skeleton_rest().affine_inverse())
		var verts: PackedVector2Array = poly.polygon
		var blended: PackedVector2Array = []
		for i in verts.size():
			var local: Vector2 = poly.position + verts[i]
			if bone_paths.is_empty():
				blended.append(skel_inv * local)
				continue
			var acc := Vector2.ZERO
			for b in bone_paths.size():
				var weights = per_bone_weights[b]
				var w: float = 1.0 if weights == null or weights.size() <= i else weights[i]
				acc += (xforms[b] * local) * w
			blended.append(skel_inv * acc)
		var slot: String = str(poly.get_meta("slot", poly.name))
		var attachment: String = str(poly.get_meta("attachment", poly.name))
		var parts := PackedStringArray(["POLY", poly.name, slot, attachment,
				str(blended.size())])
		for point in blended:
			parts.append(str(point.x))
			parts.append(str(point.y))
		print(" ".join(parts))
	quit()

func _line(kind: String, name: String, xform: Transform2D) -> void:
	print(" ".join([kind, name, str(xform.x.x), str(xform.x.y),
			str(xform.y.x), str(xform.y.y), str(xform.origin.x), str(xform.origin.y)]))

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
