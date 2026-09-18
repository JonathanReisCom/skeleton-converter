// Samples one pose of a Spine JSON with the official runtime and prints each
// bone's world matrix, so it can be compared against the Godot engine's own
// numbers for the same animation time.
//
//   node pose-sample-spine.mjs <skeleton.json> <animation> <time>
//
// Requires @esotericsoftware/spine-core (npm i @esotericsoftware/spine-core).
// A null attachment loader is used: bone transforms and animations parse
// without an atlas, which is all this comparison needs.

import { readFileSync } from "node:fs";
import {
  SkeletonJson, Skeleton, AnimationState, AnimationStateData, Physics,
} from "@esotericsoftware/spine-core";

const [, , jsonPath, animation, timeRaw] = process.argv;
if (!jsonPath || !animation || timeRaw === undefined) {
  console.error("usage: pose-sample-spine.mjs <skeleton.json> <animation> <time>");
  process.exit(2);
}

const nullLoader = {
  newRegionAttachment: () => null,
  newMeshAttachment: () => null,
  newBoundingBoxAttachment: () => null,
  newPathAttachment: () => null,
  newPointAttachment: () => null,
  newClippingAttachment: () => null,
};

const data = new SkeletonJson(nullLoader).readSkeletonData(
  JSON.parse(readFileSync(jsonPath, "utf8"))
);
const skeleton = new Skeleton(data);
const state = new AnimationState(new AnimationStateData(data));
state.setAnimation(0, animation, true);
state.update(Number(timeRaw));
state.apply(skeleton);
skeleton.updateWorldTransform(Physics.update);

const out = {};
for (const bone of skeleton.bones) {
  const pose = bone.appliedPose;
  out[bone.data.name] = [pose.a, pose.b, pose.c, pose.d, pose.worldX, pose.worldY];
}
console.log(JSON.stringify(out));
