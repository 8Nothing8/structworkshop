/** 从 deepslate 的 SpecialRenderers 导出「方块实体」几何 → packages/mcrender/data/entity_models.json
 *
 * 为什么要导：箱子/告示牌/旗帜/头颅/潜影盒/装饰罐/钟/导管/铜傀儡 在资源包里
 * **没有方块模型**（模型的几何是游戏代码按 NBT/状态现画的），所以 mcrender 以前
 * 只能给它们画一个纯色立方体。deepslate（MIT）里有一份现成的几何表，直接导出来给
 * Python 渲染器用，两个渲染器就同源了。
 *
 * 用法：node tools/export_entity_models.js [输出路径]
 */
'use strict';
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
global.deepslate = require(path.join(ROOT, 'packages/mcstudio/web/vendor/deepslate.umd.cjs'));
const D = global.deepslate;
const OUT = process.argv[2] ||
  path.join(ROOT, 'packages/mcrender/data/entity_models.json');

const DYES = ['white', 'orange', 'magenta', 'light_blue', 'yellow', 'lime', 'pink',
  'gray', 'light_gray', 'cyan', 'purple', 'blue', 'brown', 'green', 'red', 'black'];
const WOODS = ['oak', 'spruce', 'birch', 'jungle', 'acacia', 'dark_oak', 'mangrove',
  'cherry', 'pale_oak', 'bamboo', 'crimson', 'warped'];
const HEADS = {
  skeleton_skull: 'skeleton/skeleton',
  wither_skeleton_skull: 'skeleton/wither_skeleton',
  zombie_head: 'zombie/zombie',
  creeper_head: 'creeper/creeper',
  dragon_head: 'enderdragon/dragon',
  piglin_head: 'piglin/piglin',
  player_head: 'player/wide/steve',
};
const COPPER_GOLEMS = ['copper_golem', 'exposed_copper_golem', 'weathered_copper_golem',
  'oxidized_copper_golem', 'waxed_copper_golem', 'waxed_exposed_copper_golem',
  'waxed_weathered_copper_golem', 'waxed_oxidized_copper_golem'];
//: 箱子一族 → 贴图后缀（本版 deepslate 的 chest 纹理名）
const CHESTS = {
  chest: 'normal', ender_chest: 'ender', trapped_chest: 'trapped',
  copper_chest: 'copper', exposed_copper_chest: 'copper_exposed',
  weathered_copper_chest: 'copper_weathered', oxidized_copper_chest: 'copper_oxidized',
  waxed_copper_chest: 'copper', waxed_exposed_copper_chest: 'copper_exposed',
  waxed_weathered_copper_chest: 'copper_weathered',
  waxed_oxidized_copper_chest: 'copper_oxidized',
};

/** 一个状态「应该」用的贴图集合（第一个是主贴图）。
 *
 * 为什么需要它：deepslate 的材质缓存是按“同一段 UV 区间”复用的，把一个状态烘完
 * 再烘另一个状态时，某些面会认到**别的方块**的贴图（实测彩色旗帜有 6 个面被认成
 * ``entity/bed/<色>``、``waxed_copper_chest`` 被认成 ``copper_exposed``、导管会把
 * 水的贴图算进来）。从图集区间反推的贴图不在期望集合里 → 用主贴图顶回去。
 */
function expectedTextures(name, props) {
  const allow = (list) => ({ primary: list[0], all: new Set(list) });
  if (CHESTS[name]) return allow(['entity/chest/' + CHESTS[name]]);
  if (name === 'bell') return allow(['entity/bell/bell_body']);
  if (name === 'conduit') {
    return allow(['entity/conduit/base', 'block/water_still', 'block/water_flow']);
  }
  if (name === 'decorated_pot') {
    return allow(['entity/decorated_pot/decorated_pot_side',
                  'entity/decorated_pot/decorated_pot_base']);
  }
  if (HEADS[name]) return allow(['entity/' + HEADS[name]]);
  for (const dye of DYES) {
    if (name === dye + '_bed') return allow(['entity/bed/' + dye]);
    if (name === dye + '_shulker_box') return allow(['entity/shulker/shulker_' + dye]);
    if (name === dye + '_banner' || name === dye + '_wall_banner') {
      return allow(['entity/banner/banner_base']);
    }
  }
  for (const w of WOODS) {
    if (name === w + '_sign' || name === w + '_wall_sign') {
      return allow(['entity/signs/' + w]);
    }
    if (name === w + '_hanging_sign' || name === w + '_wall_hanging_sign') {
      return allow(['entity/signs/hanging/' + w]);
    }
  }
  for (const n of COPPER_GOLEMS) {
    if (name === n + '_statue') {
      // 贴图文件叫 copper_golem_exposed / copper_golem_weathered / copper_golem_oxidized
      const ox = n.replace(/^waxed_/, '');
      const suffix = ox === 'copper_golem' ? '' : '_' + ox.replace(/_copper_golem$/, '');
      return allow(['entity/copper_golem/copper_golem' + suffix]);
    }
  }
  return null;   // 没规定 → 不动
}

/** 枚举要导出的 (state 字符串, props) —— 只列渲染器真正会读的属性。 */
function states() {
  const out = [];
  const add = (name, props) => out.push({ name, props: props || {} });
  const CHEST_KINDS = Object.keys(CHESTS);
  for (const n of CHEST_KINDS) {
    add(n, { facing: 'south', waterlogged: 'false' });
  }
  add('shulker_box', { facing: 'up' });          // 无色潜影盒也导一份
  for (const c of DYES) {
    add(`${c}_bed`, { facing: 'south', part: 'foot', occupied: 'false' });
    add(`${c}_bed`, { facing: 'south', part: 'head', occupied: 'false' });
    add(`${c}_shulker_box`, { facing: 'up' });
    add(`${c}_banner`, { rotation: '0' });
    add(`${c}_wall_banner`, { facing: 'south' });
  }
  for (const w of WOODS) {
    add(`${w}_sign`, { rotation: '0', waterlogged: 'false' });
    add(`${w}_wall_sign`, { facing: 'south', waterlogged: 'false' });
    add(`${w}_hanging_sign`, { rotation: '0', attached: 'false', waterlogged: 'false' });
    add(`${w}_wall_hanging_sign`, { facing: 'south', waterlogged: 'false' });
  }
  for (const n of Object.keys(HEADS)) add(n, { rotation: '0', powered: 'false' });
  add('bell', { attachment: 'floor', facing: 'south', powered: 'false', toggle: 'false' });
  add('conduit', { waterlogged: 'true' });
  add('decorated_pot', { facing: 'south', cracked: 'false', waterlogged: 'false' });
  for (const n of COPPER_GOLEMS) {
    for (const pose of ['standing', 'sitting', 'star'])
      add(`${n}_statue`, { facing: 'south', copper_golem_pose: pose, waterlogged: 'false' });
  }
  return out;
}

/** 假图集：每个贴图给一段唯一区间，导完从区间反推用了哪张贴图。 */
function makeStubAtlas() {
  const index = new Map();
  const part = 1 / 4096;
  return {
    index,
    getTextureUV(id) {
      const key = id.toString();
      if (!index.has(key)) index.set(key, index.size + 1);
      const i = index.get(key);
      return [i * part, 0, i * part + part, part];
    },
    getTextureAtlas: () => ({ width: 4096, height: 16 }),
    getPixelSize: () => part / 16,
  };
}

function dirOf(n) {
  const ax = Math.abs(n[0]), ay = Math.abs(n[1]), az = Math.abs(n[2]);
  if (ay >= ax && ay >= az) return n[1] > 0 ? 'up' : 'down';
  if (ax >= az) return n[0] > 0 ? 'east' : 'west';
  return n[2] > 0 ? 'south' : 'north';
}

function main() {
  const atlas = makeStubAtlas();
  const part = 1 / 4096;
  const models = {};
  const textures = new Set();
  let missing = 0;
  let repaired = 0;
  for (const { name, props } of states()) {
    const state = new D.BlockState('minecraft:' + name, props);
    let mesh;
    try {
      mesh = D.SpecialRenderers.getBlockMesh(state, undefined, atlas, {});
    } catch (e) {
      missing += 1;
      continue;
    }
    if (!mesh || !mesh.quads.length) { missing += 1; continue; }
    const exp = expectedTextures(name, props);
    const quads = [];
    for (const q of mesh.quads) {
      const vs = q.vertices();
      const u0 = Math.min(...vs.map((v) => v.texture[0]));
      const v0 = Math.min(...vs.map((v) => v.texture[1]));
      const u1 = Math.max(...vs.map((v) => v.texture[0]));
      const v1 = Math.max(...vs.map((v) => v.texture[1]));
      const tileIdx = Math.round(u0 / part);
      let tex = null;
      for (const [k, i] of atlas.index) if (i === tileIdx) { tex = k.replace(/^minecraft:/, ''); break; }
      if (!tex) continue;
      if (exp && !exp.all.has(tex)) { tex = exp.primary; repaired += 1; }   // 认错了 → 顶回期望贴图
      textures.add(tex);
      const pos = [], uv = [], color = [];
      for (const v of vs) {
        pos.push(round(v.pos.x), round(v.pos.y), round(v.pos.z));
        // 贴图内 0..1 坐标（去掉图集区间偏移）
        uv.push(round((v.texture[0] - u0) / Math.max(1e-9, u1 - u0)),
                round((v.texture[1] - v0) / Math.max(1e-9, v1 - v0)));
        const c = v.color || [1, 1, 1];
        color.push(round(c[0]), round(c[1]), round(c[2]));
      }
      const n = q.normal();
      quads.push({ pos, uv, tex, dir: dirOf([n.x, n.y, n.z]), color });
    }
    if (!quads.length) { missing += 1; continue; }
    models[state.toString()] = { block: name, quads };
  }
  const payload = {
    _note: 'generated by tools/export_entity_models.js from deepslate (MIT) SpecialRenderers',
    part,
    textures: [...textures].sort(),
    models,
  };
  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, JSON.stringify(payload), { encoding: 'utf-8' });
  const bytes = fs.statSync(OUT).size;
  console.log(`${Object.keys(models).length} states, ${textures.size} textures, ` +
    `${missing} skipped, ${repaired} 贴图纠正 -> ${OUT} (${(bytes / 1024).toFixed(1)} KB)`);
}

function round(x) {
  return Math.round(x * 10000) / 10000;
}

main();
