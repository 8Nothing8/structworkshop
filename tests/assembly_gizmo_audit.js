/* mcstudio 装配 gizmo 拖拽审计（headless Chrome + CDP，无第三方依赖）。
 *
 * 用法：
 *   python -m mcstudio serve --port 8617
 *   node tests/assembly_gizmo_audit.js [chrome] [baseUrl] [outDir]
 *
 * 检查：导入模块 → 选中出现 gizmo → 拖 X 红箭头平移 → 拖 Z 蓝圆环旋转 90°
 *        → 拖 Y 绿箭头升降 → 页面无运行时异常。
 */
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const B = require('./_browser.js');
const chromePath = B.requireChrome(process.argv[2], 'assembly_gizmo_audit.js');
const base = process.argv[3] || 'http://127.0.0.1:8617';
// 要真的打开一个现代大堂模块 —— 资产包属于可选数据；先查再建目录
B.requireData('packs/modern-arch/modules/rooms/modern_lobby.schem', 'assembly_gizmo_audit.js');
const outDir = B.shotsDir(process.argv[4], 'assembly_gizmo');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function connect(wsUrl) {
  const ws = new WebSocket(wsUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0; const pending = new Map(); const errors = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.method === 'Runtime.exceptionThrown') {
      const d = m.params.exceptionDetails;
      errors.push((d.exception && (d.exception.description || d.exception.value)) || d.text);
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

(async () => {
  await B.requireServer(base, 'assembly_gizmo_audit.js');
  fs.mkdirSync(outDir, { recursive: true });
  const chrome = spawn(chromePath, ['--headless=new', '--no-sandbox', '--disable-dev-shm-usage',
    '--enable-unsafe-swiftshader', '--use-gl=angle', '--use-angle=swiftshader',
    '--remote-debugging-port=9392', `--user-data-dir=${B.profileDir('gizmo-audit')}`,
    '--window-size=1680,1050', 'about:blank'], { stdio: 'ignore' });
  let dbg = null;
  for (let i = 0; i < 60; i++) {
    await sleep(250);
    try {
      const l = await (await fetch('http://127.0.0.1:9392/json/list')).json();
      dbg = l.find((t) => t.type === 'page');
      if (dbg) break;
    } catch (e) { /* retry */ }
  }
  const { send, errors } = await connect(dbg.webSocketDebuggerUrl);
  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride',
    { width: 1680, height: 1050, deviceScaleFactor: 1, mobile: false });
  const ev = async (expr) => {
    const r = await send('Runtime.evaluate',
      { expression: `(async () => { ${expr} })()`, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) return { __err: JSON.stringify(r.exceptionDetails).slice(0, 300) };
    return r.result.value;
  };
  const check = (name, ok, detail) => {
    console.log((ok ? 'PASS ' : 'FAIL ') + name + '  ' + JSON.stringify(detail));
    if (!ok) process.exitCode = 1;
  };
  const shot = async (name) => {
    const r = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(path.join(outDir, name), Buffer.from(r.data, 'base64'));
  };

  await send('Page.navigate', { url: base + '/?view=editor&open=packs/modern-arch/modules/rooms/modern_lobby.schem' });
  await sleep(3200);

  // 页面内辅助：世界坐标投影到客户端坐标 + 合成 pointer 拖拽
  const ready = await ev(`
    window.__g = {
      project(p) {
        const v = window.Editor.E.viewer;
        const m = glMatrix.mat4.multiply(glMatrix.mat4.create(),
          v._projMatrix(), v._viewMatrix());
        const o = glMatrix.vec4.transformMat4(glMatrix.vec4.create(),
          [p[0], p[1], p[2], 1], m);
        if (Math.abs(o[3]) < 1e-6) return null;
        const r = v.canvas.getBoundingClientRect();
        return [r.left + (o[0] / o[3] * 0.5 + 0.5) * r.width,
                r.top + (1 - (o[1] / o[3] * 0.5 + 0.5)) * r.height];
      },
      send(type, pt, buttons) {
        const c = window.Editor.E.viewer.canvas;
        c.dispatchEvent(new PointerEvent(type, {
          clientX: pt[0], clientY: pt[1], button: 0, buttons: buttons || 0,
          pointerId: 7, pointerType: 'mouse', bubbles: true, cancelable: true,
        }));
      },
      drag(from, to, steps) {
        this.send('pointerdown', from, 1);
        const n = steps || 8;
        for (let i = 1; i <= n; i++) {
          this.send('pointermove', [from[0] + (to[0] - from[0]) * i / n,
                                    from[1] + (to[1] - from[1]) * i / n], 1);
        }
        this.send('pointerup', to, 0);
      },
      axisPoint(axis, t) {
        const v = window.Editor.E.viewer, g = v.gizmo;
        const S = v._gizmoScale();
        const d = axis === 'x' ? [1,0,0] : axis === 'y' ? [0,1,0] : [0,0,1];
        return [g.center[0] + d[0] * S * 1.85 * t,
                g.center[1] + d[1] * S * 1.85 * t,
                g.center[2] + d[2] * S * 1.85 * t];
      },
      ringPoint(axis, deg) {
        const v = window.Editor.E.viewer, g = v.gizmo;
        const S = v._gizmoScale(), R = S * 1.45;
        const b = v._arcBasis(axis);
        const a = deg * Math.PI / 180;
        return [g.center[0] + b.u[0]*Math.cos(a)*R + b.v[0]*Math.sin(a)*R,
                g.center[1] + b.u[1]*Math.cos(a)*R + b.v[1]*Math.sin(a)*R,
                g.center[2] + b.u[2]*Math.cos(a)*R + b.v[2]*Math.sin(a)*R];
      },
      pos(pid) { const p = window.Editor.E.placements.find(x => x.pid === pid); return p && p.pos.slice(); },
      rotz(pid) { const p = window.Editor.E.placements.find(x => x.pid === pid); return p && (p.rotz || 0); },
    };
    return { ok: !!window.Editor.E.viewer };
  `);
  check('注入拖拽辅助', ready && ready.ok, ready);

  const added = await ev(`
    const rows = [...document.querySelectorAll('#mod-results .mod-result-row')];
    const row = rows.find(r => r.querySelector('.mr-id').textContent === 'aero_core');
    row.click();                       // 新交互：点一行直接导入（旧流程要先勾选再点「导入所选」）
    await new Promise(r => setTimeout(r, 2000));
    document.querySelector('.mod-inst-row').click();
    await new Promise(r => setTimeout(r, 800));
    const E = window.Editor.E;
    return { placements: E.placements.length, pid: E.selectedPid, gizmo: !!E.viewer.gizmo,
             tool: E.tool };
  `);
  check('导入并选中模块（gizmo 出现）',
        added && added.placements >= 1 && added.gizmo && added.tool === 'move', added);
  await shot('gizmo-1-selected.png');

  const moveX = await ev(`
    const E = window.Editor.E, pid = E.selectedPid;
    const p0 = window.__g.pos(pid).slice();
    const a = window.__g.project(window.__g.axisPoint('x', 0.45));
    const t = 0.45 + 3 / (E.viewer._gizmoScale() * 1.85);
    const b = window.__g.project(window.__g.axisPoint('x', t));
    window.__g.drag(a, b, 10);
    await new Promise(r => setTimeout(r, 1200));
    return { before: p0, after: window.__g.pos(pid) };
  `);
  check('拖 X 箭头平移 (+3)',
        moveX && moveX.after && moveX.after[0] === moveX.before[0] + 3, moveX);

  const rotZ = await ev(`
    const E = window.Editor.E, pid = E.selectedPid;
    const r0 = window.__g.rotz(pid);
    const a = window.__g.project(window.__g.ringPoint('z', 150));
    const b = window.__g.project(window.__g.ringPoint('z', 240));
    window.__g.drag(a, b, 12);
    await new Promise(r => setTimeout(r, 1200));
    return { before: r0, after: window.__g.rotz(pid) };
  `);
  check('拖 Z 圆环旋转 90°', rotZ && rotZ.after === 1, rotZ);
  await shot('gizmo-2-rotated.png');

  const moveY = await ev(`
    const E = window.Editor.E, pid = E.selectedPid;
    const p0 = window.__g.pos(pid).slice();
    const a = window.__g.project(window.__g.axisPoint('y', 0.45));
    const b = window.__g.project(window.__g.axisPoint('y', 1.45));
    window.__g.drag(a, b, 10);
    await new Promise(r => setTimeout(r, 1200));
    return { before: p0, after: window.__g.pos(pid), size: E.st.size };
  `);
  check('拖 Y 箭头升降', moveY && moveY.after && moveY.after[1] > moveY.before[1], moveY);

  // 4) 3D 准星（切回放置工具，悬停应出现光标方块）
  const cursor = await ev(`
    document.querySelector('[data-tool=place]').click();
    const E = window.Editor.E, v = E.viewer, rect = v.canvas.getBoundingClientRect();
    const pt = [rect.left + rect.width / 2, rect.top + rect.height / 2];
    v.canvas.dispatchEvent(new PointerEvent('pointermove', {
      clientX: pt[0], clientY: pt[1], button: -1, buttons: 0,
      pointerId: 5, pointerType: 'mouse', bubbles: true,
    }));
    await new Promise(r => setTimeout(r, 200));
    return { cursor: E.viewer.cursor ? E.viewer.cursor.cell : null,
             mode: E.viewer.cursor ? E.viewer.cursor.mode : null };
  `);
  check('3D 准星（悬停出现光标方块；越界为 grow）',
        cursor && cursor.cursor && cursor.cursor.length === 3 &&
        ['place', 'grow'].includes(cursor.mode), cursor);

  // 5) 画布范围滑块：存在 3 条 + 拖动改的是「画布框」 + 右键可输入
  //    滑块编辑的是画布框（保存时才按它裁剪）：拖大 → 框跟着大，
  //    数据范围只按需放大（storage = max(数据范围, 框)，见 Session.resize）。
  //    旧断言比的是数据范围 +4 —— 那是画布框语义之前的行为，已作废。
  const sliders = await ev(`
    const rows = [...document.querySelectorAll('#size-sliders .slider-row')];
    const ranges = rows.map(r => r.querySelector('input[type=range]')).filter(Boolean);
    const E = window.Editor.E;
    const before = {
      size: E.st.size.slice(),
      frame: (E.st.frame || E.st.size).slice(),
      sliderX: ranges.length === 3 ? Number(ranges[0].value) : null,
    };
    if (ranges.length === 3) {
      const r0 = ranges[0];
      r0.value = String(Number(r0.value) + 4);
      r0.dispatchEvent(new Event('input', { bubbles: true }));
      r0.dispatchEvent(new Event('change', { bubbles: true }));
    }
    await new Promise(r => setTimeout(r, 1200));
    const after = {
      size: E.st.size.slice(),
      frame: (E.st.frame || E.st.size).slice(),
      status: document.querySelector('#editor-status').textContent,
    };
    // 右键 → 数字输入框
    const r1 = document.querySelector('#size-sliders input[type=range]');
    r1.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
    await new Promise(r => setTimeout(r, 120));
    const edit = document.querySelector('#size-sliders input.sl-edit');
    const hasEdit = !!edit;
    if (edit) { edit.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); }
    await new Promise(r => setTimeout(r, 120));
    const rows2 = [...document.querySelectorAll('#size-sliders .slider-row')];
    return { rows: rows.length, before, after, hasEdit, rowsAfter: rows2.length };
  `);
  const wantFrame = sliders.before.sliderX === null ? null
    : sliders.before.frame.map((v, i) => (i === 0 ? sliders.before.sliderX + 4 : v));
  check('画布范围滑块（3 条 / 拖动改画布框 / 数据范围只按需放大 / 右键输入）',
        sliders && sliders.rows === 3 && wantFrame !== null &&
        JSON.stringify(sliders.after.frame) === JSON.stringify(wantFrame) &&
        sliders.after.size.every((v, i) => v === Math.max(sliders.before.size[i], wantFrame[i])) &&
        sliders.hasEdit && sliders.rowsAfter === 3, sliders);

  // 6) 模块坐标滑块（选中模块后出现 3 条）
  const posS = await ev(`
    // 点一次是选中；若已选中会 toggle 取消，所以按需要再点一次
    for (let i = 0; i < 3 && !window.Editor.E.selectedPid; i++) {
      const row = document.querySelector('.mod-inst-row');
      if (!row) break;
      row.click();
      await new Promise(r => setTimeout(r, 350));
    }
    const rows = [...document.querySelectorAll('#mod-sliders .slider-row')];
    return { rows: rows.length, sel: !!window.Editor.E.selectedPid };
  `);
  check('模块坐标滑块（选中后 3 条）', posS && posS.rows === 3 && posS.sel, posS);
  await shot('gizmo-3-panel.png');

  // 7) 擦除工具（回归：曾因对象解构数组使 op 缺 x/y/z → 服务端 KeyError 'x'）
  //   打开模块后内容是**装配实例**（可拖动）；画笔只改基地层，所以先「固化装配」。
  await ev(`
    const b = document.querySelector('#btn-mod-bake');
    if (b) b.click();
    await new Promise((r) => setTimeout(r, 3500));
    return (window.Editor.E.placements || []).length;
  `);
  const erase = await ev(`
    const E = window.Editor.E;
    document.querySelector('[data-tool=erase]').click();
    // 在 2D 图层里挑一个实心格，保证擦除一定改变方块数
    const [sx, sy, sz] = E.st.size;
    const inModule = (x, y, z) => E.placements.some((p) => {
      const b = p.bbox;
      return x >= b[0] && x <= b[3] && y >= b[1] && y <= b[4] && z >= b[2] && z <= b[5];
    });
    let cell = null, layer = E.layer;
    for (let y = 0; y < sy && !cell; y++) {
      for (let z = 0; z < sz && !cell; z++) {
        for (let x = 0; x < sx && !cell; x++) {
          // 跳过模块范围：画笔只改基地层，被模块覆盖的格子擦了看不出变化
          if (E.voxels[(y * sz + z) * sx + x] !== 0 && !inModule(x, y, z)) {
            cell = { x, z };
            layer = y;
          }
        }
      }
    }
    if (!cell) return { skip: '没有模块范围外的实心格' };
    E.layer = layer;
    const slider = document.querySelector('#layer-slider');
    if (slider) { slider.value = String(layer); slider.dispatchEvent(new Event('input', { bubbles: true })); }
    await new Promise(r => setTimeout(r, 250));
    const sent = [];
    const orig = App.post;
    App.post = async (url, body, method) => {
      if (String(url).includes('/ops')) sent.push(JSON.parse(JSON.stringify(body)));
      return orig(url, body, method);
    };
    const before = E.st.blocks;
    const canvas = document.querySelector('#layer2d');
    const rect = canvas.getBoundingClientRect();
    const px = rect.left + (cell.x + 0.5) * E.zoom;
    const py = rect.top + (cell.z + 0.5) * E.zoom;
    canvas.dispatchEvent(new PointerEvent('pointerdown', { clientX: px, clientY: py, button: 0, buttons: 1, pointerId: 11, pointerType: 'mouse', bubbles: true, cancelable: true }));
    canvas.dispatchEvent(new PointerEvent('pointerup', { clientX: px, clientY: py, button: 0, buttons: 0, pointerId: 11, pointerType: 'mouse', bubbles: true, cancelable: true }));
    await new Promise(r => setTimeout(r, 1200));
    App.post = orig;
    const op = (sent[0] && sent[0].ops && sent[0].ops[0]) || null;
    return { cell, layer, before, after: E.st.blocks, op,
             toasts: [...document.querySelectorAll('#toasts .toast')].map(t => t.textContent) };
  `);
  const opOk = erase && erase.op &&
    [erase.op.x, erase.op.y, erase.op.z].every((v) => typeof v === 'number') &&
    erase.op.x === erase.cell.x && erase.op.z === erase.cell.z &&
    erase.op.y === erase.layer && erase.op.state === 'minecraft:air';
  const noErr = erase && Array.isArray(erase.toasts) &&
    !erase.toasts.some((t) => t.includes('编辑失败'));
  check('擦除（op 坐标正确 / 方块 -1 / 无报错）',
        !!opOk && noErr && erase.after === erase.before - 1,
        erase && (erase.__err ? erase : { op: erase.op, cell: erase.cell,
          before: erase.before, after: erase.after }));

  console.log('page errors:', errors.length ? errors : []);
  if (errors.length) process.exitCode = 1;
  chrome.kill();
  setTimeout(() => process.exit(process.exitCode || 0), 300);
})().catch((e) => { console.error(e); process.exit(1); });
