// Vértices de mundo de uma malha, pelo runtime oficial, num tempo qualquer.
//
//   node mesh-vertices.mjs <skeleton.json> <animation> <time> <slot> <attachment>
//
// Usado para decidir paridade de deform: o lado Python soma o delta ao modelo e
// reconstrói o mundo; aqui o runtime faz a conta por conta própria.

import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, basename } from "node:path";
import {
  SkeletonJson, Skeleton, AnimationState, AnimationStateData, Physics,
  TextureAtlas, AtlasAttachmentLoader,
} from "@esotericsoftware/spine-core";

const [, , jsonPath, animation, timeRaw, slotName, attName] = process.argv;
if (!jsonPath || !animation || timeRaw === undefined) {
  console.error("usage: mesh-vertices.mjs <json> <animation> <time> <slot> <attachment>");
  process.exit(2);
}

function findAtlas(path) {
  const dir = dirname(path);
  const sibling = join(dir, basename(path).replace(/\.json$/, ".atlas"));
  try { readFileSync(sibling); return sibling; } catch {}
  const candidates = readdirSync(dir).filter((f) => f.endsWith(".atlas"));
  return candidates.length === 1 ? join(dir, candidates[0]) : null;
}

const atlasPath = process.env.SPINE_ATLAS || findAtlas(jsonPath);
const atlas = new TextureAtlas(readFileSync(atlasPath, "utf8"),
  (p) => readFileSync(join(dirname(atlasPath), p)));
const data = new SkeletonJson(new AtlasAttachmentLoader(atlas))
  .readSkeletonData(JSON.parse(readFileSync(jsonPath, "utf8")));
const skeleton = new Skeleton(data);
const state = new AnimationState(new AnimationStateData(data));
state.setAnimation(0, animation, false);
state.update(Number(timeRaw));
state.apply(skeleton);
skeleton.updateWorldTransform(Physics.update);

const slot = skeleton.findSlot(slotName);
const attachment = slot && slot.getAttachment();
if (!attachment) {
  console.error(`no attachment on slot ${slotName}`);
  process.exit(1);
}
// RegionAttachment and MeshAttachment do NOT share a computeWorldVertices
// signature on this runtime — calling the mesh one on a region throws
// "Cannot create property '0' on number '0'", which is how the armour pieces
// (all regions) went unmeasured while every mesh passed.
// A region is always the four corners of its quad, and it does not carry
// `worldVerticesLength` at all.
const isRegion = attachment.constructor.name === "RegionAttachment";
const count = isRegion ? 4 : attachment.worldVerticesLength / 2;
const world = new Float32Array(count * 2);
if (isRegion) {
  attachment.computeWorldVertices(slot, world, 0, 2);
} else {
  attachment.computeWorldVertices(slot, 0, world.length, world, 0, 2);
}
const out = { name: attachment.name, at: Number(timeRaw), count };
out.vertices = Array.from(world, (v) => Math.round(v * 1e4) / 1e4);
console.log(JSON.stringify(out));
