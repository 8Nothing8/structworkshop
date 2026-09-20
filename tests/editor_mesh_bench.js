/** 编辑器网格构建基准：**我们的 mesher** vs **deepslate 的 ChunkBuilder**。
 *
 * 目的（用户要求「真的更快、更省」）：同一台机器、同一份结构、同一份语义，
 * 分别测：
 *   - 首次全量建网格（ms）
 *   - 单次涂刷（1 个 chunk 增量）延迟（ms）
 *   - 全量重建（图层滑条路径）（ms）
 *   - JS 堆占用（MB）
 *   - 四边形数（保证「更快」不是因为少画东西）
 *
 * 两种实现跑在**独立子进程**里（避免堆互相污染 / V8 优化互相影响），
 * 父进程汇总并断言预算。
 *
 * 用法：
 *   node tests/editor_mesh_bench.js                      # 默认：办公楼 + 太空探索者
 *   node tests/editor_mesh_bench.js --big                # 加上 590k 块的大结构
 *   node tests/editor_mesh_bench.js --impl=ours          # 只跑一种（调试用）
 *   node tests/editor_mesh_bench.js --json               # 机器可读
 *
 * 退出码：预算不达标 → 1（回归用）。
 */
'use strict';

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const CACHE = path.join(ROOT, '.cache', 'mcassets', '26.2');
const SEM_FILE = path.join(ROOT, '.cache', 'mcstudio', 'blocks_26.2.json');
const DEEPSLATE = path.join(ROOT, 'packages/mcstudio/web/vendor/deepslate.umd.cjs');
const GLMATRIX = path.join(ROOT, 'packages/mcstudio/web/vendor/gl-matrix.umd.js');
const RENDERER3D = path.join(ROOT, 'packages/mcstudio/web/renderer3d.js');

const B = require('./_browser.js');

const STRUCTURES = {
  office: 'builds/天际线办公系列/models/高层写字楼.schem',
  space: 'builds/太空探索者纪念中心/太空探索者纪念中心.schem',
  village: 'builds/苏联赫鲁晓夫楼/苏联赫鲁晓夫楼.schem',
};

// ---------------------------------------------------------------- 共用工具（子进程也用）
function loadStructure(rel) {
  const D = global.deepslate;
  const file = path.join(ROOT, rel);
  const root = D.NbtFile.read(fs.readFileSync(file)).root;
  const sc = root.get('Schematic');
  const top = (sc && sc.isCompound && sc.isCompound()) ? sc : root;
  const sx = Math.abs(Number(top.getNumber('Width')));
  const sy = Math.abs(Number(top.getNumber('Height')));
  const sz = Math.abs(Number(top.getNumber('Length')));
  const pj = top.getCompound('Palette').toJson();
  const bd = (top.get('BlockData') || top.get('Data')).toJson().map(x => x & 0xFF);
  const n = sx * sy * sz;
  const raw = new Uint16Array(n);
  let p = 0;
  for (let i = 0; i < n; i++) {
    let v = 0, shift = 0, b;
    do { b = bd[p++]; v |= (b & 0x7F) << shift; shift += 7; } while (b & 0x80);
    raw[i] = v;
  }
  const idxToName = {};
  for (const [name, obj] of Object.entries(pj)) idxToName[Number(obj && obj.value !== undefined ? obj.value : obj)] = name;
  const states = [{ raw: 'minecraft:air', state: new D.BlockState('minecraft:air') }];
  const map = {};
  for (let i = 0; i < n; i++) {
    const v = raw[i];
    if (map[v] === undefined) {
      const nm = idxToName[v] || 'minecraft:air';
      if (/^(minecraft:)?(cave_air|void_air|air)$/.test(nm)) map[v] = 0;
      else {
        const st = D.BlockState.parse(nm);
        map[v] = states.length;
        states.push({ raw: nm, state: st });
      }
    }
    raw[i] = map[v];
  }
  let blocks = 0;
  for (let i = 0; i < n; i++) if (raw[i]) blocks++;
  return { size: [sx, sy, sz], sx, sy, sz, voxels: raw, states, blocks, cells: n };
}

function makeResources(states, semPath) {
  const D = global.deepslate;
  const summary = JSON.parse(fs.readFileSync(path.join(CACHE, '_blocks_summary.json'), 'utf8'));
  const defs = {}, models = {};
  const names = [...new Set(states.map(s => s.state.getName().toString()))];
  for (const name of names) {
    const rel = name.replace(/^minecraft:/, '');
    const f = path.join(CACHE, 'blockstates', rel + '.json');
    if (fs.existsSync(f)) {
      try { defs[name] = D.BlockDefinition.fromJson(JSON.parse(fs.readFileSync(f, 'utf8'))); } catch (e) {}
    }
  }
  const refs = new Set();
  for (const s of states) {
    const def = defs[s.state.getName().toString()];
    if (!def) continue;
    const e = summary[s.state.getName().toString().replace(/^minecraft:/, '')] || {};
    const props = Object.assign({}, e[1] || {}, s.state.getProperties());
    let variants = [];
    try { variants = def.getModelVariants(props) || []; } catch (err) {}
    for (const v of variants) if (v && v.model) refs.add(v.model.includes(':') ? v.model : 'minecraft:' + v.model);
  }
  const collect = (id) => {
    if (id in models) return;
    const rel = id.replace(/^minecraft:/, '');
    const f = path.join(CACHE, 'models', rel + '.json');
    let json = null;
    if (fs.existsSync(f)) { try { json = JSON.parse(fs.readFileSync(f, 'utf8')); } catch (e) {} }
    models[id] = json;
    if (json && json.parent && !String(json.parent).startsWith('builtin/')) {
      collect(String(json.parent).includes(':') ? json.parent : 'minecraft:' + json.parent);
    }
  };
  for (const id of refs) collect(id);
  const built = {};
  for (const [id, json] of Object.entries(models)) {
    if (json) { try { built[id] = D.BlockModel.fromJson(json); } catch (e) {} }
  }
  const accessor = { getBlockModel: (mid) => built[mid.toString()] || null };
  for (const id of Object.keys(built)) { try { built[id].flatten(accessor); } catch (e) {} }
  const semFile = (() => {
    const f = semPath || SEM_FILE;
    if (!fs.existsSync(f)) return {};
    const raw = JSON.parse(fs.readFileSync(f, 'utf8'));
    return raw.info ? raw.info : raw;      // 基准生成器输出 {version, info, textures}
  })();
  // 假图集：像生产环境一样「tile 0 = 缺贴图，真实 tile 从 1 开始」，
  // 否则 mesher 的「缺贴图不画」判定会把所有面都当成缺图（基准会失真）。
  const ATLAS_COLS = 64;
  const texIndex = new Map();
  // deepslate 的 getBlockFlags 只按**方块名**查（编辑器 viewer3d.reloadFlags 也是这么做的）
  // —— 这里合成一份 name → flags（同名多状态：opaque 取交集，self_culling/semi 取并集）
  const flagsByName = {};
  const semByName = {};
  for (const s of states) {
    if (s.raw === 'minecraft:air') continue;
    const name = s.state.getName().toString();
    const f = semFile[s.raw];
    if (!f) continue;
    semByName[name] = semByName[name] || [];
    semByName[name].push(f);
  }
  for (const [name, list] of Object.entries(semByName)) {
    flagsByName[name] = {
      opaque: list.every(f => f.opaque === true),
      self_culling: list.some(f => f.self_culling === true),
      semi_transparent: list.some(f => f.semi_transparent === true),
    };
  }
  return {
    defs, models: built, pixelSize: 1 / 16,
    getBlockDefinition: (id) => defs[id.toString()] || null,
    getBlockModel: (id) => built[id.toString()] || null,
    getTextureAtlas: () => ({ width: ATLAS_COLS * 16, height: ATLAS_COLS * 16 }),
    getTextureUV: (id) => {
      const key = id.toString();
      let i = texIndex.get(key);
      if (i === undefined) { i = texIndex.size + 1; texIndex.set(key, i); }
      const part = 1 / ATLAS_COLS;
      const tx = i % ATLAS_COLS, ty = Math.floor(i / ATLAS_COLS);
      return [part * tx, part * ty, part * (tx + 1), part * (ty + 1)];
    },
    getPixelSize: () => 1 / (ATLAS_COLS * 16),
    getBlockFlags: (id) => flagsByName[id.toString()] || {},
    getBlockProperties: (id) => (semFile[id.toString()] || {}).properties || null,
    getDefaultBlockProperties: (id) => {
      const e = summary[id.toString().replace(/^minecraft:/, '')] || [];
      return e[1] || null;
    },
    _semFile: semFile,
  };
}

function semanticsFor(st, resources) {
  const semFile = resources._semFile || {};
  return st.states.map((s, i) => {
    const f = semFile[s.raw] || {};
    return Object.assign({
      name: i === 0 ? 'minecraft:air' : s.state.getName().toString(),
      layer: 0, tint: null, liquid: null, special: null, has_elements: true,
      ao_occluder: false, render: 'normal', opaque: false,
      self_culling: false, semi_transparent: false,
    }, f, { name: i === 0 ? 'minecraft:air' : s.state.getName().toString() });
  });
}

/** 高精度计时（ms，浮点）：亚毫秒的增量会被 Math.round 抹成 0，比率就废了。 */
function ms() { return Number(process.hrtime.bigint()) / 1e6; }

function stubGL() {
  return new Proxy({ canvas: { clientWidth: 800, clientHeight: 600 } }, {
    get: (t, p) => {
      if (p in t) return t[p];
      if (typeof p === 'string' && p === p.toUpperCase()) return 1;
      return () => true;
    },
  });
}

// ---------------------------------------------------------------- 单实现跑一次
function runImpl(impl, structures, semPath) {
  global.deepslate = require(DEEPSLATE);
  global.glMatrix = require(GLMATRIX);
  require(RENDERER3D);
  const D = global.deepslate;
  const R3 = global.McRender3D;
  const out = {};

  for (const key of structures) {
    const rel = STRUCTURES[key];
    const st = loadStructure(rel);
    const auto = path.join(ROOT, '.cache', 'mcstudio', `bench_sem_${key}.json`);
    const resources = makeResources(st.states,
      (semPath && fs.existsSync(semPath) && structures.length === 1) ? semPath
        : (fs.existsSync(auto) ? auto : semPath));
    const sem = semanticsFor(st, resources);
    const t0 = ms();
    const rows = { size: st.size, blocks: st.blocks, cells: st.cells };
    if (global.gc) { global.gc(); global.gc(); }
    const mem0 = process.memoryUsage();

    if (impl === 'ours') {
      const env = new R3.StructureEnv({
        size: st.size, voxels: st.voxels, blockStates: st.states.map(s => s.state),
        semantics: sem,
      });
      const mesher = new R3.Mesher(env, resources);
      const nx = Math.ceil(st.sx / 16), ny = Math.ceil(st.sy / 16), nz = Math.ceil(st.sz / 16);
      let quads = 0;
      const tA = ms();
      for (let cx = 0; cx < nx; cx++) {
        for (let cy = 0; cy < ny; cy++) {
          for (let cz = 0; cz < nz; cz++) {
            const m = mesher.buildChunk(cx, cy, cz);
            // 三个桶都要算（nocull = 不做背面剔除的异形/带透明像素方块）
            quads += m.solid.quads + m.nocull.quads + m.trans.quads;
          }
        }
      }
      rows.initial = ms() - tA;
      rows.quads = quads;
      rows.missing = mesher.missing;
      // 增量：改一格（在第一个实心方块所在 chunk），只重建那一个块
      let bi = 0;
      for (let i = 0; i < st.voxels.length; i++) if (st.voxels[i]) { bi = i; break; }
      const x = bi % st.sx, z = Math.floor(bi / st.sx) % st.sz, y = Math.floor(bi / (st.sx * st.sz));
      const cx = Math.floor(x / 16), cy = Math.floor(y / 16), cz = Math.floor(z / 16);
      st.voxels[bi] = st.voxels[bi] === 1 ? 2 : 1;       // 真的改一格
      const tB = ms();
      mesher.buildChunk(cx, cy, cz);                      // 一个 chunk = 4096 格
      rows.incr = ms() - tB;
      const tB2 = ms();
      for (let dx = -1; dx <= 1; dx++) {
        for (let dy = -1; dy <= 1; dy++) {
          for (let dz = -1; dz <= 1; dz++) mesher.buildChunk(cx + dx, cy + dy, cz + dz);
        }
      }
      rows.incr27 = ms() - tB2;                     // 编辑器实际一次涂刷（±1 邻域）
      const tC = ms();
      for (let cx2 = 0; cx2 < nx; cx2++) {
        for (let cy2 = 0; cy2 < ny; cy2++) {
          for (let cz2 = 0; cz2 < nz; cz2++) mesher.buildChunk(cx2, cy2, cz2);
        }
      }
      rows.full = ms() - tC;
      rows.bakeCache = mesher.baked.size;
      rows.chunks = nx * ny * nz;
    } else {
      const provider = {
        getSize: () => [st.sx, st.sy, st.sz],
        getBlock: (pos) => {
          const [x, y, z] = pos;
          if (x < 0 || y < 0 || z < 0 || x >= st.sx || y >= st.sy || z >= st.sz) return null;
          const v = st.voxels[(y * st.sz + z) * st.sx + x];
          return { pos, state: st.states[v === 0 ? 0 : v].state };
        },
        *getBlocks() {
          for (let y = 0; y < st.sy; y++) for (let z = 0; z < st.sz; z++) {
            const row = (y * st.sz + z) * st.sx;
            for (let x = 0; x < st.sx; x++) {
              const v = st.voxels[row + x];
              if (v === 0) continue;
              yield { pos: [x, y, z], state: st.states[v].state };
            }
          }
        },
      };
      // 与编辑器一致：特判方块自己有模型时不叠旧几何（viewer3d.js 里的补丁）
      const semByName = {};
      for (const sm of sem) semByName[sm.name] = sm;
      const origSpecial = D.SpecialRenderers.getBlockMesh;
      D.SpecialRenderers.getBlockMesh = function (state, nbt, atlas, cull) {
        const sm = semByName[state.getName().toString()];
        if (sm && sm.has_elements) return new D.Mesh();
        return origSpecial.call(this, state, nbt, atlas, cull);
      };
      const gl = stubGL();
      const tA = ms();
      const r = new D.StructureRenderer(gl, provider, resources,
        { chunkSize: 16, useInvisibleBlockBuffer: false });
      rows.initial = ms() - tA;
      let quads = 0;
      for (const m of r.chunkBuilder.getMeshes()) quads += m.quadVertices() / 4;
      rows.quads = quads;
      let bi = 0;
      for (let i = 0; i < st.voxels.length; i++) if (st.voxels[i]) { bi = i; break; }
      const x = bi % st.sx, z = Math.floor(bi / st.sx) % st.sz, y = Math.floor(bi / (st.sx * st.sz));
      st.voxels[bi] = st.voxels[bi] === 1 ? 2 : 1;
      const cx0 = Math.floor(x / 16), cy0 = Math.floor(y / 16), cz0 = Math.floor(z / 16);
      const tB = ms();
      r.updateStructureBuffers([[cx0, cy0, cz0]]);
      rows.incr = ms() - tB;
      const tB2 = ms();
      const box = [];
      for (let dx = -1; dx <= 1; dx++) {
        for (let dy = -1; dy <= 1; dy++) {
          for (let dz = -1; dz <= 1; dz++) box.push([cx0 + dx, cy0 + dy, cz0 + dz]);
        }
      }
      r.updateStructureBuffers(box);
      rows.incr27 = ms() - tB2;
      const tC = ms();
      r.updateStructureBuffers();
      rows.full = ms() - tC;
    }
    if (global.gc) { global.gc(); global.gc(); }
    const mem1 = process.memoryUsage();
    rows.heap = Math.round((mem1.heapUsed - mem0.heapUsed) / 1e6);
    rows.rss = Math.round((mem1.rss - mem0.rss) / 1e6);
    rows.setup = ms() - t0 - rows.initial - rows.incr - (rows.full || 0);
    out[key] = rows;
  }
  return out;
}

// ---------------------------------------------------------------- 父进程
function main() {
  const args = process.argv.slice(2);
  const jsonOnly = args.includes('--json');
  const asSub = args.find(a => a.startsWith('--impl='));
  const onlyImpl = asSub ? asSub.split('=')[1] : null;
  const semArg = args.find(a => a.startsWith('--sem='));
  const semPath = semArg ? semArg.split('=')[1] : null;
  const big = args.includes('--big') || args.includes('--impl');
  let structures = ['office', 'space', 'village'];
  if (!big && !onlyImpl) structures = ['office', 'space', 'village'];
  // 这些结构属于**可选数据**（成品归档 `builds/`，代码仓库里没有）→ 缺了就跳过
  structures = structures.filter((k) => B.hasData(STRUCTURES[k]));
  if (!structures.length) {
    B.skip('bench 需要 builds/ 下的成品结构', 'editor_mesh_bench.js');
  }

  if (onlyImpl) {
    process.stdout.write(JSON.stringify(runImpl(onlyImpl, structures, semPath)));
    return 0;
  }

  // 先用真实 Python 管线生成每个结构的语义（与浏览器 /api/palette-info 同源）
  const sems = {};
  for (const key of structures) {
    const rel = STRUCTURES[key];
    const out = path.join(ROOT, '.cache', 'mcstudio', `bench_sem_${key}.json`);
    const py = spawnSync(process.env.PYTHON || 'python',
      [path.join(ROOT, 'tests', '_bench_semantics.py'), path.join(ROOT, rel), out],
      { cwd: ROOT, encoding: 'utf8', timeout: 600000 });
    if (py.status !== 0 || !fs.existsSync(out)) {
      console.error(`[sem] ${key} 生成失败：`, (py.stderr || py.stdout || '').slice(-400));
      return 1;
    }
    sems[key] = out;
  }

  const results = {};
  for (const impl of ['ours', 'deepslate']) {
    const r = spawnSync(process.execPath, ['--max-old-space-size=4600', '--expose-gc', __filename,
      '--impl=' + impl, ...(big ? ['--big'] : []), ...structures,
      '--sem=' + (sems[structures[0]] || '')],
      { cwd: ROOT, encoding: 'utf8', timeout: 1800000 });
    if (r.status !== 0) {
      console.error(`[${impl}] 失败：`, (r.stderr || '').slice(-600));
      return 1;
    }
    try {
      results[impl] = JSON.parse(r.stdout);
    } catch (e) {
      console.error(`[${impl}] 输出不是 JSON：`, (r.stdout || '').slice(0, 200));
      return 1;
    }
  }

  const rows = [];
  const fails = [];
  for (const key of structures) {
    const a = results.ours[key];
    const b = results.deepslate[key];
    if (!a || !b) continue;
    const speed = (x, y) => (y > 0 ? (x / y) : 0);
    const row = {
      key, size: a.size, blocks: a.blocks,
      initial: [a.initial, b.initial], incr: [a.incr, b.incr],
      incr27: [a.incr27 || 0, b.incr27 || 0],
      full: [a.full, b.full], heap: [a.heap, b.heap],
      rss: [a.rss || 0, b.rss || 0],
      quads: [a.quads, b.quads],
      speedInitial: speed(b.initial, a.initial),
      speedIncr: speed(b.incr, a.incr),
      speedIncr27: speed(b.incr27 || 0, a.incr27 || 0),
      speedFull: speed(b.full, a.full),
      heapRatio: (a.rss || a.heap) / Math.max(1, (b.rss || b.heap)),
      quadDelta: Math.abs(a.quads - b.quads) / Math.max(1, b.quads),
      bakeCache: a.bakeCache,
    };
    rows.push(row);
    if (row.speedInitial < 1.5) fails.push(`${key}: 首次构建只快 ${row.speedInitial.toFixed(2)}×`);
    if (row.speedIncr < 3) fails.push(`${key}: 单块增量只快 ${row.speedIncr.toFixed(2)}×`);
    // ±1 邻域（27 个 chunk）在**小结构**上天然占整结构的大头，优势会小：
    // 只对 ≥5 万块的结构卡预算，小结构只报告不判负。
    if (a.blocks >= 50000 && row.speedIncr27 < 3) {
      fails.push(`${key}: ±1 邻域增量只快 ${row.speedIncr27.toFixed(2)}×`);
    }
    if (row.heapRatio > 1.0) fails.push(`${key}: 堆没省（${row.heapRatio.toFixed(2)}×）`);
    if (row.quadDelta > 0.15) fails.push(`${key}: 四边形数差 ${(row.quadDelta * 100).toFixed(1)}%（可能少画/多画）`);
  }

  if (jsonOnly) {
    console.log(JSON.stringify({ rows, fails }, null, 1));
  } else {
    console.log('\n编辑器网格构建基准（同一结构 / 同一语义，独立进程）\n');
    console.log('结构                          方块数     首次 ms          单块增量 ms       ±1 邻域 ms        全量重建 ms        堆/RSS MB        四边形');
    console.log('                                        ours/deep        ours/deep      ours/deep        ours/deep      ours/deep      ours/deep');
    console.log('-'.repeat(132));
    for (const r of rows) {
      const f = (pair, w1, w2, dec) => {
        const fmt = (x) => (dec === undefined ? String(Math.round(x)) : x.toFixed(dec));
        return `${fmt(pair[0]).padStart(w1)}/${fmt(pair[1]).padStart(w2)}`;
      };
      console.log(
        `${r.key.padEnd(28)} ${String(r.blocks.toLocaleString()).padStart(10)}  ` +
        `${f(r.initial, 7, 7, 1)} (${r.speedInitial.toFixed(1)}×)  ` +
        `${f(r.incr, 5, 6, 1)} (${r.speedIncr.toFixed(0)}×)  ` +
        `${f(r.incr27, 6, 7, 1)} (${r.speedIncr27.toFixed(1)}×)  ` +
        `${f(r.full, 7, 7, 1)} (${r.speedFull.toFixed(1)}×)  ` +
        `${f(r.rss, 5, 5)} (${r.heapRatio.toFixed(2)}×)  ` +
        `${f(r.quads, 7, 7)} (Δ${(r.quadDelta * 100).toFixed(1)}%)`);
    }
    console.log('-'.repeat(132));
    console.log('ours = 本仓 packages/mcstudio/web/renderer3d.js（按状态缓存烘焙 + 只扫脏块 + 平铺数组）');
    console.log('deep = deepslate 0.27.1 StructureRenderer/ChunkBuilder（逐方块烘焙 + O(全结构) 扫描）');
    console.log('\n预算：首次 ≥1.5×、增量 ≥3×、堆不高于 deepslate、四边形数差 ≤15%');
    if (fails.length) {
      console.log('\nFAILED:\n  ' + fails.join('\n  '));
    } else {
      console.log('\nALL PASS（全部结构达到预算）');
    }
  }
  return fails.length ? 1 : 0;
}

if (require.main === module) process.exit(main());
module.exports = { runImpl, STRUCTURES, loadStructure, makeResources, semanticsFor };
