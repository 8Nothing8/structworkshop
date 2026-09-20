/* mcstudio 结构编辑器：3D 预览 + 2D 俯视图层编辑（放置/擦除/替换/吸管） */
/* global McStudio3D, App */
(function (global) {
  'use strict';

  // localStorage 键：随项目改名（mcforge → structworkshop）。旧键只读一次、读到就搬过来。
  const BG_KEY = 'structworkshop.background';
  const BG_KEY_LEGACY = 'mcforge.background';
  const RECENT_KEY = 'structworkshop.recentBlocks';
  const RECENT_KEY_LEGACY = 'mcforge.recentBlocks';

  const el = (tag, attrs, ...children) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === 'class') n.className = v;
      else if (k === 'html') n.innerHTML = v;
      else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
      else if (v !== null && v !== undefined) n.setAttribute(k, v);
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      n.append(c.nodeType ? c : document.createTextNode(String(c)));
    }
    return n;
  };
  const $ = (s) => document.querySelector(s);

  /** AXIOM 工具面板开关。
   *
   * ``false`` = 面板从界面上**下线**（先打磨编辑 / 选区 / 装配这些基础功能），
   * 但引擎（``packages/mctools`` / ``python -m mctools`` /
   * ``POST /api/structure/{sid}/tool``）与下面的 ax* 接线**全部保留**：
   * 把这里改回 true、再去掉 index.html 里 ``#ax-panel`` 的 ``hidden``，
   * 工具面板就原样回来。
   */
  const AX_PANEL_ENABLED = false;

  // 工具集：吸管已去掉（中键单击=吸取方块仍在，任何工具下都能用）；
  // 「移动 / 复制」都以**框选**为前提（选区中心会出现可点的小立方体）。
  const TOOL_LABEL = { place: '放置', erase: '擦除', replace: '替换',
                       move: '移动', copy: '复制', select: '框选' };
  /** 选区变换工具（共用同一套“选区手柄 + 三箭头”机制）。 */
  const REGION_TOOLS = new Set(['move', 'copy']);
  /** 「整幅投影」自包装实例的伪包名（见 `wrapAsInstance`）：不是资产包模块。 */
  const SELF_PACK = '@self';
  /** 选区变换的颜色：移动=青蓝，复制=黄绿（与界面提示同色，一眼分得清）。 */
  const REGION_COLOR = { move: [0.36, 0.82, 1.0], copy: [0.66, 0.96, 0.42] };

  const E = {
    viewer: null, st: null, voxels: null, palette: [], colors: {}, flags: {}, extraTextures: [],
    pendingPost: null, limits: { max_mb: 24, max_cells: 20000000, max_blocks: 2000000, warn_cells: 8000000 },
    tool: 'place', state: 'minecraft:stone', layer: 0, only: false,
    toolSeq: 0,               // 「用户换过工具」的单调序号（异步导入回来时用来判断该不该抢工具）
    update: true, autoFace: true,
    pending: [], painting: false, scope: 'layer',
    zoom: 6, pan: { x: 0, y: 0 }, blockList: [], currentBlock: null,
    placements: [], selectedPid: null,
    // 选区变换（移动/复制）状态：{box, mode, armed, delta, dragging}
    //   armed=false → 选区中心画半透明小立方体（可点）
    //   armed=true  → 装三根箭头，拖箭头平移
    region: null,
    gizmoDrag: null, drag2D: null, snapPort: true,
    interfaceTypes: [],       // 仓库里用过的接口类型（含自写的）——类型下拉的"已有"部分
  };

  /** 同步调色板（必要时刷新颜色表、blockStates 与新方块资源）。 */
  async function applyPaletteStates(states) {
    if (!states) return false;
    const changed = states.length !== E.palette.length ||
      states.some((x, i) => x !== E.palette[i]);
    E.palette = states.slice();
    if (E.viewer) E.viewer.setPalette(E.palette);   // 语义表要按新调色板长度算
    if (changed) await refreshPaletteInfo();
    if (!E.viewer) return changed;
    // 本地 idxOfLocal 可能先往调色板里塞了新状态，那时 blockStates 会短一截
    const stale = !E.viewer.blockStates ||
      E.viewer.blockStates.length !== E.palette.length;
    if (changed || stale) {
      E.viewer.setBlockStates(E.palette.map((s, i) => {
        const p = McStudio3D.parseState(s);
        return i === 0 ? new deepslate.BlockState('minecraft:air')
          : new deepslate.BlockState(
            p.name.includes(':') ? p.name : 'minecraft:' + p.name, p.props);
      }));
      // setBlockStates 会同步 env 的数组引用 + 清掉按下标缓存的烘焙：
      // 墙/栅栏这种「方块名已加载、只是连接状态变新」的方块少这一步就会整块不画。
    }
    // 画布上出现了没加载过的方块类型时必须补资源，否则 deepslate 的 mesher
    // 查不到方块定义会丢掉整块 chunk（表现为 3D 画面全黑）
    const loaded = await E.viewer.ensureStates(E.palette);
    return changed || loaded;
  }

  /** 把服务端回传的区域同步进本地镜像（2D 视图与后续本地编辑都要用）。 */
  function patchLocal(bbox, b64) {
    if (!bbox || !b64 || !E.voxels || !E.st) return;
    const [sx, sy, sz] = E.st.size;
    const [x0, y0, z0, x1, y1, z1] = bbox.map(Number);
    const data = McStudio3D.b64ToU16(b64);
    let k = 0;
    for (let y = y0; y <= y1; y++) {
      for (let z = z0; z <= z1; z++) {
        for (let x = x0; x <= x1; x++) {
          if (x >= 0 && y >= 0 && z >= 0 && x < sx && y < sy && z < sz) {
            E.voxels[(y * sz + z) * sx + x] = data[k];
          }
          k++;
        }
      }
    }
  }

  // 「方块更新」开关只对会改方块的笔刷工具显示（放置/擦除/替换）
  const WRITE_TOOLS = new Set(['place', 'erase', 'replace']);

  function updatePlaceRow() {
    const row = $('#place-update');
    if (row) row.classList.toggle('hidden', !WRITE_TOOLS.has(E.tool));
  }

  /** 对当前框选（未框选则整张画布）重算一次连接状态。 */
  async function recomputeStates() {
    if (!E.st) { App.toast('先打开一个结构', 'warn'); return; }
    try {
      const r = await App.post(`/api/structure/${E.st.sid}/ops`,
        { ops: [{ type: 'update', box: AX.sel || null }], update: false });
      await applyPaletteStates(r.palette);
      if (r.bbox && r.region) {
        E.viewer.patchRegion(r.bbox, r.region);
        patchLocal(r.bbox, r.region);
      }
      E.st.dirty = r.dirty !== false;
      E.st.can_undo = r.can_undo;
      E.st.can_redo = r.can_redo;
      refreshCounts();
      updateStatus();
      renderPalette();
      render2D();
      App.toast(r.updated
        ? `已重算连接状态：${r.updated.toLocaleString()} 格`
        : '连接状态已是最新（没有需要改的格子）', 'ok');
    } catch (e) { App.toast('重算失败: ' + e.message, 'err'); }
  }

  function switchTab(name) {
    document.querySelectorAll('.tab').forEach((t) => {
      t.classList.toggle('active', t.dataset.view === name);
    });
    document.querySelectorAll('.view').forEach((v) => {
      v.classList.toggle('active', v.id === 'view-' + name);
    });
    if (name === 'editor') onShow();
  }

  /** 视口背景：dark / black / white / transparent（localStorage 记忆，与模块库共用）。 */
  function viewBgMode() {
    const sel = $('#view-bg');
    if (sel && sel.value) return sel.value;
    try {
      const v = localStorage.getItem(BG_KEY);
      if (v !== null) return v;
      const old = localStorage.getItem(BG_KEY_LEGACY);   // 改名前的偏好：搬过来
      if (old !== null) { localStorage.setItem(BG_KEY, old); return old; }
    } catch (e) { /* 忽略 */ }
    return 'dark';
  }

  function setViewBackground(mode) {
    if (!mode) return;
    if (E.viewer) E.viewer.setBackground(mode);
    const sel = $('#view-bg');
    if (sel) sel.value = mode;
    try { localStorage.setItem(BG_KEY, mode); } catch (e) { /* 忽略 */ }
  }

  function ensureViewer() {
    if (E.viewer) return E.viewer;
    const canvas = $('#gl-canvas');
    E.viewer = new McStudio3D.VoxelViewer(canvas, { onPick: null });
    E.viewer.observeResize();
    E.viewer.mode = 'edit';
    E.viewer.setBackground(viewBgMode());
    E.viewer.hooks = {
      down: (ev, erase) => onPaint(ev, erase),
      pick: (ev) => onMiddlePick(ev),      // 中键**单击** = 吸取方块（拖动仍是平移）
      move: (ev) => {
        if (E.region && E.region.dragging) onRegionMove(ev);
        else if (E.gizmoDrag) onGizmoMove(ev);
        else { axPointerMove(ev); onHover(ev); }
      },
      up: () => {
        if (E.region && E.region.dragging) commitRegion();
        else if (E.gizmoDrag) onGizmoUp();
        else if (AX.stroking) axPointerUp();
        else flushOps();
      },
    };
    return E.viewer;
  }

  /** 中键单击 = 吸取方块（原版 MC 的 pick block）：把当前方块换成准星指到的那个（含状态）。
   *  与「吸管」工具的区别：不用先切工具，任何工具下都能用；拖动则仍然是平移视角。 */
  function onMiddlePick(ev) {
    if (!E.st || !E.viewer) return;
    const hit = E.viewer.pick(ev);
    if (!hit || !hit.stateIndex) {
      status('中键吸取：这里没有方块（换个格子试试）');
      return;
    }
    applyPick(hit);
    const st = String(E.palette[hit.stateIndex] || '').replace('minecraft:', '');
    status('中键吸取：' + st);
    App.toast('当前方块 → ' + st, 'ok', 2500);
  }

  function onShow() {
    if (E.viewer) E.viewer._resize();
    const sel = $('#view-bg');
    if (sel && !sel.value) sel.value = viewBgMode();
    // 不自动新建画布：什么都没打开时编辑器保持空，状态栏提示先去打开/新建。
    updateStatus();
  }

  // ---------------------------------------------------------------- loading
  async function openPath(path, opts) {
    opts = opts || {};
    switchTab('editor');
    status('打开 ' + path + ' …');
    const seq0 = E.toolSeq || 0;   // 打开是异步的：记下「用户换工具的序号」
    try {
      const st = await App.post('/api/structure/open', { path });
      const r = await fetch(`/api/structure/${st.sid}/voxels`);
      const buf = await r.arrayBuffer();
      await adopt(st, new Uint16Array(buf));
      // 打开就变成**可拖动的装配实例**（与模块库「在编辑器中打开」同一条路）：
      //   资产包模块 → 拿它的接口（能吸附）
      //   其它投影（builds/…、上传的文件、空画布）→ 整幅一个实例（没有接口）
      // 画笔/工具也能直接改它——压在实例上的格子会**就地**改在实例上，
      // 不用先「固化装配」（见 `_punch_module_edits`）。
      const wrap = await wrapAsInstance(st, { seq0 });
      if (opts.viewOnly) status('只读预览');
      App.toast(wrap.kind === 'module'
        ? `已打开模块 ${st.name}：画布 ${st.size.join('×')}，可拖动手柄移动`
        : wrap.kind === 'self'
          ? `已打开 ${st.name} (${st.size.join('×')})：整幅是可拖动实例（切「移动」拖手柄）`
          : `已打开 ${st.name} (${st.size.join('×')})`, 'ok');
    } catch (e) {
      status('打开失败: ' + e.message);
      App.toast(e.message, 'err');
    }
  }

  /** 把刚打开的整幅内容转成一个装配实例（能拖能转，保存时写回原文件）。
   *
   * 两种身份：
   *   * ``packs/<包>/modules/…`` → 真模块：向服务端要它 spec 里的接口；
   *   * **其它任何投影** → 整幅一个实例（伪包名 `SELF_PACK`，没有接口）。
   *     以前只有前者能拖，从左上角「打开结构…」/「上传打开」进来的普通投影只能是
   *     普通方块 —— 这里把它统一了：打开即实例。
   *
   * 参数/撤销都走服务端同一套路径（``POST …/modules/detach``），带
   * ``markDirty: false, record: false`` —— 打开动作不该把结构标成「未保存」，
   * 也不该在撤销栈里凭空多一步（打开前的状态本来就是「一盘散沙」，没东西可撤）。
   *
   * 返回 ``{kind: 'module' | 'self' | null}``。
   */
  async function wrapAsInstance(st, opts) {
    opts = opts || {};
    if (!E.st) return { kind: null };
    // 文件自带装配清单（.layout.json / Metadata）时已经还原成实例了，
    // 再包一层会把同一份体素叠两遍。
    if ((E.placements || []).length) return { kind: null };
    if (!E.st.blocks) return { kind: null };          // 空画布：没什么可包的
    const ref = moduleRefFromPath(st && st.path);
    const body = { markDirty: false, record: false };
    if (ref) {
      body.id = ref.id;
      body.pack = ref.pack;
      body.ports = E.ports || [];
    }
    try {
      // 「用户在这期间换过工具吗」用**换工具序号**判：`E.toolSeq` 由工具按钮点击
      // 递增。只看 `E.tool` 不够 —— 打开文件要好几跳异步，用户完全可能在这中间
      // 切到别的工具（实测：切了接口工具，导入回来又把工具抢回「移动」）。
      const seq0 = opts.seq0 == null ? (E.toolSeq || 0) : opts.seq0;
      const r = await App.post(`/api/structure/${E.st.sid}/modules/detach`, body);
      await applyModuleResult(r);
      const pid = (r.added || [])[0] || null;
      if ((E.toolSeq || 0) === seq0) {
        // 用户没动过工具 → 按「导入即移动」对待（手柄/箭头立刻出来）
        selectPlacement(pid, false);
        setTool('move');
      } else {
        // 用户已经切走了（比如切到接口/放置）→ 只选中，不抢工具
        selectPlacement(pid, false, { keepTool: true });
      }
      if (ref) {
        status(`${ref.id}：模块是可拖动的实例——拖中心手柄 / 三箭头移动；` +
          '画笔/工具也能直接改它（压在模块上的格子会就地改在模块上，跟着模块走）');
      } else {
        status(`${st.name}：整幅是可拖动实例（拖中心手柄 / 三箭头移动）；` +
          '画笔/工具当普通方块改；要变成普通方块就「固化装配」');
      }
      return { kind: ref ? 'module' : 'self' };
    } catch (e) {
      App.toast('转装配实例失败：' + e.message, 'err');
      return { kind: null };
    }
  }

  async function openUpload(file) {
    const maxMb = (E.limits && E.limits.max_mb) || 24;
    const mb = file.size / (1024 * 1024);
    if (mb > maxMb) {
      App.toast(`结构过大：${file.name} 约 ${mb.toFixed(1)} MB，超过上限 ` +
        `${maxMb} MB（想放宽去「设置 → 打开上限」；也可用环境变量 STRUCTWORKSHOP_MAX_STRUCTURE_MB）`, 'err', 9000);
      return;
    }
    if (mb > maxMb * 0.4) {
      App.toast(`注意：${file.name} 有 ${mb.toFixed(1)} MB，打开可能较慢`, 'warn');
    }
    const q = new URLSearchParams({ filename: file.name });
    const r = await fetch('/api/structure/open?' + q.toString(),
      { method: 'POST', body: file });
    const st = await r.json();
    if (!r.ok) throw new Error(st.error || r.statusText);
    const buf = await (await fetch(`/api/structure/${st.sid}/voxels`)).arrayBuffer();
    await adopt(st, new Uint16Array(buf));
    // 上传打开的文件同样当整幅实例处理（与「打开结构…」一致）
    await wrapAsInstance(st);
    App.toast(`已打开 ${st.name}`, 'ok');
  }

  async function adopt(st, voxels, opts) {
    const keepSel = (opts && opts.keepSelection) ? E.selectedPid : null;
    E.st = st;
    E.voxels = voxels;
    E.palette = st.palette.slice();
    if (!st.frame || st.frame.length !== 3) st.frame = st.size.slice();
    E.layer = Math.floor(st.size[1] / 2);
    E.pending = [];
    E.painting = false;
    E.placements = [];
    E.selectedPid = keepSel;
    const viewer = ensureViewer();
    // 2D 层编辑器：初始缩放自动适面板宽度（小模块不放大到 24px/格以上）
    E.zoom = Math.max(4, Math.min(24,
      Math.floor(300 / Math.max(1, st.size[0], st.size[2]))));
    await refreshPaletteInfo();
    // 方块实体（旗帜图案等）随结构一起给渲染器；没有就跳过这一跳
    let blockEntities = [];
    try {
      const be = await App.api(`/api/structure/${st.sid}/blockentities`);
      blockEntities = be.block_entities || [];
    } catch (err) { /* 老服务端没有这个接口 → 忽略 */ }
    await viewer.load({ size: st.size, palette: E.palette, version: st.version,
                        colors: E.colors, flags: E.flags,
                        extraTextures: E.extraTextures || [],
                        aoLevels: E.aoLevels || null,
                        blockEntities }, E.voxels);
    viewer.setPorts([]);
    viewer.fit(1.0);
    viewer.mode = 'edit';
    const slider = $('#layer-slider');
    slider.min = 0;
    slider.max = Math.max(0, st.size[1] - 1);
    slider.value = E.layer;
    updateStatus();
    renderPalette();
    render2D();
    refreshFiles();
    syncSizeSliders();
    updateFrameOverlay();
    if (st.limits) E.limits = st.limits;
    if (st.notice) {
      const blocks = st.notice.blocks
        ? `，${Number(st.notice.blocks).toLocaleString()} 个方块` : '';
      App.toast(`结构较大：${st.notice.size.join('×')} = ` +
        `${Number(st.notice.cells).toLocaleString()} 格${blocks}，操作可能变慢` +
        `（放宽阈值：「设置 → 打开上限」）`, 'warn', 8000);
    }
    await refreshModules();
    await refreshModulePanel();
    loadPicker().then(() => { renderFams(); renderPicker(); });
    renderFaceCtl();
    AX.last = null;
    AX.points = [];
    AX.centers = [];
    AX.pickA = null;
    AX.sel = null;
    E.region = null;              // 换结构 → 旧的选区手柄/箭头失效
    if (E.viewer) E.viewer.setRegionHandle(null);
    axRenderSel();
    axUpdateOverlay();
    if (keepSel && E.selectedPid) {
      renderInstances();
      updateModuleVisuals();
      const box = $('#mod-move');
      if (box) box.classList.remove('hidden');
    }
  }

  async function refreshPaletteInfo(states) {
    const list = states || E.palette;
    const pi = await App.post('/api/palette-info', {
      states: list, version: E.st && E.st.version,
      dataVersion: E.st && E.st.data_version,
    });
    const maps = App.colorMaps(pi.info);
    Object.assign(E.colors, maps.colors);
    Object.assign(E.flags, maps.flags);
    E.extraTextures = pi.textures || [];
    E.aoLevels = pi.aoLevels || null;
    if (E.viewer && E.viewer.resources) E.viewer.reloadFlags(E.flags);
    if (E.viewer) {
      E.viewer.extraTextures = E.extraTextures;
      // flags 变了 → 「下标 → 语义」表要重算（新状态的 layer/tint/liquid/special）
      E.viewer.refreshSemantics();
    }
  }

  function status(text) {
    $('#editor-status').textContent = text || '';
  }

  // ------------------------------------------------- 模块属性（打开资产包模块时）
  // 打开的是 packs/<包>/modules/…/<id>.schem 时，右侧给一块与模块库抽屉同款的
  // 「预览图 + 简介/属性」面板：描述 / 分类 / 标签 / 备注 可改，写完 PATCH 回 .module.json；
  // 「重渲染预览」走与模块库同一条渲染队列。
  let modRef = null;                       // 当前打开的模块（null = 不是资产包模块）
  let modPreviewBust = 0;                  // 重渲染后刷新 <img> 用
  const modPreviewCache = new Map();       // id → {entry, spec, blocks}

  /** ``packs/<包>/modules/<…>/<id>.schem`` → ``{pack, id}``；不是模块返回 null。 */
  function moduleRefFromPath(path) {
    if (!path || typeof path !== 'string') return null;
    const p = path.replace(/\\/g, '/');
    const m = p.match(/(?:^|\/)packs\/([^/]+)\/modules\/(?:.+\/)?([^/]+)\.schem$/) ||
              p.match(/^([^/]+)\/modules\/(?:.+\/)?([^/]+)\.schem$/);
    return m ? { pack: m[1], id: m[2] } : null;
  }

  function modPreviewUrl(entry) {
    const u = entry && entry.preview_urls && entry.preview_urls.thumb;
    if (!u) return null;
    return u + (u.includes('?') ? '&' : '?') + 't=' + modPreviewBust;
  }

  /** 模块属性窗口顶部那行「实例」信息（只有从实例点进来时才有）。 */
  function setInstInfo(selRef) {
    const box = $('#mod-meta-inst');
    if (!box) return;
    box.innerHTML = '';
    const inst = selRef && selRef.inst;
    if (!inst) return;
    const add = (k, v) => {
      if (v === undefined || v === null || v === '') return;
      box.append(el('span', {}, k + ' ', el('b', {}, String(v))));
    };
    add('实例', `@${inst.pos.join(',')}${rotLabel(inst) ? ' · ' + rotLabel(inst) : ''}`);
    add('尺寸', (inst.dims || []).join('×'));
    if (inst.edits) add('就地改动', inst.edits + ' 格');
  }

  /** 只切「模块属性 / 投影属性」两个窗口的可见性（+ 投影面板内容）。
   *
   *  与 `refreshModulePanel()` 的区别：**不重读模块数据、不动接口待选**。
   *  用在「切工具 → 收起模块叠加层」这种只关乎显示的地方 —— 那里如果走完整刷新，
   *  会把接口工具刚点的第一个角（`E.portPicks`）清掉。
   */
  function syncPanelVisibility() {
    const modWin = $('#mod-meta-win');
    const projWin = $('#proj-meta-win');
    if (!modWin) return;
    const selRef = selectedModuleRef();
    const target = selRef || moduleRefFromPath(E.st && E.st.path);
    const on = !!target;
    modWin.classList.toggle('hidden', !on);
    if (projWin) projWin.classList.toggle('hidden', on);
    if (on) setInstInfo(selRef);      // 取消选中后实例那一行要跟着消失
    if (!on) {
      // 没有模块可显：接口也清掉（上一个实例的接口不该留在 3D 里）
      E.ports = [];
      E.portSel = null;
      E.portPicks = [];
      if (E.viewer) { E.viewer.setPorts([]); E.viewer.setPortPreview(null); }
      renderProjPanel();
    }
    if (E.viewer) E.viewer._resize();
  }

  /** 选中的模块实例对应哪个资产包模块？没有选中／不是包模块 → null。
   *
   *  「整幅投影」这种自包装实例（`pack === SELF_PACK`）没有资产包模块可编辑，
   *  所以它不算「模块属性」目标 —— 归到投影属性那边显示。
   */
  function selectedModuleRef() {
    const sel = E.placements.find((p) => p.pid === E.selectedPid);
    if (!sel) return null;
    const id = String(sel.id || '').trim();
    const pack = String(sel.pack || '').trim();
    if (!id || !pack || pack === SELF_PACK) return null;
    return { pack, id, inst: sel };
  }

  /** 左栏「投影属性」：当前打开结构的自述（未选中模块时显示）。 */
  function renderProjPanel() {
    const kv = $('#proj-meta-kv');
    if (!kv) return;
    const name = $('#proj-meta-name');
    const hint = $('#proj-meta-hint');
    kv.innerHTML = '';
    const st = E.st;
    if (!st) {
      if (name) name.textContent = '';
      kv.append(el('span', {}, '没有打开的结构 — 用左上角「打开结构…」或「新建」'));
      return;
    }
    if (name) name.textContent = st.name || '';
    const add = (k, v) => {
      if (v === undefined || v === null || v === '') return;
      kv.append(el('span', {}, k + ' ', el('b', {}, String(v))));
    };
    const [sx, sy, sz] = st.size || [0, 0, 0];
    add('路径', st.path || '（空画布，保存时选路径）');
    add('格式', st.format || (st.path || '').split('.').pop());
    add('尺寸', [sx, sy, sz].join('×'));
    add('画布框', (st.frame || st.size || []).join('×'));
    add('方块', st.blocks);
    add('格数', sx * sy * sz);
    if (st.outside) add('框外', st.outside + ' 块（保存时裁掉）');
    add('调色板', (E.palette || []).length);
    add('模块实例', (E.placements || []).length);
    add('方块实体', st.block_entities);
    add('存档版本', st.version);
    add('改动', st.dirty ? '未保存' : '已保存');
    if (!hint) return;
    if (E.selectedPid) {
      const sel = E.placements.find((p) => p.pid === E.selectedPid);
      hint.innerHTML = '';
      hint.append(el('span', {}, '选中的实例：'),
                  el('b', {}, sel ? `${sel.id} @ ${sel.pos.join(',')}` : ''),
                  el('span', {}, ' —— 它不是资产包模块（整幅导入的投影），' +
                    '所以没有模块属性可改；它可以整体拖动/旋转，也能「固化装配」成普通方块。'));
    } else {
      hint.textContent = '选中模块（切「移动」或「复制」，点模块中心的' +
        '小方块）这里就换成那个模块的属性。';
    }
  }

  /** 根据**选中状态**显示/隐藏并填充左栏属性窗口。
   *
   * 左栏现在是「模块装配（常驻）+ 属性」：
   *   * 选中了模块实例 → **那个模块**的属性编辑器（写回它的 `.module.json`）；
   *   * 没选中 → 打开的文件本身是资产包模块时显示它的属性，
   *     否则显示**当前投影**的属性（路径/尺寸/方块数/画布框/实例数…）。
   * 两者互斥，永远有一个在显示 —— 不再整块消失。
   */
  async function refreshModulePanel() {
    const win = $('#mod-meta-win');
    const projWin = $('#proj-meta-win');
    if (!win) return null;
    const show = (on) => {
      win.classList.toggle('hidden', !on);
      if (projWin) projWin.classList.toggle('hidden', on);
      if (E.viewer) E.viewer._resize();        // 画布宽度变了，重算视口
    };
    const selRef = selectedModuleRef();
    modRef = selRef || moduleRefFromPath(E.st && E.st.path);
    if (!modRef) {
      // 没有模块可显：接口也清掉（上一个文件的接口不该留在 3D 里）
      E.ports = [];
      E.portSel = null;
      E.portPicks = [];
      if (E.viewer) { E.viewer.setPorts([]); E.viewer.setPortPreview(null); }
      show(false);
      renderProjPanel();
      return null;
    }
    const hint = $('#mod-meta-hint');
    show(true);
    $('#mod-meta-id').textContent = modRef.id + ' · ' + modRef.pack;
    setInstInfo(selRef);
    renderPortList();
    const cached = modPreviewCache.get(modRef.id);
    if (cached) renderModulePanel(cached);
    else if (hint) hint.textContent = '读取模块属性…';
    try {
      const d = await App.api('/api/modules/' + encodeURIComponent(modRef.id));
      modPreviewCache.set(modRef.id, d);
      E.interfaceTypes = (App.state && App.state.interfaceTypes) || [];
      portTypeSelect(E.portSel && E.portSel.type ? E.portSel.type : undefined);
    renderModulePanel(d);
      // 接口：存下来并画在 3D 里（与模块库 / 装配同一套约定）。
      // **用 spec 里的 ports**：索引里的 entry.ports 只有 id/type/face/size，
      // 少了 `origin`（还有 tags）——拿去装配/吸附会 KeyError 'origin'。
      E.ports = (((d.spec || {}).ports) || ((d.entry || {}).ports) || [])
        .map((p) => ({ ...p }));
      E.portSel = null;
      E.portPicks = [];
      if (E.viewer) {
        E.viewer.setPorts(E.ports);
        E.viewer.setPortPreview(null);
      }
      renderPortList();
      if (hint) hint.textContent = '「保存属性」写回 .module.json（packs/…/modules/…）';
      return d;
    } catch (e) {
      E.ports = [];
      renderPortList();
      if (hint) hint.textContent = '读取失败：' + e.message;
      return null;
    }
  }

  function renderModulePanel(d) {
    const entry = (d && d.entry) || {};
    const spec = (d && d.spec) || {};
    const img = $('#mod-meta-preview');
    const url = modPreviewUrl(entry);
    if (img) {
      if (url) { img.src = url; img.classList.remove('hidden'); }
      else { img.removeAttribute('src'); img.classList.add('hidden'); }
    }
    const kv = $('#mod-meta-kv');
    if (kv) {
      kv.innerHTML = '';
      const add = (k, v) => {
        if (v === undefined || v === null || v === '') return;
        kv.append(el('span', {}, k + ' ', el('b', {}, String(v))));
      };
      add('包', entry.pack);
      add('分类', entry.category);
      add('尺寸', (entry.size || []).join('×'));
      add('方块', entry.blocks);
      add('面', spec.axis);
    }
    const set = (id, v) => { const n = $(id); if (n) n.value = v; };
    set('#mod-meta-desc', spec.description || entry.description || '');
    set('#mod-meta-cat', spec.category || entry.category || '');
    set('#mod-meta-tags', App.propsText
      ? App.propsText(spec.tags || entry.tags || [])
      : (spec.tags || entry.tags || []).join(', '));
    set('#mod-meta-notes', spec.notes || '');
    const list = $('#mod-meta-cat-list');
    if (list) {                      // 分类候选：当前库里已用过的分类
      list.innerHTML = '';
      const cats = new Set((App.state && App.state.categories) || []);
      if (entry.category) cats.add(entry.category);
      for (const c of [...cats].sort()) list.append(el('option', { value: c }));
    }
    const ports = $('#mod-meta-ports');
    if (ports) {
      ports.innerHTML = '';                       // 接口改到下面的可编辑区（列表 + 表单）
    }
  }

  /** 保存面板里的属性 → PATCH /api/modules/<id>（写回 .module.json） */
  async function saveModuleMeta() {
    if (!modRef) return null;
    const body = {
      description: $('#mod-meta-desc') ? $('#mod-meta-desc').value : '',
      category: $('#mod-meta-cat') ? $('#mod-meta-cat').value.trim() : '',
      tags: ($('#mod-meta-tags') && App.parseProps)
        ? App.parseProps($('#mod-meta-tags').value)
        : ($('#mod-meta-tags')
          ? $('#mod-meta-tags').value.split(',').map((s) => s.trim()).filter(Boolean) : []),
      notes: $('#mod-meta-notes') ? $('#mod-meta-notes').value : '',
    };
    try {
      const r = await App.patch('/api/modules/' + encodeURIComponent(modRef.id), body);
      App.toast(`已保存模块属性：${modRef.id}`, 'ok');
      modPreviewCache.delete(modRef.id);
      await App.refreshAll();          // 模块库那边的描述/标签同步
      await refreshModulePanel();
      return r;
    } catch (e) {
      App.toast('保存模块属性失败: ' + e.message, 'err');
      return null;
    }
  }

  // ------------------------------------------------- 接口（ModuleSpec.ports）
  // 与 `mccore.assemble.port_anchor3d` 同一套约定：origin/size 都是**面内**坐标——
  //   竖直面 west/east：origin=[y, z], size=[高, 宽]（面内水平轴 = z）
  //   竖直面 north/south：origin=[y, x], size=[高, 宽]（面内水平轴 = x）
  //   水平面 up/down：origin=[x, z], size=[x 宽, z 宽]
  // 接口格子就是**边界那一层**的格子（west → x=0，up → y=sy-1…）。
  //: 面选単项：**每个面都带轴向指示**（西/东 = ±X，北/南 = ±Z，顶/底 = ±Y）——
  //: 「我要接在西面」和「法向朝 −X」是一回事，写清楚省得搞反。
  const PORT_FACES = [['west', '西面（−X）'], ['east', '东面（+X）'],
                      ['north', '北面（−Z）'], ['south', '南面（+Z）'],
                      ['up', '顶面（+Y）'], ['down', '底面（−Y）']];
  const PORT_TYPES = [
    ['passage', '通道（人走 / 空洞）'], ['door', '门'], ['window', '窗'], ['vent', '风口 / 风道'],
    ['stair_up', '楼梯上'], ['stair_down', '楼梯下'], ['shaft', '竖井 / 竖向通道'],
    ['fluid_in', '进液'], ['fluid_out', '排液'], ['item_in', '进料'], ['item_out', '出料'],
    ['redstone_in', '红石入'], ['redstone_out', '红石出'],
    ['power_in', '电力入'], ['power_out', '电力出'],
    ['anchor', '锚点（只对齐）'], ['interface', '通用接口（与任何类型都能接）'], ['light', '采光'],
  ];
  E.ports = [];                 // 当前模块的接口列表
  E.portSel = null;             // 正在编辑的接口（null = 新建）
  E.portPicks = [];             // 接口工具点过的格（1~2 个）

  /** 面的两个轴叫什么（竖直面：上=Y、水平=Z/X；水平面：X/Z）。 */
  function portAxes(face) {
    if (face === 'west' || face === 'east') return ['Y', 'Z'];
    if (face === 'north' || face === 'south') return ['Y', 'X'];
    return ['X', 'Z'];
  }
  /** 面名（列表/提示里用）：**带轴向**，如「西面 −X」。 */
  const PORT_FACE_CN = { west: '西面 −X', east: '东面 +X', north: '北面 −Z',
                         south: '南面 +Z', up: '顶面 +Y', down: '底面 −Y' };
  /** 该面的法向轴字母与符号（画指示 / 写提示用）。 */
  const PORT_FACE_DIR = { west: ['X', -1], east: ['X', 1], north: ['Z', -1],
                          south: ['Z', 1], up: ['Y', 1], down: ['Y', -1] };
  const PORT_TYPE_CN = Object.fromEntries(PORT_TYPES);

  /** 面→「法向 −X（西面）　边界：原点 X=0 那一层　面内：Y × Z」这类人话
   *  （表单里那行小字：把「面名」和「朝哪边」挂钩，省得搞反）。
   */
  function portFaceHint(face) {
    const [ax, sign] = PORT_FACE_DIR[face] || ['X', 1];
    const [a1, a2] = portAxes(face);
    const where = ax === 'Y'
      ? (sign > 0 ? '边界：画布顶面那一层' : '边界：画布底面那一层')
      : (sign > 0 ? `边界：${ax}=最大值那一层` : `边界：原点 ${ax}=0 那一层`);
    return `法向 ${sign > 0 ? '+' : '−'}${ax}（${PORT_FACE_CN[face] || face}）` +
      `　${where}　面内：${a1} × ${a2}`;
  }

  // ---- 接口类型：可搜索 + **可自写**（与模块库那些搜索栏同一套组件）
  let portTypeSS = null;
  /** 下拉选项 = 内置推荐类型 + 仓库里已用过的类型（自写的会自己长出来）。 */
  function portTypeOptions() {
    const seen = new Map();
    for (const [v, t] of PORT_TYPES) seen.set(v, t);
    for (const t of E.interfaceTypes || []) {
      if (!seen.has(t)) seen.set(t, '已用过');
    }
    for (const p of E.ports || []) {
      if (p.type && !seen.has(p.type)) seen.set(p.type, '本模块已用');
    }
    return [...seen].map(([value, label]) => ({ value, label }));
  }

  function ensurePortTypeSS() {
    if (portTypeSS) return portTypeSS;
    const host = $('#port-type');
    if (!host || !App.searchSelect) return null;
    portTypeSS = App.searchSelect({
      options: portTypeOptions(), value: 'passage', allowCustom: true,
      placeholder: '选已有类型，或直接写一个新的…',
      title: '类型可以自由填写；自写的类型会出现在下拉里，也能保存',
      onPick: () => { refreshPortPreview(); },
    });
    // 与模块库那些搜索栏同一套挂载方式（searchSelect 只造节点，不包安装）
    host.append(portTypeSS.node);
    return portTypeSS;
  }

  function portTypeSelect(v) {
    const ss = ensurePortTypeSS();
    if (!ss) return;
    ss.setOptions(portTypeOptions());
    ss.set(v || 'passage');
  }

  /** 没选（用户只敲了字还没回车）就取输入框里的原文——“自己写”也能直接用。
   *
   * 注意用 ``ss.typed()`` 而不是 ``ss.input.value``：``set('vent')`` 会把输入框
   * 显示成标签「风口 / 风道」，直接读 input.value 会把标签当成类型存下去。
   */
  function portTypeValue() {
    const ss = ensurePortTypeSS();
    if (!ss) return 'passage';
    const v = ss.typed() || ss.value() || 'passage';
    return v.startsWith('minecraft:') ? v.slice(10) : v;
  }

  function refreshPortPreview() {
    previewPortForm();
  }

  /** 法向 → 面名（取主轴）。 */
  function faceFromNormal(n) {
    if (!n) return null;
    const [nx, ny, nz] = n;
    const ax = Math.abs(nx), ay = Math.abs(ny), az = Math.abs(nz);
    if (ay >= ax && ay >= az) return ny > 0 ? 'up' : 'down';
    if (ax >= az) return nx > 0 ? 'east' : 'west';
    return nz > 0 ? 'south' : 'north';
  }

  /** 该面的边界平面（axis 0=x 1=y 2=z，value = 该轴上的边界坐标）。 */
  function facePlane(face) {
    const [sx, sy, sz] = E.st ? E.st.size : [0, 0, 0];
    if (face === 'west') return { axis: 0, value: 0 };
    if (face === 'east') return { axis: 0, value: sx - 1 };
    if (face === 'north') return { axis: 2, value: 0 };
    if (face === 'south') return { axis: 2, value: sz - 1 };
    if (face === 'up') return { axis: 1, value: sy - 1 };
    return { axis: 1, value: 0 };
  }

  /** 两个格 + 面 → {origin:[a1,a2], size:[s1,s2]}（取包围盒，与 assemble 约定一致）。 */
  function portFromCells(face, c1, c2) {
    const ax = portAxes(face);
    const rt = (a) => (a === 'X' ? 0 : a === 'Y' ? 1 : 2);
    const pick = (c) => [c[rt(ax[0])], c[rt(ax[1])]];
    const a = pick(c1), b = pick(c2);
    return {
      face,
      origin: [Math.min(a[0], b[0]), Math.min(a[1], b[1])],
      size: [Math.abs(a[0] - b[0]) + 1, Math.abs(a[1] - b[1]) + 1],
    };
  }

  // ---- 形状：矩形（两点）/ 圆形（圆心 + 直径）----------------------------------
  // 存储上圆形就是「origin = 圆心、size = [直径, 直径]」（与
  // ``mccore.module_lib.port_bbox`` 同一套换算）；**引擎仍按外接矩形匹配**，
  // 圆是画出来/给 AI 看的几何意图（需求：形状可选，接口只是参考）。
  /** 面内包围盒 → 存盘用的 origin/size（圆形把包围盒还原成圆心 + 直径）。 */
  function portFromBox(face, origin, size, shape) {
    if (shape !== 'circle') {
      return { face, origin: [origin[0], origin[1]], size: [size[0], size[1]] };
    }
    const d = Math.max(1, Math.min(size[0], size[1]));
    const off = Math.floor((d - 1) / 2);
    return { face, origin: [origin[0] + off, origin[1] + off], size: [d, d] };
  }

  /** 存盘的 origin/size → 面内包围盒（矩形原样；圆形从圆心展开）。 */
  function portBoxOf(p) {
    const o = p.origin || [0, 0], s = p.size || [1, 1];
    if ((p.shape || 'rect') !== 'circle') {
      return [[o[0], o[1]], [Math.max(1, s[0]), Math.max(1, s[1])]];
    }
    const d = Math.max(1, Math.min(s[0], s[1]));
    const off = Math.floor((d - 1) / 2);
    return [[o[0] - off, o[1] - off], [d, d]];
  }

  /** 当前表单选中的形状（分段按钮上的高亮那个）。 */
  function portShape() {
    const box = $('#port-shape');
    const on = box && box.querySelector ? box.querySelector('button.on') : null;
    return (on && on.dataset.shape) || 'rect';
  }

  /** 切换形状：显隐字段组，并把当前的数字**搬过去**（别让人白填一遍）。 */
  function setPortShape(shape, move) {
    const box = $('#port-shape');
    if (box && box.querySelectorAll) {
      box.querySelectorAll('button').forEach(
        (b) => b.classList.toggle('on', b.dataset.shape === shape));
    }
    const rect = $('#port-rect-fields'), circ = $('#port-circle-fields');
    if (rect) rect.classList.toggle('hidden', shape !== 'rect');
    if (circ) circ.classList.toggle('hidden', shape !== 'circle');
    if (move !== false) transferPortFields(shape);
    renderPortAxisHint();
    refreshPortPreview();
  }

  /** 两种形状的输入互搬一次（矩形两点 ⇄ 圆心 + 直径）。 */
  function transferPortFields(to) {
    const num = (id, dflt) => {
      const n = $(id);
      const v = n ? Number(n.value) : NaN;
      return Number.isFinite(v) ? Math.round(v) : dflt;
    };
    const set = (id, v) => { const n = $(id); if (n) n.value = v; };
    if (to === 'circle') {
      const lo = [Math.min(num('#port-a1', 0), num('#port-b1', 0)),
                  Math.min(num('#port-a2', 0), num('#port-b2', 0))];
      const hi = [Math.max(num('#port-a1', 0), num('#port-b1', 0)),
                  Math.max(num('#port-a2', 0), num('#port-b2', 0))];
      const size = [hi[0] - lo[0] + 1, hi[1] - lo[1] + 1];
      const d = Math.max(1, Math.min(size[0], size[1]));
      const off = Math.floor((d - 1) / 2);
      set('#port-c1', lo[0] + off);
      set('#port-c2', lo[1] + off);
      set('#port-dia', d);
    } else {
      const c = [num('#port-c1', 0), num('#port-c2', 0)];
      const d = Math.max(1, num('#port-dia', 3));
      const off = Math.floor((d - 1) / 2);
      set('#port-a1', Math.max(0, c[0] - off));
      set('#port-a2', Math.max(0, c[1] - off));
      set('#port-b1', Math.max(0, c[0] - off) + d - 1);
      set('#port-b2', Math.max(0, c[1] - off) + d - 1);
    }
  }

  /** 接口（存盘形状）→ 世界 bbox（画预览框/圆环）。 */
  function portWorldBox(p) {
    const [o, s] = portBoxOf(p);
    return portBBox(p.face, o, s);
  }

  /** 当前表单 → 3D 预览（矩形框 / 圆形环）。 */
  function previewPortForm() {
    if (!E.st || !E.viewer) return;
    const p = readPortForm();
    E.viewer.setPortPreview({ box: portWorldBox(p), shape: p.shape || 'rect',
                             face: p.face });
  }

  /** 接口 → 世界 bbox（画预览框）。 */
  function portBBox(face, origin, size) {
    const [sx, sy, sz] = E.st ? E.st.size : [1, 1, 1];
    const [o1, o2] = origin, [s1, s2] = size;
    let x0, y0, z0, x1, y1, z1;
    if (face === 'west' || face === 'east') {
      const x = face === 'west' ? 0 : sx - 1;
      x0 = x; x1 = x; y0 = o1; y1 = o1 + s1 - 1; z0 = o2; z1 = o2 + s2 - 1;
    } else if (face === 'north' || face === 'south') {
      const z = face === 'north' ? 0 : sz - 1;
      z0 = z; z1 = z; y0 = o1; y1 = o1 + s1 - 1; x0 = o2; x1 = o2 + s2 - 1;
    } else {
      const y = face === 'down' ? 0 : sy - 1;
      y0 = y; y1 = y; x0 = o1; x1 = o1 + s1 - 1; z0 = o2; z1 = o2 + s2 - 1;
    }
    return [x0, y0, z0, x1, y1, z1];
  }

  /** 面内扫描：把该面上的**非实心格**按连通块分组，返回最大的几块（“扫开口”）。 */
  function scanFaceOpenings(face, limit = 6) {
    if (!E.st) return [];
    const [sx, sy, sz] = E.st.size;
    const solidAt = (x, y, z) => {
      if (x < 0 || y < 0 || z < 0 || x >= sx || y >= sy || z >= sz) return true;
      return E.voxels[(y * sz + z) * sx + x] !== 0;
    };
    const plane = facePlane(face);
    const ax = portAxes(face);
    const rt = (a) => (a === 'X' ? 0 : a === 'Y' ? 1 : 2);
    const n1 = (ax[0] === 'X' ? sx : ax[0] === 'Y' ? sy : sz);
    const n2 = (ax[1] === 'X' ? sx : ax[1] === 'Y' ? sy : sz);
    const cellOf = (i1, i2) => {
      const c = [0, 0, 0];
      c[plane.axis] = plane.value;
      c[rt(ax[0])] = i1;
      c[rt(ax[1])] = i2;
      return c;
    };
    const open = (i1, i2) => !solidAt(...cellOf(i1, i2));
    const seen = new Set();
    const blocks = [];
    for (let i1 = 0; i1 < n1; i1++) {
      for (let i2 = 0; i2 < n2; i2++) {
        if (seen.has(i1 + ',' + i2) || !open(i1, i2)) continue;
        seen.add(i1 + ',' + i2);
        const q = [[i1, i2]];
        let lo1 = i1, hi1 = i1, lo2 = i2, hi2 = i2, n = 0;
        while (q.length) {
          const [a, b] = q.pop();
          n++;
          lo1 = Math.min(lo1, a); hi1 = Math.max(hi1, a);
          lo2 = Math.min(lo2, b); hi2 = Math.max(hi2, b);
          for (const [da, db] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
            const na = a + da, nb = b + db;
            if (na < 0 || nb < 0 || na >= n1 || nb >= n2) continue;
            const k2 = na + ',' + nb;
            if (seen.has(k2) || !open(na, nb)) continue;
            seen.add(k2);
            q.push([na, nb]);
          }
        }
        blocks.push({ origin: [lo1, lo2], size: [hi1 - lo1 + 1, hi2 - lo2 + 1], cells: n });
      }
    }
    blocks.sort((a, b) => b.cells - a.cells);
    return blocks.slice(0, limit);
  }

  function portHint(text, warn) {
    const h = $('#port-hint');
    if (!h) return;
    h.textContent = text || '';
    h.classList.toggle('warn-text', !!warn);
  }

  function fillPortForm(p) {
    const ax = portAxes(p.face);
    const shape = (p.shape || 'rect') === 'circle' ? 'circle' : 'rect';
    const [lo, size] = portBoxOf({ face: p.face, origin: p.origin, size: p.size, shape });
    const lbl = (id, v) => { const n = $(id); if (n) n.textContent = v; };
    const set = (id, v) => { const n = $(id); if (n) n.value = v; };
    set('#port-face', p.face);
    // 矩形：两点；圆形：圆心 + 直径（同一套数字，两种说法）
    lbl('#port-a1-lbl', `点① ${ax[0]}`);
    lbl('#port-a2-lbl', `点① ${ax[1]}`);
    lbl('#port-b1-lbl', `点② ${ax[0]}`);
    lbl('#port-b2-lbl', `点② ${ax[1]}`);
    set('#port-a1', lo[0]); set('#port-a2', lo[1]);
    set('#port-b1', lo[0] + size[0] - 1); set('#port-b2', lo[1] + size[1] - 1);
    lbl('#port-c1-lbl', `圆心 ${ax[0]}`);
    lbl('#port-c2-lbl', `圆心 ${ax[1]}`);
    set('#port-c1', p.origin[0]); set('#port-c2', p.origin[1]);
    set('#port-dia', Math.max(1, Math.min(p.size[0], p.size[1])));
    set('#port-id', p.id || '');
    portTypeSelect(p.type || 'passage');
    set('#port-tags', (p.tags || []).join('; '));
    setPortShape(shape, false);          // 先定形状（不搬数字，免得覆盖刚填的）
    renderPortAxisHint();
    refreshPortPreview();
  }

  /** 表单 → 形状 + 数字（统一入口：界面填、3D 点、整面/框选/扫空洞都走它）。
   *
   * 传进来的是**面内包围盒**（lo + size）；圆形取短边当直径、圆心取盒子正中。
   */
  function fillPortBox(face, origin, size, meta) {
    const shape = (meta && meta.shape) || portShape();
    const d = Math.max(1, Math.min(size[0], size[1]));
    const off = Math.floor((d - 1) / 2);
    const store = shape === 'circle'
      ? { origin: [origin[0] + off, origin[1] + off], size: [d, d] }
      : { origin: [origin[0], origin[1]], size: [size[0], size[1]] };
    fillPortForm({
      id: (meta && meta.id) || '', type: (meta && meta.type) || 'passage',
      face, shape, origin: store.origin, size: store.size,
      tags: (meta && meta.tags) || [],
    });
  }

  /** 表单里那行「法向 −X …」提示（换面/换尺寸时同步）。 */
  function renderPortAxisHint() {
    const n = $('#port-axis-hint');
    if (n) n.textContent = portFaceHint(($('#port-face') || {}).value || 'east');
  }

  function readPortForm() {
    const num = (id, dflt) => {
      const n = $(id);
      const v = n ? Number(n.value) : NaN;
      return Number.isFinite(v) ? Math.round(v) : dflt;
    };
    const face = ($('#port-face') || {}).value || 'east';
    const id = (($('#port-id') || {}).value || '').trim();
    const shape = portShape();
    let boxOrigin, boxSize;
    if (shape === 'circle') {
      const d = Math.max(1, num('#port-dia', 3));
      boxOrigin = [Math.max(0, num('#port-c1', 0)), Math.max(0, num('#port-c2', 0))];
      boxSize = [d, d];
      const off = Math.floor((d - 1) / 2);
      boxOrigin = [Math.max(0, boxOrigin[0] - off), Math.max(0, boxOrigin[1] - off)];
    } else {
      const a = [Math.max(0, num('#port-a1', 0)), Math.max(0, num('#port-a2', 0))];
      const b = [Math.max(0, num('#port-b1', 0)), Math.max(0, num('#port-b2', 0))];
      boxOrigin = [Math.min(a[0], b[0]), Math.min(a[1], b[1])];
      boxSize = [Math.abs(a[0] - b[0]) + 1, Math.abs(a[1] - b[1]) + 1];
    }
    const p = Object.assign({ type: portTypeValue(), face },
                            portFromBox(face, boxOrigin, boxSize, shape));
    p.id = id || (face + '_' + p.origin[0] + '_' + p.origin[1]);
    if (shape === 'circle') p.shape = 'circle';
    p.tags = (($('#port-tags') || {}).value || '').split(/[;,]/)
      .map((s) => s.trim()).filter(Boolean);
    return p;
  }

  /** 接口列表（点一行 = 选中编辑；✕ = 删）。 */
  function renderPortList() {
    const box = $('#port-list');
    if (!box) return;
    box.innerHTML = '';
    const form = $('#port-form');
    if (form) form.classList.toggle('hidden', !modRef);
    if (!E.ports.length) {
      box.append(el('div', { class: 'hint2' },
        '还没有接口。选形状（矩形 / 圆形）→ 在同一个面上定位置（3D 里点两个角，' +
        '或点「整个面」/「从框选」/「扫空洞」）→ 「保存接口」。' +
        '接口里实心还是空心都无所谓，它只是装配/AI 的参考。'));
      return;
    }
    for (const p of E.ports) {
      const on = E.portSel && E.portSel.id === p.id;
      const circ = (p.shape || 'rect') === 'circle';
      const row = el('div', { class: 'port-row' + (on ? ' on' : '') },
        el('span', { class: 'port-face' }, PORT_FACE_CN[p.face] || p.face),
        el('span', { class: 'port-shape-tag', title: circ ? '圆形：圆心 + 直径' : '矩形：两点' },
          circ ? '⭘' : '▭'),
        el('span', { class: 'port-id' }, p.id),
        el('span', { class: 'port-type' }, PORT_TYPE_CN[p.type] || p.type),
        el('span', { class: 'port-size' },
          circ ? `⌀${p.size[0]}` : (p.size || []).join('×')),
        el('button', { class: 'link-btn', title: '删除这个接口', onclick: (ev) => {
          ev.stopPropagation(); removePort(p.id);
        } }, '✕'));
      row.addEventListener('click', () => {
        E.portSel = p;
        E.portPicks = [];
        fillPortForm(p);
        renderPortList();
        portHint('编辑接口 ' + p.id + '（改完点「保存接口」）');
      });
      box.append(row);
    }
  }

  /** 保存（新建或改写）接口 → PATCH /api/modules/<id>（服务端校验面/尺寸/类型，不看实心空心）。 */
  async function applyPort() {
    if (!modRef || !E.st) return null;
    const p = readPortForm();
    if (E.portSel && E.portSel.id !== p.id) {      // 改了 id → 当作重命名
      const rest = E.ports.filter((x) => x.id !== E.portSel.id && x.id !== p.id);
      rest.push(p);
      return pushPorts(rest, `已更新接口 ${p.id}`);
    }
    const next = E.ports.filter((x) => x.id !== p.id);
    next.push(p);
    return pushPorts(next, `已保存接口 ${p.id}`);
  }

  async function removePort(id) {
    if (!modRef) return null;
    const next = E.ports.filter((x) => x.id !== id);
    if (next.length === E.ports.length) return null;
    return pushPorts(next, `已删除接口 ${id}`, true);
  }

  async function pushPorts(ports, okMsg, ask) {
    if (ask && !confirm(`删除这个接口？会写回 ${modRef.id}.module.json`)) return null;
    try {
      await App.patch('/api/modules/' + encodeURIComponent(modRef.id), { ports });
      App.toast(okMsg, 'ok');
      E.portSel = null;
      await App.refreshAll();
      await refreshModulePanel();
      return ports;
    } catch (e) {
      portHint('保存失败：' + e.message, true);
      App.toast('接口保存失败: ' + e.message, 'err');
      return null;
    }
  }

  /** 「整个面」：把整个面当一个接口（柱子分段对接、构件对接最常用；实心空心都无所谓）。 */
  function portWholeFace() {
    if (!E.st) return;
    const face = ($('#port-face') || {}).value || 'east';
    const [sx, sy, sz] = E.st.size;
    const ax = portAxes(face);
    const size = [ax[0] === 'X' ? sx : ax[0] === 'Y' ? sy : sz,
                  ax[1] === 'X' ? sx : ax[1] === 'Y' ? sy : sz];
    fillPortBox(face, [0, 0], size, { type: 'interface' });
    E.portSel = null;
    const p = readPortForm();
    portHint(`整个面：${PORT_FACE_CN[face]} ` +
      (p.shape === 'circle' ? `⌀${p.size[0]} @ 圆心 ${p.origin.join(',')}`
        : `${size.join('×')}`) + '（接口里实心/空心都行）');
    renderPortList();
  }

  /** 「从框选」：把 3D 框选（两个角）投到当前面上当接口（框选不必贴边，会投到边界层）。 */
  function portFromSelection() {
    if (!E.st) return;
    const sel = AX.sel;
    if (!sel) { portHint('先用「框选」工具框一段（点两个对角方块），或直接填数字', true); return; }
    const face = ($('#port-face') || {}).value || 'east';
    const [sx, sy, sz] = E.st.size;
    const [x0, y0, z0, x1, y1, z1] = sel;
    const ax = portAxes(face);
    const rt = (a) => (a === 'X' ? 0 : a === 'Y' ? 1 : 2);
    const lo = [x0, y0, z0], hi = [x1, y1, z1];
    const lim = (a) => (a === 'X' ? sx : a === 'Y' ? sy : sz);
    const o1 = Math.max(0, Math.min(lim(ax[0]) - 1, lo[rt(ax[0])]));
    const o2 = Math.max(0, Math.min(lim(ax[1]) - 1, lo[rt(ax[1])]));
    const e1 = Math.max(o1, Math.min(lim(ax[0]) - 1, hi[rt(ax[0])]));
    const e2 = Math.max(o2, Math.min(lim(ax[1]) - 1, hi[rt(ax[1])]));
    fillPortBox(face, [o1, o2], [e1 - o1 + 1, e2 - o2 + 1], { type: 'interface' });
    E.portSel = null;
    const p = readPortForm();
    portHint(`按框选填了：${PORT_FACE_CN[face]} ` +
      (p.shape === 'circle' ? `⌀${p.size[0]} @ 圆心 ${p.origin.join(',')}`
        : `${p.size.join('×')} @ ${p.origin.join(',')}`) +
             '（框选只要罩住想接的那片区域）');
    renderPortList();
  }

  /** 「扫空洞」：在当前面上找非实心连通块，取最大的那块填进表单（风道/窗洞用）。 */
  function scanPortOpenings() {
    if (!E.st) return;
    const face = ($('#port-face') || {}).value || 'east';
    const blocks = scanFaceOpenings(face);
    if (!blocks.length) {
      portHint(`「${PORT_FACE_CN[face]}」这一面上没有非实心区域（没有开口）`, true);
      return;
    }
    const b = blocks[0];
    fillPortBox(face, b.origin, b.size, { type: 'vent' });
    E.portSel = null;
    const p = readPortForm();
    portHint(`扫到 ${blocks.length} 个开口，已填最大的：` +
      (p.shape === 'circle' ? `⌀${p.size[0]} @ 圆心 ${p.origin.join(',')}`
        : `${b.size.join('×')} @ ${b.origin.join(',')}`));
    renderPortList();
  }

  /** 接口工具：3D 里点格 → 记下角（同一个面上点两个角）。右键 = 清空。 */
  function portToolPick(ev, erase) {
    if (erase) {
      E.portPicks = [];
      E.portSel = null;
      if (E.viewer) E.viewer.setPortPreview(null);
      portHint('已清空待选/预览');
      return;
    }
    const hit = E.viewer && E.viewer.pick(ev);
    if (!hit) { portHint('接口：先用准星对准一个方块再点（空白处点不到）', true); return; }
    const face = faceFromNormal(hit.normal);
    const plane = facePlane(face);
    if (hit.cell[plane.axis] !== plane.value) {
      portHint(`这一格不在${PORT_FACE_CN[face]}的边界层（接口格子必须在 x=0 / x=${E.st.size[0] - 1} / y=0 / y=${E.st.size[1] - 1} / z=0 / z=${E.st.size[2] - 1} 那一层）`, true);
      return;
    }
    if (E.portPicks.length === 1 && E.portPicks[0].face === face) {
      E.portPicks.push({ face, cell: hit.cell.slice() });
    } else {
      E.portPicks = [{ face, cell: hit.cell.slice() }];
    }
    const first = E.portPicks[0];
    const shape = portShape();
    if (E.portPicks.length === 1) {
      const p = portFromCells(face, first.cell, first.cell);
      fillPortBox(face, p.origin, p.size, {});
      E.portSel = null;
      portHint(shape === 'circle'
        ? `圆心已设成 ${first.cell.join(',')} → 改直径 + 在同一个面上点第二个角（圆会内接那个框）`
        : `第一角已记下 ${first.cell.join(',')} → 在同一个面上再点对角那一格（或改数字 / 「扫空洞」）`);
    } else {
      const p = portFromCells(face, first.cell, E.portPicks[1].cell);
      fillPortBox(face, p.origin, p.size, {});
      E.portSel = null;
      const cur = readPortForm();
      portHint(`已取${shape === 'circle' ? '圆形' : '矩形'} ` +
        (shape === 'circle' ? `⌀${cur.size[0]} @ 圆心 ${cur.origin.join(',')}`
          : `${p.size.join('×')} @ ${p.origin.join(',')}`) +
        ' → 填 id/类型 后「保存接口」');
    }
    renderPortList();
  }

  /** 重渲染这个模块的预览图（与模块库同一条串行队列）。 */
  async function renderModulePreview() {
    if (!modRef) return null;
    const hint = $('#mod-meta-hint');
    try {
      const r = await App.post('/api/modules/' + encodeURIComponent(modRef.id) + '/preview', {});
      if (hint) hint.textContent = `渲染预览中…（${r.job}）`;
      const job = await App.waitJob(r.job);
      if (job.state === 'done') {
        modPreviewBust = Date.now();
        modPreviewCache.delete(modRef.id);
        App.toast('预览已重渲染', 'ok');
        await App.refreshAll();
        await refreshModulePanel();
      } else {
        App.toast('预览失败: ' + String(job.error || '').split('\n')[0], 'err');
      }
      return job;
    } catch (e) {
      App.toast('预览失败: ' + e.message, 'err');
      return null;
    }
  }

  /** 画布框（保存时按它裁剪）。数据范围 E.st.size 可以比它大：框外的东西留着，只是不写进文件。 */
  function frameOf() {
    const size = (E.st && E.st.size) || [1, 1, 1];
    const f = (E.st && E.st.frame) || size;
    return [0, 1, 2].map((i) => Math.max(1, Math.min(Number(f[i]) || size[i], size[i])));
  }

  function frameDiffers() {
    const f = frameOf();
    const s = (E.st && E.st.size) || f;
    return f[0] !== s[0] || f[1] !== s[1] || f[2] !== s[2];
  }

  /** 3D 视口里的画布框线框 + 2D 虚线框（框外内容不高亮）。 */
  function updateFrameOverlay() {
    if (!E.viewer || !E.st) return;
    const [fx, fy, fz] = frameOf();
    E.viewer.setFrameBox(frameDiffers() ? [0, 0, 0, fx - 1, fy - 1, fz - 1] : null);
  }

  function updateStatus() {
    if (!E.st) { status('未打开结构 — 用左上角「打开结构…」'); return; }
    const dirty = E.st.dirty ? ' · 未保存*' : '';
    const steps = (E.hist && E.hist.total)
      ? ` · 日志 ${E.hist.cursor}/${E.hist.total}` : '';
    const fr = frameDiffers()
      ? ` · 画布框 ${frameOf().join('×')}`
        + (E.st.outside ? `（框外 ${E.st.outside} 块，保存时裁掉）` : '')
      : '';
    status(`${E.st.name} · ${E.st.size.join('×')} · ${E.st.blocks} 块 · ` +
           `层 ${E.layer + 1}/${E.st.size[1]} · 模块 ${E.placements.length}${fr}${steps}${dirty}`);
    $('#btn-undo').disabled = !E.st.can_undo;
    $('#btn-redo').disabled = !E.st.can_redo;
    refreshHistory();
  }

  /** 打开结构：可搜索下拉（输入即搜 /api/files，选中就打开）。
   *  选完不回填输入框（echoPick:false），保持“搜索框”形态；当前文件看底部状态栏。 */
  let openFileSS = null;
  function openFileSelect() {
    if (openFileSS) return openFileSS;
    const host = $('#open-file');
    if (!host || !App.searchSelect) return null;
    openFileSS = App.searchSelect({
      placeholder: '打开结构…（输入即搜）',
      class: 'ss-open', echoPick: false, max: 40, debounce: 200,
      onQuery: async (q) => {
        const d = await App.api('/api/files?limit=40&q=' + encodeURIComponent(q));
        return (d.files || []).map((f) => ({
          value: f.path, label: f.path,
          hint: `${f.kind} · ${Math.max(1, Math.round(f.size / 1024))} KB`,
        }));
      },
      onPick: (path) => {
        if (path) openPath(path);
        if (openFileSS) openFileSS.set('');
      },
    });
    host.replaceWith(openFileSS.node);
    openFileSS.node.id = 'open-file';
    return openFileSS;
  }

  async function refreshFiles() {
    openFileSelect();
    if (App.state && App.state.packs) modPackOptions();
  }

  /** 模块装配的“资产包”筛选（与模块搜索联合生效）。 */
  let modPackSS = null;
  function modPackOptions() {
    if (!modPackSS) return;
    modPackSS.setOptions((App.state.packs || []).map((p) => ({
      value: p.id, label: p.name || p.id, hint: `${p.modules} 个模块`,
    })));
  }
  function modPackSelect() {
    if (modPackSS) return modPackSS;
    const host = $('#mod-pack');
    if (!host || !App.searchSelect) return null;
    modPackSS = App.searchSelect({
      placeholder: '资产包：全部', emptyLabel: '全部资产包',
      allowEmpty: true, onPick: () => renderModuleSearch(),
    });
    host.replaceWith(modPackSS.node);
    modPackSS.node.id = 'mod-pack';
    modPackOptions();
    return modPackSS;
  }

  // ------------------------------------------------------------ assembly UI
  async function refreshModules() {
    if (!E.st) return;
    try {
      const d = await App.api(`/api/structure/${E.st.sid}/modules`);
      E.placements = d.placements || [];
    } catch (e) { E.placements = E.placements || []; }
    if (E.selectedPid && !E.placements.some((x) => x.pid === E.selectedPid)) {
      E.selectedPid = null;
    }
    renderInstances();
    updateModuleVisuals();
    updateStatus();
  }

  /** 与后端同序（X→Y→Z）的 90° 尺寸置换，用于旋转时保持中心。 */
  function dimsForRot(size, rotx, rot, rotz) {
    const d = size.slice();
    const perm = (axis, times) => {
      for (let i = 0; i < ((times % 4) + 4) % 4; i++) {
        if (axis === 0) { const t = d[1]; d[1] = d[2]; d[2] = t; }
        else if (axis === 1) { const t = d[0]; d[0] = d[2]; d[2] = t; }
        else { const t = d[0]; d[0] = d[1]; d[1] = t; }
      }
    };
    perm(0, rotx); perm(1, rot); perm(2, rotz);
    return d;
  }

  function gizmoOf(sel) {
    const b = sel.bbox;
    return {
      center: [(b[0] + b[3] + 1) / 2, (b[1] + b[4] + 1) / 2,
               (b[2] + b[5] + 1) / 2],
      size: sel.dims,
      rot: sel.rot, rotx: sel.rotx || 0, rotz: sel.rotz || 0,
    };
  }

  /** 「叠加层能不能画」：只在**移动 / 复制**这两个工具下显示模块脚框、
   *  中心小方块与三箭头。
   *
   *  为什么：导入投影时那一套手柄/箭头是「刚进来的东西还没摆好」的提示；
   *  一旦切到放置/擦除这种编辑工具，投影就该当普通方块用 —— 再挂着箭头
   *  和彩色小方块只会挡视线、还让人以为「点它才能编辑」。
   *  拾取本来就已经是这个口径（`onPaint` 里只有 REGION_TOOLS 才走
   *  `regionToolDown`），这里补齐的是**绘制**。
   */
  function moduleOverlayOn() {
    return !!(E.st && REGION_TOOLS.has(E.tool));
  }

  function updateModuleVisuals() {
    const viewer = E.viewer;
    if (!viewer) return;
    if (!moduleOverlayOn()) {
      // 编辑工具：完全收起叠加层（脚框 / 手柄 / 三箭头 / 旋停高亮）。
      // 选中状态也一并清掉，否则 `onHover` 还会去 pick 已经看不见的 gizmo。
      if (E.selectedPid) {
        E.selectedPid = null;
        const box = $('#mod-move');
        if (box) box.classList.add('hidden');
        renderInstances();
        syncPanelVisibility();         // 左栏回到「当前投影属性」（不动接口待选）
      }
      viewer.setModuleBoxes([]);
      viewer.setGizmo(null);
      viewer.setHover(null);
      render2D();
      return;
    }
    // 每个模块一个**中心小立方体手柄** + 一色一个（pid 哈希出色相）：
    // 一堆模块重叠时，点模块身体完全猜不准，点手柄是确定的。
    const boxes = E.placements.map((p) => ({
      bbox: p.bbox, selected: p.pid === E.selectedPid, pid: p.pid,
      handleColor: moduleHandleColor(p.pid, p.pid === E.selectedPid, E.tool),
    }));
    viewer.setModuleBoxes(boxes);
    const sel = E.placements.find((p) => p.pid === E.selectedPid);
    // 选区的三箭头在武装时占着 gizmo——这时不显示模块的（两个 gizmo 会叠在一起）
    if (E.region && E.region.armed) viewer.setGizmo(null);
    else viewer.setGizmo(sel ? gizmoOf(sel) : null);
    render2D();
  }

  /** 模块手柄的颜色：pid 哈希 → 色相（不同模块颜色不同）；
   *  复制模式整体提亮一点，与移动模式有细微区别。
   */
  function moduleHandleColor(pid, selected, mode) {
    if (selected) return [1, 0.62, 0.2];        // 选中的统一橙色（与脚框一致）
    let h = 0;
    for (let i = 0; i < pid.length; i++) h = (h * 33 + pid.charCodeAt(i)) & 0xffff;
    const hue = (h * 47) % 360;
    const sat = mode === 'copy' ? 0.62 : 0.5;
    const lig = mode === 'copy' ? 0.66 : 0.55;
    return hslToRgb(hue / 360, sat, lig);
  }

  /** HSL(0..1) → [r,g,b](0..1)。只给手柄配色用，不必高精度。 */
  function hslToRgb(h, s, l) {
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    const f = (t) => {
      let x = t;
      if (x < 0) x += 1;
      if (x > 1) x -= 1;
      if (x < 1 / 6) return p + (q - p) * 6 * x;
      if (x < 1 / 2) return q;
      if (x < 2 / 3) return p + (q - p) * (2 / 3 - x) * 6;
      return p;
    };
    return [f(h + 1 / 3), f(h), f(h - 1 / 3)];
  }

  function selectPlacement(pid, toggle, opts) {
    opts = opts || {};
    if (toggle !== false && E.selectedPid === pid) pid = null;
    E.selectedPid = pid;
    const sel = E.placements.find((p) => p.pid === E.selectedPid);
    const box = $('#mod-move');
    if (sel) {
      box.classList.remove('hidden');
      $('#mod-sel-name').textContent = `${sel.id} @ ${sel.pos.join(',')}`;
      $('#mod-x').value = sel.pos[0];
      $('#mod-y').value = sel.pos[1];
      $('#mod-z').value = sel.pos[2];
      const v = E.viewer;
      if (v) v.cam.target = [(sel.bbox[0] + sel.bbox[3] + 1) / 2,
                             (sel.bbox[1] + sel.bbox[4] + 1) / 2,
                             (sel.bbox[2] + sel.bbox[5] + 1) / 2];
      if (v) v.frame();
      const moveBtn = document.querySelector('[data-tool=move]');
      // 选中模块后自动切到「移动」，好让三箭头马上能用；
      // 但已经处在选区变换（移动/复制）模式时不抢——那会把选区手柄顶掉；
      // `opts.keepTool` 是给「导入/打开」用的：那一步是异步的，等它回来时用户
      // 可能已经手动切到别的工具了（比如接口工具），不能把工具抢回去。
      if (!opts.keepTool && !E.region && E.tool !== 'move' && E.tool !== 'copy' &&
          moveBtn && !moveBtn.classList.contains('active')) moveBtn.click();
    } else {
      box.classList.add('hidden');
    }
    renderInstances();
    updateModuleVisuals();
    syncPosSliders();
    refreshModulePanel();      // 左栏跟着切换：实例的模块属性 ↔ 当前投影属性
  }

  function placementAtCell(x, y, z) {
    return E.placements.find((p) => {
      const b = p.bbox;
      return x >= b[0] && x <= b[3] && y >= b[1] && y <= b[4] && z >= b[2] && z <= b[5];
    }) || null;
  }

  function renderInstances() {
    const box = $('#mod-instances');
    box.innerHTML = '';
    E.placements.forEach((p) => {
      const row = el('div', {
        class: 'mod-inst-row' + (p.pid === E.selectedPid ? ' sel' : ''),
        onclick: (e) => { e.stopPropagation(); selectPlacement(p.pid); },
        onmouseenter: () => {
          if (p.pid !== E.selectedPid && moduleOverlayOn()) {
            E.viewer.setModuleBoxes(E.placements.map((q) => ({
              bbox: q.bbox,
              selected: q.pid === E.selectedPid || q.pid === p.pid,
            })));
            E.viewer.frame();
          }
        },
        onmouseleave: () => updateModuleVisuals(),
      },
        el('span', { class: 'mi-id',
                     title: `${p.id}（${p.pack === SELF_PACK ? '整幅投影，可在编辑器里拖动/旋转/固化' : p.pack}）` }, p.id),
        el('span', { class: 'mi-pos' },
          `@${p.pos.join(',')} ${rotLabel(p)} ${p.dims.join('×')}`),
        (p.edits
          ? el('span', { class: 'mi-edits',
                         title: `就地改了 ${p.edits} 格（画笔/工具压在模块上的部分，跟着模块走）` },
               `✎${p.edits}`)
          : null));
      box.append(row);
    });
    $('#mod-count').textContent = E.placements.length
      ? `（${E.placements.length}）` : '';
  }

  function rotLabel(p) {
    const bits = [];
    if (p.rot) bits.push('Y' + p.rot * 90);
    if (p.rotx) bits.push('X' + p.rotx * 90);
    if (p.rotz) bits.push('Z' + p.rotz * 90);
    return bits.join(' ');
  }

  /** 模块列表：每行点一下就直接导进当前画布（不再需要勾选 + 导入按钮）。
   *
   * 列表里**不再有「打开空白画布」那行**——建空画布走顶栏的「新建」
   * （一处入口，不重复）。
   */
  async function renderModuleSearch() {
    const box = $('#mod-results');
    if (!box) return;
    const q = ($('#mod-search').value || '').trim();
    const pack = modPackSS ? modPackSS.value() : '';
    box.innerHTML = '';
    try {
      const d = await App.api('/api/modules?q=' + encodeURIComponent(q) +
        '&limit=40&sort=id' + (pack ? '&pack=' + encodeURIComponent(pack) : ''));
      const list = d.modules || [];
      list.forEach((m) => {
        const row = el('div', {
          class: 'mod-result-row',
          title: `导入 ${m.id} 到当前画布；没打开画布时按 ${m.size.join('×')} 建一个`,
          onclick: () => { importModuleRow(m); },
        },
          el('span', { class: 'mr-plus' }, '＋'),
          el('span', { class: 'mr-id' }, m.id),
          el('span', { class: 'mr-pack' }, m.pack || ''),
          el('span', { class: 'mr-size' },
            `${m.size[0]}×${m.size[1]}×${m.size[2]}`));
        box.append(row);
      });
      if (!list.length) {
        box.append(el('div', { class: 'hint2' }, '没有匹配的模块'));
      }
    } catch (e) {
      box.append(el('div', { class: 'hint2' }, '读取模块失败：' + e.message));
    }
  }

  /** 点模块行：有画布 → 沿 +X 接排导入；没画布 → 按模块尺寸建画布并放进实例。 */
  async function importModuleRow(m) {
    if (!E.st) { await openModuleCanvas(m.id); return; }
    try {
      const r = await App.post(`/api/structure/${E.st.sid}/modules`,
        { ids: [m.id], auto: true });
      await applyModuleResult(r);
      selectPlacement((r.added || [])[0] || null, false);
      setTool('move');
      App.toast(`已导入 ${m.id}：拖中心手柄移动 / 拖三箭头平移`, 'ok');
    } catch (e) { App.toast('导入失败: ' + e.message, 'err'); }
  }

  /** 按模块尺寸建画布 + 把模块作为可拖动实例放进去。 */
  async function openModuleCanvas(id) {
    try {
      const d = await App.api('/api/modules/' + encodeURIComponent(id));
      const path = d && (d.path || ((d.entry || {}).file));
      if (!path) throw new Error('模块没有文件路径');
      await openPath(path);
    } catch (e) { App.toast('打开模块失败: ' + e.message, 'err'); }
  }

  /** 切换编辑工具：找工具按钮点一下（复用按钮上的提示文案/提示行逻辑）。 */
  function setTool(name) {
    const b = document.querySelector('#tool-buttons [data-tool=' + name + ']');
    if (b) b.click();
  }

  /** 画布尺寸变了（扩容/裁剪）：整幅重载体素。 */
  async function reloadAfterResize(r) {
    const buf = await (await fetch(`/api/structure/${E.st.sid}/voxels`)).arrayBuffer();
    const st2 = Object.assign({}, E.st, {
      size: r.size || E.st.size, palette: E.palette, blocks: 0,
    });
    const keepSel = E.selectedPid;
    await adopt(st2, new Uint16Array(buf), { keepSelection: !!keepSel });
    refreshCounts();
    updateStatus();
  }

  async function applyModuleResult(r) {
    if (!r) return;
    await applyPaletteStates(r.palette);
    if (r.placements) E.placements = r.placements;
    E.st.dirty = r.dirty !== false;
    E.st.can_undo = r.can_undo;
    E.st.can_redo = r.can_redo;
    E.st.size = r.size || E.st.size;
    if (r.frame) E.st.frame = r.frame.slice();
    if (r.resized) {
      await reloadAfterResize(r);
      return;
    }
    if (r.bbox && r.region) E.viewer.patchRegion(r.bbox, r.region);
    refreshCounts();
    updateFrameOverlay();
    syncSizeSliders();
    renderInstances();
    updateModuleVisuals();
    syncPosSliders();
    updateStatus();
    render2D();
    if (r.warnings) App.toast(r.warnings.join('；'), 'warn', 7000);
  }

  function intOr(cur, v) {
    return Math.max(0, Math.round(Number(v === undefined ? cur : v)));
  }

  async function updateSelectedModule(opts) {
    opts = opts || {};
    const sel = E.placements.find((p) => p.pid === E.selectedPid);
    if (!sel) return;
    const commit = opts.commit === true || opts.local !== true;
    const body = { pid: sel.pid };
    if (opts.delta) {
      body.delta = [intOr(0, opts.delta[0]), intOr(0, opts.delta[1]),
                    intOr(0, opts.delta[2])];
      if (opts.local !== false) {
        opts.pos = [sel.pos[0] + body.delta[0],
                    sel.pos[1] + body.delta[1],
                    sel.pos[2] + body.delta[2]];
        body.pos = opts.pos;
      }
    }
    if (opts.pos) {
      body.pos = [intOr(sel.pos[0], opts.pos[0]),
                  intOr(sel.pos[1], opts.pos[1]),
                  intOr(sel.pos[2], opts.pos[2])];
      if (opts.local) {
        sel.pos = body.pos.slice();
        const d = sel.dims;
        sel.bbox = [body.pos[0], body.pos[1], body.pos[2],
                    body.pos[0] + d[0] - 1, body.pos[1] + d[1] - 1,
                    body.pos[2] + d[2] - 1];
        E.viewer.setGizmo(gizmoOf(sel));
        renderInstances();
        render2D();
        E.viewer.frame();
      }
    }
    if (opts.rot !== undefined) body.rot = ((opts.rot % 4) + 4) % 4;
    if (opts.rotx !== undefined) body.rotx = ((opts.rotx % 4) + 4) % 4;
    if (opts.rotz !== undefined) body.rotz = ((opts.rotz % 4) + 4) % 4;
    if (E.snapPort && commit) body.snapPort = true;
    if (!commit) return;    // 本地预览：松手时由 apiCommitSelected 提交
    let p = null;
    try {
      p = App.post(`/api/structure/${E.st.sid}/modules/update`, body);
      E.pendingPost = p;
      const r = await p;
      await applyModuleResult(r);
    } catch (e) {
      App.toast('移动失败: ' + e.message, 'err');
      await refreshModules();
    } finally {
      if (E.pendingPost === p) E.pendingPost = null;
    }
  }

  async function removeSelectedModule() {
    const sel = E.placements.find((p) => p.pid === E.selectedPid);
    if (!sel) return;
    try {
      const r = await App.post(`/api/structure/${E.st.sid}/modules/remove`,
        { pids: [sel.pid] });
      E.selectedPid = null;
      await applyModuleResult(r);
    } catch (e) { App.toast('移除失败: ' + e.message, 'err'); }
  }

  async function bakeModules() {
    if (!E.placements.length) return;
    try {
      const r = await App.post(`/api/structure/${E.st.sid}/modules/bake`, {});
      E.selectedPid = null;
      await applyModuleResult(r);
      App.toast('已固化装配（体素保留，清单清空）', 'ok');
    } catch (e) { App.toast(e.message, 'err'); }
  }

  // ------------------------------------------------------------ gizmo drag
  function startGizmoDrag(ev, hit) {
    const sel = E.placements.find((p) => p.pid === E.selectedPid);
    if (!sel) return;
    E.gizmoDrag = {
      hit, kind: hit.kind, axis: hit.axis,
      start: hit.point, startS: hit.s, startAngle: hit.angle,
      startCenter: (E.viewer.gizmo ? E.viewer.gizmo.center.slice() : hit.center),
      origPos: sel.pos.slice(), origDims: sel.dims.slice(),
      origRot: sel.rot, origRotx: sel.rotx || 0, origRotz: sel.rotz || 0,
      lastDelta: 0, lastStep: 0, moved: 0,
    };
    E.viewer.setHover(null);
  }

  function onGizmoMove(ev) {
    const g = E.gizmoDrag;
    if (!g) return;
    const sel = E.placements.find((p) => p.pid === E.selectedPid);
    if (!sel) return;
    if (g.kind === 'move') {
      const d = g.hit.dir;
      const s = E.viewer.gizmoAxisParam(ev, g.axis, g.startCenter);
      if (s === null || s === undefined) return;
      const delta = Math.round(s - g.startS);
      if (delta !== g.lastDelta) {
        g.lastDelta = delta;
        const pos = [g.origPos[0] + d[0] * delta,
                     g.origPos[1] + d[1] * delta,
                     g.origPos[2] + d[2] * delta];
        if (pos.every((v) => v >= 0)) updateSelectedModule({ pos, local: true });
      }
      return;
    }
    // 拖圆弧：绕对应轴 90° 步进旋转，包围盒中心保持不动
    const a = E.viewer.gizmoAngle(ev, g.axis, g.startCenter);
    if (a === null || a === undefined) return;
    let delta = a - g.startAngle;
    while (delta > Math.PI) delta -= Math.PI * 2;
    while (delta < -Math.PI) delta += Math.PI * 2;
    const step = Math.round(delta / (Math.PI / 2));
    if (step === g.lastStep) return;
    g.lastStep = step;
    const rx = g.axis === 'x' ? g.origRotx + step : g.origRotx;
    const ry = g.axis === 'y' ? g.origRot + step : g.origRot;
    const rz = g.axis === 'z' ? g.origRotz + step : g.origRotz;
    const nd = dimsForRot(sel.size, rx, ry, rz);
    const cx = g.origPos[0] + g.origDims[0] / 2;
    const cy = g.origPos[1] + g.origDims[1] / 2;
    const cz = g.origPos[2] + g.origDims[2] / 2;
    const opts = {
      commit: true,
      pos: [Math.max(0, Math.round(cx - nd[0] / 2)),
            Math.max(0, Math.round(cy - nd[1] / 2)),
            Math.max(0, Math.round(cz - nd[2] / 2))],
    };
    if (g.axis === 'x') opts.rotx = rx;
    else if (g.axis === 'y') opts.rot = ry;
    else opts.rotz = rz;
    updateSelectedModule(opts);
  }

  async function onGizmoUp() {
    const g = E.gizmoDrag;
    E.gizmoDrag = null;
    if (!g) return;
    if (E.pendingPost) {              // 等拖动中最后一次提交落地，避免用旧值覆盖
      try { await E.pendingPost; } catch (e) { /* 已在原处提示 */ }
    }
    if (g.kind === 'rot') return;     // 旋转在拖动过程中已按步提交
    await apiCommitSelected();
  }

  async function apiCommitSelected() {
    const sel = E.placements.find((p) => p.pid === E.selectedPid);
    if (!sel) return;
    try {
      const body = { pid: sel.pid, pos: sel.pos, snapPort: E.snapPort,
                     rot: sel.rot, rotx: sel.rotx || 0, rotz: sel.rotz || 0 };
      const r = await App.post(`/api/structure/${E.st.sid}/modules/update`, body);
      await applyModuleResult(r);
    } catch (e) { await refreshModules(); }
  }


  // ------------------------------------------------------------ slider widget
  function clampNum(v, lo, hi) {
    return Math.max(lo, Math.min(hi, Math.round(Number(v) || 0)));
  }

  /** 左键拖动（有上限，超界封顶）/ 右键点击直接输入数值。 */
  function makeSlider(opts) {
    const label = el('span', { class: 'sl-label' }, opts.label);
    const range = el('input', {
      type: 'range', class: 'sl-range',
      min: opts.min === undefined ? 0 : opts.min,
      max: opts.max === undefined ? 256 : opts.max,
      step: opts.step || 1, value: opts.value || 0,
    });
    const val = el('span', { class: 'sl-val' }, String(opts.value || 0));
    const row = el('div', { class: 'slider-row' }, label, range, val);
    let editing = false;
    range.addEventListener('input', () => {
      val.textContent = range.value;
      if (opts.onInput) opts.onInput(Number(range.value));
    });
    range.addEventListener('change', () => {
      if (opts.onCommit) opts.onCommit(Number(range.value));
    });
    range.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      if (editing) return;
      editing = true;
      const box = el('input', { type: 'number', class: 'sl-edit', value: range.value });
      row.replaceChild(box, range);
      box.focus();
      box.select();
      let done = false;
      const finish = (ok) => {
        if (done) return;
        done = true;
        editing = false;
        const v = clampNum(box.value, Number(range.min), Number(range.max));
        row.replaceChild(range, box);
        if (ok) {
          range.value = v;
          val.textContent = String(v);
          if (opts.onCommit) opts.onCommit(v);
        }
      };
      box.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter') { ev.preventDefault(); finish(true); }
        else if (ev.key === 'Escape') { ev.preventDefault(); finish(false); }
      });
      box.addEventListener('blur', () => finish(true));
    });
    return {
      node: row,
      value: () => Number(range.value),
      set(v, max) {
        if (max !== undefined) range.max = Math.max(1, Math.round(max));
        range.value = clampNum(v, Number(range.min), Number(range.max));
        val.textContent = range.value;
      },
    };
  }

  // ------------------------------------------------------------ canvas size
  let sizeSliders = null;
  function buildSizeSliders() {
    if (sizeSliders) return sizeSliders;
    const box = $('#size-sliders');
    if (!box) return null;
    sizeSliders = ['X', 'Y', 'Z'].map((lb) => makeSlider({
      label: lb, min: 1, max: 512, value: 1,
      onCommit: () => applyCanvasSize(false),
    }));
    for (const s of sizeSliders) box.append(s.node);
    return sizeSliders;
  }

  function syncSizeSliders() {
    const sl = buildSizeSliders();
    if (!sl) return;
    // 滑块编辑的是「画布框」（保存时按它裁剪）：数据范围可能比它大（框外留着但不落盘）
    const size = (E.st && E.st.size) || [1, 1, 1];
    const frame = frameOf();
    const max = Math.max(512, size[0], size[1], size[2], frame[0], frame[1], frame[2]);
    sl.forEach((s, i) => s.set(frame[i], max));
  }

  async function applyCanvasSize(fit) {
    if (!E.st) return;
    try {
      const body = fit ? { fit: true }
        : { size: sizeSliders.map((s) => s.value()) };
      const r = await App.post(`/api/structure/${E.st.sid}/resize`, body);
      await applyModuleResult(r);
      const fr = (r.frame || []).join('×');
      const out = r.outside || 0;
      App.toast(fit ? `保存范围已按内容匹配：${fr}`
        + (out ? `（还有 ${out.toLocaleString()} 格在框外不会落盘）` : '')
        : `保存范围 → ${fr}`
        + (out ? `（框外还有 ${out.toLocaleString()} 格，保存时才裁掉）` : '')
        + '（框外照样可以继续放/画/抹）',
        'ok');
    } catch (e) {
      App.toast('改尺寸失败: ' + e.message, 'err');
      syncSizeSliders();
    }
  }

  // ------------------------------------------------------------ module sliders
  let posSliders = null;
  function buildPosSliders() {
    if (posSliders) return posSliders;
    const box = $('#mod-sliders');
    if (!box) return null;
    posSliders = [0, 1, 2].map((idx) => makeSlider({
      label: 'XYZ'[idx], min: 0, max: 256, value: 0,
      onInput: (v) => {
        const sel = E.placements.find((p) => p.pid === E.selectedPid);
        if (!sel) return;
        const pos = sel.pos.slice();
        pos[idx] = v;
        updateSelectedModule({ pos, local: true });
        syncPosNumberInputs(pos);
      },
      onCommit: () => apiCommitSelected(),
    }));
    for (const s of posSliders) box.append(s.node);
    return posSliders;
  }

  function syncPosNumberInputs(pos) {
    if ($('#mod-x')) $('#mod-x').value = pos[0];
    if ($('#mod-y')) $('#mod-y').value = pos[1];
    if ($('#mod-z')) $('#mod-z').value = pos[2];
  }

  function syncPosSliders() {
    const sel = E.placements.find((p) => p.pid === E.selectedPid);
    const box = $('#mod-sliders');
    if (!sel) {
      if (box) box.innerHTML = '';
      posSliders = null;
      return;
    }
    const sl = buildPosSliders();
    if (!sl) return;
    const size = (E.st && E.st.size) || [0, 0, 0];
    const max = Math.max(32, size[0], size[1], size[2],
                         sel.pos[0] + sel.dims[0], sel.pos[1] + sel.dims[1],
                         sel.pos[2] + sel.dims[2]) + 16;
    sl.forEach((s, i) => s.set(sel.pos[i], max));
    syncPosNumberInputs(sel.pos);
  }

  // ------------------------------------------------------------ 3D 准星
  function updateCursor(ev) {
    const v = E.viewer;
    if (!v || !E.st) return;
    if (REGION_TOOLS.has(E.tool)) { v.setCursor(null); return; }
    if (E.tool === 'port') {                     // 接口工具：只显示“点在哪个格”（接口可落在实心面上）
      const h = ev ? v.pick(ev) : null;
      v.setCursor(h ? { cell: h.cell, mode: 'pick' } : null);
      return;
    }
    let hit = ev ? v.pick(ev) : null;
    // 空画布/看向空白：没打到方块也能预览“第一块放哪”（与放置逻辑一致）
    if (!hit && ev && ERASE_TOOLS.has(E.tool) && E.tool !== 'erase') {
      hit = v.firstInBox ? v.firstInBox(ev) : null;
      if (hit) { v.setCursor({ cell: hit.cell, mode: 'place' }); return; }
    }
    if (!hit) { v.setCursor(null); return; }
    const [sx, sy, sz] = E.st.size;
    let cell = hit.cell;
    let mode = E.tool;
    if (E.tool === 'place') {
      cell = hit.place || hit.cell;
      if (cell[0] < 0 || cell[1] < 0 || cell[2] < 0) {
        // 画布原点固定在 (0,0,0)：−X/−Y/−Z 方向没有格子可放 → 准星标红（blocked）
        v.setCursor({ cell: hit.cell, mode: 'blocked' });
        return;
      }
      if (cell[0] >= sx || cell[1] >= sy || cell[2] >= sz) mode = 'grow';
    }
    v.setCursor({ cell, mode });
  }

  // ---------------------------------------------------------------- palette
  function stateIndex(state) {
    return E.palette.indexOf(state);
  }

  function renderPalette() {
    const list = $('#palette-list');
    list.innerHTML = '';
    const counts = {};
    const [sx, sy, sz] = E.st ? E.st.size : [0, 0, 0];
    for (let i = 0; i < E.voxels.length; i++) {
      const v = E.voxels[i];
      counts[v] = (counts[v] || 0) + 1;
    }
    E.palette.forEach((s, i) => {
      if (!counts[i]) return;
      const row = el('div', { class: 'palette-row', onclick: () => setState(s) },
        el('span', { class: 'swatch', style: 'background:' + (E.colors[s] || '#888') }),
        el('span', { class: 'nm', title: s }, s),
        el('span', { class: 'ct' }, String(counts[i])));
      list.append(row);
    });
    $('#current-state').textContent = E.state;
  }

  async function setState(s) {
    E.state = s;
    $('#current-state').textContent = s;
    const parsed = McStudio3D.parseState(s);
    const bare = parsed.name.replace('minecraft:', '');
    // 不把名字写回搜索框：那是筛选器，写了会静默地把列表锁成一项（旧行为）
    // 当前方块靠色块高亮 + 底部状态条显示
    // 取这个方块的属性表，并把「当前状态里的属性值」当作初值 → 吸管能把朝向一起吸过来
    const blocks = await searchBlocks(bare);
    const b = blocks.find((x) => x.name === bare);
    if (b) {
      E.currentBlock = b;
      renderBlockProps(b.properties, Object.assign({}, b.default, parsed.props));
    } else {
      E.currentBlock = { name: parsed.name, properties: {}, default: parsed.props };
      renderBlockProps({}, parsed.props);
    }
    renderPicker();
  }

  // ---------------------------------------------------------------- 新建画布
  /** 新建空画布（无路径；保存时走「另存为」）。 */
  async function newCanvas(size, name) {
    const st = await App.post('/api/structure/new',
      { size: size || [16, 16, 16], name: name || 'untitled' });
    const buf = await (await fetch(`/api/structure/${st.sid}/voxels`)).arrayBuffer();
    await adopt(st, new Uint16Array(buf));
    return st;
  }

  function newCanvasDialog() {
    const lim = E.limits || {};
    const maxCells = Number(lim.max_cells) || 20000000;
    const num = (v) => el('input', { class: 'ax-num', type: 'number', min: '1',
                                     max: '512', value: String(v) });
    const x = num(16), y = num(16), z = num(16);
    const nm = el('input', { value: 'untitled' });
    App.modal('新建画布', el('div', {},
      el('div', { class: 'form-row' }, el('label', {}, 'X'), x),
      el('div', { class: 'form-row' }, el('label', {}, 'Y'), y),
      el('div', { class: 'form-row' }, el('label', {}, 'Z'), z),
      el('div', { class: 'form-row' }, el('label', {}, '名字'), nm),
      el('div', { class: 'muted' }, '全空气的空画布；保存 / 存为模块 时才选路径'),
      el('div', { class: 'muted' }, `单边最大 512；总格数上限 ${maxCells.toLocaleString()} ` +
        '（想改去「设置 → 打开上限」）')),
      [{ label: '取消' }, { label: '创建', class: 'primary', onClick: async () => {
        try {
          switchTab('editor');
          const st = await newCanvas([Number(x.value) || 16, Number(y.value) || 16,
                                      Number(z.value) || 16], nm.value);
          App.toast(`已新建 ${st.size.join('×')} 空画布`, 'ok');
        } catch (e) { App.toast('新建失败: ' + e.message, 'err'); }
      } }]);
  }

  // ---------------------------------------------------------------- 方块面板
  // 数据来自 /api/blocks/picker（mcmaterials 的全量方块目录：颜色 + 材质家族），
  // 一次取回、本地筛选 —— 不用记方块 id。
  const PICK = { data: null, fam: 'all', q: '', full: false, recent: [],
                 mode: '', hasFull: true, note: '' };
  const PICK_CAP = 1200;                 // DOM 上限（目录 1100+ 个方块 → 一次全画出来；
                                         // 贴图是 loading=lazy，只有看得见的那几屏会真去拉）

  // 退化分类（服务端还是旧代码时的备用）：纯按方块名匹配
  const FALLBACK_TECH = /^(air|cave_air|void_air|barrier|light|structure_void|moving_piston|piston_head|jigsaw|spawner|trial_spawner|vault|bubble_column|end_portal|end_gateway|nether_portal|fire|soul_fire|water|lava|.*command_block|.*_head|.*_wall_head|.*_banner_pattern|.*_bucket|.*_spawn_egg)$/;
  const FALLBACK_GROUPS = [
    ['all', '全部', null],
    ['stone', '石砖', /(brick|stone|deepslate|andesite|diorite|granite|blackstone|basalt|sandstone|quartz|tuff|calcite|prismarine|netherrack)/],
    ['concrete', '混凝土', /concrete/],
    ['terracotta', '陶瓦', /terracotta/],
    ['wood', '木材', /(planks|_log|_wood|_stem|_hyphae|bamboo|crimson|warped)/],
    ['metal', '金属', /(copper|iron|gold|_bars|anvil|chain|cauldron)/],
    ['glass', '玻璃', /glass/],
    ['wool', '羊毛织物', /(wool|carpet)/],
    ['light', '灯具发光', /(lantern|torch|glowstone|sea_lantern|shroomlight|candle|lamp|beacon|froglight|magma|light)/],
    ['plant', '植物', /(leaves|flower|sapling|grass|fern|vine|moss|roots|azalea|kelp|seagrass|lily)/],
    ['slab', '半砖', /(_slab|_stairs)$/],
    ['edge', '墙栅栏', /(_wall|_fence)/],
    ['door', '门窗', /(_door|_trapdoor)$/],
    ['sign', '告示牌旗帜', /(_sign|_banner)$/],
  ];

  async function loadPicker() {
    if (PICK.data !== null) return PICK.data;
    PICK.note = '';
    // 一级：新版服务端（贴图平均色 + 材质家族）
    try {
      const d = await App.api('/api/blocks/picker');
      if (d && d.available) {
        PICK.data = d;
        PICK.mode = 'full';
        PICK.hasFull = true;
      } else {
        PICK.note = '服务端找不到方块目录数据（skills/minecraft-material-lab/data/）';
      }
    } catch (e) {
      PICK.note = '服务端还没有 /api/blocks/picker（多半是服务启动在旧代码上）';
    }
    // 二级：退化——只用拿得到的方块名单（颜色取当前调色板，其余灰）
    if (!PICK.data) {
      try {
        const ver = (E.st && E.st.version) || '';
        const d2 = await App.api('/api/blocks?q=&limit=2000'
          + (ver ? '&version=' + encodeURIComponent(ver) : ''));
        const rows = (d2.blocks || []).filter((b) => !FALLBACK_TECH.test(b.name))
          .map((b) => [b.name,
            E.colors['minecraft:' + b.name] || E.colors[b.name] || '#8a8a8a', 1, []]);
        if (rows.length) {
          PICK.data = { available: true, mode: 'names', families: [],
                        groups: FALLBACK_GROUPS, blocks: rows };
          PICK.mode = 'names';
          PICK.hasFull = false;
        } else {
          PICK.data = false;
        }
      } catch (e) { PICK.data = false; }
    }
    try {
      const raw = localStorage.getItem(RECENT_KEY) ?? localStorage.getItem(RECENT_KEY_LEGACY);
      if (raw !== null) localStorage.setItem(RECENT_KEY, raw);   // 旧键搬到新键
      PICK.recent = JSON.parse(raw || '[]');
    } catch (e) { PICK.recent = []; }
    return PICK.data;
  }

  function pickShort(name) {
    return name.replace(/^(waxed_|exposed_|weathered_|oxidized_)/, '');
  }

  function pickMatches() {
    const d = PICK.data;
    if (!d) return [];
    const q = PICK.q.trim().toLowerCase();
    const group = (d.groups || []).find((g) => g[0] === PICK.fam);
    // 两类分组都能吃：服务端给家族下标数组，退化模式给正则
    const fams = new Set();
    let re = null;
    for (const v of (group || []).slice(2)) {
      if (v instanceof RegExp) re = v;
      else if (Array.isArray(v)) for (const i of v) fams.add(i);
    }
    const out = [];
    for (const r of d.blocks) {
      const [name, , full, fl, , , alias] = r;
      if (PICK.full && PICK.hasFull && !full) continue;
      if (PICK.fam !== 'all') {
        if (re) { if (!re.test(name)) continue; }
        else if (fams.size && !fl.some((i) => fams.has(i))) continue;
      }
      // 搜索同时匹配 **id** 与**中文别名**（面板只认 id 时，搜「烟熏炉」是搜不到的）
      if (q && !(name.includes(q) || String(alias || '').toLowerCase().includes(q))) continue;
      out.push(r);
    }
    return out;
  }

  function renderFams() {
    const box = $('#blk-fams');
    if (!box || !PICK.data) return;
    box.innerHTML = '';
    for (const g of PICK.data.groups) {
      const [id, label] = g;
      // 家族表里没这个家族时（数据不全）不显示空芯片——它会变成“假全部”
      if (PICK.hasFull && id !== 'all' && Array.isArray(g[2]) && !g[2].length) continue;
      box.append(el('button', {
        class: 'blk-fam' + (PICK.fam === id ? ' on' : ''), type: 'button',
        onclick: () => { PICK.fam = id; renderFams(); renderPicker(); },
      }, label));
    }
  }

  function renderPicker() {
    const grid = $('#blk-grid');
    if (!grid) return;
    const info = $('#blk-info');
    const note = $('#blk-note');
    const fullRow = $('#blk-full-row');
    if (fullRow) fullRow.classList.toggle('hidden', PICK.hasFull === false);
    if (note) {
      // 退化模式/拿不到数据时把原因和解决办法写在脸上（不静默）
      if (!PICK.data) {
        note.classList.add('warn-text');
        note.textContent = '⚠ ' + (PICK.note || '拿不到方块名单')
          + '——可直接输入方块 id';
      } else if (PICK.mode === 'names') {
        note.classList.add('warn-text');
        note.textContent = '⚠ ' + (PICK.note || '服务端是旧版')
          + '：先按方块名单列（无贴图色/材质分类）；'
          + '重启 python -m mcstudio serve 后就是完整面板';
      } else {
        note.classList.remove('warn-text');
        note.textContent = '';
      }
    }
    if (!PICK.data) {                       // 连名单都拿不到 → 回退纯输入
      grid.classList.add('hidden');
      if (info) info.textContent = '可直接输入方块 id（回车选中）';
      return;
    }
    grid.classList.remove('hidden');
    const all = pickMatches();
    const shown = all.slice(0, PICK_CAP);
    grid.innerHTML = '';
    const cur = ((E.currentBlock && E.currentBlock.name) || '').replace('minecraft:', '');
    const ver = (E.st && E.st.version) || '';
    for (const [name, color, full, , tex, tint, alias] of shown) {
      const kind = PICK.hasFull ? (full ? '（完整方块）' : '（不完整方块）') : '';
      const sw = el('i', { style: 'background-color:' + color });
      if (tex) {
        // 直接用方块贴图当色块（懒加载：只拉看得见的那几屏）
        const img = el('img', {
          class: 'tex', loading: 'lazy', decoding: 'async', alt: '',
          src: '/api/asset?version=' + encodeURIComponent(ver)
             + '&rel=' + encodeURIComponent('textures/' + tex + '.png'),
        });
        img.addEventListener('error', () => {
          img.remove();
          sw.classList.add('no-tex');            // 贴图拿不到 → 退回底色
        });
        sw.append(img);
        if (tint) sw.append(el('span', { class: 'tint', style: 'background:' + tint }));
      } else {
        sw.classList.add('no-tex');
      }
      grid.append(el('button', {
        class: 'blk' + (name === cur ? ' on' : ''), type: 'button',
        title: name + (alias ? '（' + alias + '）' : '') + kind + (tex ? ' · ' + tex : ''),
        onclick: () => pickBlock(name),
      }, sw, el('span', {}, pickShort(name))));
    }
    if (info) {
      const q = PICK.q.trim();
      if (!all.length) {
        // 空结果要把原因说清（常见坑：搜索框里还留着上一次的词，它一直在当过滤器）
        info.textContent = '';
        info.append(`没有匹配的方块${q ? `（搜索词「${q}」）` : ''}——`);
        if (q) {
          info.append(el('button', {
            class: 'link-btn', type: 'button',
            onclick: () => {
              const inp = $('#block-search');
              if (inp) inp.value = '';
              PICK.q = '';
              renderPicker();
            },
          }, '清除搜索'));
          info.append(' ');
        }
        info.append('或直接输入 id 回车');
      } else {
        info.textContent = `${all.length} 个${all.length > shown.length
          ? `（先显示前 ${shown.length}，输入关键字缩小范围）` : ''}`
          + (PICK.mode === 'names' ? ' · 名单模式' : '');
      }
    }
    const cnt = $('#blk-count');
    if (cnt) cnt.textContent = `${all.length}/${PICK.data.blocks.length}`;
  }
  function pickBlock(name) {
    PICK.recent = [name].concat(PICK.recent.filter((x) => x !== name)).slice(0, 12);
    try { localStorage.setItem(RECENT_KEY, JSON.stringify(PICK.recent)); }
    catch (e) { /* 无 localStorage：忽略 */ }
    // 选完就清掉搜索框：它只是筛选器，留着上一次的词会把后面的分类点成 0 结果
    const inp = $('#block-search');
    if (inp) inp.value = '';
    PICK.q = '';
    chooseBlock(name);
  }

  async function searchBlocks(q, limit) {
    if (!E.st) return [];
    const d = await App.api('/api/blocks?version=' + encodeURIComponent(E.st.version) +
      '&q=' + encodeURIComponent(q) + '&limit=' + (limit || 60));
    return d.blocks || [];
  }

  function renderBlockProps(props, values) {
    const box = $('#block-props');
    box.innerHTML = '';
    const keys = Object.keys(props || {});
    for (const k of keys) {
      const sel = el('select', {
        onchange: () => composeState(),
      });
      for (const v of props[k]) sel.append(el('option', { value: v }, v));
      const cur = (values || {})[k];
      if (cur !== undefined) sel.value = cur;
      sel.dataset.prop = k;
      box.append(el('div', { class: 'prop' }, el('label', {}, k), sel));
    }
    composeState();                       // 无属性的方块也要把状态换成它自己
    renderFaceCtl();
  }

  function composeState() {
    if (!E.currentBlock) return;
    const name = E.currentBlock.name.includes(':')
      ? E.currentBlock.name : 'minecraft:' + E.currentBlock.name;
    const props = {};
    document.querySelectorAll('#block-props select').forEach((s) => {
      props[s.dataset.prop] = s.value;
    });
    const body = Object.keys(props).sort().map((k) => `${k}=${props[k]}`).join(',');
    E.state = body ? `${name}[${body}]` : name;
    $('#current-state').textContent = E.state;
  }

  async function chooseBlock(name) {
    const bare = String(name || '').replace('minecraft:', '').trim();
    if (!bare) return;
    const blocks = await searchBlocks(bare);
    let b = blocks.find((x) => x.name === bare);
    if (!b) {
      // 旧服务端是“子串搜索 + 字母序截断”，精确项可能落在第一页之外 → 放大再找一次
      const wide = await searchBlocks(bare, 400);
      b = wide.find((x) => x.name === bare);
    }
    if (!b) {
      // 找不到就明说——绝不能退成“列表里第一个”（点 stone 变成 blackstone）
      App.toast(`找不到方块「${bare}」——换个名字或从下面色块里选`, 'err');
      return;
    }
    E.currentBlock = b;
    renderBlockProps(b.properties, b.default);
    renderPicker();
  }

  // ---------------------------------------------------------------- 朝向 / 旋转
  // facing（四/六向）/ rotation（16 档 22.5°）/ axis（轴）都从「当前方块」的属性表里
  // 读，所以只显示这个方块真正支持的按钮。
  const DIR_CN = { north: '北', east: '东', south: '南', west: '西', up: '上', down: '下' };
  // rotation：0=南，顺时针 22.5°/档（wiki：4=西 / 8=北 / 12=东）
  const ROT_CN = ['南', '南南西', '西南', '西南西', '西', '西北西', '西北', '北北西',
                  '北', '北北东', '东北', '东北东', '东', '东南东', '东南', '南南东'];
  // 贴在墙上的东西：facing 取「贴着的那面墙」（点哪面就朝哪面）
  const WALL_MOUNTED = /(wall_torch|wall_sign|wall_banner|wall_head|ladder)$/;
  // 朝放置者（而不是朝视线方向）的方块：容器 / 机关 / 装饰方块
  const FACES_PLAYER = /(observer|piston|dispenser|dropper|hopper|end_rod|lightning_rod|furnace|smoker|blast_furnace|chest|barrel|glazed_terracotta|shulker_box|command_block|repeater|comparator|daylight_detector)$/;
  const FACE6 = ['north', 'east', 'south', 'west', 'up', 'down'];

  function propValues(name) {
    const p = (E.currentBlock && E.currentBlock.properties) || {};
    return p[name] || null;
  }

  function propGet(name) {
    const sel = document.querySelector(`#block-props select[data-prop="${name}"]`);
    return sel ? sel.value : null;
  }

  /** 写属性。``rerender=False`` 用于连续涂抹（每格都重建 DOM 太浪费）。 */
  function propSet(name, value, rerender) {
    const sel = document.querySelector(`#block-props select[data-prop="${name}"]`);
    if (!sel) return false;
    // 属性表必须和当前状态里的方块一致，否则“改属性”会张冠李戴
    const parsed = McStudio3D.parseState(E.state || '');
    const cb = E.currentBlock
      ? (E.currentBlock.name.includes(':') ? E.currentBlock.name
                                           : 'minecraft:' + E.currentBlock.name)
      : null;
    if (cb && parsed.name && cb !== parsed.name) return false;
    sel.value = value;
    composeState();
    if (rerender !== false) renderFaceCtl();
    return true;
  }

  /** 把属性值回写到属性选择器（不改 E.state，用于「跟随视角」自动定向后的 UI 同步） */
  function syncPropsUI(props) {
    for (const k of Object.keys(props)) {
      const sel = document.querySelector(`#block-props select[data-prop="${k}"]`);
      if (sel && Array.from(sel.options).some((o) => o.value === props[k])) {
        sel.value = props[k];
      }
    }
  }

  /** 手动定向（按钮 / R 键）——把「跟随视角」关掉，否则下一个方块会把选择覆盖掉。 */
  function manualFace() {
    if (!E.autoFace) return;
    E.autoFace = false;
    const cb = $('#face-auto');
    if (cb) cb.checked = false;
    status('已手动定向——「跟随视角」已关（可重新勾上）');
  }

  function renderFaceCtl() {
    const box = $('#face-ctl');
    if (!box) return;
    box.innerHTML = '';
    const facing = propValues('facing');
    const rotation = propValues('rotation');
    const axis = propValues('axis');
    const half = propValues('half');        // 楼梯/活板门：bottom|top
    const slabs = propValues('type');       // 半砖：bottom|top|double
    const isSlab = !!(slabs && slabs.includes('double'));
    const isHalf = !!(half && half.includes('bottom') && half.includes('top')
                       && !half.includes('lower'));   // 门是 upper/lower，不算
    if (!facing && !rotation && !axis && !isSlab && !isHalf) {
      box.classList.add('hidden');
      return;
    }
    box.classList.remove('hidden');

    // 半砖：下 / 上 / 双层（就是 type 属性）
    if (isSlab) {
      const cur = propGet('type');
      const grid = el('div', { class: 'face-grid' });
      for (const [v, label] of [["bottom", "下半砖"], ["top", "上半砖"],
                                ["double", "双层"]] ) {
        grid.append(el('button', {
          class: (v === cur ? 'on' : ''), type: 'button',
          title: `type=${v}`, onclick: () => propSet('type', v),
        }, label));
      }
      box.append(el('div', { class: 'face-row' },
        el('span', { class: 'ax-lbl' }, '半砖'), grid));
    }
    // 楼梯 / 活板门：下 / 上（half）
    if (isHalf) {
      const cur = propGet('half');
      const grid = el('div', { class: 'face-grid' });
      for (const [v, label] of [["bottom", "下半"], ["top", "上半"]]) {
        grid.append(el('button', {
          class: (v === cur ? 'on' : ''), type: 'button',
          title: `half=${v}`, onclick: () => propSet('half', v),
        }, label));
      }
      box.append(el('div', { class: 'face-row' },
        el('span', { class: 'ax-lbl' }, '上下'), grid));
    }

    if (facing) {
      const grid = el('div', { class: 'face-grid' });
      const cur = propGet('facing');
      for (const d of FACE6.filter((x) => facing.includes(x))) {
        grid.append(el('button', {
          class: (d === cur ? 'on' : ''), type: 'button', title: '朝向 ' + d,
          onclick: () => { manualFace(); propSet('facing', d); },
        }, DIR_CN[d] || d));
      }
      box.append(el('div', { class: 'face-row' },
        el('span', { class: 'ax-lbl' }, '朝向'), grid));
    }
    if (rotation) {
      const cur = Number(propGet('rotation') || 0);
      const grid = el('div', { class: 'face-grid' });
      for (let i = 0; i < 16; i += 2) {
        grid.append(el('button', {
          class: (i === cur ? 'on' : ''), type: 'button',
          title: `rotation=${i}（${ROT_CN[i]} · ${i * 22.5}°）`,
          onclick: () => { manualFace(); propSet('rotation', String(i)); },
        }, ROT_CN[i]));
      }
      const fine = (delta) => () => {
        manualFace();
        propSet('rotation', String((cur + delta + 16) % 16));
      };
      box.append(el('div', { class: 'face-row' },
        el('span', { class: 'ax-lbl' }, '旋转'), grid, 
        el('button', { class: '', type: 'button', title: '逆时针 22.5°',
                       onclick: fine(15) }, '↺'),
        el('button', { class: '', type: 'button', title: '顺时针 22.5°',
                       onclick: fine(1) }, '↻')));
      box.append(el('div', { class: 'face-row' },
        el('span', { class: 'face-val' },
           `rotation=${cur}（${ROT_CN[cur]}）· 0=南，顺时针 22.5°/档`)));
    }
    if (axis) {
      const grid = el('div', { class: 'face-grid' });
      const cur = propGet('axis');
      for (const a of axis) {
        grid.append(el('button', {
          class: (a === cur ? 'on' : ''), type: 'button', title: '轴向 ' + a,
          onclick: () => { manualFace(); propSet('axis', a); },
        }, a.toUpperCase()));
      }
      box.append(el('div', { class: 'face-row' },
        el('span', { class: 'ax-lbl' }, '轴向'), grid));
    }
    box.append(el('label', { class: 'ax-check',
      title: '放置时按视角自动定方向：楼梯/门/台阶朝向视线方向；墙上的牌子火把贴点击面；告示牌/旗帜/头颅按视线反向（正面朝你）' },
      el('input', { type: 'checkbox', id: 'face-auto',
                    checked: E.autoFace ? 'checked' : null,
                    onchange: (e) => { E.autoFace = e.target.checked; } }),
      ' 跟随视角（放置时自动定向）'));
    // 全部属性值的读数（水没水、shape 等）—— 下面「当前方块」的属性下拉才是编辑处
    const raw = Object.keys((E.currentBlock && E.currentBlock.properties) || {})
      .map((k) => `${k}=${propGet(k)}`).join(' · ');
    if (raw) box.append(el('div', { class: 'face-val' }, raw));
  }

  /** 世界方向 → 最近的 Minecraft 水平朝向 */
  function dirOf(dx, dz) {
    return Math.abs(dx) >= Math.abs(dz)
      ? (dx > 0 ? 'east' : 'west') : (dz > 0 ? 'south' : 'north');
  }

  /** 按「跟随视角」算出该方块应有的朝向属性（只改当前状态里已有的属性）
   *
   * 只认 ``E.state`` 里的方块名与属性：状态是“权威”的（脚本/工具/吸管都可以直接设），
   * 属性表只在方块名一致时才用来校验取值范围。
   */
  function autoProps(hit) {
    const out = {};
    const parsed = McStudio3D.parseState(E.state || '');
    const bare = String(parsed.name || '').replace('minecraft:', '');
    const cur = parsed.props || {};
    const cb = E.currentBlock && String(E.currentBlock.name || '').replace('minecraft:', '');
    const dom = (cb === bare) ? (E.currentBlock.properties || {}) : null;
    if (!dom) return out;                      // 属性表对不上 → 不猜
    const ok = (k, v) => !dom[k] || dom[k].includes(v);
    const dir = (E.viewer && E.viewer.viewDir) ? E.viewer.viewDir() : null;
    const n = (hit && hit.normal) || null;
    if (dom.facing && 'facing' in cur) {
      const wall = WALL_MOUNTED.test(bare);
      if (wall && n && (n[0] || n[1] || n[2])) {
        const want = n[1] ? (n[1] > 0 ? 'up' : 'down') : dirOf(n[0], n[2]);
        out.facing = ok('facing', want) ? want : dirOf(n[0], n[2]);
      } else if (dir) {
        const flip = FACES_PLAYER.test(bare);          // 容器/机关朝向放置者
        if (dom.facing.includes('up') && Math.abs(dir[1]) > 0.85) {
          out.facing = flip === (dir[1] > 0) ? 'down' : 'up';
        } else {
          const h = dirOf(flip ? -dir[0] : dir[0], flip ? -dir[2] : dir[2]);
          out.facing = ok('facing', h) ? h : dirOf(dir[0], dir[2]);
        }
      }
      if (out.facing && !ok('facing', out.facing)) delete out.facing;
    }
    if (dom.rotation && 'rotation' in cur && dir) {
      // 正面朝放置者 → 取视线反向；0=南，顺时针 22.5°/档
      const th = Math.atan2(dir[0], -dir[2]);
      let r = Math.round(th / (Math.PI / 8)) % 16;
      if (r < 0) r += 16;
      out.rotation = String(r);
    }
    if (dom.axis && 'axis' in cur && n && (n[0] || n[1] || n[2])) {
      const a = Math.abs(n[1]) ? 'y' : (Math.abs(n[0]) ? 'x' : 'z');
      if (ok('axis', a)) out.axis = a;
    }
    return out;
  }

  /** 本次放置真正用的状态：开着「跟随视角」且方块支持朝向时按视角定向。
   *
   * 直接在 ``E.state`` 字符串上改属性（不用属性选择器重建），因此不会覆盖
   * 手动/脚本设定的状态。
   */
  function stateForPlace(hit) {
    if (!E.autoFace) return E.state;
    const out = autoProps(hit);
    if (!Object.keys(out).length) return E.state;
    const parsed = McStudio3D.parseState(E.state || '');
    const props = Object.assign({}, parsed.props, out);
    const body = Object.keys(props).sort().map((k) => `${k}=${props[k]}`).join(',');
    const st = body ? `${parsed.name}[${body}]` : parsed.name;
    E.state = st;
    $('#current-state').textContent = st;
    syncPropsUI(props);                        // 按钮/下拉跟着变，看得见刚放的朝向
    return st;
  }

  /** R / Shift+R：手动把朝向转一格（facing 90°、rotation 45°） */
  function rotateFacing(step) {
    const facing = propValues('facing');
    if (facing) {
      const vals = ['north', 'east', 'south', 'west'].filter((d) => facing.includes(d));
      if (vals.length) {
        const i = vals.indexOf(propGet('facing'));
        manualFace();
        propSet('facing', vals[((i < 0 ? 0 : i) + step + vals.length * 2) % vals.length]);
        return true;
      }
    }
    if (propValues('rotation')) {
      const cur = Number(propGet('rotation') || 0);
      manualFace();
      propSet('rotation', String((cur + step * 2 + 32) % 16));
      return true;
    }
    const axis = propValues('axis');
    if (axis) {
      const i = axis.indexOf(propGet('axis'));
      manualFace();
      propSet('axis', axis[((i < 0 ? 0 : i) + step + axis.length * 2) % axis.length]);
      return true;
    }
    return false;
  }

  // ---------------------------------------------------------------- 掩码下拉
  // 与其它选择栏同一套质感（原来是原生 datalist，弹出来是系统浅色列表）
  const MASK_PRESETS = ['solid', 'surface', 'air', '!solid', 'solid & y<64',
                        'near(air)', 'above(stone)', 'neighbor(oak*)', 'oak*',
                        '(y-4)%8==0', 'y%2==0', 'x>z', 'random(0.3)', 'sky'];
  const MASK_HELP = [
    ['solid', '实体方块'], ['air', '空气'], ['surface', '表面'],
    ['sky', '见天'], ['above(stone)', '上方是石头'], ['near(air)', '附近有空气'],
    ['oak*', '方块名通配'], ['(y-4)%8==0', '坐标算术'], ['random(0.3)', '随机 30%'],
    ['a & b', '与'], ['a | b', '或'], ['!a', '非'],
  ];

  function maskDrop() { return $('#ax-mask-drop'); }

  function renderMaskDrop(open) {
    const drop = maskDrop();
    const inp = $('#ax-mask');
    if (!drop || !inp) return;
    if (!open) { drop.classList.add('hidden'); return; }
    const q = inp.value.trim().toLowerCase();
    drop.innerHTML = '';
    const items = MASK_PRESETS.filter((m) => !q || m.toLowerCase().includes(q));
    for (const m of items) {
      drop.append(el('button', { class: 'it', type: 'button',
        onclick: () => { inp.value = m; drop.classList.add('hidden'); inp.focus(); } }, m));
    }
    if (!items.length) {
      drop.append(el('div', { class: 'it dim' }, '没有匹配的预设——直接写表达式即可'));
    }
    drop.append(el('div', { class: 'it dim' }, '—— 语法 ——'));
    for (const [s, hint] of MASK_HELP) {
      drop.append(el('button', { class: 'it', type: 'button',
        onclick: () => { inp.value = s; drop.classList.add('hidden'); inp.focus(); } },
        `${s}   ${hint}`));
    }
    drop.classList.remove('hidden');
  }

  function bindMaskDrop() {
    const inp = $('#ax-mask');
    const btn = $('#ax-mask-btn');
    if (!inp || !btn) return;
    inp.addEventListener('focus', () => renderMaskDrop(true));
    inp.addEventListener('input', () => renderMaskDrop(true));
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      renderMaskDrop(maskDrop().classList.contains('hidden'));
    });
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { maskDrop().classList.add('hidden'); inp.blur(); }
    });
    document.addEventListener('pointerdown', (e) => {
      const drop = maskDrop();
      if (!drop || drop.classList.contains('hidden')) return;
      if (!drop.contains(e.target) && e.target !== inp && e.target !== btn) {
        drop.classList.add('hidden');
      }
    });
  }

  // ---------------------------------------------------------------- 2D layer
  function layerCanvas() { return $('#layer2d'); }

  // ---------------------------------------------------------------- 2D 底图（位图）
  // 旧写法是**每格一次 fillStyle + fillRect**（枪之恶魔 383x247 = 9.4 万次/重绘，
  // 涂一笔就要重画一次）。这里换成「一格一像素写进 ImageData → 一次性最近邻放大」，
  // 像素结果与原来一致（都是整格纯色块），但每次都只写 sx*sz 个 Uint32。
  const MAP2D = { img: null, w: 0, h: 0, off: null, offCtx: null };

  /** 打包成像素：小端机器上 Uint32 的字节序是 [LSB..MSB]，
   *  要让 ImageData 拿到 [R,G,B,A]，值必须是 A<<24 | B<<16 | G<<8 | R。 */
  function rgba32(r, g, b) {
    return ((255 << 24) | ((b & 255) << 16) | ((g & 255) << 8) | (r & 255)) >>> 0;
  }
  const MAP2D_AIR = rgba32(0x14, 0x18, 0x1e);       // 空气：同旧写的 '#14181e'
  const MAP2D_FALLBACK = rgba32(0x88, 0x88, 0x88); // 认不出来：同旧写的 '#888'

  /** CSS 颜色 → 打包像素（只认 #rgb / #rrggbb / rgb()；认不出来给灰，与旧代码兜底一致）。 */
  function cssToPx(css) {
    const s = String(css == null ? '' : css).trim();
    if (s[0] === '#') {
      let hex = s.slice(1);
      if (hex.length === 3) hex = hex[0] + hex[0] + hex[1] + hex[1] + hex[2] + hex[2];
      if (hex.length >= 6) {
        const r = parseInt(hex.slice(0, 2), 16);
        const g = parseInt(hex.slice(2, 4), 16);
        const b = parseInt(hex.slice(4, 6), 16);
        if (r === r && g === g && b === b) return rgba32(r, g, b);
      }
      return MAP2D_FALLBACK;
    }
    const m = /^rgba?\(([^)]+)\)$/.exec(s);
    if (m) {
      const p = m[1].split(',').map((v) => parseInt(parseFloat(v) || 0, 10));
      return rgba32(p[0] || 0, p[1] || 0, p[2] || 0);
    }
    return MAP2D_FALLBACK;
  }

  /** 复用同一块 sx x sz 的 ImageData（尺寸变了才重建）。 */
  function map2DImage(sx, sz) {
    const m = MAP2D;
    if (!m.img || m.w !== sx || m.h !== sz) {
      m.img = (typeof ImageData === 'function')
        ? new ImageData(sx, sz)
        : document.createElement('canvas').getContext('2d').createImageData(sx, sz);
      m.w = sx;
      m.h = sz;
    }
    return m.img;
  }

  /** 1:1 的离屏画布（putImageData 的落点，再放大贴到可见画布）。 */
  function map2DOffscreen(sx, sz) {
    const m = MAP2D;
    if (!m.off || m.off.width !== sx || m.off.height !== sz) {
      m.off = document.createElement('canvas');
      m.off.width = sx;
      m.off.height = sz;
      m.offCtx = m.off.getContext('2d');
    }
    return m;
  }

  function render2D() {
    const canvas = layerCanvas();
    if (!canvas) return;
    if (!E.st) return;
    const [sx, sy, sz] = E.st.size;
    const z = E.zoom;
    const w = Math.max(80, Math.round(sx * z));
    const h = Math.max(80, Math.round(sz * z));
    canvas.width = w * 2;
    canvas.height = h * 2;
    canvas.style.width = w + 'px';
    canvas.style.height = h + 'px';
    const ctx = canvas.getContext('2d');
    ctx.setTransform(2, 0, 0, 2, 0, 0);
    ctx.fillStyle = '#0b0d10';
    ctx.fillRect(0, 0, w, h);
    const y = E.layer;
    // 底图：调色板 → 像素（每次重建 —— 只有几十~几百项，且不会有「忘了失效」的缓存 bug）
    const lut = new Uint32Array(E.palette.length);
    for (let i = 1; i < E.palette.length; i++) lut[i] = cssToPx(E.colors[E.palette[i]]);
    const img = map2DImage(sx, sz);
    const px = new Uint32Array(img.data.buffer);
    const plane = y * sz * sx;
    for (let cz = 0; cz < sz; cz++) {
      const src = plane + cz * sx;
      const dst = cz * sx;
      for (let cx = 0; cx < sx; cx++) {
        const idx = E.voxels[src + cx];
        px[dst + cx] = idx === 0 ? MAP2D_AIR
          : (idx < lut.length ? lut[idx] : MAP2D_FALLBACK);
      }
    }
    const off = map2DOffscreen(sx, sz);
    off.offCtx.putImageData(img, 0, 0);
    ctx.save();
    ctx.imageSmoothingEnabled = false;          // 最近邻：每格还是干净的纯色方块
    ctx.drawImage(off.off, 0, 0, sx, sz, 0, 0, sx * z, sz * z);
    ctx.restore();
    if (z >= 4) {
      ctx.strokeStyle = 'rgba(255,255,255,.08)';
      ctx.lineWidth = 1;
      ctx.beginPath();                          // 一次建路径再一次 stroke（原来每线一次）
      for (let x = 0; x <= sx; x++) { ctx.moveTo(x * z, 0); ctx.lineTo(x * z, h); }
      for (let zz = 0; zz <= sz; zz++) { ctx.moveTo(0, zz * z); ctx.lineTo(w, zz * z); }
      ctx.stroke();
    }
    // 画布框（保存时按它裁剪）：框外压暗 + 淡灰虚线（比框选淡 —— 两者一眼分得清）
    const [fx, fy, fz] = frameOf();
    const framed = fx < sx || fz < sz;
    if (framed) {
      ctx.fillStyle = 'rgba(6,8,12,.55)';
      if (fx < sx) ctx.fillRect(fx * z, 0, (sx - fx) * z, h);
      if (fz < sz) ctx.fillRect(0, fz * z, Math.min(fx, sx) * z, (sz - fz) * z);
      ctx.save();
      ctx.setLineDash([5, 4]);
      ctx.strokeStyle = 'rgba(214,224,236,.85)';    // 画布框：浅灰
      ctx.lineWidth = 1;
      ctx.strokeRect(0.5, 0.5, fx * z - 1, fz * z - 1);
      ctx.restore();
    }
    // 框选（XZ 投影）：深蓝实线，比画布框深、比模块脚框（橙/蓝）重
    if (AX.sel && (E.tool === 'select' || E.tool === 'ax')) {
      const s = AX.sel;
      const bx = s[0] * z, bz = s[2] * z;
      const bw = (s[3] - s[0] + 1) * z, bh = (s[5] - s[2] + 1) * z;
      ctx.fillStyle = 'rgba(24,74,178,.20)';
      ctx.fillRect(bx, bz, bw, bh);
      ctx.strokeStyle = '#1e4fb4';               // 框选：深蓝
      ctx.lineWidth = 2;
      ctx.strokeRect(bx + 1, bz + 1, Math.max(1, bw - 2), Math.max(1, bh - 2));
    }
    // 装配模块脚框（XZ 投影）—— 只在移动/复制时画，与 3D 叠加层同一口径
    for (const p of moduleOverlayOn() ? E.placements : []) {
      const b = p.bbox;
      const rx0 = b[0] * z, rx1 = (b[3] + 1) * z;
      const rz0 = b[2] * z, rz1 = (b[5] + 1) * z;
      const sel = p.pid === E.selectedPid;
      if (sel) {
        ctx.fillStyle = 'rgba(255,158,51,.18)';
        ctx.fillRect(rx0, rz0, rx1 - rx0, rz1 - rz0);
      }
      ctx.strokeStyle = sel ? '#ff9e33' : '#4da0ff';
      ctx.lineWidth = sel ? 2 : 1;
      ctx.strokeRect(rx0, rz0, rx1 - rx0, rz1 - rz0);
      if (z >= 6) {
        ctx.fillStyle = sel ? '#ffce9a' : '#bcdcff';
        ctx.font = '10px sans-serif';
        ctx.fillText(p.id.slice(0, 12), rx0 + 2, rz0 + 11);
      }
    }
    $('#layer2d-info').textContent = `Y=${y} · ${sx}×${sz} · ${z}px/格`
      + (framed ? ` · 画布框 ${fx}×${fz}` : '')
      + (framed && E.st.outside ? ` · 框外 ${E.st.outside} 块` : '');
  }

  function canvasCell(ev) {
    const canvas = layerCanvas();
    const rect = canvas.getBoundingClientRect();
    const x = Math.floor((ev.clientX - rect.left) / E.zoom);
    const z = Math.floor((ev.clientY - rect.top) / E.zoom);
    const [sx, sy, sz] = E.st.size;
    if (x < 0 || z < 0 || x >= sx || z >= sz) return null;
    return { x, z, y: E.layer };
  }

  function apply2D(ev, erase) {
    if (!E.st) return;
    if (REGION_TOOLS.has(E.tool) || E.tool === 'select') return;  // 这两类在 2D 只管选中/拖脚框
    const cell = canvasCell(ev);
    if (!cell) return;
    const { x, z, y } = cell;
    const idx = E.voxels[(y * E.st.size[2] + z) * E.st.size[0] + x];
    if (E.tool === 'replace') {
      if (idx === 0) return;
      const from = E.palette[idx];
      const scope = E.scope === 'layer' ? { box: [0, y, 0, E.st.size[0] - 1, y, E.st.size[2] - 1] } : {};
      E.pending.push(Object.assign({ type: 'replace', from, to: E.state }, scope));
      localReplace(from, E.state, E.scope === 'layer' ? y : null);
      render2D();
      E.viewer._scheduleRebuild();
      return;
    }
    const wantErase = (erase && ERASE_TOOLS.has(E.tool)) || E.tool === 'erase';
    const cur = idx === 0 ? 'minecraft:air' : E.palette[idx];
    const want = wantErase ? 'minecraft:air' : stateForPlace(null);
    if (cur === want) return;
    E.pending.push({ type: 'set', x, y, z, state: want });
    E.voxels[(y * E.st.size[2] + z) * E.st.size[0] + x] = idxOfLocal(want);
    render2D();
    E.viewer.setVoxel(x, y, z, idxOfLocal(want));
  }

  function idxOfLocal(state) {
    let i = E.palette.indexOf(state);
    if (i < 0) { E.palette.push(state); i = E.palette.length - 1; }
    return i;
  }

  function localReplace(from, to, onlyY) {
    const [sx, sy, sz] = E.st.size;
    const fromIdx = E.palette.indexOf(from);
    const toIdx = idxOfLocal(to);
    if (fromIdx < 0) return;
    for (let y = 0; y < sy; y++) {
      if (onlyY !== null && y !== onlyY) continue;
      for (let z = 0; z < sz; z++) {
        for (let x = 0; x < sx; x++) {
          const p = (y * sz + z) * sx + x;
          if (E.voxels[p] === fromIdx) E.voxels[p] = toIdx;
        }
      }
    }
  }

  // ---------------------------------------------------------------- 3D edit
  /** 右键=擦除只对「笔刷类」工具生效；移动/吸管/框选等工具右键不该改方块。 */
  const ERASE_TOOLS = new Set(['place', 'erase', 'replace']);

  function onPaint(ev, erase) {
    if (!E.st) return;
    if (!ERASE_TOOLS.has(E.tool)) erase = false;   // 非笔刷工具：右键不改方块
    if (E.tool === 'ax' || E.tool === 'select') {
      axPointerDown(ev, erase);
      return;
    }
    if (REGION_TOOLS.has(E.tool)) {         // 移动 / 复制：都走选区手柄 + 三箭头
      if (erase) return;                    // 右键：不动方块
      regionToolDown(ev);
      return;
    }
    if (E.tool === 'port') {                 // 接口工具：点格记角，不动方块
      portToolPick(ev, erase);
      return;
    }
    E.painting = true;
    let hit = E.viewer.pick(ev);
    if (!hit && (E.tool === 'place' || E.tool === 'replace')) {
      // 空画布：没有任何实心块可打——退回到“射线进入画布的第一格”
      hit = E.viewer.firstInBox ? E.viewer.firstInBox(ev) : null;
    }
    if (!hit) return;
    const [x, y, z] = hit.cell;      // cell 是数组：不能用对象解构（会全 undefined）
    if (E.tool === 'replace') {
      const from = E.palette[hit.stateIndex];
      const scope = E.scope === 'layer' ? { box: [0, y, 0, E.st.size[0] - 1, y, E.st.size[2] - 1] } : {};
      E.pending.push(Object.assign({ type: 'replace', from, to: E.state }, scope));
      localReplace(from, E.state, E.scope === 'layer' ? y : null);
      render2D();
      E.viewer._scheduleRebuild();
      return;
    }
    const wantErase = erase || E.tool === 'erase';
    if (wantErase) setCell(x, y, z, 'minecraft:air');
    else placeCell(hit);
  }

  function placeCell(hit) {
    const p = hit.place;
    if (p[0] < 0 || p[1] < 0 || p[2] < 0) {
      // 画布原点固定在 (0,0,0)：这个方向没有格子可以放。别静默丢弃（用户会以为点空了），
      // 也别把负坐标发给服务端（旧行为：报「空选区」）。
      const axes = ['X', 'Y', 'Z'].filter((_, i) => p[i] < 0).join('/');
      status(`该面朝画布外侧（原点固定在 0,0,0）：−${axes} 方向没有格子，放不上去——` +
        '换个面放置即可');
      return;
    }
    setCell(p[0], p[1], p[2], stateForPlace(hit));
  }

  function setCell(x, y, z, state) {
    const [sx, sy, sz] = E.st.size;
    if (x < 0 || y < 0 || z < 0) return;      // 原点固定在 (0,0,0)
    if (x >= sx || y >= sy || z >= sz) {
      // 越界：本地镜像装不下，交给服务端自动扩容，flush 后整幅重载
      E.pending.push({ type: 'set', x, y, z, state });
      status('已放置：画布将自动扩容…');
      return;
    }
    const p = (y * sz + z) * sx + x;
    const cur = E.voxels[p];
    const curState = cur === 0 ? 'minecraft:air' : E.palette[cur];
    if (curState === state) return;
    E.pending.push({ type: 'set', x, y, z, state });
    const idx = idxOfLocal(state);
    E.voxels[p] = idx;
    E.viewer.setVoxel(x, y, z, idx);
  }

  function applyPick(hit) {
    // 中键单击 / 脚本用：把当前方块换成指到的那个（含状态）。
    // （原来的「吸管」工具已下线，但这个能力保留——它不属于框选那一套。）
    if (hit.stateIndex > 0) setState(E.palette[hit.stateIndex]);
  }

  // ================================================================== 选区变换
  // 「移动 / 复制」的交互（对齐 Axiom 的手感）：
  //   1) 用「框选」选一段（3D 点两个对角方块 / 2D 俯视图直接拖框）
  //   2) 点「移动」或「复制」→ 选区中心出现一个**半透明小立方体**
  //   3) 点那个小立方体 → 出现三根箭头（像模块 gizmo）
  //   4) 拖箭头平移 → 松手提交（一步一个撤销步）
  // 没框选时同一工具就是「模块模式」：点模块中心的小立方体选中模块。

  /** 退出选区变换模式（收起手柄与箭头，不清框选）。 */
  function clearRegion() {
    E.region = null;
    if (E.viewer) {
      E.viewer.setRegionHandle(null);
      if (!E.selectedPid || !E.viewer.gizmo) E.viewer.setGizmo(null);
    }
    updateModuleVisuals();
  }

  /** 进入「移动 / 复制」：有框选 → 建选区手柄；没框选 → 纯模块模式。 */
  function regionToolEnter(mode) {
    if (!E.st) return;
    const sel = AX.sel;
    const verb = mode === 'copy' ? '复制' : '移动';
    if (!sel) {
      clearRegion();
      status(`${verb}：现在没框选 → 点模块中心的小方块选中模块再拖；` +
        '想移动/复制一段体素，先切「框选」选一段再回来点这个工具');
      return;
    }
    E.region = { box: axNormalize(sel), mode, armed: false,
                 delta: [0, 0, 0], dragging: null };
    syncRegionVisuals();
    status(`${verb}：点选区中央的小方块 → 再拖三根箭头`);
  }

  /** 把选区手柄 / 三箭头 / 目标预览同步给 3D 视口。 */
  function syncRegionVisuals() {
    const v = E.viewer;
    if (!v) return;
    const r = E.region;
    if (!r) { v.setRegionHandle(null); return; }
    const col = REGION_COLOR[r.mode] || REGION_COLOR.move;
    v.setRegionHandle({ box: r.box, mode: r.mode, armed: r.armed,
                        delta: r.delta, color: col });
    if (r.armed) {
      const b = r.box;
      v.setGizmo({
        center: [(b[0] + b[3] + 1) / 2, (b[1] + b[4] + 1) / 2,
                 (b[2] + b[5] + 1) / 2],
        size: [b[3] - b[0] + 1, b[4] - b[1] + 1, b[5] - b[2] + 1],
        box: b, arrowsOnly: true, color: col,
      });
    } else {
      v.setGizmo(null);
    }
  }

  /** 「点中央小立方体」→ 装三箭头。 */
  function armRegion() {
    if (!E.region) return;
    E.region.armed = true;
    E.region.delta = [0, 0, 0];
    syncRegionVisuals();
    const s = E.region.box;
    status(`${E.region.mode === 'copy' ? '复制' : '移动'}选区 ` +
      `${s[3] - s[0] + 1}×${s[4] - s[1] + 1}×${s[5] - s[2] + 1}` +
      '：拖 X/Y/Z 箭头平移（松手生效）');
  }

  /** 3D 点击分派（移动/复制共用）：三箭头 → 选区手柄 → 模块手柄。 */
  function regionToolDown(ev) {
    if (!E.viewer) return;
    const g = E.viewer.pickGizmo(ev);
    if (g) {
      if (E.region && E.region.armed) startRegionDrag(ev, g);
      else startGizmoDrag(ev, g);
      return;
    }
    if (E.region && !E.region.armed && E.viewer.pickRegionHandle(ev)) {
      armRegion();
      return;
    }
    const mh = E.viewer.pickModuleHandle(ev);
    if (mh) {
      if (E.region) clearRegion();
      selectPlacement(mh.pid);
      return;
    }
  }

  function startRegionDrag(ev, hit) {
    const r = E.region;
    if (!r) return;
    r.dragging = { axis: hit.axis, dir: hit.dir, startS: hit.s,
                   startCenter: E.viewer.gizmo.center.slice(), last: 0 };
    E.viewer.setHover(null);
  }

  function onRegionMove(ev) {
    const r = E.region;
    const d = r && r.dragging;
    if (!d) return;
    const s = E.viewer.gizmoAxisParam(ev, d.axis, d.startCenter);
    if (s === null || s === undefined) return;
    const delta = Math.round(s - d.startS);
    if (delta === d.last) return;
    d.last = delta;
    r.delta = [d.dir[0] * delta, d.dir[1] * delta, d.dir[2] * delta];
    syncRegionVisuals();
    const axes = ['X', 'Y', 'Z'].filter((_, i) => r.delta[i]);
    const sign = axes.map((a) => {
      const i = 'XYZ'.indexOf(a);
      return (r.delta[i] > 0 ? '+' : '−') + a + Math.abs(r.delta[i]);
    });
    status(`${r.mode === 'copy' ? '复制' : '移动'} ${sign.join(' ')}`);
  }

  /** 松手提交：一个 step 一个撤销步。 */
  async function commitRegion() {
    const r = E.region;
    if (!r) return;
    r.dragging = null;
    const delta = r.delta.slice();
    if (!delta.some((v) => v)) { syncRegionVisuals(); return; }
    const op = { type: 'region', mode: r.mode, box: r.box.slice(), delta };
    r.delta = [0, 0, 0];
    syncRegionVisuals();
    status(`${r.mode === 'copy' ? '复制' : '移动'}中…`);
    try {
      const res = await App.post(`/api/structure/${E.st.sid}/ops`,
        { ops: [op], update: E.update });
      await afterRegionOp(res, op);
    } catch (e) {
      App.toast(e.message, 'err');
      updateStatus();
    }
  }

  /** 选区变换后的收尾：同步镜像 / 调色板 / 计数 / 历史，并让选区跟着走。
   *
   * 移动后把选区框也平移过去（可以连着推第二次）；复制则留在原处。
   */
  async function afterRegionOp(res, op) {
    if (!res || !res.bbox) return;
    if (res.resized) {
      if (res.palette) { E.palette = res.palette; await refreshPaletteInfo(); }
      E.st.dirty = true;
      await reloadAfterResize(res);
      await refreshHistory();
      status('已提交（数据范围随之扩容）');
      return;
    }
    await applyPaletteStates(res.palette);
    E.viewer.patchRegion(res.bbox, res.region);
    patchLocal(res.bbox, res.region);
    E.st.dirty = res.dirty !== false;
    E.st.can_undo = res.can_undo;
    E.st.can_redo = res.can_redo;
    if (res.frame) E.st.frame = res.frame.slice();
    if (E.region) {
      if (op.mode === 'move') {
        const d = op.delta;
        E.region.box = [op.box[0] + d[0], op.box[1] + d[1], op.box[2] + d[2],
                        op.box[3] + d[0], op.box[4] + d[1], op.box[5] + d[2]];
      }
      AX.sel = E.region.box.slice();      // 选区跟着内容走，便于连续操作
      axRenderSel();
      axUpdateOverlay();
      syncRegionVisuals();
    }
    refreshCounts();
    updateFrameOverlay();
    syncSizeSliders();
    renderPalette();
    render2D();
    await refreshHistory();
    updateStatus();
    const n = op.mode === 'copy' ? '复制' : '移动';
    status(`${n}完成（${op.delta.map((v) => (v > 0 ? '+' : '') + v).join(', ')}）`);
    if (res.warnings && res.warnings.length) {
      App.toast(res.warnings.join('；'), 'warn', 7000);
    }
  }

  function onHover(ev) {
    if (!E.st) return;
    // 三箭头悬停高亮：模块 gizmo（选中模块）与选区 gizmo（已武装的选区）共用
    if (E.viewer && (E.selectedPid || (E.region && E.region.armed))) {
      E.viewer.setHover(E.viewer.pickGizmo(ev));
    }
    updateCursor(ev);
    const hit = E.viewer.pick(ev);
    $('#view-info').textContent = hit
      ? `(${hit.cell.join(', ')}) ${E.palette[hit.stateIndex]}`
      : '';
  }

  async function flushOps() {
    E.painting = false;
    if (!E.pending.length) return;
    const ops = E.pending;
    E.pending = [];
    try {
      const r = await App.post(`/api/structure/${E.st.sid}/ops`, { ops, update: E.update });
      if (r.resized) {
        // 画布被扩容：整幅重载（本地镜像尺寸已变）
        if (r.palette) { E.palette = r.palette; await refreshPaletteInfo(); }
        E.st.dirty = true;
        await reloadAfterResize(r);
        App.toast(`画布已扩展到 ${(r.size || []).join('×')}`, 'ok');
        return;
      }
      await applyPaletteStates(r.palette);
      if (r.bbox && r.region) {
        E.viewer.patchRegion(r.bbox, r.region);
        patchLocal(r.bbox, r.region);
      }
      E.st.dirty = r.dirty !== false;
      E.st.can_undo = r.can_undo;
      E.st.can_redo = r.can_redo;
      refreshCounts();
      updateStatus();
      renderPalette();
      render2D();
      if (r.punched > 0) {
        App.toast(`压在模块上的 ${r.punched} 格已就地改在模块实例上（跟着模块走）`,
                 'ok');
      }
      if (r.placements) {
        E.placements = r.placements;
        renderInstances();
        updateModuleVisuals();
      }
      renderFaceCtl();          // 一笔画完同步朝向按钮（涂抹时每格重建 DOM 太浪费）
      if (r.resized) await reloadAfterResize(r);
    } catch (e) {
      App.toast('编辑失败: ' + e.message, 'err');
    }
  }

  function countBlocks() {
    let n = 0;
    for (let i = 0; i < E.voxels.length; i++) if (E.voxels[i]) n++;
    return n;
  }

  /** 画布框内的方块数（框 == 数据范围时不做扫描）。 */
  function countInsideFrame() {
    const [sx, sy, sz] = E.st.size;
    const [fx, fy, fz] = frameOf();
    if (fx >= sx && fy >= sy && fz >= sz) return E.st.blocks || 0;
    let n = 0;
    for (let y = 0; y < fy; y++) {
      for (let z = 0; z < fz; z++) {
        const base = (y * sz + z) * sx;
        for (let x = 0; x < fx; x++) if (E.voxels[base + x]) n++;
      }
    }
    return n;
  }

  /** 重算总格数与「框外格数」（框外的东西保存时会被裁掉）。 */
  function refreshCounts() {
    if (!E.st) return;
    E.st.blocks = countBlocks();
    E.st.outside = Math.max(0, E.st.blocks - countInsideFrame());
  }

  // ---------------------------------------------------------------- save
  async function save() {
    if (!E.st) return;
    if (!E.st.path) { saveAsDialog(); return; }   // 新建的空画布没有路径 → 另存为
    try {
      const r = await App.post(`/api/structure/${E.st.sid}/save`, {});
      E.st.dirty = false;
      E.st.path = r.rel || r.path;
      if (r.frame) { E.st.frame = r.frame.slice(); syncSizeSliders(); updateFrameOverlay(); }
      refreshCounts();
      updateStatus();
      render2D();
      App.toast('已保存 ' + (r.rel || r.path) +
        (r.cropped ? `（按画布框 ${(r.frame || []).join('×')} 裁掉框外 ${r.cropped.toLocaleString()} 格：会话里还留着，可继续编辑）` : '') +
        (r.spec && r.spec_grid_from && String(r.spec_grid_from) !== String(r.spec_grid)
          ? `（已同步 ${String(r.spec).split(/[\\/]/).pop()} 的画布尺寸 ${r.spec_grid.join('×')}）` : '') +
        (r.backup_skipped ? '（未留底：设置里关闭了备份）' : '') +
        (r.backup ? '（旧文件已备份）' : ''), 'ok', r.cropped ? 7000 : 4000);
      if (r.spec_warnings && r.spec_warnings.length) {
        App.toast('模块 spec 需要收尾：' + r.spec_warnings.join('；'), 'warn', 9000);
      }
      App.refreshOpenFiles();
      refreshModulePanel();          // 画布尺寸变了 → 面板里的尺寸/接口表跟着刷新
    } catch (e) { App.toast(e.message, 'err'); }
  }

  /** 保存/另存为/存为模块前的一句提醒：会按画布框裁剪。 */
  function cropHint() {
    if (!frameDiffers()) return null;
    const [fx, fy, fz] = frameOf();
    return el('div', { class: 'muted' },
      `会按画布框 ${fx}×${fy}×${fz} 裁剪：框外` +
      (E.st.outside ? ` ${E.st.outside.toLocaleString()} 块` : '的内容') +
      '不会写进文件（当前会话里仍保留，可以继续编辑）');
  }

  function saveAsDialog() {
    if (!E.st) return;
    const path = el('input', { value: E.st.path || '' });
    const fmt = el('select', {},
      el('option', { value: '.schem' }, '.schem (Sponge v2，推荐)'),
      el('option', { value: '.litematic' }, '.litematic (Litematica)'));
    App.modal('另存为', el('div', {},
      el('div', { class: 'form-row' }, el('label', {}, '路径'), path),
      el('div', { class: 'form-row' }, el('label', {}, '格式'), fmt),
      cropHint(),
      el('div', { class: 'muted' }, '路径相对仓库根目录，例如 packs/modern-arch/modules/custom/my_mod.schem')),
      [{ label: '取消' }, { label: '保存', class: 'primary', onClick: async () => {
        try {
          const r = await App.post(`/api/structure/${E.st.sid}/save-as`,
            { path: path.value, format: fmt.value });
          E.st.dirty = false;
          E.st.path = r.rel || r.path;
          updateStatus();
          App.toast('已保存 ' + (r.rel || r.path) +
            (r.cropped ? `（按画布框裁掉框外 ${r.cropped.toLocaleString()} 格）` : ''),
            'ok', r.cropped ? 7000 : 4000);
          App.refreshOpenFiles();
        } catch (e) { App.toast(e.message, 'err'); }
      } }]);
  }

  function saveModuleDialog() {
    if (!E.st) return;
    const pack = el('select');
    for (const p of App.state.packs) pack.append(el('option', { value: p.id }, p.name));
    const id = el('input', { value: E.st.name.replace(/[\\/:*?"<>|\s]+/g, '_') });
    const category = el('input', { value: 'custom' });
    const tags = el('input', { placeholder: '逗号分隔' });
    const desc = el('input', {});
    const box = el('div', {},
      el('div', { class: 'form-row' }, el('label', {}, '资产包'), pack),
      el('div', { class: 'form-row' }, el('label', {}, '模块 id'), id),
      el('div', { class: 'form-row' }, el('label', {}, '分类'), category),
      el('div', { class: 'form-row' }, el('label', {}, '标签'), tags),
      el('div', { class: 'form-row' }, el('label', {}, '描述'), desc),
      cropHint());
    App.modal('存为模块（写入 packs/<包>/modules/<分类>/）', box,
      [{ label: '取消' }, { label: '写入', class: 'primary', onClick: async () => {
        try {
          const r = await App.post(`/api/structure/${E.st.sid}/save-as-module`, {
            pack: pack.value, id: id.value, category: category.value,
            tags: tags.value, description: desc.value,
          });
          App.toast(`已写入模块 ${r.id} (${r.size.join('×')})`, 'ok');
          await App.refreshAll();
        } catch (e) { App.toast(e.message, 'err'); }
      } }]);
  }

  async function undo() {
    try {
      const r = await App.post(`/api/structure/${E.st.sid}/undo`, {});
      await afterHistory(r);
    } catch (e) { App.toast(e.message, 'err'); }
  }

  async function redo() {
    try {
      const r = await App.post(`/api/structure/${E.st.sid}/redo`, {});
      await afterHistory(r);
    } catch (e) { App.toast(e.message, 'err'); }
  }

  // ---------------------------------------------------------------- 操作日志
  // Axiom 式的 History 窗口：列出每步操作（含工具名），点任意一条回到那一步。
  E.hist = { undo: [], redo: [], cursor: 0, total: 0 };

  function histLabelOf(item) {
    const t = item && item.at ? new Date(item.at * 1000) : null;
    const hh = t ? String(t.getHours()).padStart(2, '0') : '';
    const mm = t ? String(t.getMinutes()).padStart(2, '0') : '';
    const ss = t ? String(t.getSeconds()).padStart(2, '0') : '';
    return `${hh}:${mm}:${ss}`;
  }

  async function refreshHistory() {
    if (!E.st || !E.st.sid) return;
    try {
      const d = await App.api(`/api/structure/${E.st.sid}/history`);
      E.hist = { undo: d.undo || [], redo: d.redo || [],
                 cursor: d.cursor || 0, total: d.total || 0 };
      renderHistory();
    } catch (e) {
      // 不打断编辑，但把原因留在控制台（以前静默失败过）
      console.warn('refreshHistory 失败：', e && e.message || e);
    }
  }

  function renderHistory() {
    const box = $('#hist-list');
    if (!box) return;
    box.innerHTML = '';
    const { undo, redo, cursor } = E.hist;
    const info = $('#hist-info');
    if (info) {
      info.textContent = E.hist.total
        ? `（${cursor}/${E.hist.total} 步）` : '（还没有操作）';
    }
    const uBtn = $('#hist-undo');
    const rBtn = $('#hist-redo');
    if (uBtn) uBtn.disabled = undo.length === 0;
    if (rBtn) rBtn.disabled = redo.length === 0;
    const row = (it, idx, undone) => el('div', {
      class: 'ax-hist-row' + (undone ? ' undone' : '')
        + (idx === cursor - 1 ? ' cur' : ''),
      title: it.cells ? `影响 ${it.cells.toLocaleString()} 格`
        : (it.to && it.to.length === 3 ? `画布框 → ${it.to.join('×')}（不裁数据）` : ''),
      onclick: () => histJump(idx + 1),
    }, el('span', { class: 'hi-n' }, String(idx + 1)),
       el('span', { class: 'hi-lb' }, it.label || 'op'),
       el('span', { class: 'hi-at' }, histLabelOf(it)),
       undone ? el('span', { class: 'hi-x' }, '已撤销') : null);
    if (!undo.length && !redo.length) {
      box.append(el('div', { class: 'ax-hist-empty' },
        '还没有操作。放置/擦除/工具/模块装配都会记在这里。'));
      return;
    }
    undo.forEach((it, i) => box.append(row(it, i, false)));
    redo.forEach((it, i) => box.append(row(it, undo.length + i, true)));
    const cur = box.querySelector('.ax-hist-row.cur');
    if (cur) cur.scrollIntoView({ block: 'nearest' });
  }

  async function histJump(index) {
    if (!E.st) return;
    if (index === E.hist.cursor) return;
    try {
      const r = await App.post(`/api/structure/${E.st.sid}/history/jump`,
                               { index });
      await afterHistory(r);
      status(`已回到第 ${index} 步`);
    } catch (e) { App.toast(e.message, 'err'); }
  }

  async function afterHistory(r) {
    await applyPaletteStates(r.palette);
    if (r.bbox && r.region) E.viewer.patchRegion(r.bbox, r.region);
    if (r.frame) {                     // 撤销/重做也可能改的是「画布框」
      E.st.frame = r.frame.slice();
      syncSizeSliders();
      updateFrameOverlay();
    }
    E.st.dirty = true;
    E.st.can_undo = r.can_undo;
    E.st.can_redo = r.can_redo;
    refreshCounts();
    updateStatus();
    renderPalette();
    render2D();
    await refreshHistory();
  }

  // ============================================================== Axiom 工具
  /** 工具面板状态：目录（后端下发）+ 选中工具 + 参数值 + 框选 + 笔刷。 */
  const AX = {
    groups: [], tools: {}, brushShapes: [], group: null, spec: null,
    values: {}, mask: '', seed: 0, query: '',
    brush: { shape: 'sphere', radius: 4 },
    sel: null, points: [], centers: [], stroking: false, last: null,
    pickA: null, dragSel: null,   // pickA = 框选的第一个角（框选工具左键直接开框）
  };

  // ---- 图标（内联 SVG，stroke=currentColor；Axiom 风：线条几何图标） ----
  const AX_GROUP_ICONS = {
    shape: '<path d="M12 3l7 4v10l-7 4-7-4V7z"/><path d="M12 12l7-4M12 12v9M12 12 5 8"/>',
    paint: '<path d="M5 20c0-3 1-4 3-5l7-7"/><path d="M13 4l6 6-3 3-6-6z"/><path d="M9 19H6"/>',
    deform: '<path d="M3 14c3-5 6 5 9 0s6-5 9 0"/><path d="M3 19h18"/>',
    solid: '<path d="M4 6h16v12H4z"/><path d="M4 12h16"/><path d="M9 6v12M15 6v12"/>',
    world: '<path d="M2 19h20"/><path d="M4 19l6-11 3 5 3-6 4 12"/>',
  };
  const AX_TOOL_ICONS = {
    shape: '<path d="M12 3l7 4v10l-7 4-7-4V7z"/><path d="M12 12l7-4M12 12v9M12 12 5 8"/>',
    path: '<path d="M3 18c4 0 5-12 9-12s5 8 9 8"/><circle cx="3" cy="18" r="1.6"/><circle cx="21" cy="14" r="1.6"/>',
    noise_painter: '<path d="M5 7h.01M9 4h.01M15 5h.01M19 8h.01M7 12h.01M12 10h.01M17 13h.01M4 17h.01M10 16h.01M14 19h.01M19 17h.01"/>',
    gradient_painter: '<path d="M4 5h16v4H4z"/><path d="M4 10h11v4H4z"/><path d="M4 15h6v4H4z"/>',
    painter: '<path d="M4 20c2 0 3-1 3-3l8-8"/><path d="M14 4l6 6-4 4-6-6z"/>',
    floodfill: '<path d="M5 11l7-7 6 6-7 7z"/><path d="M19 14c0 2 1 3 1 4a2 2 0 1 1-4 0c0-1 1-2 3-4z"/>',
    smooth: '<path d="M3 13c3-6 6 6 9 0s6-6 9 0"/><path d="M3 19h18"/>',
    rock: '<path d="M4 17l3-8 5-2 6 4-1 6z"/><path d="M7 9l3 4h5"/>',
    shatter: '<path d="M12 2v6M12 16v6M2 12h6M16 12h6M5 5l4 4M15 15l4 4M19 5l-4 4M9 15l-4 4"/>',
    melt: '<path d="M4 5h16"/><path d="M7 5v5c0 2 1 3 2 4s2 2 2 4M14 5v7c0 2 1 3 2 4"/>',
    roughen: '<path d="M3 19l3-9 3 6 3-13 3 11 3-6 3 11z"/>',
    distort: '<path d="M12 12a4 4 0 1 1 4 4 8 8 0 1 1-8-8"/>',
    weld: '<path d="M3 8h6v8H3zM15 8h6v8h-6z"/><path d="M9 12h6"/>',
    blend: '<circle cx="9.5" cy="12" r="5.5"/><circle cx="14.5" cy="12" r="5.5"/>',
    sculpt: '<path d="M14 3l7 7-8 8-7 2 2-7z"/><path d="M11 6l7 7"/>',
    fill: '<path d="M4 6h16v12H4z"/><path d="M4 12h16M12 6v12"/>',
    replace: '<path d="M4 8h11l-3-3m3 3-3 3"/><path d="M20 16H9l3-3m-3 3 3 3"/>',
    hollow: '<path d="M4 5h16v14H4z"/><path d="M8 9h8v6H8z"/>',
    grow: '<path d="M9 3H3v6M15 3h6v6M9 21H3v-6M15 21h6v-6"/><path d="M3 3l6 6M21 3l-6 6M3 21l6-6M21 21l-6-6"/>',
    gravity: '<path d="M12 3v11"/><path d="M7 10l5 5 5-5"/><path d="M4 21h16"/><path d="M8 6h.01M16 7h.01"/>',
    drain: '<path d="M12 3c3 4 5 6 5 9a5 5 0 0 1-10 0c0-3 2-5 5-9z"/><path d="M4 21h16"/>',
    autoshade: '<circle cx="12" cy="12" r="4"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2"/>',
    elevation: '<path d="M2 19h20"/><path d="M4 19l6-11 3 5 3-6 4 12"/>',
    flatten: '<path d="M3 12h18M3 17h18"/><path d="M12 3v6m0 0-3-3m3 3 3-3"/>',
    slope: '<path d="M3 20L21 4"/><path d="M3 20h18V4"/>',
    extrude: '<path d="M12 19V6"/><path d="M8 10l4-4 4 4"/><path d="M5 21h14"/>',
    stamp: '<path d="M8 3h8v4a3 3 0 0 0 1 2l1 1v3H6v-3l1-1a3 3 0 0 0 1-2z"/><path d="M5 20h14"/>',
  };
  const AX_BRUSH_ICONS = {
    sphere: '<circle cx="12" cy="12" r="7"/><path d="M5 12h14"/>',
    cube: '<path d="M5 5h14v14H5z"/>',
    cuboid: '<path d="M3 7h18v10H3z"/>',
    cylinder: '<path d="M6 7c0-2 12-2 12 0v10c0 2-12 2-12 0z"/><path d="M6 7c0 2 12 2 12 0"/>',
    cone: '<path d="M12 4l7 15H5z"/><path d="M8 19h8"/>',
    capsule: '<path d="M8 6h8a5 5 0 0 1 0 12H8a5 5 0 0 1 0-12z"/>',
    octahedron: '<path d="M12 3l7 9-7 9-7-9z"/><path d="M5 12h14"/>',
    disk: '<path d="M4 12c0-3 4-5 8-5s8 2 8 5-4 5-8 5-8-2-8-5z"/>',
    point: '<circle cx="12" cy="12" r="2.5"/>',
    supersphere: '<path d="M7 4h10a3 3 0 0 1 3 3v10a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3V7a3 3 0 0 1 3-3z"/>',
  };
  const AX_GROUP_SHORT = {
    shape: '形状', paint: '绘制', deform: '形变', solid: '体块', world: '地形',
  };
  const AX_ICON_FALLBACK =
    '<path d="M4 4h16v16H4z"/><path d="M8 8h8v8H8z"/>';

  function axIcon(map, key) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('aria-hidden', 'true');
    svg.innerHTML = map[key] || AX_ICON_FALLBACK;
    return svg;
  }

  function axEl(id) { return document.getElementById(id); }

  /** 笔刷形状：下拉（兼容旧入口）+ 图标槽位（Axiom 风）。 */
  function axRenderBrushShapes() {
    const sel = axEl('ax-brush-shape');
    const box = axEl('ax-brush-icons');
    if (!box) return;
    box.innerHTML = '';
    for (const s of AX.brushShapes) {
      box.append(el('button', {
        class: 'ax-shape' + (AX.brush.shape === s ? ' active' : ''),
        type: 'button', title: s,
        onclick: () => {
          AX.brush.shape = s;
          if (sel) sel.value = s;
          axRenderBrushShapes();
          axUpdateOverlay();
        },
      }, axIcon(AX_BRUSH_ICONS, s)));
    }
  }

  async function axInit() {
    if (!AX_PANEL_ENABLED) return;    // 面板下线：不去拉目录、不建 DOM
    try {
      const d = await App.api('/api/tools');
      AX.groups = d.groups || [];
      AX.brushShapes = d.brushShapes || ['sphere'];
      AX.tools = {};
      for (const g of AX.groups) for (const t of g.tools) AX.tools[t.id] = t;
      const sel = axEl('ax-brush-shape');
      if (sel) {
        sel.innerHTML = '';
        for (const s of AX.brushShapes) sel.append(el('option', { value: s }, s));
      }
      axRenderBrushShapes();
      const count = axEl('ax-count');
      if (count) count.textContent = `（${d.count || 0} 个）`;
      axRenderTabs();
      axRenderTools();
      // 刻意不自动选工具：保持「放置」模式，免得一点就把模型盖上基本体
      axStatus('点上面一个工具开始：笔刷类涂抹，sel 类框选后应用');
    } catch (e) {
      const box = axEl('ax-status');
      if (box) box.textContent = '工具目录加载失败：' + e.message;
    }
  }

  function axRenderTabs() {
    const tabs = axEl('ax-tabs');
    if (!tabs) return;
    tabs.innerHTML = '';
    for (const g of AX.groups) {
      tabs.append(el('button', {
        class: 'ax-tab' + (AX.group === g.id ? ' active' : ''),
        title: g.label,
        onclick: () => {
          AX.group = g.id;
          if (AX.query) {                       // 切分组 = 退出搜索
            AX.query = '';
            const q = axEl('ax-search');
            if (q) q.value = '';
          }
          axRenderTabs();
          axRenderTools();
        },
      }, axIcon(AX_GROUP_ICONS, g.id),
         el('span', {}, AX_GROUP_SHORT[g.id] || g.label)));
    }
    if (!AX.group) AX.group = AX.groups.length ? AX.groups[0].id : null;
  }

  function axRenderTools() {
    const box = axEl('ax-tools');
    if (!box) return;
    box.innerHTML = '';
    const q = (AX.query || '').trim().toLowerCase();
    let list, crossGroup = false;
    if (q) {                                  // 搜索：跨分组，只看得见命中的
      list = Object.values(AX.tools).filter((t) => (
        t.id.toLowerCase().includes(q) ||
        (t.label || '').toLowerCase().includes(q) ||
        (t.hint || '').toLowerCase().includes(q)));
      crossGroup = true;
    } else {
      const g = AX.groups.find((x) => x.id === AX.group);
      list = g ? g.tools : [];
    }
    if (!list.length) {
      box.append(el('div', { class: 'ax-note' },
        q ? `没有匹配「${AX.query}」的工具` : '这个分组没有工具'));
      return;
    }
    for (const t of list) {
      box.append(el('button', {
        class: 'ax-tool' + (AX.spec && AX.spec.id === t.id ? ' active' : ''),
        title: `${t.label}（${t.id}）` + (t.hint ? ' · ' + t.hint : '')
          + (crossGroup ? ` · ${t.groupLabel || t.group}` : ''),
        onclick: () => axSelectTool(t.id),
      }, axIcon(AX_TOOL_ICONS, t.id), el('span', {}, t.label)));
    }
  }

  /** 编辑工具与 AXIOM 工具**互斥**：选中会改方块的编辑工具 → 取消 AXIOM 工具。
   *
   * 「框选/选区」是两边共用的选区模式（AXIOM 的 sel 类工具需要它选范围），不算冲突。
   */
  function clearAxiomTool(reason) {
    if (!AX.spec && !AX.stroking) return;
    AX.spec = null;
    AX.stroking = false;
    AX.centers = [];
    AX.points = [];
    axRenderTools();
    axRenderParams();
    axStatus(reason || '已切回编辑工具（点上面一个工具重新选 AXIOM 工具）');
    axUpdateOverlay();
  }

  /** 选中 AXIOM 工具 → 接管 3D 左键，把编辑工具的外观/提示都收起来。 */
  function enterAxiomMode(t) {
    if (!AX_PANEL_ENABLED) return;    // 面板下线：不接受 AXIOM 工具接管 3D 左键
    E.tool = 'ax';
    document.querySelectorAll('#tool-buttons button').forEach(
      (x) => x.classList.toggle('active', x.dataset.tool === 'ax'));
    const hint = $('#edit-hint');
    if (hint) {
      hint.textContent = `AXIOM 工具「${t.label || t.id}」接管 3D 左键——` +
        '编辑工具已停用（按 1~6 或点上面的工具按钮切回）';
    }
    updatePlaceRow();
  }

  function axSelectTool(id) {
    const t = AX.tools[id];
    if (!t) return;
    AX.spec = t;
    if (!AX.values[id]) {
      AX.values[id] = {};
      for (const p of t.params || []) AX.values[id][p.name] = p.default;
    }
    // 进工具模式：3D 里涂抹/点击即执行该工具
    enterAxiomMode(t);
    axRenderTools();
    axRenderParams();
    axSetHint();
    axUpdateOverlay();
  }

  function axSetHint() {
    const t = AX.spec;
    const box = axEl('ax-hint');
    if (!t || !box) return;
    box.textContent = t.region === 'brush' ? '涂抹执行'
      : t.region === 'own' ? '点击定位后应用' : '框选后应用';
  }

  function axRenderParams() {
    const box = axEl('ax-params');
    if (!box) return;
    box.innerHTML = '';
    const t = AX.spec;
    if (!t) return;
    if (t.hint) box.append(el('div', { class: 'ax-note' }, t.hint));
    const vals = AX.values[t.id] || {};
    for (const p of t.params || []) {
      const v = vals[p.name] === undefined ? p.default : vals[p.name];
      const wide = p.type === 'blocks' || p.type === 'text';
      let rowClass = 'ax-param' + (wide ? ' wide' : '');
      let input, ctl = null;
      if (p.type === 'bool') {
        // Axiom 风：能力按钮——绿色=开 / 灰=关
        input = el('button', { class: 'ax-tgl' + (v ? ' on' : ''), type: 'button' },
                   v ? '✔ 开' : '✖ 关');
        input.addEventListener('click', () => {
          const on = !vals[p.name];
          vals[p.name] = on;
          input.classList.toggle('on', on);
          input.textContent = on ? '✔ 开' : '✖ 关';
        });
        rowClass += ' toggle';
      } else if (p.type === 'enum') {
        input = el('select');
        for (const o of p.options || []) {
          const lb = (p.labelOf && p.labelOf[o]) || o;
          input.append(el('option', { value: o }, lb));
        }
        input.value = v;
        input.addEventListener('change', () => { vals[p.name] = input.value; });
      } else if (p.type === 'int' || p.type === 'float') {
        const step = p.step || (p.type === 'int' ? 1 : 0.1);
        const hasRange = p.min !== undefined && p.max !== undefined;
        input = el('input', { type: 'number', step });
        if (p.min !== undefined) input.min = p.min;
        if (p.max !== undefined) input.max = p.max;
        input.value = v === undefined || v === null ? 0 : v;
        let range = null;
        const commit = (raw) => {
          const nv = p.type === 'int' ? Math.round(Number(raw) || 0)
            : Number(raw) || 0;
          vals[p.name] = nv;
          input.value = nv;
          if (range) range.value = nv;
        };
        input.addEventListener('change', () => commit(input.value));
        if (hasRange) {                       // 滑块（左）+ 数值盒（右）
          range = el('input', { type: 'range', min: p.min, max: p.max, step });
          range.value = input.value;
          range.addEventListener('input', () => commit(range.value));
          ctl = el('div', { class: 'ax-ctl' }, range, input);
        }
      } else {
        input = el('input', { type: 'text' });
        input.value = v === undefined || v === null ? '' : v;
        input.addEventListener('change', () => { vals[p.name] = input.value; });
      }
      const row = el('label', { class: rowClass, title: p.hint || '' },
                     el('span', {}, p.label), ctl || input);
      box.append(row);
    }
  }

  function axParams() {
    return AX.spec ? Object.assign({}, AX.values[AX.spec.id] || {}) : {};
  }

  function axBrushOn() { return !!(AX.spec && AX.spec.region === 'brush'); }
  function axSelOn() {
    return !!(AX.spec && (AX.spec.region === 'sel' || AX.spec.region === 'brush'));
  }

  function axCenter() {
    if (AX.points.length) return AX.points[AX.points.length - 1];
    if (!E.st) return [0, 0, 0];
    return [Math.floor(E.st.size[0] / 2), Math.floor(E.st.size[1] / 2),
            Math.floor(E.st.size[2] / 2)];
  }

  function axUpdateOverlay() {
    const v = E.viewer;
    if (!v) return;
    const o = {};
    if (AX.sel && (E.tool === 'ax' || E.tool === 'select')) o.sel = AX.sel;
    if (AX.points.length) o.points = AX.points;
    if (AX.last && axBrushOn()) {
      o.brush = { center: AX.last, radius: AX.brush.radius, shape: AX.brush.shape };
    }
    v.setOverlay(Object.keys(o).length ? o : null);
  }

  function axStatus(text) {
    const box = axEl('ax-status');
    if (box) box.textContent = text || '';
  }

  async function axApply(opts) {
    opts = opts || {};
    if (!E.st || !AX.spec) { App.toast('先打开一个结构', 'warn'); return; }
    const body = {
      tool: AX.spec.id,
      params: axParams(),
      brush: { shape: AX.brush.shape, radius: AX.brush.radius },
      block: E.state,
      mask: (axEl('ax-mask') && axEl('ax-mask').value.trim()) || '',
      seed: Number((axEl('ax-seed') && axEl('ax-seed').value) || 0),
      update: !!E.update,
    };
    const centers = opts.centers || [];
    if (centers.length) body.centers = centers;
    if (AX.spec.region === 'own') {
      if (AX.spec.id === 'path') {
        const pts = AX.points.length ? AX.points : [axCenter()];
        body.params = Object.assign(body.params, { points: pts });
      } else {
        body.centers = centers.length ? centers : [axCenter()];
      }
    } else if (opts.all) {
      body.sel = null;
    } else if (AX.sel) {
      body.sel = AX.sel;
    }
    if (AX.spec.region === 'sel' && !AX.sel && !opts.all) {
      App.toast(`${AX.spec.label} 需要先框选（或用「整张画布」）`, 'warn');
      return;
    }
    axStatus('执行中…');
    const drop = maskDrop();
    if (drop) drop.classList.add('hidden');    // 掩码下拉不要压着面板
    try {
      const r = await App.post(`/api/structure/${E.st.sid}/tool`, body);
      await axAfterTool(r);
    } catch (e) {
      axStatus('失败：' + e.message);
      App.toast(AX.spec.label + ' 失败: ' + e.message, 'err');
    }
  }

  async function axAfterTool(r) {
    await applyPaletteStates(r.palette);
    if (r.bbox && r.region) {
      E.viewer.patchRegion(r.bbox, r.region);
      patchLocal(r.bbox, r.region);
    }
    if (r.placements) { E.placements = r.placements; renderInstances(); }
    E.st.dirty = r.dirty !== false;
    E.st.can_undo = r.can_undo;
    E.st.can_redo = r.can_redo;
    refreshCounts();
    updateStatus();
    renderPalette();
    render2D();
    const notes = (r.notes || []).slice();
    if (notes.length) App.toast(notes.join('；'), 'warn', 6000);
    if (r.punched > 0) {
      App.toast(`压在模块上的 ${r.punched} 格已就地改在模块实例上（跟着模块走）`,
               'ok');
    }
    const txt = r.changed ? `${r.stats}（改 ${r.changed.toLocaleString()} 格）`
      : (r.stats || '没有改动');
    axStatus(txt);
    if (r.changed) App.toast(txt, 'ok', 4000);
  }

  // ------------------------------------------------------------ 选区
  function axNormalize(box) {
    if (!box) return null;
    const [a, b, c, d, e, f] = box.map((v) => Math.round(Number(v) || 0));
    return [Math.min(a, d), Math.min(b, e), Math.min(c, f),
            Math.max(a, d), Math.max(b, e), Math.max(c, f)];
  }

  function axSetSel(box, quiet) {
    const before = AX.sel ? AX.sel.join() : '';
    AX.sel = axNormalize(box);
    axRenderSel();
    axUpdateOverlay();
    // 2D 俯视图里也画框选（深蓝）——不然只有 3D 看得见，2D 拖框像没反应
    if ((AX.sel ? AX.sel.join() : '') !== before) render2D();
    // 重新框选 → 旧的选区手柄失效（框不在了，手柄就该收回去）
    if (E.region && (!E.region.box || !AX.sel ||
        E.region.box.join() !== AX.sel.join())) clearRegion();
    if (!quiet && AX.sel) {
      const s = AX.sel;
      axStatus(`选区 ${s[3] - s[0] + 1}×${s[4] - s[1] + 1}×${s[5] - s[2] + 1}`);
    }
  }

  function axRenderSel() {
    const ids = ['ax-sx0', 'ax-sy0', 'ax-sz0', 'ax-sx1', 'ax-sy1', 'ax-sz1'];
    ids.forEach((id, i) => {
      const n = axEl(id);
      if (n) n.value = AX.sel ? AX.sel[i] : '';
    });
    const info = axEl('ax-sel-info');
    if (info) {
      info.textContent = AX.sel
        ? `${AX.sel[3] - AX.sel[0] + 1}×${AX.sel[4] - AX.sel[1] + 1}×${AX.sel[5] - AX.sel[2] + 1}`
        : '未框选（未框选时 sel 类工具会要求框选）';
    }
  }

  function axReadSelInputs() {
    const ids = ['ax-sx0', 'ax-sy0', 'ax-sz0', 'ax-sx1', 'ax-sy1', 'ax-sz1'];
    const vals = ids.map((id) => {
      const n = axEl(id);
      return n ? Number(n.value) : 0;
    });
    if (vals.some((v) => !Number.isFinite(v))) return;
    axSetSel(vals, true);
  }

  async function axSelectAt(at, op) {
    if (!E.st) return;
    try {
      const r = await App.post(`/api/structure/${E.st.sid}/select`,
        { at, connected: op === 'connected' });
      if (!r.count) { App.toast('没选中任何方块', 'warn'); return; }
      axSetSel(r.bbox, true);
      App.toast(`${op === 'connected' ? '连通' : '同类'}选区 ${r.count.toLocaleString()} 格`,
        'ok');
    } catch (e) { App.toast('选择失败: ' + e.message, 'err'); }
  }

  async function axSelectByMask() {
    if (!E.st) return;
    const mask = (axEl('ax-mask') && axEl('ax-mask').value.trim()) || '';
    if (!mask) { App.toast('先在掩码框里写一个条件，如 stone', 'warn'); return; }
    try {
      const r = await App.post(`/api/structure/${E.st.sid}/select`, { mask });
      if (!r.count) { App.toast('掩码没有命中任何格子', 'warn'); return; }
      axSetSel(r.bbox, true);
      App.toast(`掩码选中 ${r.count.toLocaleString()} 格`, 'ok');
    } catch (e) { App.toast('掩码错误: ' + e.message, 'err'); }
  }

  // ------------------------------------------------------------ 指针交互
  function axPointerDown(ev, erase) {
    if (!E.st) return;
    // 框选是**编辑工具**（不是 AXIOM 工具）：即使 AXIOM 面板下线（AX.spec 为空）
    // 也必须能用 —— 旧写法把两者共用这个入口、开头就 `if (!AX.spec) return`，
    // 结果「框选」工具在 3D 里左键点半天没反应（只能去选区面板点「两点框选」）。
    if (E.tool === 'select') {
      // 注意：onPaint 开头会把非笔刷工具的 erase 置 false（右键不改方块），
      // 所以这里看**原始按键**判断右键=清除。
      if (ev && ev.button === 2) {
        AX.pickA = null;
        axSetSel(null);
        axStatus('已清除选区');
        return;
      }
      const hit = E.viewer.pick(ev);
      if (!hit) return;
      const c = hit.cell;
      if (!AX.pickA) {
        AX.pickA = c.slice();
        axStatus(`第一个角 (${c.join(', ')}) → 左键再点对角`);
      } else {
        axSetSel([AX.pickA[0], AX.pickA[1], AX.pickA[2], c[0], c[1], c[2]]);
        AX.pickA = null;
      }
      return;
    }
    if (!AX.spec) return;
    const hit = E.viewer.pick(ev);
    if (E.tool === 'ax') {
      if (!hit) return;
      AX.last = hit.cell.slice();
      if (AX.spec.region === 'own' && AX.spec.id !== 'path') {
        axApply({ centers: [hit.cell] });
        return;
      }
      if (AX.spec.id === 'path') {
        AX.points.push(hit.cell.slice());
        axUpdateOverlay();
        axStatus(`已取 ${AX.points.length} 个点 → 点「应用」生成路径`);
        return;
      }
      if (!axBrushOn()) { axApply({}); return; }
      AX.stroking = true;
      AX.strokeStart = performance.now();
      AX.centers = [hit.cell.slice()];
      axUpdateOverlay();
    }
  }

  function axPointerMove(ev) {
    if (E.tool !== 'ax' || !AX.spec) return;
    const hit = E.viewer.pick(ev);
    if (!hit) return;
    AX.last = hit.cell.slice();
    if (AX.stroking) {
      const c = hit.cell;
      const p = AX.centers[AX.centers.length - 1];
      const step = Math.max(1, Math.floor(AX.brush.radius / 2));
      if (!p || Math.abs(c[0] - p[0]) + Math.abs(c[1] - p[1]) +
          Math.abs(c[2] - p[2]) >= step) {
        if (AX.centers.length < 400) AX.centers.push(c.slice());
      }
    }
    axUpdateOverlay();
  }

  async function axPointerUp() {
    if (!AX.stroking) { axUpdateOverlay(); return; }
    AX.stroking = false;
    const centers = AX.centers;
    AX.centers = [];
    if (!centers.length) return;
    await axApply({ centers });
  }

  function axBind() {
    const click = (id, fn) => {
      const n = axEl(id);
      if (n) n.addEventListener('click', fn);
    };
    click('ax-apply', () => axApply({}));
    click('ax-apply-all', () => axApply({ all: true }));
    click('ax-clear', () => {
      if (!AX.spec) return;
      AX.values[AX.spec.id] = {};
      for (const p of AX.spec.params || []) AX.values[AX.spec.id][p.name] = p.default;
      axRenderParams();
      axStatus('参数已重置');
    });
    const rad = axEl('ax-brush-radius');
    if (rad) {
      rad.addEventListener('input', () => {
        AX.brush.radius = Number(rad.value) || 1;
        const lb = axEl('ax-brush-val');
        if (lb) lb.textContent = String(AX.brush.radius);
        axUpdateOverlay();
      });
    }
    const bs = axEl('ax-brush-shape');
    if (bs) bs.addEventListener('change', () => {
      AX.brush.shape = bs.value;
      axRenderBrushShapes();
      axUpdateOverlay();
    });
    const q = axEl('ax-search');
    if (q) q.addEventListener('input', () => {
      AX.query = q.value;
      axRenderTools();
    });
    for (const id of ['ax-sx0', 'ax-sy0', 'ax-sz0', 'ax-sx1', 'ax-sy1', 'ax-sz1']) {
      const n = axEl(id);
      if (n) n.addEventListener('change', axReadSelInputs);
    }
    click('ax-sel-all', () => {
      if (!E.st) return;
      axSetSel([0, 0, 0, E.st.size[0] - 1, E.st.size[1] - 1, E.st.size[2] - 1]);
      E.tool = 'select';
      document.querySelectorAll('#tool-buttons button').forEach(
        (x) => x.classList.toggle('active', x.dataset.tool === 'select'));
    });
    click('ax-sel-none', () => { axSetSel(null); AX.pickA = null; axStatus('已清除选区'); });
    click('ax-sel-mask', axSelectByMask);
    click('ax-sel-pick', () => {
      // 框选工具左键就是两点框选（这里只是切工具 + 提示）；
      // 保留这个按钮是因为面板上的按钮比快捷键好找。
      AX.pickA = null;
      E.tool = 'select';
      document.querySelectorAll('#tool-buttons button').forEach(
        (x) => x.classList.toggle('active', x.dataset.tool === 'select'));
      App.toast('左键点两个对角方块完成框选（右键/Esc 取消）', 'ok');
    });
    click('ax-sel-magic', () => {
      AX.magic = 'connected';
      E.tool = 'select';
      document.querySelectorAll('#tool-buttons button').forEach(
        (x) => x.classList.toggle('active', x.dataset.tool === 'select'));
      App.toast('点一个方块 → 选中与之连通的同类方块', 'ok');
    });
  }

  // ---------------------------------------------------------------- bind
  function bind() {
    openFileSelect();          // 「打开结构」= 可搜索下拉（输入即搜，选中即开）
    $('#btn-open-upload').addEventListener('click', () => $('#open-input').click());
    const btnNew = $('#btn-new-canvas');
    if (btnNew) btnNew.addEventListener('click', newCanvasDialog);
    const modSave = $('#mod-meta-save');
    if (modSave) modSave.addEventListener('click', saveModuleMeta);
    const modRender = $('#mod-meta-render');
    if (modRender) modRender.addEventListener('click', renderModulePreview);
    // ---- 投影属性窗口（未选中模块时显示）
    const projRefresh = $('#proj-meta-refresh');
    if (projRefresh) projRefresh.addEventListener('click', () => {
      refreshCounts();
      renderProjPanel();
    });
    const projFit = $('#proj-meta-fit');
    if (projFit) projFit.addEventListener('click', () => {
      const b = $('#btn-size-fit');
      if (b) b.click();
    });
    // ---- 接口表单（面/类型下拉 + 4 个数字 + 保存/清空/删除/扫开口）
    const faceSel = $('#port-face');
    if (faceSel) {
      for (const [v, t] of PORT_FACES) faceSel.append(el('option', { value: v }, t));
    }
    ensurePortTypeSS();          // 类型：可搜索下拉 + 可自写（不是原生 select）
    const onFormChange = () => {
      renderPortAxisHint();
      refreshPortPreview();
    };
    for (const id of ['#port-face', '#port-a1', '#port-a2', '#port-b1', '#port-b2',
                      '#port-c1', '#port-c2', '#port-dia']) {
      const n = $(id);
      if (n) { n.addEventListener('change', onFormChange); n.addEventListener('input', onFormChange); }
    }
    // 形状分段按钮：矩形（两点）/ 圆形（圆心 + 直径）
    const shapeBox = $('#port-shape');
    if (shapeBox) {
      shapeBox.addEventListener('click', (e) => {
        const b = e.target && e.target.closest ? e.target.closest('button[data-shape]') : null;
        if (!b) return;
        const shape = b.dataset.shape;
        setPortShape(shape);
        const ax = portAxes(($('#port-face') || {}).value || 'east');
        portHint(shape === 'circle'
          ? `圆形接口：圆心在面内（${ax[1]} 先数，${ax[0]} 后数——看上面的标签）+ 直径；` +
            '圆心用「接口」工具在 3D 里点一下最省事'
          : '矩形接口：两个对角点（面内两个轴各一组；3D 里点两个角会自动填）');
      });
    }
    renderPortAxisHint();
    const portApply = $('#port-apply');
    if (portApply) portApply.addEventListener('click', () => applyPort());
    const portScan = $('#port-scan');
    if (portScan) portScan.addEventListener('click', scanPortOpenings);
    const portWhole = $('#port-whole');
    if (portWhole) portWhole.addEventListener('click', portWholeFace);
    const portFromSel = $('#port-from-sel');
    if (portFromSel) portFromSel.addEventListener('click', portFromSelection);
    const portClear = $('#port-clear');
    if (portClear) portClear.addEventListener('click', () => {
      E.portSel = null;
      E.portPicks = [];
      if (E.viewer) E.viewer.setPortPreview(null);
      fillPortForm({ id: '', type: 'passage', face: 'east', origin: [0, 0], size: [1, 1],
                     shape: portShape(), tags: [] });
      renderPortList();
      portHint('已清空');
    });
    const portDel = $('#port-del');
    if (portDel) portDel.addEventListener('click', () => {
      if (E.portSel) removePort(E.portSel.id);
      else portHint('先在上面点一行选中要删的接口', true);
    });
    $('#open-input').addEventListener('change', async (e) => {
      if (!e.target.files.length) return;
      try { await openUpload(e.target.files[0]); }
      catch (err) { App.toast(err.message, 'err'); }
      e.target.value = '';
    });
    document.querySelectorAll('#tool-buttons button').forEach((b) => {
      b.addEventListener('click', () => {
        E.tool = b.dataset.tool;
        E.toolSeq = (E.toolSeq || 0) + 1;
        document.querySelectorAll('#tool-buttons button').forEach(
          (x) => x.classList.toggle('active', x === b));
        clearAxiomTool('已切回编辑工具（AXIOM 工具已取消）');
        if (E.viewer) { E.viewer.setHover(null); E.viewer.setCursor(null); }
        if (E.tool === 'select') {
          clearRegion();
          AX.pickA = null;            // 左键直接开框（不用先去选区面板点「两点框选」）
          $('#edit-hint').textContent =
            '框选：左键点两个对角方块（2D 俯视图可直接左键拖框）；右键清除选区';
          updatePlaceRow();
          return;
        }
        if (REGION_TOOLS.has(E.tool)) {
          // 移动 / 复制：有框选 → 选区中心出现可点的小方块；没框选 → 模块手柄模式
          regionToolEnter(E.tool);
          $('#edit-hint').textContent = E.tool === 'copy'
            ? '复制：框选 → 点选区中心的小方块 → 拖三箭头（原处保留）；没框选时点模块中心的小方块选模块'
            : '移动：框选 → 点选区中心的小方块 → 拖三箭头（原处搬走）；没框选时点模块中心的小方块选模块';
          updatePlaceRow();
          updateModuleVisuals();    // 进移动/复制 → 模块手柄与箭头回来
          return;
        }
        clearRegion();
        $('#edit-hint').textContent = E.tool === 'replace'
          ? '替换：点击一个方块，把同层所有该方块换成当前方块'
          : '左键放置 / 右键擦除 / 中键单击=吸取方块 / 中键拖动=平移 / 滚轮缩放';
        axUpdateOverlay();
        updatePlaceRow();
        updateModuleVisuals();      // 切到编辑工具 → 收起模块手柄与箭头（当普通方块用）
      });
    });
    const updOn = $('#place-update-on');
    if (updOn) {
      updOn.checked = !!E.update;
      updOn.addEventListener('change', () => { E.update = updOn.checked; });
    }
    const recompute = $('#btn-recompute');
    if (recompute) recompute.addEventListener('click', recomputeStates);
    updatePlaceRow();
    bindMaskDrop();
    const fullOnly = $('#blk-full-only');
    if (fullOnly) {
      fullOnly.addEventListener('change', () => {
        PICK.full = fullOnly.checked;
        renderPicker();
      });
    }
    let t = null;
    // 搜索框 = 面板筛选器（不再是原生 datalist）：输入即筛，等 250ms 看是否是完全匹配的 id
    const pickExact = (v) => {
      const q = (v || '').trim().toLowerCase();
      if (!q || !PICK.data) return false;
      const hit = PICK.data.blocks.find((r) => r[0] === q);
      if (hit) { chooseBlock('minecraft:' + hit[0]); return true; }
      return false;
    };
    $('#block-search').addEventListener('input', (e) => {
      PICK.q = e.target.value;
      renderPicker();
      clearTimeout(t);
      t = setTimeout(() => pickExact(e.target.value), 250);
    });
    $('#block-search').addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      if (pickExact(e.target.value)) return;
      const all = pickMatches();
      if (all.length) pickBlock(all[0][0]);
    });
    $('#block-search').addEventListener('change', (e) => pickExact(e.target.value));
    const slider = $('#layer-slider');
    slider.addEventListener('input', () => {
      E.layer = Number(slider.value);
      if ($('#layer-only').checked) E.only = true;
      render2D();
      updateStatus();
      E.viewer.setLayerRange(E.layer, E.layer, $('#layer-only').checked);
    });
    $('#layer-only').addEventListener('change', (e) => {
      E.viewer.setLayerRange(E.layer, E.layer, e.target.checked);
    });
    $('#btn-save').addEventListener('click', save);
    $('#btn-save-as').addEventListener('click', saveAsDialog);
    $('#btn-save-module').addEventListener('click', saveModuleDialog);
    $('#btn-undo').addEventListener('click', undo);
    $('#btn-redo').addEventListener('click', redo);
    const hUndo = $('#hist-undo');
    if (hUndo) hUndo.addEventListener('click', undo);
    const hRedo = $('#hist-redo');
    if (hRedo) hRedo.addEventListener('click', redo);
    // ---- 模块装配 UI ----
    $('#chk-mod-snap').addEventListener('change', (e) => {
      E.snapPort = e.target.checked;
    });
    $('#btn-mod-apply').addEventListener('click', () => {
      const sel = E.placements.find((p) => p.pid === E.selectedPid);
      if (!sel) return;
      const pos = [Number($('#mod-x').value) || 0,
                   Number($('#mod-y').value) || 0,
                   Number($('#mod-z').value) || 0];
      updateSelectedModule({ pos });
      syncPosSliders();
    });
    $('#btn-mod-rot').addEventListener('click', () => {
      const sel = E.placements.find((p) => p.pid === E.selectedPid);
      if (sel) updateSelectedModule({ rot: sel.rot + 1 });
    });
    $('#btn-mod-del').addEventListener('click', removeSelectedModule);
    $('#btn-mod-bake').addEventListener('click', bakeModules);
    $('#btn-size-apply').addEventListener('click', () => applyCanvasSize(false));
    $('#btn-size-fit').addEventListener('click', () => applyCanvasSize(true));
    $('#gl-canvas').addEventListener('pointerleave', () => {
      if (E.viewer) { E.viewer.setCursor(null); E.viewer.setHover(null); }
    });
    modPackSelect();
    let modT = null;
    $('#mod-search').addEventListener('input', () => {
      clearTimeout(modT);
      modT = setTimeout(renderModuleSearch, 180);
    });
    document.addEventListener('keydown', (e) => {
      if (!E.st || !$('#view-editor').classList.contains('active')) return;
      if (e.key === 'Escape') {
        AX.pickA = null;
        AX.magic = null;
        AX.points = [];
        axUpdateOverlay();
        axStatus('已取消框选/采点');
      }
      if (e.ctrlKey && e.key.toLowerCase() === 'z') { e.preventDefault(); undo(); }
      if (e.ctrlKey && e.key.toLowerCase() === 'y') { e.preventDefault(); redo(); }
      if (e.ctrlKey && e.key.toLowerCase() === 's') { e.preventDefault(); save(); }
      if (e.key === '1') document.querySelector('[data-tool=place]').click();
      if (e.key === '2') document.querySelector('[data-tool=erase]').click();
      if (e.key === '3') document.querySelector('[data-tool=replace]').click();
      if (e.key === '4') document.querySelector('[data-tool=move]').click();
      if (e.key === '5') document.querySelector('[data-tool=copy]').click();
      if (e.key === '6') document.querySelector('[data-tool=select]').click();
      if (!e.ctrlKey && (e.key === 'r' || e.key === 'R')) {
        if (rotateFacing(e.shiftKey ? -1 : 1)) {
          e.preventDefault();
          status(`朝向 → ${E.state}`);
        }
      }
      const sel = E.placements.find((p) => p.pid === E.selectedPid);
      if (sel && !e.ctrlKey) {
        const step = e.shiftKey ? 4 : 1;
        let dx = 0, dy = 0, dz = 0;
        if (e.key === 'ArrowLeft') dx = -step;
        else if (e.key === 'ArrowRight') dx = step;
        else if (e.key === 'ArrowUp') dz = -step;
        else if (e.key === 'ArrowDown') dz = step;
        else if (e.key === 'PageUp') dy = step;
        else if (e.key === 'PageDown') dy = -step;
        if (dx || dy || dz) {
          e.preventDefault();
          updateSelectedModule({ delta: [dx, dy, dz], commit: true });
        } else if (e.key.toLowerCase() === 'r') {
          e.preventDefault();
          updateSelectedModule({ rot: sel.rot + 1 });
        }
      }
    });

    const canvas = $('#layer2d');
    let painting = false;
    canvas.addEventListener('contextmenu', (e) => e.preventDefault());
    canvas.addEventListener('pointerdown', (e) => {
      if (!E.st) return;
      if (e.button === 1) return;
      painting = true;
      try { canvas.setPointerCapture(e.pointerId); } catch (err) { /* synthetic */ }
      if (E.tool === 'select') {
        if (e.button === 2) {                    // 右键：清除选区
          E.dragSel = null;
          axSetSel(null);
          axStatus('已清除选区');
          return;
        }
        const c0 = canvasCell(e);
        if (c0 && e.button === 0) {
          E.dragSel = { x0: c0.x, z0: c0.z };
          axSetSel([c0.x, 0, c0.z, c0.x, E.st.size[1] - 1, c0.z], true);
        }
        return;
      }
      if (E.tool === 'ax') {
        const c0 = canvasCell(e);
        if (c0 && AX.spec) {
          if (AX.spec.region === 'own' && AX.spec.id === 'path') {
            AX.points.push([c0.x, c0.y, c0.z]);
            axUpdateOverlay();
            return;
          }
          AX.last = [c0.x, c0.y, c0.z];
          if (AX.spec.region === 'own') {
            axApply({ centers: [[c0.x, c0.y, c0.z]] });
          } else if (axBrushOn()) {
            E.drag2DAx = { centers: [[c0.x, c0.y, c0.z]] };
            AX.stroking = true;
            AX.centers = E.drag2DAx.centers;
            axUpdateOverlay();
          } else {
            axApply({});
          }
        }
        return;
      }
      if (REGION_TOOLS.has(E.tool)) {      // 移动 / 复制：2D 也能拖模块脚框
        if (e.button === 0) {
          const cell = canvasCell(e);
          const sel = cell ? placementAtCell(cell.x, E.layer, cell.z) : null;
          if (sel) {
            E.selectedPid = sel.pid;
            selectPlacement(sel.pid, false);
            E.drag2D = { pid: sel.pid, lx: cell.x, lz: cell.z,
                         orig: sel.pos.slice(), moved: false };
          }
        }
        return;                            // 移动工具：右键不擦除（等）
      }
      if (!ERASE_TOOLS.has(E.tool) && e.button !== 0) return;
      apply2D(e, e.button === 2);
    });
    canvas.addEventListener('pointermove', (e) => {
      if (E.dragSel) {
        const c1 = canvasCell(e);
        if (c1) {
          axSetSel([E.dragSel.x0, 0, E.dragSel.z0, c1.x,
                    E.st.size[1] - 1, c1.z], true);
        }
        return;
      }
      if (E.drag2DAx) {
        const c1 = canvasCell(e);
        if (c1) {
          const p = E.drag2DAx.centers[E.drag2DAx.centers.length - 1];
          const step = Math.max(1, Math.floor(AX.brush.radius / 2));
          if (!p || Math.abs(c1.x - p[0]) + Math.abs(c1.z - p[2]) >= step) {
            if (E.drag2DAx.centers.length < 400) {
              E.drag2DAx.centers.push([c1.x, c1.y, c1.z]);
            }
          }
          AX.last = [c1.x, c1.y, c1.z];
          axUpdateOverlay();
        }
        return;
      }
      if (E.drag2D && REGION_TOOLS.has(E.tool)) {
        const cell = canvasCell(e);
        if (!cell) return;
        const sel = E.placements.find((p) => p.pid === E.drag2D.pid);
        if (!sel) return;
        const dx = cell.x - E.drag2D.lx, dz = cell.z - E.drag2D.lz;
        const pos = [E.drag2D.orig[0] + dx, E.drag2D.orig[1],
                     E.drag2D.orig[2] + dz];
        if (pos.every((v) => v >= 0)) {
          if (dx || dz) E.drag2D.moved = true;
          updateSelectedModule({ pos, local: true });
        }
        return;
      }
      if (painting) apply2D(e, e.button === 2 || (e.buttons & 2) !== 0);
    });
    canvas.addEventListener('pointerup', () => {
      painting = false;
      if (E.dragSel) {
        E.dragSel = null;
        const s = AX.sel;
        if (s) {
          axStatus(`选区 ${s[3] - s[0] + 1}×${s[4] - s[1] + 1}×${s[5] - s[2] + 1}`);
        }
        return;
      }
      if (E.drag2DAx) {
        E.drag2DAx = null;
        axPointerUp();
        return;
      }
      if (E.drag2D) { E.drag2D = null; apiCommitSelected(); return; }
      flushOps();
    });
    canvas.addEventListener('wheel', (e) => {
      e.preventDefault();
      E.zoom = Math.max(1, Math.min(24, E.zoom * (e.deltaY < 0 ? 1.2 : 0.8)));
      render2D();
    }, { passive: false });
  }

  function openSession(st) {
    if (st && st.path) openPath(st.path);
  }

  function init() {
    bind();
    axBind();
    updateStatus();
    buildSizeSliders();
    syncSizeSliders();
    renderModuleSearch();
    axRenderSel();
    axInit();
  }

  global.Editor = {
    init, openPath, openUpload, onShow, openSessionRef: openSession, E, AX,
    switchTab, undo, redo, refreshHistory, histJump, setViewBackground,
    wrapAsInstance,
    refreshFiles,
    // 脚本/测试用：方块面板、朝向自动定向、手动旋转、掩码预设、2D 底图
    PICK, pickBlock, chooseBlock, renderPicker, loadPicker, renderFams, render2D,
    newCanvas, newCanvasDialog,
    refreshModulePanel, saveModuleMeta, renderModulePreview, moduleRefFromPath,
    syncPanelVisibility,
    applyPort, removePort, scanPortOpenings, portFromCells, scanFaceOpenings, portBBox,
    portBoxOf, portWorldBox, portShape, setPortShape, fillPortBox,
    portWholeFace, portFromSelection,
    autoProps, stateForPlace, rotateFacing, renderFaceCtl,
    MASK_PRESETS,
    // 接口（面/类型下拉：类型可自写）/ 选区变换（移动·复制）/ 模块手柄
    portFaceHint, portTypeOptions, portTypeSelect, portTypeValue,
    regionToolEnter, armRegion, commitRegion, clearRegion, syncRegionVisuals,
    moduleHandleColor, REGION_COLOR, REGION_TOOLS, AX_PANEL_ENABLED,
    moduleOverlayOn, selectedModuleRef, renderProjPanel, SELF_PACK, selectPlacement,
    // 选区（脚本/测试用：框选是移动·复制的前提）
    axSetSel, axRenderSel, axNormalize,
  };
  document.addEventListener('DOMContentLoaded', init);
})(window);
