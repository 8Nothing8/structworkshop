/* mcstudio 3D renderer —— 我们自己的 chunk mesher + WebGL 绘制。
 *
 * 定位：deepslate（MIT，vendor 在 ./vendor/deepslate.umd.cjs）**只当模型烘焙库**
 * （BlockDefinition / BlockModel / TextureAtlas / SpecialRenderers），
 * 网格构建与绘制是我们自己的实现。换掉 deepslate 的 ChunkBuilder/StructureRenderer
 * 是为了三件实测出来的事：
 *
 *   1. 它是**逐方块**烘焙的：每个方块都重新做 variant 匹配、Identifier 解析、
 *      再 new 出一堆 Quad/Vector 对象（96k 块 → 6.7s / 300MB 堆）。
 *      这里按「调色板状态」缓存烘焙结果（96k 块 / 27 个状态 → 只烘焙 27 次），
 *      逐方块只剩「剔除判断 + AO + 搬 4 个顶点」。
 *   2. 它按 16³ 分块，但 rebuild 时仍然遍历**整个结构**的方块列表
 *      （`for (const b of structure.getBlocks())`），所以涂一格 = O(全结构)
 *      （96k 块实测 208ms）。这里直接按脏块的 16³ 格子扫体素数组（≤4096 格）。
 *   3. 它每帧对每个 chunk mesh 都调 `gl.getAttribLocation`（150 个 chunk × 5 个
 *      属性）；这里属性位置只查一次。
 *
 * 另外在这里补齐 deepslate 没有/不适配当前版本的东西（都按下面这套语义走，语义来自
 * Python `/api/palette-info`，与 mcrender 同源）：
 *   - 面明暗 = vanilla 四档 1.0(上)/0.5(下)/0.8(南北)/0.6(东西)；
 *   - AO = 4 级 + 四边形翻转（表与 mcrender 一致）；
 *   - 流体 = 角点高度（源自 PrismarineJS/prismarine-viewer 的 MIT 实现）+ waterlogged；
 *   - 特判方块（箱/旗帜/头颅…）只在「自己没模型」时补几何；缺贴图的面直接丢掉（不画品红棋盘）。
 *
 * API（与原来 deepslate.StructureRenderer 的用法保持兼容，viewer3d.js 改动很小）：
 *   const r = new McRender3D.Renderer3D(gl);
 *   r.setResources(resources);  r.setEnv(env);
 *   r.markDirty([[cx,cy,cz], …]);  r.rebuildAll();
 *   const done = r.pump(budgetMs, onProgress);      // 时间分片
 *   r.flush();                                      // 同步建完（Node 测试用）
 *   r.drawStructure(viewMatrix); r.drawGrid(view); r.drawOutline(view, pos);
 */
(function (global) {
  'use strict';

  // ---------------------------------------------------------------- 常量
  //: AO 四档（兜底值；启动后由 Python 的 /api/palette-info 覆盖成 mcrender 那张表）
  let AO_LEVELS = [0.45, 0.62, 0.80, 1.00];
  //: 面明暗（vanilla）：+x/-x 0.6，+y 1.0，-y 0.5，+z/-z 0.8
  const FACE_SHADE = [0.6, 0.6, 1.0, 0.5, 0.8, 0.8];
  //: 方向表（索引即 dir）：0=+x 1=-x 2=+y 3=-y 4=+z 5=-z
  const DIR_VEC = [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]];
  const DIR_NAME = ['east', 'west', 'up', 'down', 'south', 'north'];
  const AXIS_OF_DIR = [0, 0, 1, 1, 2, 2];
  const OTHER_AXES = [[1, 2], [1, 2], [0, 2], [0, 2], [0, 1], [0, 1]];
  //: 液体的「上下面」与「侧面」贴图 ref（static 与 flowing）
  const FLUID_TEX = {
    water: { still: 'block/water_still', flow: 'block/water_flow' },
    lava: { still: 'block/lava_still', flow: 'block/lava_flow' },
  };
  const DEFAULT_SEM = {
    layer: 0, tint: null, liquid: null, special: null,
    has_elements: true, ao_occluder: false, render: 'normal',
    opaque: false, self_culling: false, semi_transparent: false,
  };
  const EMPTY_CULL = {};
  const WHITE = [1, 1, 1];
  //: 剔除用的六个方向（顺序与 deepslate 的 cull 字典一致）
  const CULL_DIRS = ['east', 'west', 'up', 'down', 'south', 'north'];
  //: vanilla 水色（管线里用不到 biome，所以是定值；和 mcrender / deepslate 一致）
  const WATER_TINT = [0x3f / 255, 0x76 / 255, 0xe4 / 255];

  function dirOfNormal(nx, ny, nz) {
    const ax = Math.abs(nx), ay = Math.abs(ny), az = Math.abs(nz);
    if (ay >= ax && ay >= az) return ny > 0 ? 2 : 3;
    if (ax >= az) return nx > 0 ? 0 : 1;
    return nz > 0 ? 4 : 5;
  }

  function fillDefaults(props, defaults) {
    if (!defaults) return props;
    for (const k in defaults) {
      if (props[k] === undefined) props[k] = defaults[k];
    }
    return props;
  }

  // ---------------------------------------------------------------- env
  /** 体素访问器：扁平 Uint16 + 调色板状态 + 语义数组（无对象分配）。 */
  class StructureEnv {
    constructor(opts) {
      this.size = opts.size.slice();
      this.voxels = opts.voxels;
      this.blockStates = opts.blockStates;      // deepslate.BlockState[]
      this.semantics = opts.semantics || [];    // 与调色板下标对齐
      this.nbt = opts.nbt || null;              // {"x,y,z": NbtCompound}（可选）
      this.layerY0 = 0;
      this.layerY1 = Math.max(0, this.size[1] - 1);
      this.only = false;
    }

    setLayerRange(y0, y1, only) {
      this.layerY0 = y0;
      this.layerY1 = y1;
      this.only = !!only;
    }

    /** 层的可见性（只有「只显示该层」时层滑条才影响几何）。 */
    visible(y) {
      return !this.only || (y >= this.layerY0 && y <= this.layerY1);
    }

    idxAt(x, y, z) {
      const s = this.size;
      if (x < 0 || y < 0 || z < 0 || x >= s[0] || y >= s[1] || z >= s[2]) return -1;
      return this.voxels[(y * s[2] + z) * s[0] + x];
    }

    stateAt(i) { return this.blockStates[i] || this.blockStates[0]; }
    semAt(i) { return this.semantics[i] || DEFAULT_SEM; }

    nbtAt(x, y, z) {
      return this.nbt ? this.nbt[x + ',' + y + ',' + z] : undefined;
    }
  }

  // ---------------------------------------------------------------- mesher
  /** 一个 chunk 的网格数据（先用普通数组累加，收尾转 typed array）。 */
  function newMesh() {
    return { pos: [], color: [], uv: [], limit: [], index: [], quads: 0, verts: 0 };
  }

  class Mesher {
    constructor(env, resources, opts) {
      this.env = env;
      this.resources = resources;
      this.opts = opts || {};
      this.baked = new Map();          // 调色板下标 → { quads:[…], missing, special }
      this.missing = 0;                // 缺贴图被丢掉的面数（诊断用）
      this._tmp = new Int32Array(3);
      this._colors = (global.deepslate && global.deepslate.BlockColors) || null;
      // 液体的 UV 缓存（每 kind 一次）
      this._fluidUVCache = new Map();
      // 流体高度/角点用到的 3×3 缓冲（避免每格 new 数组）
      this._hh = new Float64Array(9);
    }

    // ------------------------------------------------------------ 烘焙（按状态缓存）
    bake(idx) {
      const hit = this.baked.get(idx);
      if (hit) return hit;
      const env = this.env;
      const state = env.stateAt(idx);
      const sem = env.semAt(idx);
      const name = state.getName();
      const path = name.path !== undefined ? name.path : String(name).replace(/^minecraft:/, '');
      const props = fillDefaults(
        Object.assign({}, state.getProperties()),
        this.resources.getDefaultBlockProperties(name));
      const out = { quads: [], missing: 0, special: false };

      const def = this.resources.getBlockDefinition(name);
      if (def) {
        // 单个状态的模型缺失（老资源集里没有的变体）**只能丢这个状态的面**：
        // 异常冒出去会让整个 chunk buildChunk 失败 → 方块连邻居一起消失
        //（实测：墙/栅栏出现新的连接状态时“变透明”）。
        try {
          const cullDir = this._resolveCullFaces(def, name, props);
          const mesh = this._bakeWithSentinel(def, name, props, path, sem);
          this._pushMesh(mesh, out, sem, cullDir);
        } catch (e) {
          this.bakeFailures = this.bakeFailures || new Map();
          this.bakeFailures.set(name, String((e && e.message) || e));
        }
      }
      if (sem.special && !sem.has_elements) {
        // 方块实体（箱子/旗帜/头颅…）：自己没模型时才补
        try {
          const sp = global.deepslate.SpecialRenderers.getBlockMesh(
            state, undefined, this.resources, EMPTY_CULL);
          out.special = true;
          this._pushMesh(sp, out, sem, null);
        } catch (e) { /* 缺贴图/几何 → 下面按 missing 计数 */ }
      }
      // 还是没面（例：**无色潜影盒** —— deepslate 的 shulker 渲染器只认带颜色的）
      // → 用方块自己的贴图画个立方体兜底，绝不“放了什么也看不到”。
      if (!out.quads.length && sem.fallback_texture) {
        try {
          this._fallbackCube(sem.fallback_texture, out, sem);
          out.fallbackCube = true;
        } catch (e) {
          this.bakeFailures = this.bakeFailures || new Map();
          this.bakeFailures.set(name, 'fallback: ' + String((e && e.message) || e));
        }
      }
      this.baked.set(idx, out);
      return out;
    }

    /** 自身/特判几何都拿不到时的兜底：用 ``ref`` 这张贴图画一个立方体。
     *
     * 模型是现造的（6 面同贴图，不写 cullface → 自己剔邻面），按 ref 缓存复用。
     */
    _fallbackCube(ref, out, sem) {
      const res = this.resources;
      const key = '__structworkshop_fallback:' + ref;
      res.models = res.models || {};
      if (!res.models[key] && global.deepslate.BlockModel) {
        const faces = {};
        for (const d of CULL_DIRS) faces[d] = { texture: '#all' };
        res.models[key] = global.deepslate.BlockModel.fromJson({
          textures: { all: 'minecraft:' + ref, particle: 'minecraft:' + ref },
          elements: [{ from: [0, 0, 0], to: [16, 16, 16], faces }],
        });
      }
      const def = global.deepslate.BlockDefinition.fromJson(
        { variants: { '': { model: key } } });
      const mesh = def.getMesh('minecraft:stone', {}, res, res, EMPTY_CULL);
      this._pushMesh(mesh, out, sem, null);
    }

    /** 每个面**自己的 cullface**（不是法线方向）。
     *
     * 为什么不能用法线：像铁栅栏的“柱顶/端帽”这类面在模型里根本没写 cullface，
     * 在游戏里永远看得见；而它们的法线恰好指向邻居（比如上方是羊毛）——
     * 用法线剔就会把看得见的面剔掉（实测栅栏顶面/灯笼端盖会消失，村庄四边形少了 20%）。
     *
     * 做法：先用空 cull 烘一遍拿到全部面，再逐个方向只开一个 cull 烘一遍，
     * 少了谁就是“被这个方向剔掉”的面。代价是每个状态多 6 次烘焙（状态数不多，可接受）。
     */
    _resolveCullFaces(def, name, props) {
      const key = (q) => {
        const vs = q.vertices();
        let k = '';
        for (let i = 0; i < 4; i++) {
          const v = vs[i];
          k += v.pos.x.toFixed(4) + ',' + v.pos.y.toFixed(4) + ',' + v.pos.z.toFixed(4) + ',';
          if (v.texture) k += v.texture[0].toFixed(5) + ',' + v.texture[1].toFixed(5);
          k += ';';
        }
        return k;
      };
      let full;
      try {
        full = def.getMesh(name, props, this.resources, this.resources, EMPTY_CULL);
      } catch (e) {
        return null;
      }
      if (!full || !full.quads.length) return null;
      const fullKeys = new Map();
      for (const q of full.quads) fullKeys.set(key(q), true);
      const dirOf = new Map();
      for (const d of CULL_DIRS) {
        let m;
        try {
          m = def.getMesh(name, props, this.resources, this.resources, { [d]: true });
        } catch (e) { continue; }
        const surv = new Set();
        for (const q of m.quads) surv.add(key(q));
        for (const k of fullKeys.keys()) {
          if (!surv.has(k)) dirOf.set(k, d);
        }
      }
      return dirOf.size ? dirOf : null;
    }

    /** 用「哨兵色」包住 deepslate 的染色表：模型里有 tintindex 的面会变成 [2,2,2]，
     *  于是我们能区分「该染色的面」和「不染色的面」，再乘 Python 下发的 tint。 */
    _bakeWithSentinel(def, name, props, path, sem) {
      const BC = this._colors;
      const prev = BC ? BC[path] : undefined;
      const had = BC ? Object.prototype.hasOwnProperty.call(BC, path) : false;
      if (BC) BC[path] = () => [2, 2, 2];
      let mesh;
      try {
        mesh = def.getMesh(name, props, this.resources, this.resources, EMPTY_CULL);
      } finally {
        if (BC) {
          if (had) BC[path] = prev;
          else delete BC[path];
        }
      }
      return mesh;
    }

    /** Python 下发的 tint 是 0..255（与 mcrender 同源），shader 要 0..1 —— 这里归一化。 */
    _tintOf(sem) {
      const t = sem && sem.tint;
      if (!t) return null;
      if (t[0] <= 1.001 && t[1] <= 1.001 && t[2] <= 1.001) return [t[0], t[1], t[2]];
      return [t[0] / 255, t[1] / 255, t[2] / 255];
    }

    _pushMesh(mesh, out, sem, cullDir) {
      if (!mesh) return;
      const semTint = this._tintOf(sem);
      for (const q of mesh.quads) {
        const vs = q.vertices();
        const limit = vs[0].textureLimit || [0, 0, 0, 0];
        if (this._isMissing(limit)) { out.missing++; this.missing++; continue; }
        const n = q.normal();
        const dir = dirOfNormal(n.x, n.y, n.z);
        const c0 = vs[0].color || [1, 1, 1];
        const tinted = c0[0] > 1.5;               // 哨兵 [2,2,2]
        const tint = (tinted && semTint) ? semTint : WHITE;
        let cull = -1;                            // 该面自己的 cullface（-1 = 永不剔）
        if (cullDir) {
          let k = '';
          for (let i = 0; i < 4; i++) {
            const v = vs[i];
            k += v.pos.x.toFixed(4) + ',' + v.pos.y.toFixed(4) + ',' + v.pos.z.toFixed(4) + ',';
            if (v.texture) k += v.texture[0].toFixed(5) + ',' + v.texture[1].toFixed(5);
            k += ';';
          }
          const d = cullDir.get(k);
          if (d !== undefined) cull = CULL_DIRS.indexOf(d);
        }
        const pos = new Float32Array(12);
        const uv = new Float32Array(8);
        for (let i = 0; i < 4; i++) {
          const v = vs[i];
          pos[i * 3] = v.pos.x;
          pos[i * 3 + 1] = v.pos.y;
          pos[i * 3 + 2] = v.pos.z;
          const t = v.texture || [0, 0];
          uv[i * 2] = t[0];
          uv[i * 2 + 1] = t[1];
        }
        out.quads.push({
          pos, uv,
          limit: [limit[0], limit[1], limit[2], limit[3]],
          tint, dir, cull,
          layer: sem.layer === 2 ? 2 : 0,
          name: DIR_NAME[dir],
        });
      }
    }

    /** 图集 0 号 tile 是 deepslate 画的「缺贴图」品红/黑棋盘：认出来就别画。 */
    _isMissing(limit) {
      const part = this.part !== undefined ? this.part : (this.part = this._atlasPart());
      return limit[0] === 0 && limit[1] === 0 && limit[2] <= part && limit[3] <= part;
    }

    _atlasPart() {
      try {
        const img = this.resources.getTextureAtlas();
        return 16 / img.width;
      } catch (e) {
        return 1 / 16;
      }
    }

    // ------------------------------------------------------------ 剔除
    _cull(x, y, z, idx, sem, dir) {
      const env = this.env;
      const d = DIR_VEC[dir];
      const ni = env.idxAt(x + d[0], y + d[1], z + d[2]);
      if (ni <= 0) return false;                      // 外面/空气 → 画
      const ns = env.semAt(ni);
      const nname = ns.name;
      const same = nname !== undefined && nname === sem.name;
      if (same && ns.self_culling) return true;
      if (ns.opaque) return !(dir === 2 && sem.waterlogged);
      return !!sem.waterlogged && !!ns.waterlogged;
    }

    // ------------------------------------------------------------ AO
    _occ(x, y, z) {
      const i = this.env.idxAt(x, y, z);
      return i > 0 && this.env.semAt(i).ao_occluder ? 1 : 0;
    }

    /** dir 面的一个角（su/sv 为面内两个轴的 ±1）的 AO 亮度。 */
    _aoAt(x, y, z, dir, su, sv) {
      const d = DIR_VEC[dir];
      const b = OTHER_AXES[dir][0];
      const c = OTHER_AXES[dir][1];
      const bx = x + d[0], by = y + d[1], bz = z + d[2];
      const o1x = b === 0 ? su : 0, o1y = b === 1 ? su : 0, o1z = b === 2 ? su : 0;
      const o2x = c === 0 ? sv : 0, o2y = c === 1 ? sv : 0, o2z = c === 2 ? sv : 0;
      const s1 = this._occ(bx + o1x, by + o1y, bz + o1z);
      const s2 = this._occ(bx + o2x, by + o2y, bz + o2z);
      const sc = this._occ(bx + o1x + o2x, by + o1y + o2y, bz + o1z + o2z);
      const level = (s1 && s2) ? 0 : 3 - (s1 + s2 + sc);
      return AO_LEVELS[level];
    }

    // ------------------------------------------------------------ 发射一个方块
    _emit(baked, x, y, z, sem, mesh) {
      const quads = baked.quads;
      for (let i = 0; i < quads.length; i++) {
        const q = quads[i];
        const layer = q.layer === 2 ? 2 : sem.layer === 2 ? 2 : 0;
        if (this.opts.layers !== false && layer === 2) {
          this._emitQuad(q, x, y, z, sem, mesh.trans, true);
        } else if (sem.cull_safe) {
          this._emitQuad(q, x, y, z, sem, mesh.solid, false);
        } else {
          // 异形 / 薄片 / 带透明像素：面可能只有一片或朝里，剔背面会丢面 → 走不剔桶
          this._emitQuad(q, x, y, z, sem, mesh.nocull, false);
        }
      }
    }

    _emitQuad(q, x, y, z, sem, out, translucent) {
      const dir = q.dir;
      // 只看这个面**自己声明的** cullface（-1 = 模型没写 → 永不剔），
      // 不是看法线：柱顶/端帽这类面必须画出来（见 _resolveCullFaces）
      if (q.cull >= 0 && this._cull(x, y, z, 0, sem, q.cull)) return;
      // 顶点（含 AO 与面明暗），局部坐标 0..1 → 世界坐标
      let a0 = 1, a1 = 1, a2 = 1, a3 = 1;
      const shade = dir >= 0 ? FACE_SHADE[dir] : 1.0;
      const t = q.tint;
      const base = out.pos.length / 3;          // 顶点下标基数
      // 面的两个面内轴（用于算角点符号）
      const b = dir >= 0 ? OTHER_AXES[dir][0] : -1;
      const c = dir >= 0 ? OTHER_AXES[dir][1] : -1;
      const cornerAO = [1, 1, 1, 1];
      if (dir >= 0) {
        for (let i = 0; i < 4; i++) {
          const lx = q.pos[i * 3], ly = q.pos[i * 3 + 1], lz = q.pos[i * 3 + 2];
          const su = (b === 0 ? lx : b === 1 ? ly : lz) > 0.5 ? 1 : -1;
          const sv = (c === 0 ? lx : c === 1 ? ly : lz) > 0.5 ? 1 : -1;
          cornerAO[i] = this._aoAt(x, y, z, dir, su, sv);
        }
        a0 = cornerAO[0]; a1 = cornerAO[1]; a2 = cornerAO[2]; a3 = cornerAO[3];
      }
      const flip = dir >= 0 && (a1 + a3) > (a0 + a2);
      const r = t[0] * shade, g = t[1] * shade, bl = t[2] * shade;
      const aoArr = [a0, a1, a2, a3];
      for (let i = 0; i < 4; i++) {
        const ao = aoArr[i];
        out.pos.push(x + q.pos[i * 3], y + q.pos[i * 3 + 1], z + q.pos[i * 3 + 2]);
        out.color.push(r * ao, g * ao, bl * ao);
        out.uv.push(q.uv[i * 2], q.uv[i * 2 + 1]);
        out.limit.push(q.limit[0], q.limit[1], q.limit[2], q.limit[3]);
      }
      if (flip) {
        out.index.push(base + 1, base + 2, base + 3, base + 1, base + 3, base + 0);
      } else {
        out.index.push(base, base + 1, base + 2, base, base + 2, base + 3);
      }
      out.quads += 1;
      out.verts += 4;
    }

    // ------------------------------------------------------------ 流体
    /** 角点高度（PrismarineJS/prismarine-viewer 的 MIT 实现：源 8/9、流动按 level、
     *  角点取相邻 4 格的最大值），比 deepslate 的「整层盒子」更接近游戏。 */
    _fluidHeight(x, y, z, kind) {
      const env = this.env;
      const i = env.idxAt(x, y, z);
      if (i <= 0) return 1 / 9;
      const s = env.semAt(i);
      const isThis = s.liquid === kind || (kind === 'water' && s.waterlogged);
      if (!isThis) return 1 / 9;
      const above = env.idxAt(x, y + 1, z);
      const aSem = above > 0 ? env.semAt(above) : null;
      const aboveSame = !!aSem && (aSem.liquid === kind
        || (kind === 'water' && aSem.waterlogged));
      const st = env.stateAt(i);
      const level = parseInt(st.getProperty ? (st.getProperty('level') || '0') : '0', 10);
      if (!(level > 0)) return aboveSame ? 1 : 8 / 9;      // 源方块
      if (level >= 8) return 1;                            // 下落水
      return (7 - level + 1) / 9;
    }

    _fluidUV(kind) {
      let hit = this._fluidUVCache.get(kind);
      if (hit) return hit;
      const refs = FLUID_TEX[kind] || FLUID_TEX.water;
      const D = global.deepslate;
      const uv = (ref) => {
        try { return this.resources.getTextureUV(D.Identifier.parse(ref)); }
        catch (e) { return null; }
      };
      hit = { still: uv(refs.still), flow: uv(refs.flow) };
      if (!hit.still) hit.still = hit.flow;
      if (!hit.flow) hit.flow = hit.still;
      this._fluidUVCache.set(kind, hit);
      return hit;
    }

    /** 一个流体格：顶面（角点高度）+ 底面 + 4 个侧面（UV 锚在液面上，不拉伸）。 */
    _fluid(x, y, z, kind, mesh) {
      const env = this.env;
      const uv = this._fluidUV(kind);
      if (!uv.still || this._isMissing(uv.still)) return;   // 贴图没取到 → 不画（别画棋盘）
      const tint = kind === 'water' ? this._waterTint() : WHITE;
      const out = mesh.trans;
      const hh = this._hh;                                  // 3×3，索引 (dz+1)*3+(dx+1)
      for (let dz = -1; dz <= 1; dz++) {
        for (let dx = -1; dx <= 1; dx++) {
          hh[(dz + 1) * 3 + (dx + 1)] = this._fluidHeight(x + dx, y, z + dz, kind);
        }
      }
      const m4 = (a, b, c, d) => Math.max(Math.max(a, b), Math.max(c, d));
      const c00 = m4(hh[0], hh[1], hh[3], hh[4]);          // (x,z) 角
      const c10 = m4(hh[1], hh[2], hh[4], hh[5]);
      const c01 = m4(hh[3], hh[4], hh[6], hh[7]);
      const c11 = m4(hh[4], hh[5], hh[7], hh[8]);
      const above = env.idxAt(x, y + 1, z);
      const aSem = above > 0 ? env.semAt(above) : null;
      const same = (s) => !!s && (s.liquid === kind || (kind === 'water' && s.waterlogged));
      const aboveSame = same(aSem);
      const below = env.idxAt(x, y - 1, z);
      const belowSame = same(below > 0 ? env.semAt(below) : null);

      if (!aboveSame) {
        // 顶面：u=x、v=z；四角各自高度
        this._pushQuadRaw(out, x, y, z, [
          [0, c00, 0, 0, 0], [1, c10, 0, 1, 0], [1, c11, 1, 1, 1], [0, c01, 1, 0, 1],
        ], uv.still, tint, 2);
      }
      if (!belowSame) {
        this._pushQuadRaw(out, x, y, z, [
          [0, 0, 1, 0, 1], [1, 0, 1, 1, 1], [1, 0, 0, 1, 0], [0, 0, 0, 0, 0],
        ], uv.still, tint, 3);
      }
      // 4 个侧面：邻居不是同类流体、也不是不透明方块 → 画；v 轴锚在液面
      const inv = (h) => 1 - h;
      const sides = [
        [1, [[0, c00, 0, 0, inv(c00)], [0, c01, 1, 1, inv(c01)],
             [0, 0, 1, 1, 1], [0, 0, 0, 0, 1]]],
        [0, [[1, c10, 0, 1, inv(c10)], [1, c11, 1, 0, inv(c11)],
             [1, 0, 1, 0, 1], [1, 0, 0, 1, 1]]],
        [5, [[0, c00, 0, 0, inv(c00)], [1, c10, 0, 1, inv(c10)],
             [1, 0, 0, 1, 1], [0, 0, 0, 0, 1]]],
        [4, [[0, c01, 1, 1, inv(c01)], [0, 0, 1, 1, 1],
             [1, 0, 1, 0, 1], [1, c11, 1, 0, inv(c11)]]],
      ];
      for (const [dir, pts] of sides) {
        const d = DIR_VEC[dir];
        const ni = env.idxAt(x + d[0], y + d[1], z + d[2]);
        const ns = ni > 0 ? env.semAt(ni) : null;
        if (same(ns) || (ns && ns.opaque)) continue;
        this._pushQuadRaw(out, x, y, z, pts, uv.flow, tint, dir);
      }
    }

    _waterTint() {
      const env = this.env;
      // 语义表里 water 的 tint 由 Python 下发（与 mcrender 同源），没有就用 vanilla 水色
      for (let i = 0; i < env.semantics.length; i++) {
        const s = env.semantics[i];
        if (s && s.liquid === 'water' && s.tint) return this._tintOf(s);
      }
      return WATER_TINT;
    }

    /** 直接写一个四边形（流体用）：pts 为 ``[dx,dy,dz, tu,tv]``（tu/tv 是贴图内 0..1）。 */
    _pushQuadRaw(out, x, y, z, pts, uvRect, tint, dir) {
      if (!uvRect) return;
      if (this._isMissing(uvRect)) return;
      const [u0, v0, u1, v1] = uvRect;
      const shade = dir >= 0 ? FACE_SHADE[dir] : 1.0;
      const base = out.pos.length / 3;
      const b = dir >= 0 ? OTHER_AXES[dir][0] : -1;
      const c = dir >= 0 ? OTHER_AXES[dir][1] : -1;
      const ao = [1, 1, 1, 1];
      if (dir >= 0) {
        for (let i = 0; i < 4; i++) {
          const p = pts[i];
          const su = (b === 0 ? p[0] : b === 1 ? p[1] : p[2]) > 0.5 ? 1 : -1;
          const sv = (c === 0 ? p[0] : c === 1 ? p[1] : p[2]) > 0.5 ? 1 : -1;
          ao[i] = this._aoAt(x, y, z, dir, su, sv);
        }
      }
      const flip = dir >= 0 && (ao[1] + ao[3]) > (ao[0] + ao[2]);
      for (let i = 0; i < 4; i++) {
        const p = pts[i];
        const a = ao[i];
        out.pos.push(x + p[0], y + p[1], z + p[2]);
        out.color.push(tint[0] * shade * a, tint[1] * shade * a, tint[2] * shade * a);
        out.uv.push(u0 + (u1 - u0) * p[3], v0 + (v1 - v0) * p[4]);
        out.limit.push(u0, v0, u1, v1);
      }
      if (flip) {
        out.index.push(base + 1, base + 2, base + 3, base + 1, base + 3, base + 0);
      } else {
        out.index.push(base, base + 1, base + 2, base, base + 2, base + 3);
      }
      out.quads += 1;
      out.verts += 4;
    }

    // ------------------------------------------------------------ 一个 chunk
    buildChunk(cx, cy, cz) {
      const env = this.env;
      const [sx, sy, sz] = env.size;
      const x0 = Math.max(0, cx * 16), x1 = Math.min(sx, x0 + 16);
      const y0 = Math.max(0, cy * 16), y1 = Math.min(sy, y0 + 16);
      const z0 = Math.max(0, cz * 16), z1 = Math.min(sz, z0 + 16);
      // 三个桶：solid=可背面剔除（整方块不透明）、nocull=不剔除（异形/带透明像素）、
      // trans=混合层。桶只影响绘制状态，几何与旧实现完全一致。
      const mesh = { solid: newMesh(), nocull: newMesh(), trans: newMesh() };
      // 画布外（负 chunk / 越过最后一块）直接给空网格：负坐标不能靠上面的 Math.max
      // 夹到 0——一夹就把第 0 块的体素也画一份，贴原点的结构会重复 8 次。
      if (cx < 0 || cy < 0 || cz < 0 || x0 >= sx || y0 >= sy || z0 >= sz) return mesh;
      const hook = this.opts.onBlockQuads;      // 诊断用（按状态统计四边形）
      for (let y = y0; y < y1; y++) {
        if (!env.visible(y)) continue;
        for (let z = z0; z < z1; z++) {
          const row = (y * sz + z) * sx;
          for (let x = x0; x < x1; x++) {
            const idx = env.voxels[row + x];
            if (idx === 0) continue;
            const sem = env.semAt(idx);
            if (sem.render === 'invisible') continue;
            const q0 = hook ? mesh.solid.quads + mesh.nocull.quads + mesh.trans.quads : 0;
            if (sem.liquid) {
              this._fluid(x, y, z, sem.liquid, mesh);
            } else {
              const baked = this.bake(idx);
              if (baked.quads.length) this._emit(baked, x, y, z, sem, mesh);
              if (sem.waterlogged) this._fluid(x, y, z, 'water', mesh);
            }
            if (hook) hook(idx, mesh.solid.quads + mesh.nocull.quads + mesh.trans.quads - q0);
          }
        }
      }
      return mesh;
    }
  }

  // ---------------------------------------------------------------- GL 渲染器
  const VS = [
    'attribute vec3 aPos;',
    'attribute vec3 aColor;',
    'attribute vec2 aUV;',
    'attribute vec4 aLimit;',
    'uniform mat4 uView;',
    'uniform mat4 uProj;',
    'uniform float uScale;',        // LOD 用：粗网格坐标 × 比例 = 世界坐标（全精度时 = 1）
    'varying vec3 vColor;',
    'varying vec2 vUV;',
    'varying vec4 vLimit;',
    'void main() {',
    '  vColor = aColor; vUV = aUV; vLimit = aLimit;',
    '  gl_Position = uProj * uView * vec4(aPos, 1.0);',
    '}',
  ].join('\n');
  const FS = [
    'precision mediump float;',
    'varying vec3 vColor;',
    'varying vec2 vUV;',
    'varying vec4 vLimit;',
    'uniform sampler2D uAtlas;',
    'uniform float uPixel;',
    'uniform float uCut;',
    'void main() {',
    '  vec2 uv = clamp(vUV, vLimit.xy + vec2(0.5) * uPixel, vLimit.zw - vec2(0.5) * uPixel);',
    '  vec4 c = texture2D(uAtlas, uv);',
    '  if (c.a < uCut) discard;',
    '  gl_FragColor = vec4(c.rgb * vColor, c.a);',
    '}',
  ].join('\n');

  const LINE_VS = [
    'attribute vec3 aPos;',
    'attribute vec3 aColor;',
    'uniform mat4 uView;',
    'uniform mat4 uProj;',
    'uniform float uScale;',
    'varying vec3 vColor;',
    'void main() { vColor = aColor; gl_Position = uProj * uView * vec4(aPos * uScale, 1.0); }',
  ].join('\n');
  const LINE_FS = [
    'precision mediump float;',
    'varying vec3 vColor;',
    'void main() { gl_FragColor = vec4(vColor, 1.0); }',
  ].join('\n');

  function compile(gl, type, src) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
      throw new Error('shader: ' + gl.getShaderInfoLog(s));
    }
    const p = gl.createProgram();
    gl.attachShader(p, s);
    return p;
  }

  class Renderer3D {
    constructor(gl, opts) {
      this.gl = gl;
      this.opts = opts || {};
      this.chunkSize = 16;
      this.mesher = null;
      this.resources = null;
      this.atlasTexture = null;
      this.chunks = new Map();          // "cx,cy,cz" → {solid:[], trans:[], quads}
      this.dirty = new Set();
      this._pumpQueue = null;
      this._pumpDone = 0;
      this._pumpTotal = 0;
      this._onProgress = null;
      this.stats = { chunks: 0, quads: 0, built: 0, missing: 0 };
      this.cull = true;                 // 视锥剔除开关（诊断/A-B 用）
      this.cullFace = true;             // 背面剔除开关（诊断/A-B 用）
      this.gl2 = (typeof WebGL2RenderingContext !== 'undefined'
                  && gl instanceof WebGL2RenderingContext);
      this.legacyBuffers = false;       // true = 强制旧「每块一套缓冲」路径（做 A/B）
      this.pools = {};                  // 桶 → 共享顶点/索引池（批次合并）
      this._vp = mat4Create();
      this._planes = new Float64Array(24);
      this._initPrograms();
      this._initLine();
    }

    // ------------------------------------------------------------ 程序/缓冲
    _initPrograms() {
      const gl = this.gl;
      const prog = compile(gl, gl.VERTEX_SHADER, VS);
      compile(gl, gl.FRAGMENT_SHADER, FS);
      const fs = gl.createShader(gl.FRAGMENT_SHADER);
      gl.shaderSource(fs, FS);
      gl.compileShader(fs);
      gl.attachShader(prog, fs);
      gl.linkProgram(prog);
      if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
        throw new Error('link: ' + gl.getProgramInfoLog(prog));
      }
      this.prog = prog;
      // 属性/uniform 位置只查一次（deepslate 是每帧每 chunk 都查）
      this.aPos = gl.getAttribLocation(prog, 'aPos');
      this.aColor = gl.getAttribLocation(prog, 'aColor');
      this.aUV = gl.getAttribLocation(prog, 'aUV');
      this.aLimit = gl.getAttribLocation(prog, 'aLimit');
      this.uView = gl.getUniformLocation(prog, 'uView');
      this.uProj = gl.getUniformLocation(prog, 'uProj');
      this.uAtlas = gl.getUniformLocation(prog, 'uAtlas');
      this.uPixel = gl.getUniformLocation(prog, 'uPixel');
      this.uCut = gl.getUniformLocation(prog, 'uCut');
      this.uScale = gl.getUniformLocation(prog, 'uScale');
      this.coordScale = 1;      // LOD：粗网格边长（1 = 全精度）
      this.projMatrix = mat4Create();
    }

    _initLine() {
      const gl = this.gl;
      const vs = gl.createShader(gl.VERTEX_SHADER);
      gl.shaderSource(vs, LINE_VS);
      gl.compileShader(vs);
      const fs = gl.createShader(gl.FRAGMENT_SHADER);
      gl.shaderSource(fs, LINE_FS);
      gl.compileShader(fs);
      const p = gl.createProgram();
      gl.attachShader(p, vs);
      gl.attachShader(p, fs);
      gl.linkProgram(p);
      this.lineProg = p;
      this.lPos = gl.getAttribLocation(p, 'aPos');
      this.lScale = gl.getUniformLocation(p, 'uScale');
      this.lColor = gl.getAttribLocation(p, 'aColor');
      this.lView = gl.getUniformLocation(p, 'uView');
      this.lProj = gl.getUniformLocation(p, 'uProj');
      this.lineBuf = gl.createBuffer();
      this.lineVerts = 0;
      this.gridBuf = gl.createBuffer();
      this.gridVerts = 0;
      // 坐标轴箭头单独一个 buffer：地面网格要吃深度（不然从上看会盖在被子上），
      // 但方向指示必须**始终看得见**（结构一挡就找不到 X/Y/Z 了）。
      this.axisBuf = gl.createBuffer();
      this.axisVerts = 0;
      //: 坐标轴箭头（默认画；回归测试量“地面网格被模型遮挡”时会临时关掉）
      this.showAxisArrows = true;
    }

    setViewport(x, y, w, h) {
      const gl = this.gl;
      gl.viewport(x, y, w, h);
      const aspect = (this.gl.canvas.clientWidth || 1) /
        (this.gl.canvas.clientHeight || 1);
      this.projMatrix = perspective(70 * Math.PI / 180, aspect, 0.1, 2000);
    }

    setResources(resources) {
      this.resources = resources;
      this.atlasTexture = this._createAtlasTexture();
      if (this.mesher) this.mesher.resources = resources;
    }

    _createAtlasTexture() {
      const gl = this.gl;
      const img = this.resources.getTextureAtlas();
      const tex = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, tex);
      // 自己生成 mip 链：**alpha 取 max**（cutout 的洞与实心面不被平均掉）。
      // 直接用 gl.generateMipmap 会把树叶/铁栅栏的 alpha 平均到 0.5 上下，
      // 再走 alpha 测试就会变成“纱窗/棋盘”——这是标准 mipmap 伪影。
      let w = img.width, h = img.height;
      let data = new Uint8Array(img.data.buffer, img.data.byteOffset, img.data.length);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, data);
      let level = 0;
      while (w > 1 || h > 1) {
        const nw = Math.max(1, w >> 1), nh = Math.max(1, h >> 1);
        const out = new Uint8Array(nw * nh * 4);
        for (let y = 0; y < nh; y++) {
          for (let x = 0; x < nw; x++) {
            let r = 0, g = 0, b = 0, aSum = 0, aMax = 0;
            for (let dy = 0; dy < 2; dy++) {
              for (let dx = 0; dx < 2; dx++) {
                const sx = Math.min(w - 1, x * 2 + dx), sy = Math.min(h - 1, y * 2 + dy);
                const i = (sy * w + sx) * 4;
                const al = data[i + 3];
                r += data[i] * al; g += data[i + 1] * al; b += data[i + 2] * al;
                aSum += al; if (al > aMax) aMax = al;
              }
            }
            const o = (y * nw + x) * 4;
            if (aSum > 0 && data) {
              out[o] = Math.min(255, Math.round(r / aSum));
              out[o + 1] = Math.min(255, Math.round(g / aSum));
              out[o + 2] = Math.min(255, Math.round(b / aSum));
            }
            // 硬边（cutout）保住：有接近不透明的样本就拿 max；半透明（玻璃）才平均
            out[o + 3] = aMax >= 200 ? aMax : Math.round(aSum / 4);
          }
        }
        level += 1;
        w = nw; h = nh;
        gl.texImage2D(gl.TEXTURE_2D, level, gl.RGBA, w, h, 0, gl.RGBA,
                      gl.UNSIGNED_BYTE, out);
        data = out;
        if (level > 12) break;
      }
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER,
        gl.NEAREST_MIPMAP_LINEAR);
      this.pixelSize = this.resources.getPixelSize ? this.resources.getPixelSize() : 1 / 16;
      return tex;
    }

    // ------------------------------------------------------------ 结构
    setEnv(env) {
      this.env = env;
      this.mesher = new Mesher(env, this.resources, this.opts);
      this.nonEmpty = this._scanNonEmptyChunks();
      this.clearChunks();
    }

    /** 扫一遍体素，标出**哪些 16³ 块里有方块**（掩码按 cx*ny*nz + cy*nz + cz）。
     *
     * 大结构的包围盒里绝大多数块是全空的：太空探索者 5292 块里只有 853 块有东西，
     * 剩下 4439 块每块还要白扫 4096 格（合计 2 千多万次）——实测占建网格时间的一大半。
     * 掩码是**保守**的：编辑时只会补位（不清位），最坏情况是多重扫一次空块。
     */
    _scanNonEmptyChunks() {
      const env = this.env;
      if (!env || !env.voxels || !env.size) return null;
      const [sx, sy, sz] = env.size;
      const nx = Math.ceil(sx / 16), ny = Math.ceil(sy / 16), nz = Math.ceil(sz / 16);
      const mask = new Uint8Array(nx * ny * nz);
      const v = env.voxels;
      const sxy = sx * sz;
      let n = 0, blocks = 0;
      for (let cy = 0; cy < ny; cy++) {
        const y1 = Math.min(sy, cy * 16 + 16);
        for (let cz = 0; cz < nz; cz++) {
          const z1 = Math.min(sz, cz * 16 + 16);
          for (let cx = 0; cx < nx; cx++) {
            const x1 = Math.min(sx, cx * 16 + 16);
            let hit = 0;
            for (let y = cy * 16; y < y1 && !hit; y++) {
              for (let z = cz * 16; z < z1 && !hit; z++) {
                const row = y * sxy + z * sx;
                for (let x = cx * 16; x < x1; x++) {
                  if (v[row + x]) { hit = 1; blocks += 1; }
                }
              }
            }
            if (hit) { mask[(cx * ny + cy) * nz + cz] = 1; n += 1; }
          }
        }
      }
      this.stats.nonEmptyChunks = n;
      // 预估池容量：实测约 7 个顶点/方块（含 AO 与薄片），余量 15%。
      // 池子一次要到位 —— 否则翻倍扩容会反复整块拷贝（实测让上传从 107ms 涨到 359ms）。
      this.poolInitV = Math.max(1 << 16, Math.round(blocks * 7 * 12 * 1.15));
      this.poolInitI = Math.max(1 << 15, Math.round(blocks * 7 / 4 * 6 * 1.15));
      return mask;
    }

    /** 编辑时把一个块标成「可能有东西」（只补位、不清位 —— 保守方向是安全的）。 */
    markChunkUsed(cx, cy, cz) {
      const m = this.nonEmpty;
      if (!m || !this.env) return;
      const [sx, sy, sz] = this.env.size;
      const nx = Math.ceil(sx / 16), ny = Math.ceil(sy / 16), nz = Math.ceil(sz / 16);
      if (cx < 0 || cy < 0 || cz < 0 || cx >= nx || cy >= ny || cz >= nz) return;
      m[(cx * ny + cy) * nz + cz] = 1;
    }

    clearChunks() {
      const gl = this.gl;
      for (const c of this.chunks.values()) this._dropChunkBuffers(c);
      this._resetPools();
      this.chunks.clear();
      this.dirty.clear();
      this._pumpQueue = null;
      this.stats = { chunks: 0, quads: 0, built: 0, missing: 0 };
    }

    /** 有效 chunk 数（画布外的坐标要丢掉，见 markDirty / buildChunk）。 */
    _chunkGrid() {
      const size = this.env && this.env.size;
      if (!size) return null;
      return [Math.ceil(size[0] / 16), Math.ceil(size[1] / 16), Math.ceil(size[2] / 16)];
    }

    /** 只标脏块（真正的增量入口：涂一格 = 27 个 chunk 的 16³ 格子）。 */
    markDirty(list) {
      const gl = this.gl;
      if (!list) { this.rebuildAll(); return; }
      const grid = this._chunkGrid();
      for (const p of list) {
        const cx = p[0], cy = p[1], cz = p[2];
        // 画布外的 chunk（负坐标 / 越过最后一块）里没有体素：**必须丢掉**。
        // buildChunk 会把负坐标夹到 0，于是同一份网格被塞进 8 个 key
        // （结构贴原点时 8 倍重复绘制）。
        if (grid && (cx < 0 || cy < 0 || cz < 0
                     || cx >= grid[0] || cy >= grid[1] || cz >= grid[2])) continue;
        const key = cx + ',' + cy + ',' + cz;
        const c = this.chunks.get(key);
        if (c && !this.dirty.has(key)) {
          // 脏块只清缓冲，网格在 pump 里重建
          this.dirty.add(key);
        } else if (!c) {
          this.dirty.add(key);
        }
      }
      void gl;
    }

    /** 全量重建：只标**非空**块（掩码来自 setEnv 的扫描 + 编辑时的补位）。
     *  ``all=true`` 时连空块一起标（诊断/回归用，语义与旧实现完全一致）。 */
    rebuildAll(all) {
      const [sx, sy, sz] = this.env.size;
      this.dirty.clear();
      const nx = Math.ceil(sx / 16), ny = Math.ceil(sy / 16), nz = Math.ceil(sz / 16);
      const m = all ? null : this.nonEmpty;
      for (let cx = 0; cx < nx; cx++) {
        for (let cy = 0; cy < ny; cy++) {
          for (let cz = 0; cz < nz; cz++) {
            if (m && !m[(cx * ny + cy) * nz + cz]) continue;
            this.dirty.add(cx + ',' + cy + ',' + cz);
          }
        }
      }
    }

    /** 按到中心点的距离重排待建块（**先建相机附近的**）。
     *
     * 打开大结构时几何是「先近后远地长出来」而不是「一次全出来」：
     * 首帧就有东西看，页面也不用等全部建完。只是顺序变了，几何一模一样。
     */
    prioritizeDirty(center) {
      if (!this.dirty.size) return;
      const c = center || [0, 0, 0];
      const ox = c[0] || 0, oy = c[1] || 0, oz = c[2] || 0;
      const dist = new Map();
      for (const k of this.dirty) {
        const p = k.split(',');
        const dx = parseInt(p[0], 10) * 16 + 8 - ox;
        const dy = parseInt(p[1], 10) * 16 + 8 - oy;
        const dz = parseInt(p[2], 10) * 16 + 8 - oz;
        dist.set(k, dx * dx + dy * dy + dz * dz);
      }
      this.dirty = new Set([...this.dirty].sort((a, b) => dist.get(a) - dist.get(b)));
    }

    /** 时间分片构建：每帧建到预算用完，返回 true 表示建完。 */
    pump(budgetMs, onProgress) {
      if (!this.dirty.size) return true;
      const t0 = (global.performance || Date).now();
      const budget = budgetMs === undefined ? 12 : budgetMs;
      let n = 0;
      for (const key of [...this.dirty]) {
        this._buildChunkKey(key);
        this.dirty.delete(key);
        n += 1;
        if (((global.performance || Date).now() - t0) >= budget) break;
      }
      if (onProgress) onProgress(n, this.dirty.size);
      return this.dirty.size === 0;
    }

    /** 同步建完（Node 基准/测试）。 */
    flush(onProgress) {
      let guard = 0;
      while (this.dirty.size && guard++ < 100000) {
        const key = this.dirty.values().next().value;
        this._buildChunkKey(key);
        this.dirty.delete(key);
        if (onProgress) onProgress(guard, this.dirty.size);
      }
    }

    _chunkKeyToPos(key) {
      const p = key.split(',');
      return [parseInt(p[0], 10), parseInt(p[1], 10), parseInt(p[2], 10)];
    }

    /** 从 view-projection 提 6 个裁剪面（Gribb–Hartmann；列主序 = glMatrix 约定）。 */
    _frustumPlanes(vp) {
      const p = this._planes;
      // left / right
      p[0] = vp[3] + vp[0]; p[1] = vp[7] + vp[4]; p[2] = vp[11] + vp[8]; p[3] = vp[15] + vp[12];
      p[4] = vp[3] - vp[0]; p[5] = vp[7] - vp[4]; p[6] = vp[11] - vp[8]; p[7] = vp[15] - vp[12];
      // bottom / top
      p[8] = vp[3] + vp[1]; p[9] = vp[7] + vp[5]; p[10] = vp[11] + vp[9]; p[11] = vp[15] + vp[13];
      p[12] = vp[3] - vp[1]; p[13] = vp[7] - vp[5]; p[14] = vp[11] - vp[9]; p[15] = vp[15] - vp[13];
      // near / far
      p[16] = vp[3] + vp[2]; p[17] = vp[7] + vp[6]; p[18] = vp[11] + vp[10]; p[19] = vp[15] + vp[14];
      p[20] = vp[3] - vp[2]; p[21] = vp[7] - vp[6]; p[22] = vp[11] - vp[10]; p[23] = vp[15] - vp[14];
      return p;
    }

    /** 块包围盒是否与视锥相交（p-vertex 测试；保守 → 绝不误剔可见块）。 */
    _chunkInFrustum(box, planes) {
      for (let i = 0; i < 24; i += 4) {
        const a = planes[i], b = planes[i + 1], c = planes[i + 2], d = planes[i + 3];
        const px = a >= 0 ? box[3] : box[0];
        const py = b >= 0 ? box[4] : box[1];
        const pz = c >= 0 ? box[5] : box[2];
        if (a * px + b * py + c * pz + d < 0) return false;
      }
      return true;
    }

    _buildChunkKey(key) {
      const [cx, cy, cz] = this._chunkKeyToPos(key);
      const perf = global.performance || Date;      // 注意：now 要连着接收者调
      const t0 = perf.now();
      const mesh = this.mesher.buildChunk(cx, cy, cz);
      const t1 = perf.now();
      this._uploadChunk(key, mesh);
      // 建网格 vs 上传 GL 的耗时分开记（诊断用：大结构在浏览器里往往卡在上传）
      this.stats.meshMs = (this.stats.meshMs || 0) + (t1 - t0);
      this.stats.uploadMs = (this.stats.uploadMs || 0) + (perf.now() - t1);
      this.stats.verts = (this.stats.verts || 0)
        + (mesh.solid.pos.length + mesh.nocull.pos.length + mesh.trans.pos.length) / 3;
    }

    /** 上传一个 chunk 的两套网格（顶点 > 65532 就切段，WebGL1 索引是 uint16）。 */
    _uploadChunk(key, mesh) {
      const gl = this.gl;
      const old = this.chunks.get(key);
      // 老槽位先留一份引用（重建时优先原地复用；_dropChunkBuffers 会把没复用的标死）
      const oldSlots = (old && old.slots) ? Object.assign({}, old.slots) : {};
      if (old) this._dropChunkBuffers(old);
      const cp = this._chunkKeyToPos(key);
      const es = this.env ? this.env.size : [0, 0, 0];
      const entry = { slots: {}, inPool: false,
                      solid: [], nocull: [], trans: [], solidIndex: null,
                      nocullIndex: null, transIndex: null,
                      solidCount: 0, nocullCount: 0, transCount: 0,
                      quads: 0, key,
                      // 块包围盒（块位置固定，只在建立时算一次 —— 视锥剔除每帧都要用）
                      box: [cp[0] * 16, cp[1] * 16, cp[2] * 16,
                            Math.min(es[0], cp[0] * 16 + 16),
                            Math.min(es[1], cp[1] * 16 + 16),
                            Math.min(es[2], cp[2] * 16 + 16)] };
      this.chunks.set(key, entry);
      // 池模式：solid/nocull 进共享池（连续区间合并成少数几次 draw call）；
      // 透明桶照旧逐块（要按距离从远到近画，合并会改混合顺序）。
      const pooled = this._usePool();
      const pack = (m, field) => {
        if (!m.quads) return;
        if (pooled && field !== 'trans') {
          this._appendToPool(entry, m, field, oldSlots[field]);
          return;
        }
        const n = m.pos.length / 3;
        const inter = new Float32Array(n * 12);      // pos3 + color3 + uv2 + limit4
        for (let i = 0; i < n; i++) {
          const o = i * 12;
          inter[o] = m.pos[i * 3]; inter[o + 1] = m.pos[i * 3 + 1]; inter[o + 2] = m.pos[i * 3 + 2];
          inter[o + 3] = m.color[i * 3]; inter[o + 4] = m.color[i * 3 + 1]; inter[o + 5] = m.color[i * 3 + 2];
          inter[o + 6] = m.uv[i * 2]; inter[o + 7] = m.uv[i * 2 + 1];
          inter[o + 8] = m.limit[i * 4]; inter[o + 9] = m.limit[i * 4 + 1];
          inter[o + 10] = m.limit[i * 4 + 2]; inter[o + 11] = m.limit[i * 4 + 3];
        }
        const segs = [];
        let vi = 0;
        while (vi < n) {
          const cnt = Math.min(n - vi, 65532);
          const buf = gl.createBuffer();
          gl.bindBuffer(gl.ARRAY_BUFFER, buf);
          gl.bufferData(gl.ARRAY_BUFFER, inter.subarray(vi * 12, (vi + cnt) * 12),
                        gl.STATIC_DRAW);
          segs.push({ buf, verts: cnt, base: vi });
          vi += cnt;
        }
        if (n <= 65532) {
          const idx = new Uint16Array(m.index.length);
          for (let i = 0; i < m.index.length; i++) idx[i] = m.index[i];
          const ib = gl.createBuffer();
          gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ib);
          gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, idx, gl.STATIC_DRAW);
          entry[field + 'Index'] = ib;
          entry[field] = segs;
          entry[field + 'Count'] = m.index.length;
        } else {
          // 分段：每段自己的索引缓冲
          let n2 = 0;
          for (const seg of segs) {
            const idx = [];
            for (let i = 0; i < m.index.length; i += 3) {
              const a = m.index[i], b = m.index[i + 1], c2 = m.index[i + 2];
              if (a < seg.base || a >= seg.base + seg.verts) continue;
              idx.push(a - seg.base, b - seg.base, c2 - seg.base);
            }
            const ib = gl.createBuffer();
            gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ib);
            gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint16Array(idx), gl.STATIC_DRAW);
            seg.index = ib;
            seg.indexCount = idx.length;
            n2 += idx.length;
          }
          entry[field] = segs;
          entry[field + 'Count'] = n2;
        }
        entry.quads += m.quads;
      };
      pack(mesh.solid, 'solid');
      pack(mesh.nocull, 'nocull');
      pack(mesh.trans, 'trans');
      this.stats.chunks = this.chunks.size;
      this.stats.quads += 0;   // 增量统计不好算，全量时另算
      this.stats.built += 1;
      this.stats.missing = this.mesher ? this.mesher.missing : 0;
    }

    /** 当前是否走共享池（WebGL2 才有 copyBufferSubData 与 uint32 索引）。 */
    _usePool() {
      return !!(this.gl2 && this.legacyBuffers !== true);
    }

    /** 两种模式通用的「丢一块的缓冲」：池模式只把槽位标死（留洞），旧模式删缓冲。 */
    _dropChunkBuffers(entry) {
      const gl = this.gl;
      // 老槽位一律先标死（算成洞）；马上就重建的块会在 _appendToPool 里**原地复活**它，
      // 并把这份洞冲减掉。绝不能「留着不标」—— 那会留下继续画旧数据的幽灵槽位。
      if (entry.slots) {
        for (const field of ['solid', 'nocull']) {
          const s = entry.slots[field];
          if (s && !s.dead) {
            s.dead = true;
            const p = this.pools[field];
            if (p) p.waste += s.vCap * 4 + s.iCap * 4;
          }
        }
      }
      for (const field of ['solid', 'nocull', 'trans']) {
        for (const seg of entry[field] || []) gl.deleteBuffer(seg.buf);
        if (entry[field + 'Index']) gl.deleteBuffer(entry[field + 'Index']);
      }
      entry.solid = []; entry.nocull = []; entry.trans = [];
      entry.solidIndex = null; entry.nocullIndex = null; entry.transIndex = null;
      entry.solidCount = 0; entry.nocullCount = 0; entry.transCount = 0;
    }

    // ------------------------------------------------------------ 顶点池（批次合并）
    _pool(field) {
      let p = this.pools[field];
      if (!p) {
        // 初始容量按预估给（solid 全额，nocull/trans 给 1/8）——省掉十几次翻倍拷贝
        const div = field === 'solid' ? 1 : 8;
        p = this.pools[field] = { v: null, i: null, vLen: 0, iLen: 0, vCap: 0, iCap: 0,
                                  slots: [], waste: 0,
                                  vInit: Math.max(1 << 16, (this.poolInitV || 0) / div),
                                  iInit: Math.max(1 << 15, (this.poolInitI || 0) / div) };
      }
      return p;
    }

    _resetPools() {
      const gl = this.gl;
      for (const p of Object.values(this.pools || {})) {
        if (p.v) gl.deleteBuffer(p.v);
        if (p.i) gl.deleteBuffer(p.i);
      }
      this.pools = {};
    }

    /** 池扩容：新建更大的缓冲，把老数据原样搬过去（WebGL2 copyBufferSubData）。 */
    _growPool(p, needV, needI) {
      const gl = this.gl;
      if (!p.v) { p.v = gl.createBuffer(); p.i = gl.createBuffer(); }
      if (needV > p.vCap) {
        const cap = Math.max(1 << 16, p.vInit || 0, p.vCap * 2, needV);
        p.vInit = 0;
        const nv = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, nv);
        gl.bufferData(gl.ARRAY_BUFFER, cap * 4, gl.DYNAMIC_DRAW);
        if (p.vLen) {
          gl.bindBuffer(gl.COPY_READ_BUFFER, p.v);
          gl.copyBufferSubData(gl.COPY_READ_BUFFER, gl.ARRAY_BUFFER, 0, 0, p.vLen * 4);
        }
        gl.deleteBuffer(p.v);
        p.v = nv;
        p.vCap = cap;
      }
      if (needI > p.iCap) {
        const cap = Math.max(1 << 15, p.iInit || 0, p.iCap * 2, needI);
        p.iInit = 0;
        const ni = gl.createBuffer();
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ni);
        gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, cap * 4, gl.DYNAMIC_DRAW);
        if (p.iLen) {
          gl.bindBuffer(gl.COPY_READ_BUFFER, p.i);
          gl.copyBufferSubData(gl.COPY_READ_BUFFER, gl.ELEMENT_ARRAY_BUFFER, 0, 0, p.iLen * 4);
        }
        gl.deleteBuffer(p.i);
        p.i = ni;
        p.iCap = cap;
      }
    }

    /** 把一块的一个桶写进池：优先**原地复用**老槽位，装不下才在池尾开新槽。 */
    _appendToPool(entry, m, field, oldSlot) {
      const gl = this.gl;
      const p = this._pool(field);
      const n = m.pos.length / 3;
      const needV = n * 12;
      const needI = m.index.length;
      let slot = null;
      if (oldSlot && oldSlot.vCap >= needV && oldSlot.iCap >= needI) {
        slot = oldSlot;                       // 原地复用：连续区间不碎（涂抹的常见情形）
        if (slot.dead) {                      // 刚从 _dropChunkBuffers 标死的 → 复活
          slot.dead = false;
          p.waste = Math.max(0, p.waste - (slot.vCap * 4 + slot.iCap * 4));
        }
        p.reused = (p.reused || 0) + 1;
      }
      if (!slot) {
        if (oldSlot && !oldSlot.dead) {        // 老槽位装不下 → 留洞
          oldSlot.dead = true;
          p.waste += oldSlot.vCap * 4 + oldSlot.iCap * 4;
        }
        // vCap 必须**对齐到一个顶点**（12 个 float）：池里后续块的顶点基址是按
        // 「已分配长度」算的，不整除就会出现「半个顶点」的错位（索引全指错 → 画面崩）。
        let vCap = needV + Math.max(96, needV >> 4);      // ~6% 余量：涂几格方块不用搬家
        vCap += (12 - (vCap % 12)) % 12;
        // 索引余量补齐成 3 的倍数，并用**退化三角形**（全零索引 = 零面积）填充：
        // 这样「连续分配」就能整段一次 drawElements，缩减的块也不用搬家。
        let iCap = needI + Math.max(120, needI >> 4);
        iCap += (3 - (iCap % 3)) % 3;
        this._growPool(p, p.vLen + vCap, p.iLen + iCap);
        slot = { box: entry.box, v0: p.vLen, vn: n, vCap, i0: p.iLen, iCount: needI,
                 iCap, dead: false, field };
        p.slots.push(slot);
        p.added = (p.added || 0) + 1;
        p.vLen += vCap;
        p.iLen += iCap;
      }
      const inter = new Float32Array(needV);   // pos3 + color3 + uv2 + limit4
      for (let i = 0; i < n; i++) {
        const o = i * 12;
        inter[o] = m.pos[i * 3]; inter[o + 1] = m.pos[i * 3 + 1]; inter[o + 2] = m.pos[i * 3 + 2];
        inter[o + 3] = m.color[i * 3]; inter[o + 4] = m.color[i * 3 + 1]; inter[o + 5] = m.color[i * 3 + 2];
        inter[o + 6] = m.uv[i * 2]; inter[o + 7] = m.uv[i * 2 + 1];
        inter[o + 8] = m.limit[i * 4]; inter[o + 9] = m.limit[i * 4 + 1];
        inter[o + 10] = m.limit[i * 4 + 2]; inter[o + 11] = m.limit[i * 4 + 3];
      }
      gl.bindBuffer(gl.ARRAY_BUFFER, p.v);
      gl.bufferSubData(gl.ARRAY_BUFFER, slot.v0 * 4, inter);
      const base = slot.v0 / 12;               // 索引 → 池内绝对顶点号（uint32）
      const idx = new Uint32Array(needI);
      for (let i = 0; i < needI; i++) idx[i] = m.index[i] + base;
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, p.i);
      gl.bufferSubData(gl.ELEMENT_ARRAY_BUFFER, slot.i0 * 4, idx);
      // 变短时把余量重新填成退化三角形（复用槽位时尾巴上还留着老数据）
      const pad = slot.iCap - needI;
      if (pad > 0 && (slot.iFilled === undefined || slot.iFilled > needI)) {
        gl.bufferSubData(gl.ELEMENT_ARRAY_BUFFER, (slot.i0 + needI) * 4,
                         new Uint32Array(pad));
      }
      slot.iFilled = needI;
      slot.vn = n;
      slot.iCount = needI;
      slot.box = entry.box;
      entry.slots[field] = slot;
      entry.inPool = true;
      entry[field + 'Count'] = needI;
      entry.quads += m.quads;
    }

    /** 池模式绘制：按槽位顺序把**连续区间**合并成最少的 drawElements。 */
    _drawPool(field, planes, countCull) {
      const gl = this.gl;
      const p = this.pools[field];
      if (!p || !p.v || !p.iLen) return;
      gl.bindBuffer(gl.ARRAY_BUFFER, p.v);
      this._bindAttribs();
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, p.i);
      // 槽位是**连续分配**的（i0 紧挨上一个的 i0+iCap），所以只要活着且没被视锥剔掉，
      // 就能把整段一次画完；余量是退化三角形，画了也不出像素。
      let runI0 = -1, runEnd = -1, drawn = 0;
      for (let i = 0; i < p.slots.length; i++) {
        const s = p.slots[i];
        if (s.dead) continue;
        if (planes && !this._chunkInFrustum(s.box, planes)) {
          if (countCull) this.stats.culled += 1;
          continue;
        }
        drawn += 1;
        if (runI0 >= 0 && s.i0 === runEnd) {
          runEnd = s.i0 + s.iCap;
        } else {
          if (runI0 >= 0) {
            gl.drawElements(gl.TRIANGLES, runEnd - runI0, gl.UNSIGNED_INT, runI0 * 4);
            this._dc += 1;
          }
          runI0 = s.i0;
          runEnd = s.i0 + s.iCap;
        }
      }
      if (runI0 >= 0) {
        gl.drawElements(gl.TRIANGLES, runEnd - runI0, gl.UNSIGNED_INT, runI0 * 4);
        this._dc += 1;
      }
      this.stats.drawn += drawn;
      let live = 0, dead = 0;
      for (const s of p.slots) { if (s.dead) dead += 1; else live += 1; }
      this.stats.slots = live;
      this.stats.slotsAdded = p.added || 0;
      this.stats.slotsReused = p.reused || 0;
      this.stats.deadSlots = dead;
      this.stats.poolMB = Math.round((p.vLen * 4 + p.iLen * 4) / 1e5) / 10;
      this.stats.wasteMB = Math.round(p.waste / 1e5) / 10;
    }

    /** 压实：把池里编辑留下的洞清掉（整结构重排 + 时间分片重建）。 */
    compact() {
      if (!this._usePool()) return false;
      this._resetPools();
      for (const c of this.chunks.values()) {
        c.slots = {};
        c.inPool = false;
        c.solidCount = 0;
        c.nocullCount = 0;
      }
      this.rebuildAll();
      return true;
    }

    /** 全量统计（测试/诊断）。 */
    countQuads() {
      let n = 0;
      for (const c of this.chunks.values()) n += c.quads;
      return n;
    }

    // ------------------------------------------------------------ 绘制
    _bindStructure() {
      const gl = this.gl;
      gl.useProgram(this.prog);
      gl.bindTexture(gl.TEXTURE_2D, this.atlasTexture);
      gl.uniform1i(this.uAtlas, 0);
      gl.uniform1f(this.uPixel, this.pixelSize);
    }

    /** 顶点属性指针（交错 48 字节：pos3 + color3 + uv2 + limit4）。给当前绑定的 ARRAY_BUFFER 用。 */
    _bindAttribs() {
      const gl = this.gl;
      gl.enableVertexAttribArray(this.aPos);
      gl.vertexAttribPointer(this.aPos, 3, gl.FLOAT, false, 48, 0);
      gl.enableVertexAttribArray(this.aColor);
      gl.vertexAttribPointer(this.aColor, 3, gl.FLOAT, false, 48, 12);
      gl.enableVertexAttribArray(this.aUV);
      gl.vertexAttribPointer(this.aUV, 2, gl.FLOAT, false, 48, 24);
      gl.enableVertexAttribArray(this.aLimit);
      gl.vertexAttribPointer(this.aLimit, 4, gl.FLOAT, false, 48, 32);
    }

    _drawSegs(segs, index, count) {
      const gl = this.gl;
      if (!count) return;
      this._dc += segs.length;                  // 本帧结构绘制的 draw call 数（收尾时记进 stats）
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, index);
      for (const seg of segs) {
        gl.bindBuffer(gl.ARRAY_BUFFER, seg.buf);
        this._bindAttribs();
        if (seg.index) {
          gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, seg.index);
          gl.drawElements(gl.TRIANGLES, seg.indexCount, gl.UNSIGNED_SHORT, 0);
        } else {
          gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, index);
          gl.drawElements(gl.TRIANGLES, seg.verts / 4 * 6, gl.UNSIGNED_SHORT, 0);
        }
      }
    }

    /** 主绘制：双 pass（不透明/cutout → 混合透明，按距离从远到近、不写深度）。 */
    drawStructure(view) {
      const gl = this.gl;
      this._dc = 0;                             // 本帧 draw call 计数（网格线/叠加层不掺进来）
      if (!this.chunks.size || !this.atlasTexture) return;
      // 投影每帧按当前画布尺寸重算（避免 setViewport 之后画布又变了 → 画面拉伸）
      const cw = gl.canvas.clientWidth || 1, ch = gl.canvas.clientHeight || 1;
      this.projMatrix = perspective(70 * Math.PI / 180, cw / ch, 0.1, 2000);
      this._bindStructure();
      gl.uniformMatrix4fv(this.uView, false, view);
      gl.uniformMatrix4fv(this.uProj, false, this.projMatrix);
      gl.uniform1f(this.uScale, this.coordScale || 1);
      gl.enable(gl.DEPTH_TEST);
      gl.depthMask(true);
      gl.disable(gl.BLEND);
      gl.uniform1f(this.uCut, 0.5);
      // 背面剔除：**只对 cull_safe 桶**（整方块、贴图全不透明、面必朝外）开，
      // 填充量省掉小一半；异形/薄片/带透明像素在 nocull 桶里照旧不剔。
      const cullFaces = this.cullFace !== false;
      if (cullFaces) gl.enable(gl.CULL_FACE);
      // 视锥剔除：包围盒完全在视锥外的块不画（大建筑环绕时省掉一大半 draw call 与光栅）。
      // 保守判定，绝不误剔；renderer.cull=false 可关掉做像素 A/B。
      let planes = null;
      if (this.cull) {
        mat4Mul(this._vp, this.projMatrix, view);
        planes = this._frustumPlanes(this._vp);
      }
      this.stats.culled = 0;
      this.stats.drawn = 0;
      this.stats.dcSolid = 0;
      this.stats.dcNocull = 0;
      this.stats.dcTrans = 0;
      if (this._usePool()) {
        // 池模式：solid/nocull 各一次（或几次）合并绘制，不再逐块 draw call
        let d0 = this._dc;
        this._drawPool('solid', planes, true);
        this.stats.dcSolid = this._dc - d0;
        // 之后（nocull 与透明桶）都必须是**关掉** CULL_FACE 的状态：
        // 旧模式就是这么留着的，重新打开会把玻璃/水的背面剔掉（画面会变）。
        if (cullFaces) gl.disable(gl.CULL_FACE);
        d0 = this._dc;
        this._drawPool('nocull', planes, false);
        this.stats.dcNocull = this._dc - d0;
      } else {
        for (const c of this.chunks.values()) {
          if (planes && !this._chunkInFrustum(c.box, planes)) { this.stats.culled += 1; continue; }
          this.stats.drawn += 1;
          this._drawSegs(c.solid, c.solidIndex, c.solidCount);
        }
        // 不剔除桶：先关 CULL_FACE 再画（异形/薄片/带透明像素）
        if (cullFaces) gl.disable(gl.CULL_FACE);
        for (const c of this.chunks.values()) {
          if (!c.nocullCount) continue;
          if (planes && !this._chunkInFrustum(c.box, planes)) continue;
          this._drawSegs(c.nocull, c.nocullIndex, c.nocullCount);
        }
      }
      // 透明：远的先画（画家算法），且不写深度
      const eye = this._eye;
      if (!eye) {
        const inv = mat4Invert(view);
        this._eye = inv ? [inv[12], inv[13], inv[14]] : null;
      }
      const e = this._eye || [0, 0, 0];
      const list = [];
      for (const [key, c] of this.chunks) {
        if (!c.transCount) continue;
        if (planes && !this._chunkInFrustum(c.box, planes)) continue;
        const p = c.box;                      // 已是 [x0,y0,z0,x1,y1,z1]
        const dx = p[0] + 8 - e[0], dy = p[1] + 8 - e[1], dz = p[2] + 8 - e[2];
        list.push([dx * dx + dy * dy + dz * dz, c]);
      }
      if (list.length) {
        list.sort((a, b) => b[0] - a[0]);
        gl.enable(gl.BLEND);
        gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
        gl.depthMask(false);
        gl.uniform1f(this.uCut, 0.01);
        const d0 = this._dc;
        for (const [, c] of list) this._drawSegs(c.trans, c.transIndex, c.transCount);
        this.stats.dcTrans = this._dc - d0;
        gl.depthMask(true);
        gl.disable(gl.BLEND);
      }
      this.stats.drawCalls = this._dc;
    }

    setGrid(size) {
      const gl = this.gl;
      const [sx, sy, sz] = size;
      const a = [0.8, 0.8, 0.8];
      // 地面网格/画布边框沉在方块底面之下一点点：与底面共面会 z-fighting
      const gy = -0.003;
      const segs = [];
      const line = (x1, y1, z1, x2, y2, z2, c, out) => {
        (out || segs).push(x1, y1, z1, c[0], c[1], c[2],
                           x2, y2, z2, c[0], c[1], c[2]);
      };
      line(0, 0, 0, 0, sy, 0, a);
      line(sx, 0, 0, sx, sy, 0, a);
      line(0, 0, sz, 0, sy, sz, a);
      line(sx, 0, sz, sx, sy, sz, a);
      line(0, sy, 0, 0, sy, sz, a);
      line(sx, sy, 0, sx, sy, sz, a);
      line(0, sy, 0, sx, sy, 0, a);
      line(0, sy, sz, sx, sy, sz, a);
      for (let x = 1; x <= sx; x++) line(x, gy, 0, x, gy, sz, a);
      for (let z = 1; z <= sz; z++) line(0, gy, z, sx, gy, z, a);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.gridBuf);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(segs), gl.STATIC_DRAW);
      this.gridVerts = segs.length / 6;

      // ---- 坐标轴：三条**箭头**（不再是两根细彩线）+ 箭头旁的文字
      // （文字由 viewer3d 把 _axisTips 投影到屏幕，用 HTML 标签画：清晰、可选中）
      //
      // 重要：箭头画在数据盒**外面**的“沟边”里（X 轴沿 z=-gap、Z 轴沿 x=-gap、
      // Y 轴沿外侧那一竖），所以它们不会穿过模型。这样既能让它们**不吃深度**
      // （永远看得见），又不会把彩线盖在建筑顶面上（旧写法两根贴在 y=0 的轴上，
      // 从正上方俯视时红/蓝线会直接穿过屋顶）。
      const ax = [];
      const gap = 0.75;
      const len = 1.0;                       // 超出数据盒的“出头”长度
      const gy2 = -0.004;
      this._axisTips = [[sx + gap + len, gy2, -gap],
                        [-gap, sy + len, -gap],
                        [-gap, gy2, sz + gap + len]];
      const C = { x: [0.96, 0.32, 0.28], y: [0.36, 0.88, 0.44], z: [0.34, 0.60, 1.0] };
      this._arrow3d(ax, line, [0, gy2, -gap], [1, 0, 0], sx + gap + len, C.x);
      this._arrow3d(ax, line, [-gap, 0, -gap], [0, 1, 0], sy + len, C.y);
      this._arrow3d(ax, line, [-gap, gy2, 0], [0, 0, 1], sz + gap + len, C.z);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.axisBuf);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(ax), gl.STATIC_DRAW);
      this.axisVerts = ax.length / 6;
    }

    /** 一条带箭头的坐标轴：杆 + 四条“回折”线组成的尖头。
     *
     * 只用线（跟已有 line program 共用），不求几何精确，求的是
     * 「从任何角度看都知道这是哪根轴、朝哪一边」。
     */
    _arrow3d(out, line, o, d, len, color) {
      const [ox, oy, oz] = o;
      const [dx, dy, dz] = d;
      // 在垂直于 d 的平面里取一组基（d 必是某个单位轴）
      const u = dx ? [0, 1, 0] : [1, 0, 0];
      const v = dz ? [0, 1, 0] : [0, 0, 1];
      const P = (t, uu, vv) => [ox + dx * t + u[0] * uu + v[0] * vv,
                                oy + dy * t + u[1] * uu + v[1] * vv,
                                oz + dz * t + u[2] * uu + v[2] * vv];
      const p0 = P(0, 0, 0), p1 = P(len, 0, 0);
      line(p0[0], p0[1], p0[2], p1[0], p1[1], p1[2], color, out);
      const head = Math.min(1.8, Math.max(0.6, len * 0.10));
      const wide = head * 0.5;
      for (const [uu, vv] of [[wide, wide], [-wide, wide],
                              [-wide, -wide], [wide, -wide]]) {
        const q = P(len - head, uu, vv);
        line(p1[0], p1[1], p1[2], q[0], q[1], q[2], color, out);
      }
    }

    _bindLines(view, depth) {
      const gl = this.gl;
      gl.useProgram(this.lineProg);
      gl.uniformMatrix4fv(this.lView, false, view);
      gl.uniformMatrix4fv(this.lProj, false, this.projMatrix);
      gl.uniform1f(this.lScale, this.coordScale || 1);
      gl.depthMask(false);
      // depth=true：线要被建筑遮挡（地面网格 / 画布边框）；
      // depth=false（默认）：覆盖层线（准星、高亮框、模块框、gizmo）永远看得见
      if (depth) gl.enable(gl.DEPTH_TEST); else gl.disable(gl.DEPTH_TEST);
    }

    _drawLines(buf, verts) {
      const gl = this.gl;
      gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      gl.enableVertexAttribArray(this.lPos);
      gl.vertexAttribPointer(this.lPos, 3, gl.FLOAT, false, 24, 0);
      gl.enableVertexAttribArray(this.lColor);
      gl.vertexAttribPointer(this.lColor, 3, gl.FLOAT, false, 24, 12);
      gl.drawArrays(gl.LINES, 0, verts);
    }

    drawGrid(view) {
      if (!this.gridVerts && !this.axisVerts) return;
      // 地面网格必须**吃深度**：旧写法关着深度测试画线 → 从上看下去网格会盖在
      // 模型上（一片格子线穿过建筑，看不清方块）。高亮/准星那些覆盖层线不受影响。
      if (this.gridVerts) {
        this._bindLines(view, true);
        this._drawLines(this.gridBuf, this.gridVerts);
      }
      // 坐标轴箭头**不吃深度**：它就是回答“哪边是哪个方向”的，
      // 被建筑挡住就失去意义了（与准星/gizmo 同一类覆盖层）。
      // ``showAxisArrows=false`` 供回归测试单独量大网格遮挡（见 studio_ui_audit）。
      if (this.axisVerts && this.showAxisArrows !== false) {
        this._bindLines(view);
        this._drawLines(this.axisBuf, this.axisVerts);
      }
      this.gl.enable(this.gl.DEPTH_TEST);
      this.gl.depthMask(true);
    }

    /** 单个格子的描边（拾取高亮/接口/画布框都用它）。 */
    drawOutline(view, pos) {
      const gl = this.gl;
      const x = pos[0], y = pos[1], z = pos[2];
      const d = 0.002;
      const a = [x - d, y - d, z - d], b = [x + 1 + d, y + 1 + d, z + 1 + d];
      const c = [1, 1, 1];
      const E = [[0, 0, 0, 1, 0, 0], [0, 0, 0, 0, 1, 0], [0, 0, 0, 0, 0, 1],
                 [1, 0, 0, 1, 1, 0], [1, 0, 0, 1, 0, 1], [0, 1, 0, 1, 1, 0],
                 [0, 1, 0, 0, 1, 1], [1, 1, 0, 1, 1, 1], [0, 0, 1, 1, 0, 1],
                 [0, 0, 1, 0, 1, 1], [1, 0, 1, 1, 1, 1], [0, 1, 1, 1, 1, 1]];
      const segs = [];
      for (const e of E) {
        segs.push(a[0] + (b[0] - a[0]) * e[0], a[1] + (b[1] - a[1]) * e[1],
                  a[2] + (b[2] - a[2]) * e[2], c[0], c[1], c[2],
                  a[0] + (b[0] - a[0]) * e[3], a[1] + (b[1] - a[1]) * e[4],
                  a[2] + (b[2] - a[2]) * e[5], c[0], c[1], c[2]);
      }
      gl.bindBuffer(gl.ARRAY_BUFFER, this.lineBuf);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(segs), gl.DYNAMIC_DRAW);
      this._bindLines(view);
      this._drawLines(this.lineBuf, segs.length / 6);
      gl.enable(gl.DEPTH_TEST);
      gl.depthMask(true);
    }
  }

  // ---------------------------------------------------------------- 小工具
  /** out = a * b（列主序，与 glMatrix 一致）。视锥剔除要 proj*view。 */
  function mat4Mul(out, a, b) {
    for (let c = 0; c < 4; c++) {
      const b0 = b[c * 4], b1 = b[c * 4 + 1], b2 = b[c * 4 + 2], b3 = b[c * 4 + 3];
      out[c * 4] = a[0] * b0 + a[4] * b1 + a[8] * b2 + a[12] * b3;
      out[c * 4 + 1] = a[1] * b0 + a[5] * b1 + a[9] * b2 + a[13] * b3;
      out[c * 4 + 2] = a[2] * b0 + a[6] * b1 + a[10] * b2 + a[14] * b3;
      out[c * 4 + 3] = a[3] * b0 + a[7] * b1 + a[11] * b2 + a[15] * b3;
    }
    return out;
  }

  function mat4Create() {
    const m = new Float32Array(16);
    m[0] = m[5] = m[10] = m[15] = 1;
    return m;
  }

  function perspective(fovy, aspect, near, far) {
    const f = 1.0 / Math.tan(fovy / 2);
    const nf = 1 / (near - far);
    const m = mat4Create();
    m[0] = f / aspect;
    m[5] = f;
    m[10] = (far + near) * nf;
    m[11] = -1;
    m[14] = 2 * far * near * nf;
    m[15] = 0;
    return m;
  }

  function mat4Invert(m) {
    // 只有视图矩阵求逆（rigid body）：用 gl-matrix 已有实现
    const g = global.glMatrix;
    if (g && g.mat4) {
      const out = g.mat4.create();
      return g.mat4.invert(out, m);
    }
    return null;
  }

  global.McRender3D = {
    StructureEnv, Mesher, Renderer3D,
    FACE_SHADE, DIR_VEC, DIR_NAME,
    get AO_LEVELS() { return AO_LEVELS; },
    /** AO 四档表由 Python（/api/palette-info 的 aoLevels）下发，保证与 mcrender 同源。 */
    setAoLevels(list) {
      if (Array.isArray(list) && list.length === 4 && list.every((x) => x > 0 && x <= 1)) {
        AO_LEVELS = list.slice();
      }
    },
  };
})(typeof window !== 'undefined' ? window : globalThis);
