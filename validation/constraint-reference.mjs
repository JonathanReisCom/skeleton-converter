// Ground truth for constraint baking: dumps the world transforms the official
// runtime produces after IK/path constraints run, at the setup pose and at
// sampled animation times. The Python baker in src/constraints.py must match
// these numbers.
//
//   node constraint-reference.mjs <skeleton.json> <atlas> [animation] [time]

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import {
  SkeletonJson, Skeleton, AnimationState, AnimationStateData, Physics,
  TextureAtlas, AtlasAttachmentLoader,
} from "@esotericsoftware/spine-core";

const [, , jsonPath, atlasPath, animation, timeRaw] = process.argv;
const atlas = new TextureAtlas(readFileSync(atlasPath, "utf8"), (p) =>
  readFileSync(join(dirname(atlasPath), p)));
const data = new SkeletonJson(new AtlasAttachmentLoader(atlas)).readSkeletonData(
  JSON.parse(readFileSync(jsonPath, "utf8")));

const skeleton = new Skeleton(data);
// The active skin decides whether a `skin: true` constraint runs. Default to
// the rig's first skin; SPINE_SKIN=<name> picks another (the hero's path
// constraint lives in the "weapon/morningstar" skin).
if (data.skins.length > 0) {
  const wanted = process.env.SPINE_SKIN;
  const skin = wanted ? data.skins.find((s) => s.name === wanted) : data.skins[0];
  if (skin) {
    skeleton.setSkin(skin);
    skeleton.setToSetupPose();
  }
}

if (animation) {
  const state = new AnimationState(new AnimationStateData(data));
  state.setAnimation(0, animation, true);
  state.update(Number(timeRaw ?? 0));
  state.apply(skeleton);
}

skeleton.updateWorldTransform(Physics.update);

const out = { world: {}, local: {} };
for (const bone of skeleton.bones) {
  out.world[bone.data.name] = [
    Math.round(bone.worldX * 1e6) / 1e6,
    Math.round(bone.worldY * 1e6) / 1e6,
  ];
  out.local[bone.data.name] = [
    Math.round(bone.arotation * 1e6) / 1e6,
    Math.round(bone.ax * 1e6) / 1e6,
    Math.round(bone.ay * 1e6) / 1e6,
  ];
}
console.log(JSON.stringify(out));
