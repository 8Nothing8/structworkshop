/** 渲染器 A/B：**我们自己的 mesher** vs **deepslate 原路径**，同一结构、同一机位。
 *
 * 用途：
 *   1. 视觉保真 —— 两边画布逐像素比（平均差 / 品红棋盘像素数）；差主要来自我们多了
 *      AO 与 vanilla 面明暗（deepslate 没有），所以看的是「没有大块缺失/错位」。
 *   2. 性能 —— 首次建网格耗时（ms）与四边形数；增量延迟见 tests/studio_ui_audit.js。
 *
 * 用法：
 *   python -m mcstudio serve --port 8617 &
 *   node tests/renderer_ab.js "<chrome.exe>" http://127.0.0.1:8617 [outDir]
 *
 * 退出码：品红棋盘 > 0、四边形数差 > 15%、两边都没出画 → 1；缺可选数据/浏览器 → 2（跳过）。
 */
'use strict';
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const B = require('./_browser.js');
const chromePath = B.requireChrome(process.argv[2], 'renderer_ab.js');
const base = process.argv[3] || 'http://127.0.0.1:8617';
const CASES = [
  ['bath', 'packs/soviet-khrushchyovka/modules/props/bath_soviet.schem'],
  ['tower', 'builds/天际线办公系列/models/高层写字楼.schem'],
];
// 这两个文件属于**可选数据**（资产包 / 成品归档），纯代码 checkout 里没有 → 跳过。
// 先查数据再建目录，跳过时才不会留下空目录。
B.requireData(CASES.map((c) => c[1]), 'renderer_ab.js');
const outDir = B.shotsDir(process.argv[4], 'renderer_ab');
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

async function connect(wsUrl) {
  const ws = new WebSocket(wsUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0; const pending = new Map(); const errors = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.method === 'Runtime.exceptionThrown') errors.push(JSON.stringify(m.params.exceptionDetails).slice(0, 200));
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
  await B.requireServer(base, 'renderer_ab.js');
  fs.mkdirSync(outDir, { recursive: true });
  const port = 9388;
  const chrome = spawn(chromePath, ['--headless=new', '--no-sandbox', '--disable-dev-shm-usage',
    '--enable-unsafe-swiftshader', '--use-gl=angle', '--use-angle=swiftshader',
    `--remote-debugging-port=${port}`, `--user-data-dir=${B.profileDir('renderer-ab')}`,
    '--window-size=1280,900', 'about:blank'], { stdio: 'ignore' });
  let dbg = null;
  for (let i = 0; i < 60; i++) {
    await sleep(300);
    try {
      const l = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      dbg = l.find((t) => t.type === 'page');
      if (dbg) break;
    } catch (e) { /* retry */ }
  }
  if (!dbg) { console.error('Chrome 起不来'); chrome.kill(); process.exit(1); }
  const { send, errors } = await connect(dbg.webSocketDebuggerUrl);
  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
  const ev = async (expr) => {
    const r = await send('Runtime.evaluate', {
      expression: `(async () => { ${expr} })()`, awaitPromise: true, returnByValue: true,
    });
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 300));
    return r.result.value;
  };

  const results = [];
  const fails = [];
  for (const [name, rel] of CASES) {
    const shots = {};
    const rows = {};
    for (const mode of ['own', 'deepslate']) {
      const q = mode === 'own' ? '' : '&renderer=deepslate';
      await send('Page.navigate', { url: `${base}/?view=editor&open=${encodeURIComponent(rel)}${q}` });
      await sleep(9000);
      const info = await ev(`
        const v = Editor.E.viewer;
        v.fit(1.0);
        v.draw();
        const gl = v.gl, w = gl.drawingBufferWidth, h = gl.drawingBufferHeight;
        const buf = new Uint8Array(w * h * 4);
        gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, buf);
        // 缩略成 256×N 的指纹，便于跨模式比像素（同一机位、同尺寸）
        let magenta = 0, bright = 0;
        const stride = 4;
        const small = [];
        for (let i = 0; i < buf.length; i += 4) {
          const r = buf[i], g = buf[i+1], b = buf[i+2];
          if (r > 180 && g < 90 && b > 180) magenta++;
          if (r + g + b > 120) bright++;
        }
        for (let i = 0; i < buf.length; i += 4 * stride) small.push(buf[i], buf[i+1], buf[i+2]);
        // 全量重建计时（两种模式都能跑，apples to apples）
        let rebuild = -1, quads = -1;
        const t0 = performance.now();
        if (v.rendererMode === 'deepslate') v.renderer.updateStructureBuffers();
        else { v.renderer.rebuildAll(); v.renderer.flush(); }
        rebuild = performance.now() - t0;
        if (v.renderer.countQuads) quads = v.renderer.countQuads();
        else { let n = 0; for (const m of v.renderer.chunkBuilder.getMeshes()) n += m.quadVertices() / 4; quads = n; }
        v.draw();
        return { mode: v.rendererMode, meshMs: v.meshMs || -1, rebuild,
                 quads,
                 pale: (Editor.E.palette || []).length,
                 size: [v.canvas.width, v.canvas.height],
                 magenta, bright, small };
      `);
      rows[mode] = info;
      shots[mode] = info.small;
      const png = await send('Page.captureScreenshot', { format: 'png' });
      fs.writeFileSync(path.join(outDir, `${name}-${mode}.png`), Buffer.from(png.data, 'base64'));
    }
    const a = shots.own || [], b = shots.deepslate || [];
    let diff = 0, n = Math.min(a.length, b.length);
    for (let i = 0; i < n; i++) diff += Math.abs(a[i] - b[i]);
    const mean = n ? diff / n : 255;
    const quadRatio = rows.deepslate.quads > 0 ? rows.own.quads / rows.deepslate.quads : 0;
    results.push({ name,
                   own: { meshMs: rows.own.meshMs, rebuild: rows.own.rebuild, quads: rows.own.quads,
                          magenta: rows.own.magenta, mode: rows.own.mode },
                   deep: { meshMs: rows.deepslate.meshMs, rebuild: rows.deepslate.rebuild, quads: rows.deepslate.quads,
                           magenta: rows.deepslate.magenta, mode: rows.deepslate.mode },
                   meanDiff: mean });
    if (rows.own.magenta > 0 || rows.deepslate.magenta > 0) fails.push(`${name}: 有品红棋盘像素`);
    if (!(quadRatio > 0.85 && quadRatio < 1.15)) fails.push(`${name}: 四边形数比 ${quadRatio.toFixed(2)}（>15% 偏差）`);
    if (rows.own.bright < 1000) fails.push(`${name}: 我们的渲染器没画出东西`);
    if (mean > 30) fails.push(`${name}: 像素平均差 ${mean.toFixed(1)}（可能丢几何/错位）`);
  }
  chrome.kill();

  console.log('\n渲染器 A/B（同一结构 / 同一机位）\n');
  console.log('用例    模式(ours/deep)        首次 meshMs   全量重建 ms(ours/deep)  四边形(ours/deep)     品红(ours/deep)  像素平均差');
  console.log('-'.repeat(120));
  for (const r of results) {
    console.log(`${r.name.padEnd(8)}${(r.own.mode + '/' + r.deep.mode).padEnd(20)}` +
      `${String(r.own.meshMs).padStart(9)}   ` +
      `${String(r.own.rebuild).padStart(10)}/${String(r.deep.rebuild).padEnd(10)}` +
      `${String(r.own.quads).padStart(9)}/${String(r.deep.quads).padEnd(9)}` +
      `${String(r.own.magenta).padStart(6)}/${String(r.deep.magenta).padEnd(8)}` +
      `${r.meanDiff.toFixed(1).padStart(6)}/255`);
  }
  console.log('-'.repeat(96));
  console.log(`截图：${outDir}/{bath,tower}-{own,deepslate}.png（可肉眼对比）`);
  if (errors.length) console.log('页面异常：', errors.slice(0, 3));
  if (fails.length) { console.log('\nFAILED:\n  ' + fails.join('\n  ')); process.exit(1); }
  console.log('\nALL PASS（无品红棋盘 / 四边形数一致 / 像素差在 AO·明暗差异范围内）');
})();
