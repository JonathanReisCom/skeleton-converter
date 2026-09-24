// Samples one pose of a Spine JSON with the official runtime and prints each
// bone's world matrix, so it can be compared against the Godot engine's own
// numbers for the same animation time.
//
//   node pose-sample-spine.mjs <skeleton.json> <animation> <time>
//
// Requires @esotericsoftware/spine-core (npm i @esotericsoftware/spine-core).
// A null attachment loader is used: bone transforms and animations parse
// without an atlas, which is all this comparison needs.

import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, basename } from "node:path";
import {
  SkeletonJson, Skeleton, AnimationState, AnimationStateData, Physics,
  TextureAtlas, AtlasAttachmentLoader,
} from "@esotericsoftware/spine-core";

const [, , jsonPath, animation, timeRaw] = process.argv;
if (!jsonPath || !animation || timeRaw === undefined) {
  console.error("usage: pose-sample-spine.mjs <skeleton.json> <animation> <time>");
  process.exit(2);
}

// The atlas is required: weighted mesh attachments cannot be built by a null
// loader (it throws on `attachment.bones`), and the page size it declares is
// what UVs are computed against. Prefer a sibling .atlas, else the only one
// beside the JSON (hero-pro.json ships with hero.atlas).
function findAtlas(path) {
  const dir = dirname(path);
  const sibling = join(dir, basename(path).replace(/\.json$/, ".atlas"));
  try {
    readFileSync(sibling);
    return sibling;
  } catch {}
  const candidates = readdirSync(dir).filter((f) => f.endsWith(".atlas"));
  return candidates.length === 1 ? join(dir, candidates[0]) : null;
}

const atlasPath = process.env.SPINE_ATLAS || findAtlas(jsonPath);
const atlas = atlasPath
  ? new TextureAtlas(readFileSync(atlasPath, "utf8"), (p) =>
      readFileSync(join(dirname(atlasPath), p)))
  : null;
const loader = atlas ? new AtlasAttachmentLoader(atlas) : nullLoader;

const data = new SkeletonJson(loader).readSkeletonData(
  JSON.parse(readFileSync(jsonPath, "utf8"))
);
const skeleton = new Skeleton(data);
const state = new AnimationState(new AnimationStateData(data));
// Looping is OFF deliberately: the Godot AnimationPlayer sampler freezes
// past the last key, and a looping Spine track would wrap to t=0 instead —
// comparing t beyond the animation length would then be invalid.
state.setAnimation(0, animation, false);
state.update(Number(timeRaw));
state.apply(skeleton);
skeleton.updateWorldTransform(Physics.update);

const out = {};
for (const bone of skeleton.bones) {
  // spine-core 4.2 exposes the world transform directly on the Bone
  // (4.3 renamed it to `appliedPose`).
  out[bone.data.name] = [bone.a, bone.b, bone.c, bone.d, bone.worldX, bone.worldY];
}
console.log(JSON.stringify(out));
