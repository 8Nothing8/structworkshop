/* mcstudio 鼠标拾取（3D 体积射线）回归冒烟。
 *
 * 编辑器「放置 / 擦除 / 替换 / 吸管」把鼠标位置翻译成体素坐标，全靠 viewer3d.js 里
 * VoxelViewer.pick() 的体素射线步进（DDA）。旧实现有两个毛病：
 *   1) place（放置位）记的是「命中格再往回第二格」，不是紧贴命中面的那一格
 *      → 某些角度点击会在离表面一格远的地方冒出方块，或算到背后去（负数被丢弃）；
 *   2) 起始格的 tMax 用进入点微调**之前**的坐标算，射线压着格界/棱边时 DDA 会跳格
 *      → 某些地方点不着（返回 null）或点到相邻的另一格。
 * 修好后：pick().place 恒等于 cell + normal（紧贴命中面的入射侧格子）。
 *
 * 本测试用**独立的解析参考实现**（射线 × 每个实心单元的 slab 求交）逐像素比对：
 *   - 参考有命中而 pick() 返回 null → 漏检；参考没命中而有值 → 误报；
 *   - cell 必须落在「最小 t 的并列集合」里；
 *   - place 必须 = cell + normal，且是空格（越界只允许出现在入射侧）；
 *   - 非擦边（唯一最小 t + 不贴格界）时必须与参考完全一致（cell + place）。
 *
 * Usage:  node tests/pick_raycast_smoke.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const VIEWER = path.join(ROOT, 'packages', 'mcstudio', 'web', 'viewer3d.js');
const EDITOR = path.join(ROOT, 'packages', 'mcstudio', 'web', 'editor.js');
const GLM = path.join(ROOT, 'packages', 'mcstudio', 'web', 'vendor', 'gl-matrix.umd.js');

let pass = 0, fail = 0;
function check(name, ok, detail) {
  if (ok) { pass++; console.log('PASS  ' + name + (detail === undefined ? '' : '  ' + JSON.stringify(detail))); }
  else { fail++; console.log('FAIL  ' + name + '  ' + JSON.stringify(detail)); }
}
const eq = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);

// ---------------------------------------------------------------- 加载 viewer
function loadViewer() {
  const sandbox = { window: {}, console, Math, setTimeout, clearTimeout,
                    Uint8Array, Uint16Array, Float64Array, Float32Array, JSON };
  sandbox.window.addEventListener = () => {};
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(GLM, 'utf8'), sandbox);
  sandbox.glMatrix = sandbox.glMatrix || sandbox.window.glMatrix;
  sandbox.deepslate = { BlockState: function () {}, BlockDefinition: function () {},
                        StructureProvider: function () {} };
  vm.runInContext(fs.readFileSync(VIEWER, 'utf8'), sandbox);
  return sandbox.window.McStudio3D;
}

function makeViewer(M, size, solidFn, cam, viewport) {
  const v = Object.create(M.VoxelViewer.prototype);
  v.size = size.slice();
  v.voxels = new Uint16Array(size[0] * size[1] * size[2]);
  for (let y = 0; y < size[1]; y++) {
    for (let z = 0; z < size[2]; z++) {
      for (let x = 0; x < size[0]; x++) {
        if (solidFn(x, y, z)) v.voxels[(y * size[2] + z) * size[0] + x] = 1;
      }
    }
  }
  const vp = viewport || [480, 360];
  v.canvas = { clientWidth: vp[0], clientHeight: vp[1],
               getBoundingClientRect: () => ({ left: 0, top: 0, width: vp[0], height: vp[1] }) };
  v.cam = Object.assign({ yaw: -0.65, pitch: 0.55, dist: 40,
                          target: [size[0] / 2, size[1] / 2, size[2] / 2] }, cam || {});
  v.flat = new Uint16Array(v.voxels);            // 只读：防止测试里被改
  return v;
}

// ------------------------------------------------- 参考实现：解析求交
function solidCells(v) {
  const [sx, sy, sz] = v.size;
  const out = [];
  for (let x = 0; x < sx; x++) {
    for (let y = 0; y < sy; y++) {
      for (let z = 0; z < sz; z++) {
        if (v.voxels[(y * sz + z) * sx + x]) out.push([x, y, z]);
      }
    }
  }
  return out;
}

/** 射线 × 单元 slab 求交（与 DDA 完全独立的算法）。 */
function refPick(cells, o, d) {
  let best = null;
  for (const c of cells) {
    let t0 = -Infinity, t1 = Infinity, ok = true;
    for (let a = 0; a < 3 && ok; a++) {
      if (Math.abs(d[a]) < 1e-12) {
        if (o[a] < c[a] || o[a] > c[a] + 1) ok = false;
        continue;
      }
      let ta = (c[a] - o[a]) / d[a], tb = (c[a] + 1 - o[a]) / d[a];
      if (ta > tb) { const t = ta; ta = tb; tb = t; }
      if (ta > t0) t0 = ta;
      if (tb < t1) t1 = tb;
      if (t0 > t1) ok = false;
    }
    if (!ok || t1 < 0) continue;
    const t = Math.max(t0, 0);
    if (!best || t < best.t - 1e-9) best = { t, cell: c.slice(), tie: [c.slice()] };
    else if (Math.abs(t - best.t) <= 1e-9) best.tie.push(c.slice());
  }
  if (!best) return null;
  if (best.t <= 1e-9) {                          // 起点就在命中格里
    return { cell: best.cell, place: best.cell.slice(), tie: best.tie, t: best.t, sharp: true };
  }
  const p = [0, 1, 2].map((a) => o[a] + d[a] * (best.t - 1e-6));
  const place = p.map(Math.floor);
  // 「锐利」= 唯一最小 t，且入射点不贴格棱/格角（擦边时并列解都算对）：
  // 入射点必然压在**穿越轴**的格界上，所以只查另外两轴：
  //   d[a]≈0（射线与该轴平行）时要求它不在格界平面上；否则要求离格界 > 1e-5。
  let crossed = 0;
  let sharp = best.tie.length === 1;
  const entry = [0, 1, 2].map((a) => o[a] + d[a] * best.t);
  for (let a = 0; a < 3; a++) {
    if (place[a] !== best.cell[a]) { crossed++; continue; }
    const frac = Math.abs(entry[a] - Math.round(entry[a]));
    if (Math.abs(d[a]) < 1e-12) { if (frac < 1e-5) sharp = false; }
    else if (frac < 1e-5 || frac > 1 - 1e-5) sharp = false;
  }
  if (crossed !== 1) sharp = false;
  return { cell: best.cell, place, tie: best.tie, t: best.t, sharp };
}

/** 射线是否穿过某个单位格（格 slab 求交；与 DDA 无关）。 */
function rayHitsCell(o, d, c) {
  let t0 = 0, t1 = Infinity;
  for (let a = 0; a < 3; a++) {
    if (Math.abs(d[a]) < 1e-12) {
      if (o[a] < c[a] || o[a] > c[a] + 1) return false;
      continue;
    }
    let ta = (c[a] - o[a]) / d[a], tb = (c[a] + 1 - o[a]) / d[a];
    if (ta > tb) { const t = ta; ta = tb; tb = t; }
    if (ta > t0) t0 = ta;
    if (tb < t1) t1 = tb;
    if (t0 > t1) return false;
  }
  return t1 >= 0;
}

/** 解析入口点 + 「可接受的入口格」集合。
 *
 * 擦边时（射线正好压在格界平面上）入口点落在整数坐标上，两侧的格子都算对：
 * 这正是 firstInBox 与 DDA 可能差一格的原因（两边用了不同的微推步长）。
 */
function entryAcceptable(v, o, d) {
  const hi = v.size;
  let tEnter = 0;
  for (let a = 0; a < 3; a++) {
    if (Math.abs(d[a]) < 1e-12) {
      if (o[a] < 0 || o[a] > hi[a]) return null;
      continue;
    }
    const t1 = (0 - o[a]) / d[a], t2 = (hi[a] - o[a]) / d[a];
    const tmin = Math.min(t1, t2);
    if (tmin > tEnter) tEnter = tmin;
  }
  const p = [0, 1, 2].map((a) => o[a] + d[a] * (tEnter + 1e-9));
  let cells = [[]];
  for (let a = 0; a < 3; a++) {
    const n = Math.round(p[a]);
    // 正好压在格界上：两侧（n−1 / n）都算对
    const opts = Math.abs(p[a] - n) < 1e-6 ? [n - 1, n] : [Math.floor(p[a])];
    const next = [];
    for (const c of cells) for (const o2 of opts) next.push(c.concat([o2]));
    cells = next;
  }
  return cells;
}

/** place 语义检查：紧贴命中面（= cell + normal）且在入射侧是空格。 */
function placeProblem(v, got) {
  const { cell, place, normal } = got;
  const expect = [cell[0] + normal[0], cell[1] + normal[1], cell[2] + normal[2]];
  if (!eq(place, expect)) return 'place≠cell+normal ' + JSON.stringify({ place, expect });
  const nz = Math.abs(normal[0]) + Math.abs(normal[1]) + Math.abs(normal[2]);
  if (nz !== 0 && nz !== 1) return 'normal 不是单位轴向量 ' + JSON.stringify(normal);
  const [sx, sy, sz] = v.size;
  const inside = place[0] >= 0 && place[1] >= 0 && place[2] >= 0 &&
                 place[0] < sx && place[1] < sy && place[2] < sz;
  if (!inside) return nz === 0 ? 'place 越界却没有入射法向' : null;
  const idx = v.voxels[(place[1] * sz + place[2]) * sx + place[0]];
  if (idx !== 0 && !eq(place, cell)) return 'place 落在实心格 ' + JSON.stringify(place);
  return null;
}

/** 逐像素扫一遍：返回统计与首个反例。 */
function sweep(v, opts) {
  opts = opts || {};
  const step = opts.step || 2;
  const [w, h] = [v.canvas.clientWidth, v.canvas.clientHeight];
  const cells = solidCells(v);
  const st = { rays: 0, hits: 0, missed: 0, spurious: 0, cellBad: 0, placeBad: 0,
               placeOutside: 0, strict: 0, first: {} };
  for (let py = 0; py < h; py += step) {
    for (let px = 0; px < w; px += step) {
      const ev = { clientX: px + 0.5, clientY: py + 0.5 };
      const got = v.pick(ev);
      const r = v.ray(ev);
      const exp = refPick(cells, r.origin, r.dir);
      st.rays++;
      if (!exp && !got) continue;
      if (exp && !got) { st.missed++; st.first.missed = st.first.missed || { px, py, exp }; continue; }
      if (!exp && got) { st.spurious++; st.first.spurious = st.first.spurious || { px, py, got }; continue; }
      st.hits++;
      const inTie = exp.tie.some((c) => eq(c, got.cell));
      if (!inTie) { st.cellBad++; st.first.cellBad = st.first.cellBad || { px, py, got, exp }; }
      const pb = placeProblem(v, got);
      if (pb) { st.placeBad++; st.first.placeBad = st.first.placeBad || { px, py, pb, got }; }
      const [sx, sy, sz] = v.size;
      if (got.place[0] < 0 || got.place[1] < 0 || got.place[2] < 0 ||
          got.place[0] >= sx || got.place[1] >= sy || got.place[2] >= sz) st.placeOutside++;
      if (exp.sharp) {
        st.strict++;
        if (!eq(got.cell, exp.cell) || !eq(got.place, exp.place)) {
          st.strictBad = (st.strictBad || 0) + 1;
          st.first.strict = st.first.strict || { px, py, got, exp };
        }
      }
    }
  }
  return st;
}

// ---------------------------------------------------------------- 用例主体
function main() {
  const M = loadViewer();
  console.log('== 1. 三组结构 × 多机位：逐像素比对解析参考 ==');
  const cases = [
    ['地板 + 方柱（旧实现的翻车场景）', [16, 16, 16], (x, y, z) => y < 3 || (x === 8 && z === 8),
      [{ yaw: -0.65, pitch: 0.55, dist: 40, target: [8, 4, 8] },
       { yaw: 2.2, pitch: -0.2, dist: 26, target: [8, 8, 8] },
       { yaw: 0.4, pitch: 1.2, dist: 33, target: [8, 8, 8] }]],
    ['贴满画布的外壳（边界 / 负方向放置）', [12, 20, 12],
      (x, y, z) => x === 0 || x === 11 || z === 0 || z === 11 || y === 0 || y === 19,
      [{ yaw: -2.3, pitch: 0.3, dist: 44, target: [6, 10, 6] },
       { yaw: 0.9, pitch: -0.9, dist: 38, target: [6, 10, 6] },
       { yaw: -0.65, pitch: 0.55, dist: 30, target: [6, 10, 6] }]],
    ['稀疏棋盘（大量棱边擦边）', [14, 14, 14], (x, y, z) => (x + y + z) % 3 === 0,
      [{ yaw: -0.65, pitch: 0.55, dist: 34, target: [7, 7, 7] },
       { yaw: 1.7, pitch: 0.25, dist: 28, target: [7, 7, 7] },
       { yaw: 3.0, pitch: 0.0, dist: 25, target: [7, 7, 7] }]],
  ];
  let rays = 0, strict = 0;
  for (const [label, size, solid, cams] of cases) {
    let missed = 0, spurious = 0, cellBad = 0, placeBad = 0, strictBad = 0, outside = 0, first = {};
    for (const cam of cams) {
      const v = makeViewer(M, size, solid, cam);
      const st = sweep(v, { step: 2 });
      rays += st.rays; strict += st.strict;
      missed += st.missed; spurious += st.spurious; cellBad += st.cellBad;
      placeBad += st.placeBad; strictBad += (st.strictBad || 0);
      outside += st.placeOutside;
      for (const k of Object.keys(st.first)) if (!first[k]) first[k] = st.first[k];
    }
    const ok = !missed && !spurious && !cellBad && !placeBad && !strictBad;
    check('拾取精确（' + label + '）', ok,
          ok ? { 机位: cams.length, 采样: '每 2 像素' }
             : { missed, spurious, cellBad, placeBad, strictBad, first });
    if (outside) console.log('        （其中 place 落在画布外 = 入射侧没有格子：' + outside + ' 条，编辑器给提示/自动扩容）');
  }
  console.log('   比对射线 ' + rays + ' 条，其中「唯一解」严格比对 ' + strict + ' 条');

  console.log('\n== 2. 旧实现的固定反例（回归钉死）==');
  {
    const size = [16, 16, 16];
    const solid = (x, y, z) => y < 3 || (x === 8 && z === 8);
    const cam = { yaw: -0.65, pitch: 0.55, dist: 40, target: [8, 4, 8] };
    const v = makeViewer(M, size, solid, cam, [800, 600]);
    const cells = solidCells(v);
    const pin = [
      // [px, py, 期望 cell, 期望 place, 旧行为]
      [395, 175, [8, 15, 8], [8, 15, 9], '旧: cell 少走一格 [8,14,8]（柱顶漏一格）'],
      [400, 215, [8, 12, 8], [8, 12, 9], '旧: place 飞到 [9,13,8]（不贴命中面）'],
      [405, 220, [8, 12, 8], [9, 12, 8], '旧: 返回 null（点不着）'],
      [385, 215, null, null, '旧: 误报命中 [8,12,8]（射线其实从旁边擦过）'],
    ];
    for (const [px, py, cell, place, note] of pin) {
      const got = v.pick({ clientX: px + 0.5, clientY: py + 0.5 });
      const r = v.ray({ clientX: px + 0.5, clientY: py + 0.5 });
      const exp = refPick(cells, r.origin, r.dir);           // 参考实现独立复算
      let ok;
      if (cell === null) ok = got === null && exp === null;
      else ok = !!got && eq(got.cell, cell) && eq(got.place, place) &&
                exp !== null && eq(exp.cell, cell) && eq(exp.place, place);
      check('像素 (' + px + ',' + py + ') ' + note, ok,
            ok ? undefined : { got: got && { cell: got.cell, place: got.place },
                               exp: exp && { cell: exp.cell, place: exp.place } });
    }
  }

  console.log('\n== 3. 纯函数 raycast：轴平行 / 格界 / 盒内起点 / 负方向 ==');
  {
    const box = makeViewer(M, [8, 8, 8], () => true, {});
    const air = makeViewer(M, [8, 8, 8], () => false, {});
    let r = box.raycast([-5, 2.5, 2.5], [1, 0, 0]);
    check('从 −X 外面打进来：place 落在 −X 外侧（画布外）',
          !!r && eq(r.cell, [0, 2, 2]) && eq(r.place, [-1, 2, 2]) && eq(r.normal, [-1, 0, 0]), r);
    r = box.raycast([12, 2.5, 2.5], [-1, 0, 0]);
    check('从 +X 外面打进来：place 落在 +X 外侧',
          !!r && eq(r.cell, [7, 2, 2]) && eq(r.place, [8, 2, 2]) && eq(r.normal, [1, 0, 0]), r);
    r = box.raycast([2.5, 20, 2.5], [0, -1, 0]);
    check('顶视图垂直下打：place = 命中格上方',
          !!r && eq(r.cell, [2, 7, 2]) && eq(r.place, [2, 8, 2]) && eq(r.normal, [0, 1, 0]), r);
    r = box.raycast([20, 2.5, 2.5], [0, 0, 1]);
    check('轴平行且在盒外 → null', r === null, r);
    r = box.raycast([2.5, 2.5, 2.5], [1, 0, 0]);
    check('起点在盒内（相机贴到方块里）：首格即命中，place = cell',
          !!r && eq(r.cell, [2, 2, 2]) && eq(r.place, [2, 2, 2]) && eq(r.normal, [0, 0, 0]), r);
    r = box.raycast([-5, 10, 10], [1, 0, 0]);            // y/z 超出 → 盒外
    check('错开盒子 → null（起点在盒外平行轴）', r === null, r);
    // 空气盒：不打到任何东西
    check('空结构 → null', air.raycast([-5, 4, 4], [1, 0, 0]) === null);
    // 格界上的起点：正好在 x=4 平面上，朝 −x 走 → 落在 3 号格（不是 4 号格）
    r = box.raycast([4, 2.5, 2.5], [-1, 0, 0]);
    check('起点正好压在格界：朝 −X 落在 3 号格', !!r && eq(r.cell, [3, 2, 2]), r);
    r = box.raycast([4, 2.5, 2.5], [1, 0, 0]);
    check('起点正好压在格界：朝 +X 落在 4 号格', !!r && eq(r.cell, [4, 2, 2]), r);
    // 斜穿整盒：跨的格数必须与解析参考一致（从盒外斜射进来）
    const big = makeViewer(M, [16, 16, 16], (x, y, z) => (x + y + z) % 7 === 5, {});
    const diag = [-3.2, -1.7, -2.4], dir = [1, 0.55, 0.8];
    const n = Math.hypot(dir[0], dir[1], dir[2]);
    const d = dir.map((c) => c / n);
    const g1 = big.raycast(diag, d);
    const g2 = refPick(solidCells(big), diag, d);
    check('斜穿整盒：与解析参考一致（cell + place）',
          !!g1 && !!g2 && eq(g1.cell, g2.cell) && eq(g1.place, g2.place),
          { got: g1, exp: g2 });
  }

  console.log('\n== 4. 多格空气后命中：place 必须是「紧贴表面的那一格」==');
  {
    // 空心壳：空气 4 格 → 命中第 5 格；旧实现会把 place 算到第 3 格（离表面一格远）
    const v = makeViewer(M, [8, 8, 8], (x) => x === 4, {});
    const r = v.raycast([0.5, 4.5, 4.5], [1, 0, 0]);
    check('穿 4 格空气打到墙：cell=[4,y,z] 且 place=[3,y,z]（紧贴）',
          !!r && eq(r.cell, [4, 4, 4]) && eq(r.place, [3, 4, 4]) && eq(r.normal, [-1, 0, 0]), r);
    const v2 = makeViewer(M, [8, 8, 8], (x) => x === 0 || x === 7, {});
    const r2 = v2.raycast([3.5, 4.5, 4.5], [1, 0, 0]);
    check('中间空气层打到对面墙：place = 墙前一格',
          !!r2 && eq(r2.cell, [7, 4, 4]) && eq(r2.place, [6, 4, 4]), r2);
  }

  console.log('\n== 5. firstInBox（空画布 / 看向空白区的落点）==');
  {
    // 旧 bug：只取各轴 tmin 再 clamp，没有「tEnter > tExit 就拒绝」这一步 →
    // 完全错过画布的射线（鼠标在建筑之外的背景上）也被夹出一个边界格：
    // 绿框长在建筑上，点下去在边界格放一块被邻居完全包住的方块
    //（六个面全被剔除 → 看上去「放了没渲染」，而那格已被占用 → 再放不进去）。
    // 参考：全实心镜像的 pick 首格 = 射线进入画布的入口格（pick 已被上面 22 项钉死）。
    const cases = [
      ['地板 + 方柱', [16, 16, 16], (x, y, z) => y < 3 || (x === 8 && z === 8),
       [{ yaw: -0.65, pitch: 0.55, dist: 40, target: [8, 4, 8] },
        { yaw: 2.2, pitch: -0.2, dist: 26, target: [8, 8, 8] },
        { yaw: 0.4, pitch: 1.2, dist: 33, target: [8, 8, 8] }]],
      ['贴满画布的外壳', [12, 20, 12],
       (x, y, z) => x === 0 || x === 11 || z === 0 || z === 11 || y === 0 || y === 19,
       [{ yaw: -2.3, pitch: 0.3, dist: 44, target: [6, 10, 6] },
        { yaw: 0.9, pitch: -0.9, dist: 38, target: [6, 10, 6] },
        { yaw: -0.65, pitch: 0.55, dist: 30, target: [6, 10, 6] }]],
    ];
    for (const [label, size, solid, cams] of cases) {
      let rays = 0, mismatch = 0, offRay = 0, badPlace = 0, first = null;
      for (const cam of cams) {
        const air = makeViewer(M, size, () => false, cam);   // 空画布：只有 firstInBox 能落点
        const full = makeViewer(M, size, () => true, cam);   // 全实心：pick 的首格 = 入口格
        const [w, h] = [air.canvas.clientWidth, air.canvas.clientHeight];
        for (let py = 0; py < h; py += 2) {
          for (let px = 0; px < w; px += 2) {
            const ev = { clientX: px + 0.5, clientY: py + 0.5 };
            const got = air.firstInBox(ev);
            const ref = full.pick(ev);
            const r = air.ray(ev);
            rays++;
            const bad = (why, extra) => {
              mismatch += (why === 'mismatch') ? 1 : 0;
              offRay += (why === 'offRay') ? 1 : 0;
              badPlace += (why === 'badPlace') ? 1 : 0;
              if (!first) first = Object.assign({ px, py, why, got, ref }, extra || {});
            };
            if (!got) { if (ref) bad('mismatch'); continue; }   // 该给 null 就必须给 null
            // 返回的格子必须真的在射线上（旧实现夹出来的格子不在 → 就是这条抓它）
            if (!r || !rayHitsCell(r.origin, r.dir, got.cell)) { bad('offRay'); continue; }
            if (!ref) { bad('mismatch'); continue; }
            // 入口格：与全实心 pick 一致；擦边像素允许并列解（±1 都算对）
            const acc = r ? entryAcceptable(air, r.origin, r.dir) : null;
            const tieOk = acc && acc.some((c) => eq(c, got.cell));
            if (!eq(got.cell, ref.cell) && !tieOk) { bad('mismatch'); continue; }
            const [sx, sy, sz] = size;
            if (!eq(got.place, got.cell) ||
                got.cell[0] < 0 || got.cell[1] < 0 || got.cell[2] < 0 ||
                got.cell[0] >= sx || got.cell[1] >= sy || got.cell[2] >= sz) bad('badPlace');
          }
        }
      }
      check('firstInBox 只在射线真的穿过画布时给落点，且就是入口格：' + label,
            !mismatch && !offRay && !badPlace,
            (mismatch || offRay || badPlace)
              ? { rays, mismatch, offRay, badPlace, first } : { 采样射线: rays });
    }
    // 画布在相机背后 / 完全错开 → 一律 null（空画布也一样）
    const away = makeViewer(M, [16, 16, 16], () => false,
      { yaw: 0, pitch: 0, dist: 30, target: [1000, 1000, 1000] }, [200, 150]);
    let nulls = 0;
    for (let py = 0; py < 150; py += 10) {
      for (let px = 0; px < 200; px += 10) {
        if (away.firstInBox({ clientX: px, clientY: py }) === null) nulls++;
      }
    }
    check('画布完全不在视野里 → firstInBox 全为 null',
          nulls === 20 * 15, { nulls, total: 20 * 15 });
  }

  console.log('\n== 6. 编辑器接线（静态）：负方向放不上去要明说 ==');
  {
    const js = fs.readFileSync(EDITOR, 'utf8');
    check('placeCell 命中负坐标时给状态提示（不再静默丢弃 / 报「空选区」）',
          /function placeCell[\s\S]{0,700}?p\[0\] < 0[\s\S]{0,400}?status\(/.test(js));
    check('准星对负方向用 blocked 模式（红框提示）',
          /mode: 'blocked'/.test(js) && /blocked/.test(fs.readFileSync(VIEWER, 'utf8')));
    check('拾取走 raycast 纯函数（DOM 无关，可单测）',
          /raycast\(o, d\)/.test(fs.readFileSync(VIEWER, 'utf8')));
  }

  console.log('\n' + (fail ? 'FAILED' : 'ALL PASS') + ': mcstudio 拾取射线  (' + pass + ' passed, ' + fail + ' failed)');
  return fail ? 1 : 0;
}

process.exit(main());
