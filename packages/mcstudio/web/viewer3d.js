/* mcstudio 3D viewer — deepslate (MIT) + gl-matrix (MIT), vendored under /static/vendor/.
   Custom StructureProvider reads the flat Uint16Array directly (no sparse objects),
   resources are loaded from /api/asset (mcmeta mirror, cached in .cache/mcassets). */
/* global deepslate, glMatrix */
(function (global) {
  'use strict';
  const { mat4, vec3 } = glMatrix;

  // ---------------------------------------------------------------- helpers
  /** 当前结构的「渲染语义」表（方块名 → {layer,tint,special,has_elements,…}）。
   * 由 VoxelViewer.load 填；特判渲染器补丁要用它判断「这方块自己有模型吗」。 */
  const ACTIVE = { semanticsByName: {}, renderer: null };
  // ?renderer=deepslate 可切回旧渲染路径（A/B 对比 / 回归用）
  try {
    const q = new URLSearchParams((global.location && global.location.search) || '');
    if (q.get('renderer')) ACTIVE.renderer = q.get('renderer');
  } catch (e) { /* ignore */ }

  /** 面明暗：vanilla 的 1.0 / 0.5 / 0.8 / 0.6（deepslate 原式算不出 0.6，见下）。 */
  const VANILLA_LIGHT_GLSL = [
    '    float lY = step(0.5, normal.y);',
    '    float lDn = step(0.5, -normal.y);',
    '    float lNS = step(0.5, abs(normal.z));',
    '    float lEW = (1.0 - lY) * (1.0 - lDn) * (1.0 - lNS);',
    '    vLighting = 1.0 * lY + 0.5 * lDn + 0.8 * lNS + 0.6 * lEW;',
  ].join('\n');

  /** 把 deepslate 顶点的明暗公式换成 vanilla 的四档（up/down/南北/东西）。
   *
   * 它原本是 ``normal.y*0.2 + abs(normal.z)*0.1 + 0.8``：上下=1.0/0.6、
   * 南北=0.9、东西=0.8 —— 和游戏不一致（应为 1.0/0.5/0.8/0.6），而且这个
   * 形式根本表达不出 0.6（东西向）。这里在 shaderSource 里整体替换，
   * 找不到原文就原样放过（并计数，方便发现上游改版）。
   */
  function patchShaderLighting(gl) {
    if (!gl || gl.__mcLightPatched) return;
    const orig = gl.shaderSource.bind(gl);
    let hits = 0, misses = 0;
    gl.shaderSource = (shader, source) => {
      if (typeof source === 'string' && source.includes('vLighting = normal.y')) {
        const next = source.replace(/\s*vLighting = normal\.y[^;]*;\s*/,
                                    '\n' + VANILLA_LIGHT_GLSL + '\n');
        if (next !== source) { hits += 1; source = next; } else { misses += 1; }
      }
      return orig(shader, source);
    };
    gl.__mcLightPatched = true;
    gl.__mcLightStats = () => ({ hits, misses });
  }

  /** 特判方块（箱/床/告示牌/旗帜/头颅/潜影盒/罐/钟/导管/铜傀儡）在**自己没模型**时才补几何。
   *
   * deepslate 0.27.1 的表是按老版本写的：床和告示牌在 1.21.4+ 已经是普通方块模型，
   * 它还会再叠一套 ``entity/*`` 几何（而且那些贴图在新版本已经 404）→ 旧行为是
   * 床/牌子花屏。这里用 Python 端的 ``has_elements`` 语义把重复的那套关掉。
   */
  /** 从方块实体 NBT 里推出旗帜图案层贴图（deepslate 的 bannerRenderer 会按图案要贴图）。
   *
   * 图案层贴图不在任何 blockstate/model 里，Python 侧只能列出底色层（banner_base）；
   * 这里按实际 NBT 补上 ``entity/banner/<pattern>``，否则图案层会采样到 0 号“缺贴图”。
   */
  function bannerPatternTextures(rows) {
    const out = new Set();
    for (const row of rows || []) {
      const id = String(row && row[3] || '');
      if (!id.includes('banner')) continue;
      const data = (row && row[4]) || {};
      const list = data.patterns || data.Patterns || [];
      for (const p of (Array.isArray(list) ? list : [])) {
        const pattern = p && (p.pattern || p.Pattern);
        if (pattern) out.add('entity/banner/' + pattern);
      }
    }
    return [...out];
  }

  function patchSpecialRenderersOnce() {
    const D = global.deepslate;
    if (!D || !D.SpecialRenderers || D.__mcSpecialPatched) return;
    const SR = D.SpecialRenderers;
    const orig = SR.getBlockMesh.bind(SR);
    SR.getBlockMesh = function (state, nbt, atlas, cull) {
      try {
        const name = idStr(state.getName().toString());
        const sem = ACTIVE.semanticsByName[name];
        if (sem && sem.has_elements) return new D.Mesh();
        // 模型里没有几何、但语义表认不出（旧版本数据）时照旧走特判
        return orig(state, nbt, atlas, cull);
      } catch (e) {
        return orig(state, nbt, atlas, cull);
      }
    };
    D.__mcSpecialPatched = true;
  }
  function parseState(s) {
    const i = s.indexOf('[');
    if (i < 0 || !s.endsWith(']')) return { name: s, props: {} };
    const name = s.slice(0, i);
    const props = {};
    for (const pair of s.slice(i + 1, -1).split(',')) {
      const j = pair.indexOf('=');
      if (j > 0) props[pair.slice(0, j).trim()] = pair.slice(j + 1).trim();
    }
    return { name, props };
  }
  function idStr(name) { return name.includes(':') ? name : 'minecraft:' + name; }
  function pathOf(name) { return name.replace(/^minecraft:/, ''); }
  function b64ToU16(b64) {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new Uint16Array(bytes.buffer);
  }
  async function getJSON(url) {
    const r = await fetch(url);
    if (!r.ok) {
      let msg = r.statusText;
      try { msg = (await r.json()).error || msg; } catch (e) { /* ignore */ }
      throw new Error(msg);
    }
    return r.json();
  }
  async function post(url, body) {
    const r = await fetch(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    if (!r.ok) {
      let msg = r.statusText;
      try { msg = (await r.json()).error || msg; } catch (e) { /* ignore */ }
      throw new Error(msg);
    }
    return r.json();
  }

  // ---------------------------------------------------------------- assets
  const CUBE_FACES = ['up', 'down', 'north', 'south', 'east', 'west'];
  function cubeModelJSON(texture) {
    const faces = {};
    for (const f of CUBE_FACES) faces[f] = { texture: '#all', cullface: f };
    return { textures: { all: texture, particle: texture },
             elements: [{ from: [0, 0, 0], to: [16, 16, 16], faces }] };
  }

  class McResources {
    constructor(version) {
      this.version = version;
      this.defs = {};
      this.models = {};
      this.summary = {};
      this.flags = {};
      this.atlas = null;
      this._texCache = new Map();
      this._modelCache = new Map();
    }
    getBlockDefinition(id) { return this.defs[id.toString()] || null; }
    getBlockModel(id) { return this.models[id.toString()] || null; }
    getTextureAtlas() { return this.atlas.getTextureAtlas(); }
    getTextureUV(id) { return this.atlas.getTextureUV(id); }
    getPixelSize() { return this.atlas.getPixelSize(); }
    getBlockFlags(id) { return this.flags[id.toString()] || {}; }
    getBlockProperties(id) {
      const e = this.summary[id.toString()];
      return e ? e.properties : null;
    }
    getDefaultBlockProperties(id) {
      const e = this.summary[id.toString()];
      return e ? e.default : null;
    }
  }

  function assetURL(version, rel) {
    return '/api/asset?version=' + encodeURIComponent(version) +
      '&rel=' + encodeURIComponent(rel);
  }

  /** 把一个 blockstate JSON 里**所有** `"model": "…"` 引用挖出来（variants + multipart，不分属性）。 */
  function collectModelRefs(node, out) {
    if (!node || typeof node !== 'object') return;
    if (Array.isArray(node)) { for (const x of node) collectModelRefs(x, out); return; }
    for (const [k, v] of Object.entries(node)) {
      if (k === 'model' && typeof v === 'string') out.add(idStr(v));
      else collectModelRefs(v, out);
    }
  }

  /* Load blockstates/models/textures for a palette. Falls back to flat colors. */
  async function loadResources(version, states, opts) {
    opts = opts || {};
    const res = new McResources(version);
    try {
      const names = [...new Set(states.map(s => idStr(s.name)))];
      const defs = await post('/api/blockdefs', { version, names });
      res.summary = defs.blocks || {};
      const rawStates = new Map();
      await Promise.all(names.map(async (name) => {
        const r = await fetch(assetURL(version, 'blockstates/' + pathOf(name) + '.json'));
        if (!r.ok) return;
        try {
          const json = await r.json();
          rawStates.set(name, json);
          res.defs[name] = deepslate.BlockDefinition.fromJson(json);
        } catch (e) { /* skip */ }
      }));

      // collect model refs from palette variants
      const modelRefs = new Set();
      for (const s of states) {
        const name = idStr(s.name);
        const def = res.defs[name];
        if (!def) continue;
        const defs2 = res.summary[name] || {};
        const props = Object.assign({}, defs2.default || {}, s.props || {});
        let variants = [];
        try { variants = def.getModelVariants(props) || []; } catch (e) { variants = []; }
        for (const v of variants) if (v && v.model) modelRefs.add(idStr(v.model));
      }
      // **还要**把 blockstate 文件里引用的所有模型都取下来（不分属性）：
      // 墙的 east=tall 用 cobblestone_wall_side_tall、楼梯的 shape=outer_left 用另一个模型……
      // 只取「调色板当前状态对应的变体」的话，编辑器里一旦出现新连接状态（方块更新重算 /
      // 手动改属性），那个状态的模型就不在资源里 → getMesh 抛异常 → **整块 chunk 一起消失**
      //（实测就是「墙/栅栏旁边放个方块之后变透明」）。
      for (const json of rawStates.values()) collectModelRefs(json, modelRefs);

      const raw = new Map();
      async function collect(id) {
        if (raw.has(id)) return;
        const r = await fetch(assetURL(version, 'models/' + pathOf(id) + '.json'));
        const json = r.ok ? await r.json().catch(() => null) : null;
        raw.set(id, json);
        if (json && json.parent && !String(json.parent).startsWith('builtin/')) {
          await collect(idStr(json.parent));
        }
      }
      await Promise.all([...modelRefs].map(collect));

      for (const [id, json] of raw) {
        if (json) {
          try { res.models[id] = deepslate.BlockModel.fromJson(json); }
          catch (e) { /* skip */ }
        }
      }
      const accessor = { getBlockModel: (mid) => res.models[mid.toString()] || null };
      for (const id of Object.keys(res.models)) {
        try { res.models[id].flatten(accessor); } catch (e) { /* skip */ }
      }

      // textures
      const texRefs = new Set();
      for (const json of raw.values()) {
        if (!json || !json.textures) continue;
        for (const v of Object.values(json.textures)) {
          const ref = typeof v === 'string' ? v : (v && v.sprite) || null;
          if (ref && !ref.startsWith('#')) texRefs.add(idStr(ref));
        }
      }
      const blobs = {};
      const wanted = [...texRefs];
      // 模型里引用不到的贴图（方块实体 entity/*、液体流面）由 Python 语义表点名
      for (const ref of opts.extraTextures || []) wanted.push(idStr(ref));
      await Promise.all([...new Set(wanted)].map(async (id) => {
        const r = await fetch(assetURL(version, 'textures/' + pathOf(id) + '.png'));
        if (r.ok) blobs[id] = await r.blob();
      }));
      if (opts.onProgress) opts.onProgress('atlas ' + Object.keys(blobs).length);
      res.atlas = await deepslate.TextureAtlas.fromBlobs(blobs);
      return { resources: res, mode: 'texture', missing: raw.size - Object.keys(res.models).length };
    } catch (e) {
      console.warn('mcstudio: 资源加载失败，回退色彩模式', e);
      return colorResources(states, opts.colors || {});
    }
  }

  /* Fallback: one solid-color cube per state; works offline without textures. */
  function colorResources(states, colors) {
    const res = new McResources('color');
    const n = Math.max(1, states.length);
    const cols = Math.max(1, Math.ceil(Math.sqrt(n)));
    const size = 1;
    while (size < cols) size *= 2;
    const px = size * 16;
    const canvas = document.createElement('canvas');
    canvas.width = px; canvas.height = px;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#ff00ff'; ctx.fillRect(0, 0, 16, 16);
    const uv = {};
    const byIndex = {};
    states.forEach((s, i) => {
      const x = (i + 1) % size, y = Math.floor((i + 1) / size);
      const col = (colors[s.raw] || colors[String(i)] || '#8a8f98');
      ctx.fillStyle = col;
      ctx.fillRect(x * 16, y * 16, 16, 16);
      const part = 1 / size;
      uv['mcstudio:color/' + i] = [part * x, part * y, part * (x + 1), part * (y + 1)];
      byIndex[s.name] = 'mcstudio:color/' + i;
    });
    const imageData = ctx.getImageData(0, 0, px, px);
    res.atlas = {
      getTextureAtlas: () => imageData,
      getTextureUV: (id) => uv[id.toString()] || [0, 0, 1 / size, 1 / size],
      getPixelSize: () => 1 / (size * 16),
    };
    for (const name of Object.keys(byIndex)) {
      const id = byIndex[name];
      res.defs[name] = new deepslate.BlockDefinition({ '': { model: id } }, undefined);
      const bm = deepslate.BlockModel.fromJson(cubeModelJSON(id));
      bm.flatten({ getBlockModel: () => null });
      res.models[id] = bm;
    }
    return { resources: res, mode: 'color' };
  }

  // ---------------------------------------------------------------- provider
  class FlatProvider {
    constructor(viewer) { this.v = viewer; }
    getSize() { return this.v.size; }
    getBlock(pos) {
      const v = this.v;
      const [x, y, z] = pos;
      const [sx, sy, sz] = v.size;
      if (x < 0 || y < 0 || z < 0 || x >= sx || y >= sy || z >= sz) return null;
      const idx = v.voxels[(y * sz + z) * sx + x];
      return { pos: [x, y, z], state: v.blockStates[idx] || v.blockStates[0] };
    }
    *getBlocks() {
      const v = this.v;
      const [sx, sy, sz] = v.size;
      const l = v.layer;
      const y0 = l.only ? l.y0 : 0;
      const y1 = l.only ? l.y1 + 1 : sy;
      for (let y = y0; y < y1; y++) {
        for (let z = 0; z < sz; z++) {
          const row = (y * sz + z) * sx;
          for (let x = 0; x < sx; x++) {
            const idx = v.voxels[row + x];
            if (idx === 0) continue;
            yield { pos: [x, y, z], state: v.blockStates[idx] || v.blockStates[0] };
          }
        }
      }
    }
  }

  // ---------------------------------------------------------------- viewer
  class VoxelViewer {
    constructor(canvas, opts) {
      opts = opts || {};
      this.canvas = canvas;
      this.opts = opts;
      // 优先 WebGL2（uint32 索引 → 顶点池批处理；老浏览器退 WebGL1 走旧路径）
      this.gl = canvas.getContext('webgl2', { antialias: true })
        || canvas.getContext('webgl', { antialias: true });
      if (!this.gl) throw new Error('浏览器不支持 WebGL');
      patchShaderLighting(this.gl);      // 面明暗 → vanilla 四档（1.0/0.5/0.8/0.6）
      patchSpecialRenderersOnce();       // 床/告示牌这类「自己有模型」的不再叠旧特判几何
      this.bg = 'dark';                 // dark / black / white / transparent
      this._bgColor = [0.055, 0.06, 0.07, 1];
      this.size = [1, 1, 1];
      this.voxels = new Uint16Array(1);
      this.palette = ['minecraft:air'];
      this.blockStates = [new deepslate.BlockState('minecraft:air')];
      this.provider = new FlatProvider(this);
      this.renderer = null;
      this.resources = null;
      this.version = opts.version || '26.2';
      this.mode = 'view';               // 'view' | 'edit'
      this.hooks = null;                // editor pointer hooks
      this.ports = [];
      this.moduleBoxes = [];
      this.portPreview = null;          // 待填接口预览框（接口工具）
      this.frameBox = null;             // 画布框（保存时按它裁剪）
      this.gizmo = null;
      this.cursor = null;
      this.region = null;               // 选区手柄/目标框（移动、复制）
      this.showAxisLabels = true;       // 坐标轴 X/Y/Z 文字（HTML 叠加层，投影定位）
      this._axisLabels = null;
      this.layer = { y0: 0, y1: 0, only: false };
      this.highlight = null;
      this.cam = { yaw: -0.65, pitch: 0.55, dist: 40, target: [0, 0, 0] };
      // LOD：超大结构拉远时用粗网格（中小结构完全不受影响）
      this.lodEnabled = true;
      this.lodK = 4;              // 每 4³ 格压成一格
      this.lodMinCells = 2e6;     // 只对 >200 万格的结构启用
      this.lodEnter = 1.7;        // 距离 > 1.7×最长边 → 切粗网格
      this.lodExit = 1.25;        // 距离 < 1.25×最长边 → 回全精度（滞回）
      this._lodActive = false;
      this._lodDirty = false;
      this._lodRenderer = null;
      this._dirtyChunks = new Set();
      this._rebuildTimer = null;
      this._raf = null;
      this._resizeObserver = null;
      this._bindInput();
      this._resize();
    }

    dispose() {
      this.renderer = null;
      this.resources = null;
      this._raf = null;
    }

    // ------------------------------------------------------------ loading
    async load(info, voxels) {
      this.size = info.size.slice();
      this.palette = info.palette.slice();
      this.version = info.version || this.version;
      this.voxels = voxels;
      const states = this.palette.map((s) => {
        const p = parseState(s);
        return { name: p.name, props: p.props, raw: s };
      });
      this.states = states;
      this.blockStates = this.palette.map((s, i) => {
        const p = parseState(s);
        return i === 0 ? new deepslate.BlockState('minecraft:air')
          : new deepslate.BlockState(idStr(p.name), p.props);
      });
      const colors = {};
      this.palette.forEach((s, i) => { colors[s] = info.colors ? info.colors[s] : '#8a8f98'; });
      this._colors = info.colors || {};
      this._flags = info.flags || {};
      this.extraTextures = info.extraTextures || info.textures || [];
      if (info.aoLevels && global.McRender3D && global.McRender3D.setAoLevels) {
        global.McRender3D.setAoLevels(info.aoLevels);
      }
      this._syncSemantics(info.flags || {});
      this.setBlockEntities(info.blockEntities || []);
      const loaded = await loadResources(this.version, states, {
        colors, onProgress: this.opts.onProgress,
        extraTextures: this.extraTextures,
      });
      this.resources = loaded.resources;
      this.reloadFlags(info.flags || {});
      this.renderMode = loaded.mode;
      if (this.opts.onProgress) this.opts.onProgress('mesh');
      await this._attachRenderer();
      this._resize();
      this.frame();
      return loaded.mode;
    }

    /** 建渲染器：默认用我们自己的 mesher（../renderer3d.js）；
     * ``?renderer=deepslate`` 或 ``McStudio3D.renderer = 'deepslate'`` 可切回旧路径（A/B 对比用）。 */
    async _attachRenderer() {
      const mode = this.opts.renderer || ACTIVE.renderer || 'own';
      this.rendererMode = mode;
      if (mode === 'deepslate' || !global.McRender3D) {
        this.renderer = new deepslate.StructureRenderer(
          this.gl, this.provider, this.resources,
          { chunkSize: 16, useInvisibleBlockBuffer: false });
        return;
      }
      const r = new global.McRender3D.Renderer3D(this.gl);
      r.setResources(this.resources);
      r.setEnv(new global.McRender3D.StructureEnv({
        size: this.size,
        voxels: this.voxels,
        blockStates: this.blockStates,
        semantics: this.semantics,
        nbt: this.blockEntityNbt || null,
      }));
      r.setViewport(0, 0, this.canvas.width, this.canvas.height);
      r.setGrid(this.size);
      r.rebuildAll();
      this.renderer = r;
      this._lodRenderer = null;      // 换了结构：旧的粗网格作废
      this._lodActive = false;
      this._lodDirty = false;
      // 建网格分两段：**先按到结构中心的距离同步建一批**（200ms 预算，打开这一段本来就
      // 要等读盘+传输），剩下的交给 _pumpRest() 在 rAF 里 24ms/帧 建完。
      // （旧写法是 `while (!pump(24)) await setTimeout(0)`：load() 要等全部建完才返回，
      //   18.5M 格的太空探索者会假死好几秒。）
      const t0 = Date.now();
      this._meshT0 = t0;
      this._meshPumping = true;
      this.meshMs = -1;
      r.prioritizeDirty([this.size[0] / 2, this.size[1] / 2, this.size[2] / 2]);
      r.pump(200, (n, left) => {
        if (this.opts.onProgress && left) this.opts.onProgress(`mesh ${left}`);
      });
      this._meshFirstMs = Date.now() - t0;
      if (!r.dirty.size) {
        this.meshMs = this._meshFirstMs;      // 小结构：一批就建完了
        this._meshPumping = false;
      } else {
        this._pumpRest();
      }
    }

    /** 队列空了 → 记下总建网格耗时（与旧实现 ``meshMs`` 同义：load→网格齐）并收尾。
     *  rAF 路径与 setTimeout 兜底路径都会调，保证这个数一定会被记上。 */
    _meshSettled() {
      const r = this.renderer;
      if (!r || !r.dirty || r.dirty.size) return false;
      if (this._meshPumping) {
        this.meshMs = Date.now() - this._meshT0;
        this._meshPumping = false;
        if (this.opts.onProgress) this.opts.onProgress('');
      }
      return true;
    }

    /** 把剩下的脏块建完：正常走 rAF（frame() 里 24ms/帧 的分片）；rAF 被节流
     *  （后台标签页 / 被遮挡 / headless）时用 setTimeout 兜底，保证几何一定会建完。 */
    _pumpRest() {
      if (!this.renderer || this.rendererMode === 'deepslate') return;
      if (this._meshSettled()) return;
      this.frame();
      if (this._meshFallback) return;
      this._meshFallback = setTimeout(() => {
        this._meshFallback = null;
        const r = this.renderer;
        if (!r || this.rendererMode === 'deepslate') return;
        if (r.dirty && r.dirty.size) r.pump(24);
        this._pumpRest();
      }, 120);
    }

    /** 方块实体 NBT（"x,y,z" → NbtCompound），供特判渲染器用（旗帜图案等）。 */
    setBlockEntities(rows) {
      if (!rows || !rows.length || !global.deepslate.jsonToNbt) {
        this.blockEntityNbt = null;
        return;
      }
      const map = {};
      for (const row of rows) {
        try {
          if (!row[3]) continue;
          map[row[0] + ',' + row[1] + ',' + row[2]] =
            global.deepslate.jsonToNbt(Object.assign({ id: row[3] }, row[4] || {}));
        } catch (e) { /* 单条坏了不影响其它 */ }
      }
      this.blockEntityNbt = Object.keys(map).length ? map : null;
      const extra = bannerPatternTextures(rows);
      if (extra.length) {
        const have = new Set(this.extraTextures || []);
        for (const ref of extra) have.add(ref);
        this.extraTextures = [...have];
      }
    }

    /** 把「方块名 → 渲染语义」推给全局（特判渲染器补丁要用），并整理成按下标的数组。 */
    _syncSemantics(flags) {
      const byName = {};
      const list = new Array(this.palette.length);
      for (let i = 0; i < this.palette.length; i++) {
        const s = this.palette[i];
        const p = parseState(s);
        const f = flags[s] || {};
        const sem = Object.assign({
          layer: 0, tint: null, liquid: null, special: null, has_elements: true,
          ao_occluder: false, render: 'normal', opaque: false,
          self_culling: false, semi_transparent: false, waterlogged: false,
        }, f, { name: i === 0 ? 'minecraft:air' : idStr(p.name) });
        if (f.waterlogged === undefined) {
          sem.waterlogged = String((p.props || {}).waterlogged || '').toLowerCase() === 'true';
        }
        list[i] = sem;
        byName[sem.name] = sem;
      }
      this.semantics = list;
      ACTIVE.semanticsByName = byName;
      this.semanticsByName = byName;
      this._syncEnvArrays();
    }

    /** env 是在 _attachRenderer 时**按引用**拿到 blockStates / semantics 的，
     *  所以换数组（`= list.map(...)`）之后必须把引用同步过去。
     *
     * 不同步会怎样：mesher 继续查旧数组，新下标越界 → `stateAt()` 退回方块 0（air）
     * → 整个方块不画；表现为「墙/栅栏在方块更新（连接状态变了）之后变透明」。
     */
    _syncEnvArrays() {
      const r = this.renderer;
      if (!r || !r.env) return;
      r.env.blockStates = this.blockStates;
      r.env.semantics = this.semantics;
    }

    /** 按当前 ``_flags`` / 调色板重建「下标 → 语义」表（新状态或新 flags 之后调用）。 */
    refreshSemantics() {
      this._syncSemantics(this._flags || {});
    }

    /** 调色板/语义变了（出现了新状态）→ 同步 env 数组 + 清按下标缓存的烘焙 + 重画。
     *
     * 两件事缺一不可：
     *  1. ``env.*`` 的数组引用要跟上（见 :meth:`_syncEnvArrays`）；
     *  2. ``mesher.baked`` 是**按调色板下标**缓存的，下标换了定义必须清掉。
     * 只发生在调色板/语义变化时（新增连接状态、工具引入新方块），平时改一格走增量路径。
     */
    refreshEnvArrays() {
      this._syncEnvArrays();
      const r = this.renderer;
      if (!r) return;
      if (r.mesher && r.mesher.baked) r.mesher.baked.clear();
      if (r.rebuildAll) { r.rebuildAll(); r.pump(24); }
      this.frame();
    }

    /** 调色板状态表（deepslate.BlockState[]）整体替换：同步 env + 重烘焙。 */
    setBlockStates(list) {
      this.blockStates = list;
      this.refreshEnvArrays();
    }

    /** 调色板里「没有方块定义」的状态（缺了就会让整块 chunk 网格崩掉）。 */
    resourceGaps(palette) {
      const defs = (this.resources && this.resources.defs) || {};
      return (palette || this.palette).filter((s) => !defs[idStr(parseState(s).name)]);
    }

    /** 调色板里出现了没加载过的方块类型 → 重建资源与渲染器。
     *
     * deepslate 的 mesher 对每个方块查 ``resources.getBlockDefinition``，
     * 查不到就抛异常跳过 —— 结果是整块 chunk 变成空网格（画面全黑）。
     * 所以工具往调色板里加新方块后，必须先补资源再重画。
     */
    async ensureStates(palette) {
      const list = (palette || this.palette).map((s) => {
        const p = parseState(s);
        return { name: p.name, props: p.props, raw: s };
      });
      if (!list.length || !this.resources) return false;
      if (!this.resourceGaps(palette).length) return false;
      if (this.opts.onProgress) this.opts.onProgress('新方块资源');
      const loaded = await loadResources(this.version, list,
                                         { colors: this._colors || {},
                                           extraTextures: this.extraTextures || [] });
      this.resources = loaded.resources;
      this.renderMode = loaded.mode;
      this.reloadFlags(this._flags || {});
      this._syncSemantics(this._flags || {});
      await this._attachRenderer();
      this._dirtyChunks.clear();
      this._resize();
      this.frame();
      return true;
    }

    reloadFlags(flags) {
      // flags come per state; map back to block name
      this._flags = flags || this._flags || {};
      for (const [state, f] of Object.entries(flags || {})) {
        const name = idStr(parseState(state).name);
        if (this.resources) this.resources.flags[name] = f;
      }
    }

    // ------------------------------------------------------------ updates
    setPalette(palette) { this.palette = palette.slice(); }
    setPorts(ports) { this.ports = ports || []; this.frame(); }

    /** 待填接口的预览（``[x0,y0,z0,x1,y1,z1]`` 或 ``{box, shape, face}``；圆形画圆环）。 */
    setPortPreview(preview) {
      const b = Array.isArray(preview) ? { box: preview, shape: 'rect' }
        : (preview && preview.box
          ? { box: preview.box, shape: preview.shape || 'rect', face: preview.face }
          : null);
      const a = this.portPreview;
      const same = (!a && !b) || (a && b && a.shape === b.shape && a.face === b.face
        && a.box.join() === b.box.join());
      this.portPreview = b ? { box: b.box.slice(), shape: b.shape, face: b.face } : null;
      if (!same) this.frame();
    }
    _drawPortPreview(view) {
      this._drawOnePort(view, this.portPreview, [0.25, 1.0, 0.85]);
    }

    /** 接口 → 面内**外接矩形**（圆形存的是圆心 + 直径，先化成盒子）。
     *
     * 与 ``mccore.module_lib.port_bbox`` 同一套换算（引擎就是按外接矩形
     * 匹配的，圆只是画出来/给 AI 看的几何意图）。
     */
    _portBoxOf(port) {
      const [sx, sy, sz] = this.size;
      const o = port.origin || [0, 0], s = port.size || [1, 1];
      let c1 = o[0], c2 = o[1], s1 = Math.max(1, s[0]), s2 = Math.max(1, s[1]);
      if ((port.shape || 'rect') === 'circle') {
        const d = Math.max(1, Math.min(s1, s2));
        const off = Math.floor((d - 1) / 2);
        c1 -= off; c2 -= off; s1 = d; s2 = d;
      }
      const face = port.face;
      let x0, y0, z0, x1, y1, z1;
      if (face === 'west' || face === 'east') {
        const x = face === 'west' ? 0 : sx - 1;
        x0 = x; x1 = x; y0 = c1; y1 = c1 + s1 - 1; z0 = c2; z1 = c2 + s2 - 1;
      } else if (face === 'north' || face === 'south') {
        const z = face === 'north' ? 0 : sz - 1;
        z0 = z; z1 = z; y0 = c1; y1 = c1 + s1 - 1; x0 = c2; x1 = c2 + s2 - 1;
      } else {
        const y = face === 'down' ? 0 : sy - 1;
        y0 = y; y1 = y; x0 = c1; x1 = c1 + s1 - 1; z0 = c2; z1 = c2 + s2 - 1;
      }
      return [x0, y0, z0, x1, y1, z1];
    }

    /** 画一个接口：矩形 = 逐格线框（原样），圆形 = 面内的一个圆环 + 淡外接框。 */
    _drawOnePort(view, spec, rgb) {
      if (!spec || !spec.box) return;
      const box = spec.box;
      const face = spec.face || this._faceOfBox(box);
      if ((spec.shape || 'rect') !== 'circle') {
        this._drawLines(view, new Float32Array(
          this._boxSegments(box, rgb, 0.008)));
        return;
      }
      // 圆环画在**边界层的中间平面**：半径取直径的一半（刚好盖住那几格）
      const mid = [(box[0] + box[3] + 1) / 2, (box[1] + box[4] + 1) / 2,
                   (box[2] + box[5] + 1) / 2];
      const d = Math.max(1, Math.max(box[3] - box[0], box[4] - box[1],
                                      box[5] - box[2])) + 1;
      const R = d / 2;
      const N = 40;
      const seg = [];
      for (let i = 0; i < N; i++) {
        const a0 = (i / N) * Math.PI * 2, a1 = ((i + 1) / N) * Math.PI * 2;
        const p0 = this._ringPoint(mid, face, R, a0);
        const p1 = this._ringPoint(mid, face, R, a1);
        seg.push(p0[0], p0[1], p0[2], rgb[0], rgb[1], rgb[2],
                 p1[0], p1[1], p1[2], rgb[0], rgb[1], rgb[2]);
      }
      // 淡一档的外接矩形：让人一眼看出圆是哪几格
      const dim = [rgb[0] * 0.55 + 0.1, rgb[1] * 0.55 + 0.1, rgb[2] * 0.55 + 0.1];
      seg.push.apply(seg, this._boxSegments(box, dim, 0.004));
      this._drawLines(view, new Float32Array(seg));
    }

    /** 面平面内的圆环取点（竖直面 → 用 Y / 水平轴；水平面 → X / Z）。 */
    _ringPoint(c, face, R, ang) {
      const cs = Math.cos(ang) * R, sn = Math.sin(ang) * R;
      if (face === 'west' || face === 'east') return [c[0], c[1] + cs, c[2] + sn];
      if (face === 'north' || face === 'south') return [c[0] + cs, c[1] + sn, c[2]];
      return [c[0] + cs, c[1], c[2] + sn];
    }

    /** bbox 落在哪个面上（只有一层厚，取那个退化轴）。 */
    _faceOfBox(b) {
      if (b[0] === b[3]) return b[0] === 0 ? 'west' : 'east';
      if (b[2] === b[5]) return b[2] === 0 ? 'north' : 'south';
      return b[1] === 0 ? 'down' : 'up';
    }

    setLayerRange(y0, y1, only) {
      this.layer = { y0, y1, only: !!only };
      if (!this.renderer) return;
      if (this.rendererMode === 'deepslate') {
        this.renderer.updateStructureBuffers();
      } else {
        if (this.renderer.env) this.renderer.env.setLayerRange(y0, y1, !!only);
        this.renderer.rebuildAll();
        this.renderer.pump(24);
      }
      this.frame();
    }

    setVoxel(x, y, z, idx) {
      const [sx, sy, sz] = this.size;
      if (x < 0 || y < 0 || z < 0 || x >= sx || y >= sy || z >= sz) return;
      this.voxels[(y * sz + z) * sx + x] = idx;
      this.invalidateLod();                    // 编辑了 → 粗网格作废
      // 往空块里放东西 → 掩码补位（不清位：保守方向只是多重扫一次空块）
      if (idx && this.renderer && this.renderer.markChunkUsed) {
        this.renderer.markChunkUsed(x >> 4, y >> 4, z >> 4);
      }
      this._markDirty(x, y, z);
    }

    patchRegion(bbox, b64) {
      const [x0, y0, z0, x1, y1, z1] = bbox;
      const [sx, sy, sz] = this.size;
      const data = b64ToU16(b64);
      let k = 0;
      for (let y = y0; y <= y1; y++) {
        for (let z = z0; z <= z1; z++) {
          for (let x = x0; x <= x1; x++) {
            if (x >= 0 && y >= 0 && z >= 0 && x < sx && y < sy && z < sz) {
              this.voxels[(y * sz + z) * sx + x] = data[k];
            }
            k++;
          }
        }
      }
      // 受影响的是「写入区 + 外扩 1 格」覆盖到的块：邻块只有贴着我们改动的边界面
      // 才需要重建，没必要每块再套一层 3×3×3 邻域（旧写法小改动也要重建 27 块）。
      for (let cx = Math.max(0, Math.floor((x0 - 1) / 16));
           cx <= Math.floor((x1 + 1) / 16); cx++) {
        for (let cy = Math.max(0, Math.floor((y0 - 1) / 16));
             cy <= Math.floor((y1 + 1) / 16); cy++) {
          for (let cz = Math.max(0, Math.floor((z0 - 1) / 16));
               cz <= Math.floor((z1 + 1) / 16); cz++) {
            this._dirtyChunks.add(`${cx},${cy},${cz}`);
            // 补丁区可能把空块填上 → 掩码补位
            if (this.renderer && this.renderer.markChunkUsed) {
              this.renderer.markChunkUsed(cx, cy, cz);
            }
          }
        }
      }
      this.invalidateLod();                    // 区域补丁 → 粗网格作废
      this._scheduleRebuild();
    }

    _markChunkNeighborhood(cx, cy, cz, radius) {
      // 面剔除会看邻接方块：改动本块时必须重建相邻 chunk，否则会缺面
      const r = radius === undefined ? 1 : radius;
      for (let dx = -r; dx <= r; dx++) {
        for (let dy = -r; dy <= r; dy++) {
          for (let dz = -r; dz <= r; dz++) {
            this._dirtyChunks.add(`${cx + dx},${cy + dy},${cz + dz}`);
          }
        }
      }
    }

    /** 涂**一格**：只标脏真正受影响的块。
     *
     * 旧写法是无条件 3×3×3 的 27 块（大结构实测一次涂抹重建 18 块 / 21–40ms，
     * 涂抹时明显拖手）。面剔除确实会读邻块，但**只有编辑格压在块边界（局部 0 / 15）上时**
     * 邻块的网格才会过期；块内编辑只重建本块 → 一次涂抹 1 块（大结构 ~1–2ms）。
     */
    _markVoxelDirty(x, y, z) {
      const cx = x >> 4, cy = y >> 4, cz = z >> 4;
      this._dirtyChunks.add(`${cx},${cy},${cz}`);
      const bx = x & 15, by = y & 15, bz = z & 15;
      const sx = bx === 0 ? -1 : (bx === 15 ? 1 : 0);
      const sy = by === 0 ? -1 : (by === 15 ? 1 : 0);
      const sz = bz === 0 ? -1 : (bz === 15 ? 1 : 0);
      if (!sx && !sy && !sz) return;               // 块内部：邻块不受影响
      for (let dx = Math.min(0, sx); dx <= Math.max(0, sx); dx++) {
        for (let dy = Math.min(0, sy); dy <= Math.max(0, sy); dy++) {
          for (let dz = Math.min(0, sz); dz <= Math.max(0, sz); dz++) {
            if (dx || dy || dz) this._dirtyChunks.add(`${cx + dx},${cy + dy},${cz + dz}`);
          }
        }
      }
    }

    _markDirty(x, y, z) {
      this._markVoxelDirty(x, y, z);
      this._scheduleRebuild();
    }

    _scheduleRebuild() {
      if (this._rebuildTimer) return;
      this._rebuildTimer = setTimeout(() => {
        this._rebuildTimer = null;
        if (!this.renderer) return;
        const chunks = [...this._dirtyChunks].map((k) => k.split(',').map(Number));
        this._dirtyChunks.clear();
        if (this.rendererMode === 'deepslate') {
          this.renderer.updateStructureBuffers(chunks.length ? chunks : undefined);
        } else {
          // 真正的增量：只扫脏块的 16³ 格子（不再 O(全结构)）
          this.renderer.markDirty(chunks.length ? chunks : null);
          this.renderer.pump(16);
        }
        this.frame();
      }, 60);
    }

    // ------------------------------------------------------------ LOD（超远距离粗网格）
    /** 拉远到一定程度就用**粗网格**画：每个 lodK³ 组压成一格。
     *
     * 只对**超大结构**（>lodMinCells 格）启用，所以中小建筑的行为与以前一模一样。
     * 整个场景一次性切换（不做逐块分区）—— 避免"块边界空洞/接缝"这类最容易出 bug 的地方；
     * 用两个阈值做滞回（enter > exit），拉近立刻回全精度。
     * **编辑一律作废 LOD**（invalidateLod）：能编辑的距离本来就在全精度范围内，
     * 这样就不需要维护"编辑后粗网格同步"那套复杂逻辑。
     */
    _lodShouldBeOn() {
      if (this.lodEnabled === false || !this.renderer || !this.voxels) return this._lodActive === true;
      const [sx, sy, sz] = this.size;
      if (sx * sy * sz < (this.lodMinCells === undefined ? 2e6 : this.lodMinCells)) return false;
      // 注意：_lodDirty（编辑过）**不能**在这里拦 —— 那会让 LOD 被永久关掉；
      // 重建交给 _ensureLod()（它建完会把 _lodDirty 清掉）。
      const maxDim = Math.max(sx, sy, sz, 1);
      const enter = maxDim * (this.lodEnter === undefined ? 1.7 : this.lodEnter);
      const exit = maxDim * (this.lodExit === undefined ? 1.25 : this.lodExit);
      if (this._lodActive) {
        if (this.cam.dist < exit) { this._lodActive = false; return false; }
        return true;
      }
      if (this.cam.dist > enter) { this._lodActive = true; return true; }
      return false;
    }

    /** 编辑过 → 丢掉 LOD 网格（下次拉远时重建），并立刻回全精度。 */
    invalidateLod() {
      if (!this._lodRenderer && !this._lodActive && !this._lodDirty) return;
      this._lodRenderer = null;
      this._lodDirty = true;
      this._lodActive = false;
    }

    /** 当前该用哪个渲染器画（LOD 命中且建好 → 粗的；否则全精度）。 */
    _activeRenderer() {
      if (!this._lodShouldBeOn()) return this.renderer;
      const r = this._ensureLod();
      return r || this.renderer;
    }

    /** 建粗网格渲染器（按 lodK 下采样：实心优先、其次靠上 —— 屋顶/天线不会被内部空气吃掉）。 */
    _ensureLod() {
      if (this._lodRenderer && !this._lodDirty) return this._lodRenderer;
      const R3 = global.McRender3D;
      if (!R3 || !R3.StructureEnv) return null;
      const k = this.lodK || 4;
      const [sx, sy, sz] = this.size;
      const cxs = Math.ceil(sx / k), cys = Math.ceil(sy / k), czs = Math.ceil(sz / k);
      const vox = new Uint16Array(cxs * cys * czs);
      const v = this.voxels;
      const perf = global.performance || Date;
      const t0 = perf.now();
      for (let cy = 0; cy < cys; cy++) {
        const y1 = Math.min(sy, (cy + 1) * k);
        for (let cz = 0; cz < czs; cz++) {
          const z1 = Math.min(sz, (cz + 1) * k);
          for (let cx = 0; cx < cxs; cx++) {
            const x1 = Math.min(sx, (cx + 1) * k);
            let best = 0, bestScore = -1;
            for (let y = cy * k; y < y1; y++) {
              for (let z = cz * k; z < z1; z++) {
                const row = (y * sz + z) * sx;
                for (let x = cx * k; x < x1; x++) {
                  const idx = v[row + x];
                  if (!idx) continue;
                  const sem = this.semantics[idx];
                  const score = (sem && sem.ao_occluder ? 2 : 1) + (y - cy * k) * 0.01;
                  if (score > bestScore) { bestScore = score; best = idx; }
                }
              }
            }
            vox[(cy * czs + cz) * cxs + cx] = best;
          }
        }
      }
      const r = new R3.Renderer3D(this.gl);
      r.setResources(this.resources);
      r.coordScale = k;                       // 粗网格坐标 × k = 世界坐标
      r.setEnv(new R3.StructureEnv({
        size: [cxs, cys, czs], voxels: vox,
        blockStates: this.blockStates, semantics: this.semantics,
        nbt: this.blockEntityNbt || null,
      }));
      r.setViewport(0, 0, this.canvas.width, this.canvas.height);
      r.setGrid([cxs, cys, czs]);
      r.prioritizeDirty([cxs / 2, cys / 2, czs / 2]);
      r.rebuildAll();
      r.pump(60);                             // 粗网格很小，一次建完绝大部分
      this._lodMs = Math.round(perf.now() - t0);
      this._lodQuads = r.countQuads ? r.countQuads() : -1;
      this._lodRenderer = r;
      this._lodDirty = false;
      return r;
    }

    frame() {
      if (this._raf) return;
      this._raf = requestAnimationFrame(() => {
        this._raf = null;
        // 时间分片把剩下的脏块建完（一帧最多 12ms）
        if (this.renderer && this.rendererMode !== 'deepslate'
            && this.renderer.dirty && this.renderer.dirty.size) {
          const more = !this.renderer.pump(24);
          if (more) this.frame();
          else this._meshSettled();      // 全部建完：记下总耗时（load→网格齐）
        }
        // LOD 粗网格也可能没建完（编辑后重建 / 首次拉远）：同样时间分片继续
        const lr = this._lodRenderer;
        if (lr && lr.dirty && lr.dirty.size) {
          if (!lr.pump(12)) this.frame();
          if (lr.countQuads) this._lodQuads = lr.countQuads();
        }
        this.draw();
      });
    }

    // ------------------------------------------------------------ camera
    _viewMatrix() {
      const v = mat4.create();
      const c = this.cam;
      mat4.translate(v, v, [0, 0, -c.dist]);
      mat4.rotateX(v, v, c.pitch);
      mat4.rotateY(v, v, c.yaw);
      mat4.translate(v, v, [-c.target[0], -c.target[1], -c.target[2]]);
      return v;
    }

    _projMatrix() {
      const aspect = (this.canvas.clientWidth || 1) / (this.canvas.clientHeight || 1);
      const p = mat4.create();
      mat4.perspective(p, (70 * Math.PI) / 180, aspect, 0.1, 2000);
      return p;
    }

    fit(zoom) {
      const [sx, sy, sz] = this.size;
      const maxDim = Math.max(sx, sy, sz, 1);
      this.cam.target = [sx / 2, sy * 0.42, sz / 2];
      this.cam.dist = Math.max(3, maxDim * (zoom === undefined ? 0.95 : zoom) + 3);
      this.frame();
    }

    /** 切换视口背景：dark / black / white / transparent（透明时露出底色/棋盘格）。 */
    setBackground(mode) {
      const MODES = {
        dark: [0.055, 0.06, 0.07, 1],
        black: [0, 0, 0, 1],
        white: [1, 1, 1, 1],
        transparent: [0, 0, 0, 0],
      };
      this.bg = MODES[mode] ? mode : 'dark';
      this._bgColor = MODES[this.bg];
      const c = this.canvas;
      if (c) {
        c.style.background = this.bg === 'transparent' ? 'transparent'
          : `rgba(${Math.round(this._bgColor[0] * 255)},` +
            `${Math.round(this._bgColor[1] * 255)},` +
            `${Math.round(this._bgColor[2] * 255)},1)`;
        const wrap = c.parentElement;
        if (wrap) wrap.classList.toggle('bg-checker', this.bg === 'transparent');
      }
      this.draw();
    }

    draw() {
      const gl = this.gl;
      if (!gl) return;
      gl.viewport(0, 0, this.canvas.width, this.canvas.height);
      gl.clearColor(this._bgColor[0], this._bgColor[1], this._bgColor[2],
                    this._bgColor[3]);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      if (!this.renderer) return;
      const view = this._viewMatrix();
      const R = this._activeRenderer();       // 拉得很远时是粗网格（LOD）
      this.rActive = R;
      R.drawStructure(view);
      R.drawGrid(view);
      this._updateAxisLabels(view);
      if (this.highlight) R.drawOutline(view, this.highlight);
      if (this.moduleBoxes && this.moduleBoxes.length) this._drawBoxes(view);
      if (this.frameBox) this._drawFrame(view);
      if (this.portPreview) this._drawPortPreview(view);
      if (this.region) this._drawRegion(view);
      if (this.gizmo) this._drawGizmo(view);
      if (this.overlay) this._drawOverlay(view);
      if (this.cursor) this._drawCursor(view);
      if (this.ports.length) this._drawPorts(view);
    }

    /** 坐标轴文字：把 3D 箭头尖端投影到屏幕，定位 index.html 里那三个 <span>。
     *
     * 用 HTML 而不是在 canvas 里画字：不用加载字体、不受 DPR 呼吸影响、
     * 永远清晰（而且三个元素复用，不每帧重建 DOM）。
     */
    _updateAxisLabels(view) {
      const src = this.rActive || this.renderer;
      const tips = src && src._axisTips;
      let els = this._axisLabels;
      if (!els) {
        const host = this.canvas.parentElement;
        if (!host) return;
        const list = host.querySelectorAll ? host.querySelectorAll('.axis-labels .al') : [];
        if (!list.length) return;
        els = this._axisLabels = { x: list[0], y: list[1], z: list[2] };
      }
      if (!tips || !this.showAxisLabels) {
        for (const k of ['x', 'y', 'z']) if (els[k]) els[k].classList.remove('on');
        return;
      }
      const pv = mat4.multiply(mat4.create(), this._projMatrix(), view);
      const W = this.canvas.clientWidth || 1;
      const H = this.canvas.clientHeight || 1;
      const keys = ['x', 'y', 'z'];
      for (let i = 0; i < 3; i++) {
        const el = els[keys[i]];
        if (!el) continue;
        const p = tips[i];
        const w = pv[3] * p[0] + pv[7] * p[1] + pv[11] * p[2] + pv[15];
        if (!(w > 0.0001)) { el.classList.remove('on'); continue; }
        const cx = (pv[0] * p[0] + pv[4] * p[1] + pv[8] * p[2] + pv[12]) / w;
        const cy = (pv[1] * p[0] + pv[5] * p[1] + pv[9] * p[2] + pv[13]) / w;
        el.style.transform = 'translate(' +
          Math.round((cx * 0.5 + 0.5) * W - 5) + 'px,' +
          Math.round((0.5 - cy * 0.5) * H - 7) + 'px)';
        el.classList.add('on');
      }
    }

    _drawPorts(view) {
      // ModuleSpec ports -> 矩形逐格线框 / 圆形面内圆环（形状只是几何意图，
      // 引擎匹配仍按外接矩形 + 类型）
      for (const p of this.ports) {
        const box = this._portBoxOf(p);
        if ((p.shape || 'rect') === 'circle') {
          this._drawOnePort(view, { box, shape: 'circle', face: p.face },
                            [0.25, 1.0, 0.85]);
          continue;
        }
        for (let y = box[1]; y <= box[4]; y++)
          for (let z = box[2]; z <= box[5]; z++)
            for (let x = box[0]; x <= box[3]; x++) this.renderer.drawOutline(view, [x, y, z]);
      }
    }

    _resize() {
      const dpr = Math.min(2, global.devicePixelRatio || 1);
      const w = Math.max(1, Math.floor(this.canvas.clientWidth * dpr));
      const h = Math.max(1, Math.floor(this.canvas.clientHeight * dpr));
      if (this.canvas.width !== w || this.canvas.height !== h) {
        this.canvas.width = w;
        this.canvas.height = h;
        if (this.renderer) {
          this.renderer.setViewport(0, 0, w, h);
        }
      }
      this.frame();
    }

    observeResize() {
      if (this._resizeObserver || !global.ResizeObserver) return;
      this._resizeObserver = new ResizeObserver(() => this._resize());
      this._resizeObserver.observe(this.canvas.parentElement || this.canvas);
    }

    // ------------------------------------------------------------ picking
    /** 屏幕正中的世界方向（单位向量）——「跟随视角」定朝向用。 */
    viewDir() {
      const pv = mat4.multiply(mat4.create(), this._projMatrix(), this._viewMatrix());
      const inv = mat4.invert(mat4.create(), pv);
      if (!inv) return [0, 0, 1];
      const near = vec3.transformMat4(vec3.create(), [0, 0, -1], inv);
      const far = vec3.transformMat4(vec3.create(), [0, 0, 1], inv);
      const d = vec3.normalize(vec3.create(), vec3.subtract(vec3.create(), far, near));
      return [d[0], d[1], d[2]];
    }

    /** 空画布（或看空的区域）用：射线进入画布的第一个格子。
     *
     * ``pick()`` 需要打到实心块；空画布没有任何方块，没有它就没法放第一块。
     * 返回 ``{cell, place, normal}``，形状与 ``pick()`` 一致，拿不到返回 null。
     */
    firstInBox(ev) {
      if (!(this.size[0] > 0)) return null;
      const r = this.ray(ev);
      if (!r) return null;
      const { origin: o, dir: d } = r;
      const hi = this.size;
      // 与包围盒求交：**必须**判定「射线到底有没有穿过画布」。
      // 旧写法只取各轴的 tmin 再 clamp，没有 tEnter > tExit 的拒绝条件 ——
      // 于是完全错过画布的射线（鼠标在建筑之外的背景上）也会被夹出一个边界格：
      // 绿框长在建筑上，点下去在边界格放一块（通常被邻居完全包住 → 六个面全被剔除，
      // 看不出任何变化），而那格已经被占用 → 再想放也放不进去。
      let tEnter = 0, tExit = Infinity, axis = -1, fromLow = true;
      for (let a = 0; a < 3; a++) {
        if (Math.abs(d[a]) < 1e-12) {
          if (o[a] < 0 || o[a] > hi[a]) return null;
          continue;
        }
        const t1 = (0 - o[a]) / d[a], t2 = (hi[a] - o[a]) / d[a];
        const tmin = Math.min(t1, t2), tmax = Math.max(t1, t2);
        if (tmin > tEnter) { tEnter = tmin; axis = a; fromLow = t1 < t2; }
        if (tmax < tExit) tExit = tmax;
        if (tEnter > tExit) return null;             // 射线错过画布：没有落点
      }
      if (tExit < 0) return null;                     // 画布整个在射线背后
      // 入口点微推：必须与 raycast 用同一个 1e-6 —— 用 1e-4 的话，
      // 入口点在**另一个轴**上离格界不足 1e-4 时会被推过界，落点差一格。
      const t = tEnter + 1e-6;
      const cell = [
        Math.max(0, Math.min(hi[0] - 1, Math.floor(o[0] + d[0] * t))),
        Math.max(0, Math.min(hi[1] - 1, Math.floor(o[1] + d[1] * t))),
        Math.max(0, Math.min(hi[2] - 1, Math.floor(o[2] + d[2] * t))),
      ];
      const normal = [0, 0, 0];
      if (axis >= 0) normal[axis] = fromLow ? -1 : 1;
      return { cell, place: cell.slice(), normal, empty: true };
    }

    ray(ev) {
      const rect = this.canvas.getBoundingClientRect();
      const nx = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
      const ny = 1 - ((ev.clientY - rect.top) / rect.height) * 2;
      const pv = mat4.multiply(mat4.create(), this._projMatrix(), this._viewMatrix());
      const inv = mat4.invert(mat4.create(), pv);
      if (!inv) return null;
      const near = vec3.transformMat4(vec3.create(), [nx, ny, -1], inv);
      const far = vec3.transformMat4(vec3.create(), [nx, ny, 1], inv);
      const dir = vec3.normalize(vec3.create(), vec3.subtract(vec3.create(), far, near));
      return { origin: near, dir };
    }

    /** 纯体素射线步进（Amanatides & Woo DDA）——不碰 DOM，便于单测。
     *
     * o / d：世界坐标（单位=格）的射线起点与**单位**方向向量。
     * 返回 {cell, place, normal, stateIndex}：
     *   cell   —— 命中的第一个实心格；
     *   place  —— 放置位 = 射线经过的最后一个空格（紧贴命中面的那一格）；
     *             若首格即命中则取 cell + normal（盒内起点时 normal 为零向量，place = cell）；
     *   normal —— 命中面的法向（指向射线来向）；
     * 返回 null 表示没打到任何实心格。
     */
    raycast(o, d) {
      const [sx, sy, sz] = this.size;
      if (!(sx > 0 && sy > 0 && sz > 0)) return null;
      const hi = [sx, sy, sz];
      // 1) 与包围盒 [0,sx]×[0,sy]×[0,sz] 求交：进入参数 tEnter / 离开参数 tExit
      let tEnter = 0, tExit = Infinity;
      const normal = [0, 0, 0];
      for (let a = 0; a < 3; a++) {
        if (Math.abs(d[a]) < 1e-12) {
          if (o[a] < 0 || o[a] > hi[a]) return null;   // 与该轴平行且在盒外
          continue;
        }
        let t1 = -o[a] / d[a];                         // 交低面
        let t2 = (hi[a] - o[a]) / d[a];                // 交高面
        let sign = -1;                                 // 从低面进入 → 法向指 −轴
        if (t1 > t2) { const t = t1; t1 = t2; t2 = t; sign = 1; }
        if (t1 > tEnter) {                             // 最晚的那个面才是真正的进入面
          tEnter = t1;
          normal[0] = normal[1] = normal[2] = 0;
          normal[a] = sign;
        }
        if (t2 < tExit) tExit = t2;
        if (tEnter > tExit) return null;
      }
      if (tExit < 0) return null;                      // 整个盒子在射线背后
      if (tEnter < 0) {                                // 起点已在盒内：没有「进入面」
        tEnter = 0;
        normal[0] = normal[1] = normal[2] = 0;
      }
      // 2) 起点所在格：沿射线微推 1e-6 格，避免正好压格界时 floor() 落到盒外
      //    （从低面进入 → 该轴落在 0；从高面进入 → 落在 size-1）
      const fine = 1e-6;
      const cell = [0, 0, 0];
      for (let a = 0; a < 3; a++) {
        const p = o[a] + d[a] * (tEnter + fine);
        cell[a] = Math.min(hi[a] - 1, Math.max(0, Math.floor(p)));
      }
      // 3) 步进参数：tMax[a] = 走到该轴下一格界的绝对 t，tDelta[a] = 跨一整格的距离
      const step = [0, 0, 0];
      const tMax = [Infinity, Infinity, Infinity];
      const tDelta = [Infinity, Infinity, Infinity];
      for (let a = 0; a < 3; a++) {
        if (d[a] > 1e-12) step[a] = 1; else if (d[a] < -1e-12) step[a] = -1; else continue;
        const p = o[a] + d[a] * tEnter;
        const bound = step[a] > 0 ? cell[a] + 1 : cell[a];
        tMax[a] = tEnter + (bound - p) / d[a];
        tDelta[a] = Math.abs(1 / d[a]);
      }
      // 4) 逐格走：命中实心格即停；place = 上一个空格（= 紧贴命中面的放置位）
      let place = null;
      const guard = sx + sy + sz + 3;                  // 盒内最多穿 sx+sy+sz 格
      for (let n = 0; n < guard; n++) {
        const [ix, iy, iz] = cell;
        if (ix >= 0 && iy >= 0 && iz >= 0 && ix < sx && iy < sy && iz < sz) {
          const idx = this.voxels[(iy * sz + iz) * sx + ix];
          if (idx !== 0) {
            return {
              cell: [ix, iy, iz],
              place: place ? place.slice()
                : [ix + normal[0], iy + normal[1], iz + normal[2]],
              normal: normal.slice(),
              stateIndex: idx,
            };
          }
          place = [ix, iy, iz];                        // 空格：下一格命中时放这里
        }
        let axis = 0;                                  // 走到最近的那个格界
        if (tMax[1] < tMax[axis]) axis = 1;
        if (tMax[2] < tMax[axis]) axis = 2;
        if (!(tMax[axis] <= tExit)) break;             // 再走就出盒了
        cell[axis] += step[axis];
        tMax[axis] += tDelta[axis];
        normal[0] = normal[1] = normal[2] = 0;         // 法向 = 刚穿过的那一面
        normal[axis] = -step[axis];
      }
      return null;
    }

    /** 鼠标事件 → 体素：DDA 体素射线拾取。
     * 返回 {cell, place, normal, stateIndex} 或 null（没打到方块）。 */
    pick(ev) {
      const r = this.ray(ev);
      if (!r) return null;
      return this.raycast(r.origin, r.dir);
    }


    // ------------------------------------------------------------ assembly overlays
    setModuleBoxes(boxes) {
      // [{bbox:[x0,y0,z0,x1,y1,z1], selected:bool, pid, mode:'move'|'copy'|null}]
      // 每个模块另画一个**中心小立方体**（手柄）：一堆模块叠在一起时，
      // 点模块身体选哪个全靠猜，点手柄是确定的（需求：模块手柄）。
      this.moduleBoxes = boxes || [];
      this.frame();
    }
    /** 画布框（保存时按它裁剪）：bbox 为 null 时不画。框 = 数据范围时也不用画。 */
    setFrameBox(bbox) {
      const a = this.frameBox, b = bbox;
      const same = (!a && !b) || (a && b && a.join() === b.join());
      this.frameBox = b ? b.slice() : null;
      if (!same) this.frame();
    }
    _drawFrame(view) {
      // 画布框（保存时按它裁剪）：比框选**浅一档**的灰蓝 —— 两者叠在一起时分得清。
      const b = this.frameBox;
      const rgb = [0.72, 0.80, 0.90];
      this._drawLines(view, new Float32Array(this._boxSegments(b, rgb, 0.006)));
      const solid = [];
      this._pushBoxFaces(solid, b, [rgb[0], rgb[1], rgb[2], 0.035]);
      this._drawSolid(view, new Float32Array(solid));
    }
    setGizmo(g) {
      // {center:[x,y,z], size:[dx,dy,dz], rot, rotx, rotz,
      //  arrowsOnly?:bool, color?:[r,g,b]}
      // arrowsOnly = 只要三根平移箭头、不画旋转圆环（选区移动/复制用：
      // 体素区域旋转还没有实现，画出来就是骗人）。
      this.gizmo = g || null;
      this.frame();
    }
    /** 编辑准星：{cell:[x,y,z], mode:'place'|'erase'|'replace'|'pick'|'grow'|'blocked'}
     *  grow = 放到画布外（会自动扩容）；blocked = 该方向没有格子（画布原点固定在 (0,0,0)）。 */
    setCursor(c) {
      this.cursor = c && c.cell ? c : null;
      this.frame();
    }
    setHover(part) {
      const key = part ? part.kind + ':' + part.axis : '';
      const cur = this._hover ? this._hover.kind + ':' + this._hover.axis : '';
      if (key !== cur) { this._hover = part || null; this.frame(); }
    }
    /** gizmo 尺寸随镜头缩放：屏幕上看起来恒定大小（同 Axiom）。 */
    _gizmoScale() {
      return Math.max(2.4, this.cam.dist * 0.17);
    }
    _axisColor(axis) {
      if (axis === 'x') return [0.93, 0.29, 0.26];
      if (axis === 'y') return [0.34, 0.83, 0.40];
      return [0.30, 0.55, 0.98];
    }
    _axisBasis(axis) {
      if (axis === 'x') return { d: [1, 0, 0], u: [0, 1, 0], v: [0, 0, 1] };
      if (axis === 'y') return { d: [0, 1, 0], u: [0, 0, 1], v: [1, 0, 0] };
      return { d: [0, 0, 1], u: [1, 0, 0], v: [0, 1, 0] };
    }
    // ------------------------------------------------------------ GL programs
    _lineProgram() {
      if (this._lineProg) return this._lineProg;
      const gl = this.gl;
      const vs = 'attribute vec3 aPos;attribute vec3 aColor;uniform mat4 uView;uniform mat4 uProj;varying vec3 vColor;void main(){gl_Position=uProj*uView*vec4(aPos,1.0);vColor=aColor;}';
      const fs = 'precision mediump float;varying vec3 vColor;void main(){gl_FragColor=vec4(vColor,1.0);}';
      const mk = (type, src) => {
        const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
        if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
        return s;
      };
      const prog = gl.createProgram();
      gl.attachShader(prog, mk(gl.VERTEX_SHADER, vs));
      gl.attachShader(prog, mk(gl.FRAGMENT_SHADER, fs));
      gl.linkProgram(prog);
      const buf = gl.createBuffer();
      this._lineProg = { prog, buf, aPos: gl.getAttribLocation(prog, 'aPos'),
                         aColor: gl.getAttribLocation(prog, 'aColor'),
                         uView: gl.getUniformLocation(prog, 'uView'),
                         uProj: gl.getUniformLocation(prog, 'uProj') };
      return this._lineProg;
    }
    _solidProgram() {
      if (this._solidProg) return this._solidProg;
      const gl = this.gl;
      const vs = 'attribute vec3 aPos;attribute vec4 aColor;uniform mat4 uView;uniform mat4 uProj;varying vec4 vColor;void main(){gl_Position=uProj*uView*vec4(aPos,1.0);vColor=aColor;}';
      const fs = 'precision mediump float;varying vec4 vColor;void main(){gl_FragColor=vColor;}';
      const mk = (type, src) => {
        const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
        if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
        return s;
      };
      const prog = gl.createProgram();
      gl.attachShader(prog, mk(gl.VERTEX_SHADER, vs));
      gl.attachShader(prog, mk(gl.FRAGMENT_SHADER, fs));
      gl.linkProgram(prog);
      const buf = gl.createBuffer();
      this._solidProg = { prog, buf, aPos: gl.getAttribLocation(prog, 'aPos'),
                          aColor: gl.getAttribLocation(prog, 'aColor'),
                          uView: gl.getUniformLocation(prog, 'uView'),
                          uProj: gl.getUniformLocation(prog, 'uProj') };
      return this._solidProg;
    }
    _drawLines(view, segs) {
      // segs: [x0,y0,z0,x1,y1,z1,r,g,b] × n
      if (!segs.length) return;
      const gl = this.gl;
      const P = this._lineProgram();
      gl.useProgram(P.prog);
      gl.uniformMatrix4fv(P.uView, false, view);
      gl.uniformMatrix4fv(P.uProj, false, this._projMatrix());
      gl.bindBuffer(gl.ARRAY_BUFFER, P.buf);
      gl.bufferData(gl.ARRAY_BUFFER, segs, gl.DYNAMIC_DRAW);
      gl.enableVertexAttribArray(P.aPos);
      gl.vertexAttribPointer(P.aPos, 3, gl.FLOAT, false, 24, 0);
      gl.enableVertexAttribArray(P.aColor);
      gl.vertexAttribPointer(P.aColor, 3, gl.FLOAT, false, 24, 12);
      gl.depthMask(false);
      gl.disable(gl.DEPTH_TEST);          // gizmo/线框始终画在最上层
      gl.drawArrays(gl.LINES, 0, segs.length / 6);
      gl.enable(gl.DEPTH_TEST);
      gl.depthMask(true);
    }
    _drawSolid(view, verts) {
      // verts: [x,y,z,r,g,b,a] × n (TRIANGLES)
      if (!verts.length) return;
      const gl = this.gl;
      const P = this._solidProgram();
      gl.useProgram(P.prog);
      gl.uniformMatrix4fv(P.uView, false, view);
      gl.uniformMatrix4fv(P.uProj, false, this._projMatrix());
      gl.bindBuffer(gl.ARRAY_BUFFER, P.buf);
      gl.bufferData(gl.ARRAY_BUFFER, verts, gl.DYNAMIC_DRAW);
      gl.enableVertexAttribArray(P.aPos);
      gl.vertexAttribPointer(P.aPos, 3, gl.FLOAT, false, 28, 0);
      gl.enableVertexAttribArray(P.aColor);
      gl.vertexAttribPointer(P.aColor, 4, gl.FLOAT, false, 28, 12);
      const cull = gl.isEnabled(gl.CULL_FACE);
      gl.disable(gl.CULL_FACE);
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
      gl.depthMask(false);
      gl.disable(gl.DEPTH_TEST);          // 半透明选框同样置顶
      gl.drawArrays(gl.TRIANGLES, 0, verts.length / 7);
      gl.enable(gl.DEPTH_TEST);
      gl.depthMask(true);
      gl.disable(gl.BLEND);
      if (cull) gl.enable(gl.CULL_FACE);
    }
    // ------------------------------------------------------------ geometry builders
    /** **格索引包框** → 两端世界坐标（右下角 +1：格子 [x,y,z] 占 [x, x+1]）。 */
    _cellCorners(box, inflate) {
      const [x0, y0, z0, x1, y1, z1] = box;
      const d = inflate === undefined ? 0.02 : inflate;
      return [[x0 - d, y0 - d, z0 - d], [x1 + 1 + d, y1 + 1 + d, z1 + 1 + d]];
    }
    /** **世界尺寸盒**（中心 + 半边长）→ 两端世界坐标。
     *
     * 手柄（模块中心小方块 / 选区中心小方块）用这个：它已经算好了世界尺寸，
     * 不能再走 ``_cellCorners``（那里右下 +1）——旧写法把 ``[c-r, c+r]``
     * 当格索引喂进 ``_pushBoxFaces``，被撑成 ``2r+1`` 格（0.84 画成 1.86），
     * 所以「中间那个方块」看着比一格还大、还和拾取框对不上。
     */
    _worldCorners(center, half) {
      const c = center, h = half;
      return [[c[0] - h, c[1] - h, c[2] - h], [c[0] + h, c[1] + h, c[2] + h]];
    }
    _boxSegments(box, rgb, inflate) {
      const [a, b] = this._cellCorners(box, inflate);
      return this._segmentsBetween(a, b, rgb);
    }
    _segmentsBetween(a, b, rgb) {
      const c = [];
      const xs = [a[0], b[0]], ys = [a[1], b[1]], zs = [a[2], b[2]];
      const E = [[0,0,0,1,0,0],[0,0,0,0,1,0],[0,0,0,0,0,1],
                 [1,0,0,1,1,0],[1,0,0,1,0,1],[0,1,0,1,1,0],
                 [0,1,0,0,1,1],[1,1,0,1,1,1],[0,0,1,1,0,1],
                 [0,0,1,0,1,1],[1,0,1,1,1,1],[0,1,1,1,1,1]];
      for (const e of E) {
        // 每顶点 [x, y, z, r, g, b] 交错排列
        c.push(xs[e[0]], ys[e[1]], zs[e[2]], rgb[0], rgb[1], rgb[2],
               xs[e[3]], ys[e[4]], zs[e[5]], rgb[0], rgb[1], rgb[2]);
      }
      return c;
    }
    _pushTri(out, a, b, c, rgba) {
      out.push(a[0], a[1], a[2], rgba[0], rgba[1], rgba[2], rgba[3]);
      out.push(b[0], b[1], b[2], rgba[0], rgba[1], rgba[2], rgba[3]);
      out.push(c[0], c[1], c[2], rgba[0], rgba[1], rgba[2], rgba[3]);
    }
    _pushQuad(out, a, b, c, d, rgba) {
      this._pushTri(out, a, b, c, rgba);
      this._pushTri(out, a, c, d, rgba);
    }
    /** 半透明包围盒六面（Axiom 式选框）。 */
    _pushBoxFaces(out, box, rgba) {
      const [a, b] = this._cellCorners(box, 0.01);
      this._pushFacesBetween(out, a, b, rgba);
    }
    /** 世界尺寸盒（中心 + 半边长）的六面——与 :meth:`_pushBoxFaces` 同一张面表。 */
    _pushWorldBoxFaces(out, center, half, rgba) {
      const [a, b] = this._worldCorners(center, half);
      this._pushFacesBetween(out, a, b, rgba);
    }
    _pushFacesBetween(out, a, b, rgba) {
      const V = (x, y, z) => [x ? b[0] : a[0], y ? b[1] : a[1], z ? b[2] : a[2]];
      const f = [[0,0,0,0,1,0,1,1,0,1,0,0],   // z-
                 [0,0,1,1,0,1,1,1,1,0,1,1],   // z+
                 [0,0,0,1,0,0,1,0,1,0,0,1],   // y-
                 [0,1,0,0,1,1,1,1,1,1,1,0],   // y+
                 [0,0,0,0,0,1,0,1,1,0,1,0],   // x-
                 [1,0,0,1,1,0,1,1,1,1,0,1]];  // x+
      for (const q of f) {
        this._pushQuad(out, V(q[0], q[1], q[2]), V(q[3], q[4], q[5]),
                       V(q[6], q[7], q[8]), V(q[9], q[10], q[11]), rgba);
      }
    }
    /** 实心箭头：方柱杆 + 四棱锥箭头（Axiom 风）。
     *
     * ``colorOverride`` 为 ``[r,g,b]`` 时用它（选区移动/复制、模块手柄需要区分颜色），
     * 否则用标准轴色（X 红 / Y 绿 / Z 蓝）。
     */
    _pushArrow(out, c, axis, len, S, bright, colorOverride) {
      const { d, u, v } = this._axisBasis(axis);
      const base = colorOverride || this._axisColor(axis);
      const k = bright ? 1.0 : 0.82;
      const rgb = [Math.min(1, base[0] * k + (bright ? 0.18 : 0)),
                   Math.min(1, base[1] * k + (bright ? 0.18 : 0)),
                   Math.min(1, base[2] * k + (bright ? 0.18 : 0)), 1];
      const P = (t, uu, vv) => [c[0] + d[0] * t + u[0] * uu + v[0] * vv,
                                c[1] + d[1] * t + u[1] * uu + v[1] * vv,
                                c[2] + d[2] * t + u[2] * uu + v[2] * vv];
      const shaftEnd = len * 0.72;
      const r0 = S * 0.030, hR = S * 0.100;
      const quad = (a, b, cc, dd) => this._pushQuad(out, a, b, cc, dd, rgb);
      quad(P(0, r0, r0), P(shaftEnd, r0, r0), P(shaftEnd, -r0, r0), P(0, -r0, r0));
      quad(P(0, -r0, r0), P(shaftEnd, -r0, r0), P(shaftEnd, -r0, -r0), P(0, -r0, -r0));
      quad(P(0, -r0, -r0), P(shaftEnd, -r0, -r0), P(shaftEnd, r0, -r0), P(0, r0, -r0));
      quad(P(0, r0, -r0), P(shaftEnd, r0, -r0), P(shaftEnd, r0, r0), P(0, r0, r0));
      const apex = P(len, 0, 0);
      const b0 = P(shaftEnd, hR, hR), b1 = P(shaftEnd, -hR, hR);
      const b2 = P(shaftEnd, -hR, -hR), b3 = P(shaftEnd, hR, -hR);
      this._pushTri(out, b0, b1, apex, rgb);
      this._pushTri(out, b1, b2, apex, rgb);
      this._pushTri(out, b2, b3, apex, rgb);
      this._pushTri(out, b3, b0, apex, rgb);
      this._pushQuad(out, b0, b3, b2, b1, rgb);
    }
    _arcBasis(axis) {
      // 圆环平面基向量（与轴垂直）
      if (axis === 'x') return { u: [0, 0, 1], v: [0, 1, 0] };
      if (axis === 'y') return { u: [1, 0, 0], v: [0, 0, 1] };
      return { u: [1, 0, 0], v: [0, 1, 0] };
    }
    _arcSpan(axis) {
      // 圆弧从 30° 扫 300°，给箭头留缺口（Axiom 风）
      return [Math.PI / 6, Math.PI / 6 + Math.PI * 5 / 3];
    }
    // ------------------------------------------------------------ draw
    _drawCursor(view) {
      const c = this.cursor;
      if (!c) return;
      const [x, y, z] = c.cell;
      const rgb = c.mode === 'erase' ? [1, 0.32, 0.32]
        : c.mode === 'replace' ? [0.40, 0.70, 1.0]
          : c.mode === 'pick' ? [1, 0.84, 0.32]
            : c.mode === 'grow' ? [1, 0.62, 0.22]
              : c.mode === 'blocked' ? [0.80, 0.10, 0.35] : [0.42, 1.0, 0.55];
      const solid = [];
      this._pushBoxFaces(solid, [x, y, z, x, y, z], [rgb[0], rgb[1], rgb[2], 0.18]);
      this._drawSolid(view, new Float32Array(solid));
      this._drawLines(view, new Float32Array(this._boxSegments([x, y, z, x, y, z], rgb, 0.035)));
    }
    /** 集合层（下拉框里那个「选中」的模块）的脚框 + 中心手柄。 */
    _drawBoxes(view) {
      const segs = [];
      const solid = [];
      for (const b of this.moduleBoxes || []) {
        const rgb = b.selected ? [1, 0.62, 0.2] : [0.3, 0.7, 1.0];
        segs.push.apply(segs, this._boxSegments(b.bbox, rgb, b.selected ? 0.03 : 0.02));
        // 中心手柄：小立方体，**略小于一格**（选中时更大更亮）
        const c = this._boxCenter(b.bbox);
        const hc = b.handleColor || rgb;
        this._pushWorldBoxFaces(solid, c, this.moduleHandleHalf(!!b.selected),
                                [hc[0], hc[1], hc[2], b.selected ? 0.72 : 0.5]);
        segs.push.apply(segs, this._segmentsBetween(
          ...this._worldCorners(c, this.moduleHandleHalf(!!b.selected) + 0.01), hc));
      }
      if (solid.length) this._drawSolid(view, new Float32Array(solid));
      this._drawLines(view, new Float32Array(segs));
    }

    /** 模块中心手柄的半边长（格）：**上限略小于 1 格**，近了再小一点。
     *
     * 手柄是「点它才选中模块」的确定入口（模块叠在一起时点身体全靠猜），
     * 所以近了要小（不遮挡内容）、远了要够大（还点得中）——
     * 但永远不超过 0.42 格（≈ 一格的 84%）。
     */
    moduleHandleHalf(selected) {
      const cap = selected ? 0.42 : 0.34;
      return Math.max(0.12, Math.min(cap, this.cam.dist * 0.010));
    }

    _boxCenter(b) {
      return [(b[0] + b[3] + 1) / 2, (b[1] + b[4] + 1) / 2, (b[2] + b[5] + 1) / 2];
    }

    // ------------------------------------------------------------ 选区手柄（移动 / 复制）
    /** 框选后的选区手柄：
     *
     * ``{box, mode:'move'|'copy', armed:false, color:[r,g,b]}``——
     * ``armed=false`` 先在选区中心画一个**半透明小立方体**（这就是可点区域）；
     * 点了它才 ``armed=true``，再由 editor 把三根箭头 gizmo 装上去。
     * 这样「选一段再移」与「随手点一下就把东西拖走」不会混淆。
     */
    setRegionHandle(h) {
      this.region = h || null;
      this.frame();
    }

    _drawRegion(view) {
      const r = this.region;
      if (!r) return;
      const c = this._boxCenter(r.box);
      const s = this._regionHandleHalf();
      const col = r.color || [0.45, 0.9, 1.0];
      const solid = [];
      const lines = [];
      if (!r.armed) {
        this._pushWorldBoxFaces(solid, c, s, [col[0], col[1], col[2], 0.42]);
        lines.push.apply(lines, this._segmentsBetween(
          ...this._worldCorners(c, s + 0.01), col));
      }
      // 目标框（拖动时跟着走；未拖动就画在原位）
      const d = r.delta || [0, 0, 0];
      const t = [r.box[0] + d[0], r.box[1] + d[1], r.box[2] + d[2],
                 r.box[3] + d[0], r.box[4] + d[1], r.box[5] + d[2]];
      if (r.armed && (d[0] || d[1] || d[2])) {
        this._pushBoxFaces(solid, t, [col[0], col[1], col[2], 0.14]);
        lines.push.apply(lines, this._boxSegments(t, col, 0.02));
      }
      if (solid.length) this._drawSolid(view, new Float32Array(solid));
      if (lines.length) this._drawLines(view, new Float32Array(lines));
    }

    /** 点中选区中心的那个小立方体？返回 {hit:true, box, center} 或 null。 */
    pickRegionHandle(ev) {
      const r = this.region;
      if (!r || r.armed) return null;
      const ray = this.ray(ev);
      if (!ray) return null;
      const c = this._boxCenter(r.box);
      const s = this._regionHandleHalf();
      const t = this._rayAABB(ray, [c[0] - s, c[1] - s, c[2] - s,
                                    c[0] + s, c[1] + s, c[2] + s], 0.35);
      return t === null ? null : { hit: true, box: r.box.slice(), center: c, t };
    }

    /** 选区中心手柄的半边长（格）：画多大就拾多大（上限同样**略小于一格**）。 */
    _regionHandleHalf() {
      return Math.max(0.14, Math.min(0.42, this.cam.dist * 0.011));
    }

    /** 点中哪个模块的中心手柄？返回 ``{pid, center, t}``（取最近的一个）或 null。
     *
     * 这就是“一堆模块堆在一起时也能准确抓到指定那个”的关键：
     * 手柄在模块**中心**、尺寸固定、与模块体积无关。
     */
    pickModuleHandle(ev) {
      const ray = this.ray(ev);
      if (!ray) return null;
      let best = null;
      for (const b of this.moduleBoxes || []) {
        const c = this._boxCenter(b.bbox);
        // 拾取盒 = 画面上的手柄 × 1.6（远景下也点得中），不再是固定的 1.35 倍尺
        const s = this.moduleHandleHalf(!!b.selected) * 1.6 + 0.08;
        const t = this._rayAABB(ray, [c[0] - s, c[1] - s, c[2] - s,
                                      c[0] + s, c[1] + s, c[2] + s], 0);
        if (t === null) continue;
        if (!best || t < best.t) best = { pid: b.pid, center: c, t, bbox: b.bbox };
      }
      return best;
    }

    /** 射线 × AABB（slab 法）。返回进入参数 t（起点在盒内为 0），未命中 null。
     *
     * ``pad`` 给一个“宽容半径”，让小方块在远景下也点得中。
     */
    _rayAABB(ray, box, pad) {
      const o = ray.origin, d = ray.dir;
      const p = pad || 0;
      const lo = [box[0] - p, box[1] - p, box[2] - p];
      const hi = [box[3] + p, box[4] + p, box[5] + p];
      let t0 = -Infinity, t1 = Infinity;
      for (let a = 0; a < 3; a++) {
        if (Math.abs(d[a]) < 1e-9) {
          if (o[a] < lo[a] || o[a] > hi[a]) return null;
          continue;
        }
        let ta = (lo[a] - o[a]) / d[a];
        let tb = (hi[a] - o[a]) / d[a];
        if (ta > tb) { const q = ta; ta = tb; tb = q; }
        if (ta > t0) t0 = ta;
        if (tb < t1) t1 = tb;
        if (t0 > t1) return null;
      }
      if (t1 < 0) return null;
      return Math.max(0, t0);
    }
    /** 工具叠加层：框选盒 / 笔刷线框 / 路径控制点。
     *  overlay = {sel:[box], brush:{center,radius,shape}, points:[[x,y,z],…]} */
    setOverlay(o) {
      this.overlay = o || null;
      this.frame();
    }
    _ringSegments(c, radius, axis, rgb) {
      const { u, v } = this._arcBasis(axis);
      const segs = [];
      const N = 40;
      for (let i = 0; i < N; i++) {
        const t0 = (i / N) * Math.PI * 2;
        const t1 = ((i + 1) / N) * Math.PI * 2;
        const p0 = [c[0] + (u[0] * Math.cos(t0) + v[0] * Math.sin(t0)) * radius,
                    c[1] + (u[1] * Math.cos(t0) + v[1] * Math.sin(t0)) * radius,
                    c[2] + (u[2] * Math.cos(t0) + v[2] * Math.sin(t0)) * radius];
        const p1 = [c[0] + (u[0] * Math.cos(t1) + v[0] * Math.sin(t1)) * radius,
                    c[1] + (u[1] * Math.cos(t1) + v[1] * Math.sin(t1)) * radius,
                    c[2] + (u[2] * Math.cos(t1) + v[2] * Math.sin(t1)) * radius];
        segs.push(p0[0], p0[1], p0[2], rgb[0], rgb[1], rgb[2],
                  p1[0], p1[1], p1[2], rgb[0], rgb[1], rgb[2]);
      }
      return segs;
    }
    _drawOverlay(view) {
      const o = this.overlay;
      if (!o) return;
      const lines = [];
      const solid = [];
      if (o.sel) {
        // 框选：深蓝（与画布框的浅灰蓝拉开），面片也比画布框重
        this._pushBoxFaces(solid, o.sel, [0.09, 0.31, 0.72, 0.18]);
        lines.push.apply(lines, this._boxSegments(o.sel, [0.09, 0.31, 0.72], 0.03));
      }
      if (o.points && o.points.length) {
        for (const p of o.points) {
          lines.push.apply(lines, this._boxSegments(
            [p[0], p[1], p[2], p[0], p[1], p[2]], [1, 0.85, 0.25], 0.06));
        }
        for (let i = 1; i < o.points.length; i++) {
          const a = o.points[i - 1], b = o.points[i];
          lines.push(a[0], a[1], a[2], 1, 0.85, 0.25, b[0], b[1], b[2], 1, 0.85, 0.25);
        }
      }
      if (o.brush && o.brush.center) {
        const c = o.brush.center, r = Math.max(0.5, o.brush.radius || 1);
        const rgb = [0.55, 1.0, 0.75];
        const cc = [c[0] + 0.5, c[1] + 0.5, c[2] + 0.5];
        lines.push.apply(lines, this._ringSegments(cc, r, 'y', rgb));
        lines.push.apply(lines, this._ringSegments(cc, r, 'x', [0.35, 0.8, 0.65]));
        lines.push.apply(lines, this._ringSegments(cc, r, 'z', [0.35, 0.8, 0.65]));
      }
      if (solid.length) this._drawSolid(view, new Float32Array(solid));
      if (lines.length) this._drawLines(view, new Float32Array(lines));
    }
    _drawGizmo(view) {
      const g = this.gizmo;
      if (!g) return;
      const S = this._gizmoScale();
      const c = g.center;
      const L = S * 1.85;                       // 箭头长度（屏幕恒定）
      const R = S * 1.45;                       // 圆弧半径
      const col = g.color || null;              // 选区移动/复制 / 模块用不同颜色
      const solid = [];
      const lines = [];
      // 1) 半透明选框：g.box（选区）优先，否则当前选中的模块
      const box = g.box || ((this.moduleBoxes || []).find((b) => b.selected) || {}).bbox;
      if (box) {
        const bc = col || [0.62, 0.78, 1.0];
        this._pushBoxFaces(solid, box, [bc[0], bc[1], bc[2], 0.07]);
      }
      // 2) 三根实心箭头（含悬停高亮）
      const hover = this._hover || null;
      for (const axis of ['x', 'y', 'z']) {
        this._pushArrow(solid, c, axis, L, S,
                        !!(hover && hover.kind === 'move' && hover.axis === axis),
                        col);
      }
      // 3) 三道旋转圆弧——``arrowsOnly`` 时不画（选区移动/复制不支持旋转，
      //    画个拖不动的环比不画更让人困惑）
      if (!g.arrowsOnly) {
        for (const axis of ['x', 'y', 'z']) {
          const { u, v } = this._arcBasis(axis);
          const [a0, a1] = this._arcSpan(axis);
          const base = col || this._axisColor(axis);
          const hot = !!(hover && hover.kind === 'rot' && hover.axis === axis);
          const rgb = hot ? [1, 0.85, 0.35]
            : base.map((x) => Math.min(1, x * 0.75 + 0.22));
          const N = 56;
          for (let i = 0; i < N; i++) {
            const A = a0 + (a1 - a0) * (i / N), B = a0 + (a1 - a0) * ((i + 1) / N);
            const pa = [c[0] + u[0] * Math.cos(A) * R + v[0] * Math.sin(A) * R,
                        c[1] + u[1] * Math.cos(A) * R + v[1] * Math.sin(A) * R,
                        c[2] + u[2] * Math.cos(A) * R + v[2] * Math.sin(A) * R];
            const pb = [c[0] + u[0] * Math.cos(B) * R + v[0] * Math.sin(B) * R,
                        c[1] + u[1] * Math.cos(B) * R + v[1] * Math.sin(B) * R,
                        c[2] + u[2] * Math.cos(B) * R + v[2] * Math.sin(B) * R];
            lines.push(pa[0], pa[1], pa[2], rgb[0], rgb[1], rgb[2],
                       pb[0], pb[1], pb[2], rgb[0], rgb[1], rgb[2]);
          }
        }
      }
      this._drawSolid(view, new Float32Array(solid));
      this._drawLines(view, new Float32Array(lines));
    }
    // ------------------------------------------------------------ gizmo picking
    /** 射线（半直线）与轴线（过 center、方向 d）的最近点。

    返回 { s: 轴参数, dist: 两线距离, point: 轴上的最近点 }；
    射线方向与轴平行或最近点在射线背面时退化处理。
    */
    _rayAxis(ray, center, d) {
      const rd = ray.dir;
      const w0 = [ray.origin[0] - center[0], ray.origin[1] - center[1],
                  ray.origin[2] - center[2]];
      const dd = rd[0] * rd[0] + rd[1] * rd[1] + rd[2] * rd[2];
      const bb = rd[0] * d[0] + rd[1] * d[1] + rd[2] * d[2];
      const cc = d[0] * d[0] + d[1] * d[1] + d[2] * d[2];
      const de = rd[0] * w0[0] + rd[1] * w0[1] + rd[2] * w0[2];
      const ee = d[0] * w0[0] + d[1] * w0[1] + d[2] * w0[2];
      const denom = dd * cc - bb * bb;
      let s, t;
      if (Math.abs(denom) < 1e-9) {           // 平行：取原点在轴上的投影
        s = -ee / Math.max(1e-9, cc);
        t = 0;
      } else {
        t = (bb * ee - cc * de) / denom;      // 沿射线
        s = (dd * ee - bb * de) / denom;      // 沿轴
        if (t < 0) {                          // 最近点在射线背面
          t = 0;
          s = -ee / Math.max(1e-9, cc);
        }
      }
      const pAxis = [center[0] + d[0] * s, center[1] + d[1] * s,
                     center[2] + d[2] * s];
      const pRay = [ray.origin[0] + rd[0] * t, ray.origin[1] + rd[1] * t,
                    ray.origin[2] + rd[2] * t];
      return { s, t, point: pAxis,
               dist: Math.hypot(pRay[0] - pAxis[0], pRay[1] - pAxis[1],
                                pRay[2] - pAxis[2]) };
    }
    /** 指针在轴上的投影参数（拖轴用），失败返回 null。 */
    gizmoAxisParam(ev, axis, centerOverride) {
      if (!this.gizmo) return null;
      const ray = this.ray(ev);
      if (!ray) return null;
      const { d } = this._axisBasis(axis);
      const hit = this._rayAxis(ray, centerOverride || this.gizmo.center, d);
      return hit ? hit.s : null;
    }
    /** 射线与「以 center 为心、axis 为法向」的圆环求交；命中返回角度与半径。 */
    gizmoRingHit(ev, axis) {
      if (!this.gizmo) return null;
      const ray = this.ray(ev);
      if (!ray) return null;
      const g = this.gizmo;
      const S = this._gizmoScale();
      const R = S * 1.45;
      const { d, u, v } = this._axisBasis(axis);
      const denom = d[0] * ray.dir[0] + d[1] * ray.dir[1] + d[2] * ray.dir[2];
      if (Math.abs(denom) < 1e-6) return null;
      const t = ((g.center[0] - ray.origin[0]) * d[0] +
                 (g.center[1] - ray.origin[1]) * d[1] +
                 (g.center[2] - ray.origin[2]) * d[2]) / denom;
      if (t <= 0) return null;
      const p = [ray.origin[0] + ray.dir[0] * t, ray.origin[1] + ray.dir[1] * t,
                 ray.origin[2] + ray.dir[2] * t];
      const rel = [p[0] - g.center[0], p[1] - g.center[1], p[2] - g.center[2]];
      const radius = Math.hypot(rel[0], rel[1], rel[2]);
      if (Math.abs(radius - R) > S * 0.26) return null;
      const angle = Math.atan2(rel[0] * v[0] + rel[1] * v[1] + rel[2] * v[2],
                               rel[0] * u[0] + rel[1] * u[1] + rel[2] * u[2]);
      // 只响应实际画出来的弧段（箭头方向留了缺口，避免和轴拖抢）
      const [a0, a1] = this._arcSpan(axis);
      const span = a1 - a0;
      let arcT = (angle - a0) % (Math.PI * 2);
      if (arcT < 0) arcT += Math.PI * 2;
      if (arcT > span) return null;
      return { kind: 'rot', axis, point: p, angle, radius,
               facing: Math.abs(denom) };
    }
    /** 用于拖动旋转：返回当前指针在圆环平面上的角度（不需要命中）。 */
    gizmoAngle(ev, axis, centerOverride) {
      if (!this.gizmo) return null;
      const ray = this.ray(ev);
      if (!ray) return null;
      const g = { center: centerOverride || this.gizmo.center };
      const { d, u, v } = this._axisBasis(axis);
      const denom = d[0] * ray.dir[0] + d[1] * ray.dir[1] + d[2] * ray.dir[2];
      if (Math.abs(denom) < 1e-6) return null;
      const t = ((g.center[0] - ray.origin[0]) * d[0] +
                 (g.center[1] - ray.origin[1]) * d[1] +
                 (g.center[2] - ray.origin[2]) * d[2]) / denom;
      if (t <= 0) return null;
      const p = [ray.origin[0] + ray.dir[0] * t, ray.origin[1] + ray.dir[1] * t,
                 ray.origin[2] + ray.dir[2] * t];
      const rel = [p[0] - g.center[0], p[1] - g.center[1], p[2] - g.center[2]];
      return Math.atan2(rel[0] * v[0] + rel[1] * v[1] + rel[2] * v[2],
                        rel[0] * u[0] + rel[1] * u[1] + rel[2] * u[2]);
    }
    /** 命中测试：箭头（平移）优先，其次圆弧（旋转）。
     *  ``arrowsOnly`` 的 gizmo 不做圆环命中（它根本没画环）。 */
    pickGizmo(ev) {
      if (!this.gizmo) return null;
      const ray = this.ray(ev);
      if (!ray) return null;
      const g = this.gizmo;
      const S = this._gizmoScale();
      const L = S * 1.85;
      for (const axis of ['x', 'y', 'z']) {
        const { d } = this._axisBasis(axis);
        const hit = this._rayAxis(ray, g.center, d);
        if (hit && hit.s > -S * 0.3 && hit.s < L + S * 0.3 &&
            hit.dist < S * 0.26) {
          return { kind: 'move', axis, dir: d.slice(), center: g.center.slice(),
                   point: g.center.slice(), s: hit.s };
        }
      }
      if (g.arrowsOnly) return null;
      // 三个环都可能被同一束射线擦到：取最正对镜头的那个（Axiom 同款优先级）
      let best = null;
      for (const axis of ['x', 'y', 'z']) {
        const hit = this.gizmoRingHit(ev, axis);
        if (hit && (!best || hit.facing > best.facing)) best = hit;
      }
      return best;
    }

    // ------------------------------------------------------------ input
    _bindInput() {
      const c = this.canvas;
      c.addEventListener('contextmenu', (e) => e.preventDefault());
      let drag = null;
      c.addEventListener('pointerdown', (e) => {
        try { c.setPointerCapture(e.pointerId); } catch (err) { /* synthetic events */ }
        const edit = this.mode === 'edit' && this.hooks;
        if (e.button === 1 || (e.button === 2 && !edit)) {
          drag = { type: 'pan', button: e.button, x: e.clientX, y: e.clientY, moved: 0 };
        } else if (e.button === 0 && (!edit || e.shiftKey)) {
          drag = { type: 'rotate', x: e.clientX, y: e.clientY, moved: 0 };
        } else if (edit && (e.button === 0 || e.button === 2)) {
          this.hooks.down(e, e.button === 2);
        }
      });
      c.addEventListener('pointermove', (e) => {
        const edit = this.mode === 'edit' && this.hooks;
        if (!drag && edit && this.hooks.move) this.hooks.move(e);
        if (!drag) return;
        if (drag.type === 'rotate') {
          this.cam.yaw += (e.clientX - drag.x) / 120;
          this.cam.pitch = Math.max(-1.5, Math.min(1.5,
            this.cam.pitch + (e.clientY - drag.y) / 120));
          drag.moved += Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y);
          drag.x = e.clientX; drag.y = e.clientY;
          this.frame();
        } else if (drag.type === 'pan') {
          const dx = (e.clientX - drag.x) / c.clientHeight * this.cam.dist;
          const dy = (e.clientY - drag.y) / c.clientHeight * this.cam.dist;
          drag.moved = (drag.moved || 0)
            + Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y);
          const right = [Math.cos(this.cam.yaw), 0, -Math.sin(this.cam.yaw)];
          const up = [0, 1, 0];
          const t = this.cam.target;
          for (let i = 0; i < 3; i++) t[i] += -dx * right[i] + dy * up[i];
          drag.x = e.clientX; drag.y = e.clientY;
          this.frame();
        }
      });
      c.addEventListener('pointerup', (e) => {
        const edit = this.mode === 'edit' && this.hooks;
        // 中键**单击**（几乎没拖动）= 吸取方块；拖动过就是平移，不当点击
        if (drag && drag.type === 'pan' && drag.button === 1 && edit &&
            (drag.moved || 0) < 4 && this.hooks.pick) {
          this.hooks.pick(e);
        }
        if (drag && drag.type === 'rotate' && drag.moved < 4 && !edit) {
          const hit = this.pick(e);
          if (hit) { this.highlight = hit.cell; this.frame(); }
          if (this.opts.onPick) this.opts.onPick(hit);
        }
        if (edit && (e.button === 0 || e.button === 2)) this.hooks.up(e, e.button === 2);
        drag = null;
      });
      c.addEventListener('wheel', (e) => {
        e.preventDefault();
        this.cam.dist = Math.max(2, this.cam.dist * (1 + Math.sign(e.deltaY) * 0.12));
        this.frame();
      }, { passive: false });
    }
  }

  // 脚本加载顺序：index.html 先 vendor/deepslate.js 再 viewer3d.js
  patchSpecialRenderersOnce();

  global.McStudio3D = { VoxelViewer, parseState, idStr, loadResources, b64ToU16,
                        semantics: ACTIVE,
                        bannerPatternTextures,
                        setRenderer: (mode) => { ACTIVE.renderer = mode; } };
})(window);
