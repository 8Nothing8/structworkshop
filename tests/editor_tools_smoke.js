/* 编辑器基础功能冒烟（headless Chrome + CDP，无第三方依赖）。
 *
 * 用法：
 *   python -m mcstudio serve --port 8617
 *   node tests/editor_tools_smoke.js [chrome] [baseUrl] [outDir]
 *
 * 覆盖本轮 8 项改动里**界面可验证**的部分：
 *   1. 端口 → 接口（文案全换）
 *   2. 面选择器带方向指示（−X/+X/−Z/+Z/+Y/−Y）
 *   3. 接口类型可自写 + 可搜索（不是原生 select）
 *   4. 3D 里的方向指示：轴线 → 箭头 + X/Y/Z 文字标签
 *   5. 保存范围（画布框）：只影响保存；「自动匹配内容」按钮
 *   6. 吸管已去掉、改为「复制」；移动/复制都依赖框选 + 选区中心小方块 + 三箭头
 *   7. 导入模块后每个模块有中心小方块手柄（按 pid 配色）
 *   8. AXIOM 面板下线（DOM 仍在，代码保留）
 */
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const B = require('./_browser.js');
const chromePath = B.requireChrome(process.argv[2], 'editor_tools_smoke.js');
const base = process.argv[3] || 'http://127.0.0.1:8617';
const outDir = B.shotsDir(process.argv[4], 'editor_tools');
const PORT = 9381;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function connect(wsUrl) {
  const ws = new WebSocket(wsUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0; const pending = new Map(); const errors = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.method === 'Runtime.exceptionThrown') {
      errors.push(JSON.stringify(m.params.exceptionDetails).slice(0, 240));
    }
    if (m.id && pending.has(m.id)) {
      const { res, rej } = pending.get(m.id); pending.delete(m.id);
      if (m.error) rej(new Error(JSON.stringify(m.error))); else res(m.result);
    }
  };
  const send = (method, params = {}) => new Promise((res, rej) => {
    id += 1; pending.set(id, { res, rej });
    ws.send(JSON.stringify({ id, method, params }));
  });
  return { send, errors };
}

let pass = 0, fail = 0, skip = 0;
const failures = [];
const skips = [];
function check(name, ok, extra) {
  if (ok) { pass += 1; console.log('  ok   ' + name); }
  else {
    fail += 1;
    failures.push(name);
    console.log('  FAIL ' + name + (extra === undefined ? ''
      : '  ' + JSON.stringify(extra).slice(0, 400)));
  }
}
/** 需要本地可选数据（资产包 / builds）的断言：没有数据就记 SKIP，不计入失败。 */
function skipCheck(name, why) {
  skip += 1;
  skips.push(name);
  console.log('  SKIP ' + name + '  (' + why + ')');
}

(async () => {
  await B.requireServer(base, 'editor_tools_smoke.js');
  fs.mkdirSync(outDir, { recursive: true });
  const chrome = spawn(chromePath, [
    '--headless=new', '--no-sandbox', '--disable-dev-shm-usage',
    '--enable-unsafe-swiftshader', '--use-gl=angle', '--use-angle=swiftshader',
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${B.profileDir('editor-tools')}`,
    '--window-size=1680,1050', 'about:blank'], { stdio: 'ignore' });

  let dbg = null;
  for (let i = 0; i < 60; i++) {
    await sleep(300);
    try {
      const l = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      dbg = l.find((t) => t.type === 'page');
      if (dbg) break;
    } catch (e) { /* 还没起来 */ }
  }
  if (!dbg) { console.error('连不上 headless Chrome'); process.exit(2); }
  const { send, errors } = await connect(dbg.webSocketDebuggerUrl);
  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride',
    { width: 1680, height: 1050, deviceScaleFactor: 1, mobile: false });

  const ev = async (expr) => {
    const r = await send('Runtime.evaluate', {
      expression: `(async () => { ${expr} })()`, awaitPromise: true, returnByValue: true,
    });
    if (r.exceptionDetails) return { __err: JSON.stringify(r.exceptionDetails).slice(0, 400) };
    return r.result.value;
  };
  const shot = async (name) => {
    const r = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(path.join(outDir, name), Buffer.from(r.data, 'base64'));
  };

  await send('Page.navigate', { url: base + '/?view=editor' });
  await sleep(3500);

  // ============================================================ 1. 工具集（吸管→复制）
  const tools = await ev(`
    return [...document.querySelectorAll('#tool-buttons button')].map((b) => b.dataset.tool);
  `);
  check('1a 工具按钮 = 放置/擦除/替换/复制/移动/框选/接口（没有吸管）',
    Array.isArray(tools) && tools.join(',') === 'place,erase,replace,copy,move,select,port', tools);
  check('1b 吸管按钮已下线', !String(tools).includes('pick'), tools);
  check('1c 中键吸取仍提示（保留能力）',
    ((await ev(`return document.querySelector('#gl-hud').textContent;`)) || '')
      .includes('吸取方块'));

  // ============================================================ 2. AXIOM 面板下线
  const axState = await ev(`
    const p = document.querySelector('#ax-panel');
    const sel = document.querySelector('#ax-sel-info');
    return { exists: !!p, hidden: p ? p.classList.contains('hidden') : null,
             visible: p ? !!(p.offsetParent) : null,
             selPanelVisible: sel ? !!(sel.offsetParent) : null,
             toolsHtml: (document.querySelector('#ax-tools') || {}).innerHTML || '',
             magic: !!document.querySelector('#ax-sel-magic') };
  `);
  check('2a AXIOM 面板 DOM 仍在（代码保留）', axState.exists === true, axState);
  check('2b AXIOM 面板已隐藏', axState.hidden === true && axState.visible === false, axState);
  check('2c AXIOM 工具没被加载（面板下线时不拉目录）',
    !axState.toolsHtml || axState.toolsHtml.length === 0, axState.toolsHtml.slice(0, 80));
  check('2d 选区面板仍然可用（框选是基础功能）', axState.selPanelVisible === true, axState);
  check('2e 魔棒（死代码按钮）已从选区面板去掉', axState.magic === false, axState);

  // ============================================================ 3. 接口文案 + 面方向指示
  const iface = await ev(`
    const txt = document.body.innerText;
    const notUsed = (txt.match(/端口/g) || []).length;
    return { portWord: notUsed, hasIface: txt.includes('接口'),
             title: (document.querySelector('#mod-meta-win .ax-win-title')||{}).textContent||'' };
  `);
  check('3a 界面上不再出现「端口」字样', iface.portWord === 0, iface);

  // 3b–4e 需要「一个带接口的**真实模块**」-> 需要资产包。
  // 没有包就只跳过这一段：后面的 5/6/7/8/9 只用 tests/fixtures/small_house.schem 就能跑，
  // 不能因为缺本地数据把整个 45 项冒烟都停掉。
  if (B.hasPacks()) {
    // 打开一个带接口的模块，让接口表单出现
    const openRes = await ev(`
      const idx = await App.api('/api/modules?hasPort=1&limit=1&sort=id');
      const m = (idx.modules || [])[0];
      if (!m) return { err: '没有带接口的模块' };
      await Editor.openPath(m.file || ('packs/' + m.path));
      return { id: m.id, path: m.file || m.path };
    `);
    check('3b 打开带接口的模块', !openRes.__err && !openRes.err, openRes);
    await sleep(2200);

    const faceOpts = await ev(`
      return [...document.querySelectorAll('#port-face option')].map((o) => [o.value, o.textContent]);
    `);
    const faceLabels = (faceOpts || []).map((x) => x[1]).join(' | ');
    check('3c 六个面都有轴向指示（−X/+X/−Z/+Z/+Y/−Y）',
      faceLabels.includes('−X') && faceLabels.includes('+X') && faceLabels.includes('−Z') &&
      faceLabels.includes('+Z') && faceLabels.includes('+Y') && faceLabels.includes('−Y'),
      faceLabels);
    check('3d 面选单里同时写了方位名（西/东/北/南/顶/底）',
      ['西面', '东面', '北面', '南面', '顶面', '底面'].every((w) => faceLabels.includes(w)),
      faceLabels);
    const axisHint = await ev(`
      document.querySelector('[data-tool=port]').click();
      return document.querySelector('#port-axis-hint').textContent;
    `);
    check('3e 表单里有「法向 ±轴 + 面内轴」那行提示',
      /法向/.test(axisHint) && /面内/.test(axisHint), axisHint);

    // ============================================================ 4. 接口类型可自写 + 可搜索
    const typeCtl = await ev(`
      const host = document.querySelector('#port-type');
      const ss = host ? host.querySelector('.ss') : null;
      return { tag: host ? host.tagName : null,
               isSS: !!ss, isHost: !!(host && host.classList.contains('ss-host')),
               hasInput: !!(ss && ss.querySelector('input')),
               hasPop: !!(ss && ss.querySelector('.ss-pop')),
               nativeSelects: document.querySelectorAll('#port-form select').length };
    `);
    check('4a 类型是搜索下拉组件（不是原生 select）',
      typeCtl.isSS === true && typeCtl.isHost === true && typeCtl.hasInput === true &&
      typeCtl.hasPop === true && typeCtl.tag === 'DIV', typeCtl);
    check('4b 接口表单里只剩「面」一个原生 select', typeCtl.nativeSelects === 1, typeCtl);

    const custom = await ev(`
      const host = document.querySelector('#port-type');
      const input = host.querySelector('.ss input');
      input.value = 'cargo_dock';              // 用户自己写的类型
      input.dispatchEvent(new Event('input', { bubbles: true }));
      await new Promise((r) => setTimeout(r, 60));
      const rows = [...host.querySelectorAll('.ss-row')].map((x) => x.textContent);
      return { value: Editor.portTypeValue(), rows,
               firstIsCustom: rows.length ? rows[0].includes('cargo_dock') : false };
    `);
    check('4c 自己写的类型能直接用（portTypeValue 取原文）',
      custom.value === 'cargo_dock', custom);
    check('4d 自写类型在候选里排第一（回车即采用）',
      custom.firstIsCustom === true, custom.rows);

    const builtinList = await ev(`return Editor.portTypeOptions().length;`);
    check('4e 下拉里同时给内置推荐类型（≥18 个）', builtinList >= 18, builtinList);
  } else {
    for (const n of ['3b 打开带接口的模块',
                     '3c 六个面都有轴向指示（−X/+X/−Z/+Z/+Y/−Y）',
                     '3d 面选单里同时写了方位名（西/东/北/南/顶/底）',
                     '3e 表单里有「法向 ±轴 + 面内轴」那行提示',
                     '4a 类型是搜索下拉组件（不是原生 select）',
                     '4b 接口表单里只剩「面」一个原生 select',
                     '4c 自己写的类型能直接用（portTypeValue 取原文）',
                     '4d 自写类型在候选里排第一（回车即采用）',
                     '4e 下拉里同时给内置推荐类型（≥18 个）']) {
      skipCheck(n, '需要资产包：带接口的真实模块');
    }
  }

  // 5 之后的断言都需要「编辑器里真的有一栋建筑」：
  // 以前这里靠 3b 顺手打开的资产包模块撑着 —— 那是隐式耦合，
  // 没有 `packs/` 时 `Editor.E.viewer.renderer` 是 null，5~9 全崩。
  // 现在显式用仓库自带的夹具，跟有没有资产包无关。
  await ev(`
    await Editor.openPath('tests/fixtures/small_house.schem');
    await new Promise((r) => setTimeout(r, 1500));
    return true;
  `);

  // ============================================================ 5. 方向指示箭头 + X/Y/Z 文字
  const axis = await ev(`
    const els = [...document.querySelectorAll('#axis-labels .al')];
    await new Promise((r) => setTimeout(r, 300));
    return { n: els.length, text: els.map((e) => e.textContent).join(''),
             on: els.map((e) => e.classList.contains('on')),
             transform: els.map((e) => e.style.transform),
             tips: Editor.E.viewer.renderer._axisTips,
             axisVerts: Editor.E.viewer.renderer.axisVerts };
  `);
  check('5a 视口里有 X/Y/Z 三个文字标签', axis.n === 3 && axis.text === 'XYZ', axis);
  check('5b 标签已投影定位并显示', axis.on.every(Boolean) &&
    axis.transform.every((t) => /translate\(/.test(t || '')), axis);
  check('5c 三根坐标轴箭头几何已生成（axisVerts>0）', axis.axisVerts > 0, axis);
  check('5d 箭头尖端位置 = 三轴之外（给标签留位置）',
    Array.isArray(axis.tips) && axis.tips.length === 3 && axis.tips[1][1] > 0, axis.tips);
  await shot('axis-arrows.png');

  // ============================================================ 6. 保存范围（画布框）
  const frame = await ev(`
    const titles = [...document.querySelectorAll('.tool-panel .ax-win-title')].map((x) => x.textContent);
    const t = titles.find((x) => x.includes('保存范围')) || '';
    const hint = document.querySelector('#size-sliders').closest('.ax-win-body').innerText;
    return { title: t, fitLabel: (document.querySelector('#btn-size-fit')||{}).textContent,
             fitClass: (document.querySelector('#btn-size-fit')||{}).className,
             hint, old: titles.some((x) => x.includes('画布范围')) };
  `);
  check('6a 面板改名「保存范围（画布框）」', frame.title.includes('保存范围'), frame.title);
  check('6b 按钮叫「自动匹配内容」', frame.fitLabel === '自动匹配内容', frame);
  check('6c 面板写明只影响保存 / 框外不受限',
    frame.hint.includes('只影响保存') && frame.hint.includes('不受限制'), frame.hint.slice(0, 120));

  const fit = await ev(`
    await Editor.openPath('tests/fixtures/small_house.schem');
    await new Promise((r) => setTimeout(r, 900));
    const size = Editor.E.st.size.slice();
    // 先把框缩到 3×3×3（框外的东西留着），再「自动匹配内容」→ 应该长回内容外框
    // （走编辑器自己的同步路径：save/applyCanvasSize 会 applyModuleResult）
    const r0 = await App.post('/api/structure/' + Editor.E.st.sid + '/resize',
                              { size: [3, 3, 3] });
    Editor.E.st.frame = r0.frame.slice();
    Editor.syncRegionVisuals();
    const shrunk = Editor.E.st.frame.slice();
    document.querySelector('#btn-size-fit').click();
    await new Promise((r) => setTimeout(r, 1400));
    return { size, shrunk, after: Editor.E.st.frame.slice(),
             outside: Editor.E.st.outside };
  `);
  check('6d 自动匹配内容：缩小的框一次长回内容外框',
    fit.shrunk && fit.after && fit.size &&
    fit.shrunk.join() === '3,3,3' && fit.after.join() === fit.size.join() &&
    fit.outside === 0, fit);

  const outside = await ev(`
    // 把框缩到 4×4×4，然后往框外 8 格处放一块 —— 必须允许（框只管保存）
    await App.post('/api/structure/' + Editor.E.st.sid + '/resize', { size: [4, 4, 4] });
    await new Promise((r) => setTimeout(r, 400));
    const r = await App.post('/api/structure/' + Editor.E.st.sid + '/ops',
      { ops: [{ type: 'set', x: 8, y: 8, z: 8, state: 'minecraft:stone' }], update: false });
    return { frame: r.frame, outside: r.outside, bbox: r.bbox, resized: r.resized,
             size: r.size };
  `);
  check('6e 往保存范围外放置不受限制（服务端照写，并报框外格数）',
    Array.isArray(outside.bbox) && outside.bbox[0] === 8 && outside.outside > 0, outside);
  await shot('frame-save-scope.png');

  // ============================================================ 7. 选区移动 / 复制
  const setup = await ev(`
    await Editor.openPath('tests/fixtures/small_house.schem');
    await new Promise((r) => setTimeout(r, 900));
    // 画一段 2×2×2 的确定内容在 (1,1,1)-(2,2,2)
    await App.post('/api/structure/' + Editor.E.st.sid + '/ops',
      { ops: [{ type: 'fill', from: [1,1,1], to: [2,2,2], state: 'minecraft:bricks' }],
        update: false });
    await new Promise((r) => setTimeout(r, 250));
    const buf = await (await fetch('/api/structure/' + Editor.E.st.sid + '/voxels')).arrayBuffer();
    Editor.E.voxels = new Uint16Array(buf);
    return true;
  `);
  const regionFlow = await ev(`
    const Ed = Editor, E = Ed.E;
    // 没有框选 → 移动工具应为模块模式（不建选区手柄）
    Ed.AX.sel = null; Ed.axRenderSel();
    document.querySelector('[data-tool=move]').click();
    const noSel = !!E.region;
    // 框选一段再进移动
    Ed.AX.sel = [1, 1, 1, 2, 2, 2]; Ed.axRenderSel();
    document.querySelector('[data-tool=move]').click();
    const withSel = E.region ? { box: E.region.box.slice(), mode: E.region.mode,
                                armed: E.region.armed } : null;
    const handle = E.viewer.region ? { box: E.viewer.region.box.slice(),
                                       armed: E.viewer.region.armed } : null;
    return { noSel, withSel, handle, color: Ed.REGION_COLOR.move };
  `);
  check('7a 没框选时点「移动」不建选区手柄（退化成模块模式）',
    regionFlow.noSel === false, regionFlow);
  check('7b 框选后点「移动」→ 建立选区手柄（未武装）',
    regionFlow.withSel && regionFlow.withSel.mode === 'move' &&
    regionFlow.withSel.armed === false, regionFlow.withSel);
  check('7c 视口拿到选区手柄（画半透明小方块）',
    regionFlow.handle && regionFlow.handle.armed === false, regionFlow.handle);

  const arm = await ev(`
    const E = Editor.E, v = E.viewer;
    // 手柄中心投影到画布正中：把相机 target 指到选区中心即可
    const b = E.region.box;
    const c = [(b[0]+b[3]+1)/2, (b[1]+b[4]+1)/2, (b[2]+b[5]+1)/2];
    v.cam.target = c; v.frame(); v.draw();
    const rect = v.canvas.getBoundingClientRect();
    const ev2 = { clientX: rect.left + rect.width / 2,
                  clientY: rect.top + rect.height / 2 };
    const hit = v.pickRegionHandle(ev2);
    document.querySelector('[data-tool=move]').click();  // 重新进一次（保证状态干净）
    E.region.armed = true; Editor.syncRegionVisuals();
    return { hit: !!hit, gizmo: v.gizmo ? { arrowsOnly: v.gizmo.arrowsOnly,
             color: v.gizmo.color, center: v.gizmo.center } : null };
  `);
  check('7d 点中选区中心的小方块（pickRegionHandle 命中）', arm.hit === true, arm);
  check('7e 点完出现三箭头（arrowsOnly + 选区配色）',
    arm.gizmo && arm.gizmo.arrowsOnly === true &&
    JSON.stringify(arm.gizmo.color) === JSON.stringify([0.36, 0.82, 1.0]), arm.gizmo);

  const drag = await ev(`
    const E = Editor.E, v = E.viewer;
    const g = v.gizmo;
    // 模拟拖 X 箭头：直接算 delta（拖动路径由 gizmoAxisParam 提供，这里只验提交）
    E.region.delta = [5, 0, 0];
    await Editor.commitRegion();
    await new Promise((r) => setTimeout(r, 600));
    const buf = await (await fetch('/api/structure/' + E.st.sid + '/voxels')).arrayBuffer();
    E.voxels = new Uint16Array(buf);
    const [sx, sy, sz] = E.st.size;
    const pal = E.palette;
    const bricks = pal.indexOf('minecraft:bricks');
    const at = (x, y, z) => E.voxels[(y * sz + z) * sx + x];
    return { moved: at(6,1,1) === bricks, cleared: at(1,1,1) !== bricks,
             box: E.region ? E.region.box.slice() : null, arrowsOnly: g.arrowsOnly };
  `);
  check('7f 移动提交：目标处出现内容', drag.moved === true, drag);
  check('7g 移动提交：原处已搬走', drag.cleared === true, drag);
  check('7h 移动后选区跟着走（可连续推）',
    drag.box && drag.box[0] === 6 && drag.box[3] === 7, drag.box);
  await shot('region-move.png');

  const copyFlow = await ev(`
    const Ed = Editor, E = Ed.E;
    Ed.AX.sel = [6, 1, 1, 7, 2, 2]; Ed.axRenderSel();
    document.querySelector('[data-tool=copy]').click();
    const st1 = { mode: E.region.mode, armed: E.region.armed };
    E.region.armed = true; Ed.syncRegionVisuals();
    E.region.delta = [0, 4, 0];
    await Editor.commitRegion();
    await new Promise((r) => setTimeout(r, 600));
    const buf = await (await fetch('/api/structure/' + E.st.sid + '/voxels')).arrayBuffer();
    E.voxels = new Uint16Array(buf);
    const [sx, sy, sz] = E.st.size;
    const bricks = E.palette.indexOf('minecraft:bricks');
    const at = (x, y, z) => E.voxels[(y * sz + z) * sx + x];
    return { st1, kept: at(6,1,1) === bricks, copied: at(6,5,1) === bricks,
             color: Ed.REGION_COLOR.copy };
  `);
  check('7i 复制工具建立选区手柄', copyFlow.st1.mode === 'copy', copyFlow.st1);
  check('7j 复制：原处保留', copyFlow.kept === true, copyFlow);
  check('7k 复制：目标处出现副本', copyFlow.copied === true, copyFlow);
  check('7l 复制与移动的配色不同（黄绿 vs 青蓝）',
    JSON.stringify(copyFlow.color) !== JSON.stringify([0.36, 0.82, 1.0]), copyFlow.color);
  await shot('region-copy.png');

  // 8 区要往画布里导入真实模块 -> 需要资产包（同 3b–4e）。
  if (B.hasPacks()) {
    // ============================================================ 8. 模块中心手柄
    const mods = await ev(`
      const Ed = Editor, E = Ed.E;
      // 导入两个模块（列表里的前两个）
      const idx = await App.api('/api/modules?limit=2&sort=id');
      const ids = (idx.modules || []).map((m) => m.id);
      if (!ids.length) return { err: '没有模块' };
      await App.post('/api/structure/' + E.st.sid + '/modules', { ids, auto: true });
      await new Promise((r) => setTimeout(r, 900));
      await Ed.openPath('tests/fixtures/small_house.schem');   // 丢掉临时画布
      return { ids };
    `);
    check('8a 能导入模块做手柄测试', !mods.__err && !mods.err, mods);

    const handles = await ev(`
      const Ed = Editor, E = Ed.E;
      const sid = E.st.sid;
      const idx = await App.api('/api/modules?limit=2&sort=id');
      const ids = (idx.modules || []).map((m) => m.id);
      await App.post('/api/structure/' + sid + '/modules', { ids, auto: true });
      await new Promise((r) => setTimeout(r, 700));
      const d = await App.api('/api/structure/' + sid + '/modules');
      E.placements = d.placements || [];
      document.querySelector('[data-tool=move]').click();
      await new Promise((r) => setTimeout(r, 400));
      const boxes = (E.viewer.moduleBoxes || []).map((b) => ({
        pid: b.pid, color: b.handleColor, bbox: b.bbox }));
      return { n: E.placements.length, boxes };
    `);
    check('8b 导入的模块都进了视口手柄列表',
      handles.boxes && handles.boxes.length === (handles.n || 0) && handles.n >= 1, handles);
    check('8c 每个模块都有中心手柄配色（不同模块颜色不同）',
      handles.boxes.length >= 2 &&
      JSON.stringify(handles.boxes[0].color) !== JSON.stringify(handles.boxes[1].color),
      handles.boxes.map((b) => b.color));

    const pickMod = await ev(`
      const Ed = Editor, E = Ed.E, v = E.viewer;
      const b = (E.viewer.moduleBoxes || [])[0];
      if (!b) return { err: '没有手柄' };
      const c = [(b.bbox[0]+b.bbox[3]+1)/2, (b.bbox[1]+b.bbox[4]+1)/2,
                 (b.bbox[2]+b.bbox[5]+1)/2];
      v.cam.target = c; v.frame(); v.draw();
      const rect = v.canvas.getBoundingClientRect();
      const hit = v.pickModuleHandle({ clientX: rect.left + rect.width/2,
                                       clientY: rect.top + rect.height/2 });
      return { pid: b.pid, hit: hit ? hit.pid : null, center: c };
    `);
    check('8d 点模块中心的小方块能命中对应模块（不是靠点模块身体）',
      pickMod.hit === pickMod.pid && !!pickMod.pid, pickMod);
    await shot('module-handles.png');
  } else {
    for (const n of ['8a 能导入模块做手柄测试',
                     '8b 导入的模块都进了视口手柄列表']) {
      skipCheck(n, '需要资产包：要导入真实模块');
    }
  }

  // ============================================================ 打开即实例 / 属性面板 / 叠加层 / 滚动条
  // 这四项**不需要资产包**（用 tests/fixtures 里的结构），所以纯代码 checkout 也跑得到 ——
  // `studio_ui_audit.js` 的断言写死了配体包里的具体模块，没 packs/ 时整脚本跳过。
  {
    await send('Page.navigate', {
      url: base + '/?view=editor&open=tests/fixtures/small_house.schem' });
    let ready = false;
    for (let i = 0; i < 40 && !ready; i++) {
      await sleep(1000);
      ready = await ev(`const v = window.Editor.E.viewer;
        return !!(window.Editor.E.st && v && v.renderer && v.renderer.mesher);`) === true;
    }
    await sleep(3000);        // 「打开即实例」是异步的（POST detach + 重合成）
    const openInst = await ev(`
      const E = Editor.E, v = E.viewer, p = E.placements[0] || {};
      const q = (s) => document.querySelector(s);
      return { n: E.placements.length, pack: p.pack, id: p.id,
               tool: E.tool, boxes: v.moduleBoxes.length, gizmo: !!v.gizmo,
               dirty: E.st.dirty, undo: E.st.can_undo,
               projWin: !q('#proj-meta-win').classList.contains('hidden'),
               modWin: !q('#mod-meta-win').classList.contains('hidden'),
               projName: q('#proj-meta-name').textContent,
               projKV: q('#proj-meta-kv').textContent };
    `);
    check('9a 打开普通投影 = 整幅实例（@self）+ 自动进「移动」，不标未保存也不占撤销栈',
      openInst.n === 1 && openInst.pack === '@self' && openInst.tool === 'move' &&
      openInst.boxes === 1 && openInst.gizmo === true &&
      openInst.dirty === false && openInst.undo === false, openInst);
    check('9b 左栏「投影属性」：普通投影显示路径/尺寸/方块数（与模块属性互斥）',
      openInst.projWin === true && openInst.modWin === false &&
      openInst.projName === 'small_house' &&
      openInst.projKV.includes('尺寸') && openInst.projKV.includes('方块'),
      { name: openInst.projName, kv: openInst.projKV.slice(0, 100) });

    const overlay = await ev(`
      const E = Editor.E, v = E.viewer;
      document.querySelector('[data-tool=place]').click();
      await new Promise((r) => setTimeout(r, 600));
      const off = { tool: E.tool, boxes: v.moduleBoxes.length, gizmo: !!v.gizmo,
                    sel: E.selectedPid };
      document.querySelector('[data-tool=move]').click();
      await new Promise((r) => setTimeout(r, 600));
      const back = { tool: E.tool, boxes: v.moduleBoxes.length };
      return { off, back };
    `);
    check('9c 叠加层只在「移动/复制」：切到「放置」手柄/箭头/选中全收起，切回来又出现',
      overlay.off.tool === 'place' && overlay.off.boxes === 0 &&
      overlay.off.gizmo === false && overlay.off.sel === null &&
      overlay.back.tool === 'move' && overlay.back.boxes === 1, overlay);

    const sb = await ev(`
      const want = 'rgb(70, 85, 107) rgb(26, 30, 35)';
      const probe = (sel) => {
        const host = document.querySelector(sel) || document.body;
        const d = document.createElement('div');
        d.style.overflow = 'auto'; d.style.maxHeight = '8px';
        host.appendChild(d);
        const v = getComputedStyle(d).scrollbarColor;
        d.remove(); return v;
      };
      const root = getComputedStyle(document.documentElement);
      return { want, root: root.scrollbarColor, scheme: root.colorScheme,
               tool: probe('.tool-panel'), left: probe('.left-panel'),
               modal: probe('#modal-root'), settings: probe('#view-settings'),
               pop: probe('.ss .ss-pop') };
    `);
    const sbOK = (v) => v === sb.want;
    check('9d 滚动条全站深色（:root 继承到弹窗/设置页/下拉/右栏）+ color-scheme: dark',
      sbOK(sb.root) && sbOK(sb.tool) && sbOK(sb.left) && sbOK(sb.modal) &&
      sbOK(sb.settings) && sbOK(sb.pop) && sb.scheme === 'dark', sb);
    await shot('open-as-instance.png');
  }

  // ============================================================ 运行时异常
  check('9 页面无运行时异常', errors.length === 0, errors.slice(0, 3));

  console.log(`\n${pass} 通过 / ${fail} 失败`
    + (skip ? ` / ${skip} 跳过（需要本地数据）` : ''));
  if (fail) console.log('失败项：\n  - ' + failures.join('\n  - '));
  if (skip) console.log('跳过项（packs/ 是本地内容）：\n  - ' + skips.join('\n  - '));
  try { chrome.kill(); } catch (e) { /* ignore */ }
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
