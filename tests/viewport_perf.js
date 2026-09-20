/* 视口性能基准（headless Chrome + CDP，无第三方依赖）—— 在线编辑器（mcstudio）用。
 *
 * 用法：
 *   python -m mcstudio serve --port 8617
 *   node tests/viewport_perf.js [chrome] [baseUrl] [outDir]
 *
 * 量的是「打开大建筑到底卡在哪」的六个数：
 *   1. 打开→首帧可见 ms（旧实现 = 等全部网格建完，所以看 meshFirstMs vs meshMs）
 *   2. 打开→网格齐 ms（meshMs）
 *   3. 环绕帧时间 p50 / p95 ms（固定相机路径，直接测 draw()）
 *   4. 每帧 draw call 数（renderer.stats.drawCalls）
 *   5. 2D 俯视图重绘 ms（Editor.render2D()，含 sx×sz 格）
 *   6. /voxels 载荷字节数（gzip 前后）
 *
 * 退出码：任一项超预算 → 1（回归用）。预算见 BUDGET。
 */
'use strict';
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const B = require('./_browser.js');
const chromePath = B.requireChrome(process.argv[2], 'viewport_perf.js');
const base = process.argv[3] || 'http://127.0.0.1:8617';
const outDir = B.shotsDir(process.argv[4], 'viewport_perf');
const PORT = 9411;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 结构 → 预算（毫秒/字节/draw call）。改这个表就是改验收门槛。
const CASES = [
  ['office 96k', 'builds/天际线办公系列/models/高层写字楼.schem',
   { firstMs: 400, workMs: 400, frameMs: 30, drawCalls: 130, map2dMs: 6 }],
  ['math 476k', 'compositions/math-cube/out/数学域.schem',
   { firstMs: 400, workMs: 1400, frameMs: 40, drawCalls: 300, map2dMs: 6 }],
  ['space 590k', 'builds/太空探索者纪念中心/太空探索者纪念中心.schem',
   // 注：workMs 含「顶点池扩容拷贝」，在 headless 软件光栅（SwiftShader）上被放大，
   // 真实 GPU 上这部分便宜一个数量级；draw call 里 trans 仍是逐块（画家算法，未合并）。
   { firstMs: 400, workMs: 6000, frameMs: 60, drawCalls: 700, map2dMs: 6 }],
];

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

(async () => {
  await B.requireServer(base, 'viewport_perf.js');
  // 基准结构来自 builds/ 与组合产物，都是本地内容 → 缺了就跳过
  B.requireData(CASES.map((c) => c[1]), 'viewport_perf.js');
  fs.mkdirSync(outDir, { recursive: true });
  const chrome = spawn(chromePath, [
    '--headless=new', '--no-sandbox', '--disable-dev-shm-usage',
    // 不关节流的话 headless 里 rAF 会被压到几帧/秒，时间分片建网格的墙钟时间会失真
    '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
    '--disable-backgrounding-occluded-windows',
    '--enable-unsafe-swiftshader', '--use-gl=angle', '--use-angle=swiftshader',
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${B.profileDir('viewport-perf')}`,
    '--window-size=1680,1050', 'about:blank'], { stdio: 'ignore' });
  let dbg = null;
  for (let i = 0; i < 60; i++) {
    await sleep(300);
    try {
      const l = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      dbg = l.find((t) => t.type === 'page');
      if (dbg) break;
    } catch (e) { /* retry */ }
  }
  if (!dbg) { console.error('连不上 headless Chrome'); chrome.kill(); process.exit(2); }
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

  const rows = [];
  const fails = [];
  for (const [name, rel, budget] of CASES) {
    const t0 = Date.now();
    await send('Page.navigate', { url: `${base}/?view=editor&open=${encodeURIComponent(rel)}` });
    // 打开→有结构（st 就绪）
    let stReady = -1;
    for (let i = 0; i < 400; i++) {
      await sleep(50);
      const ok = await ev(`return !!(window.Editor && Editor.E && Editor.E.st);`);
      if (ok === true) { stReady = Date.now() - t0; break; }
    }
    // 网格齐
    let meshMs = -1, firstMs = -1;
    for (let i = 0; i < 600; i++) {
      const v = await ev(`
        const v = Editor.E.viewer;
        return v ? { meshMs: v.meshMs, first: v._meshFirstMs, left: (v.renderer && v.renderer.dirty) ? v.renderer.dirty.size : 0 } : null;
      `);
      if (v && v.meshMs >= 0) { meshMs = v.meshMs; firstMs = v.first; break; }
      await sleep(50);
    }
    const info = await ev(`
      const E = Editor.E, v = E.viewer;
      // ---- 环绕帧时间：**固定 3 个机位**各 15 帧取中位数（headless 是软件光栅，
      //      角度带来的填充量差异极大，跑随机角度只会得到噪声）----
      const cfg = [['whole', 0.9], ['near', 0.45], ['axis', 1.35]];
      const med = [];
      let drawCalls = 0;
      const [sx0, sy0, sz0] = E.st.size;
      const tAll = [];
      for (const [, d] of cfg) {
        for (const yaw of [-0.65, 0.9, 2.2]) {
          v.cam.yaw = yaw; v.cam.pitch = 0.5;
          v.cam.target = [sx0 / 2, sy0 * 0.42, sz0 / 2];
          v.cam.dist = Math.max(3, Math.max(sx0, sy0, sz0) * d + 3);
          v.draw();
          const t = [];
          for (let i = 0; i < 15; i++) {
            const a = performance.now();
            v.draw();
            t.push(performance.now() - a);
            drawCalls = Math.max(drawCalls, (v.renderer.stats && v.renderer.stats.drawCalls) || 0);
          }
          t.sort((a, b) => a - b);
          const m = t[7];
          med.push(m);
          tAll.push(m);
        }
      }
      tAll.sort((a, b) => a - b);
      const p50 = tAll[Math.floor(tAll.length / 2)];
      const p95 = tAll[Math.floor(tAll.length * 0.95)];
      // 相机回正（截图用）
      v.cam.yaw = -0.65; v.cam.pitch = 0.55;
      v.cam.dist = Math.max(3, Math.max(sx0, sy0, sz0) * 0.95 + 3);
      // ---- 2D 俯视图重绘 ----
      const m = [];
      for (let i = 0; i < 5; i++) { const a = performance.now(); Editor.render2D(); m.push(performance.now() - a); }
      m.sort((a, b) => a - b);
      const cells = E.st.size[0] * E.st.size[2];
      // ---- 传输：voxels 原始 vs gzip ----
      const raw = E.st.size[0] * E.st.size[1] * E.st.size[2] * 2;
      return { size: E.st.size, blocks: (() => { let n = 0; const V = E.voxels;
                 for (let i = 0; i < V.length; i++) if (V[i]) n++; return n; })(),
               chunks: (v.renderer.chunks ? v.renderer.chunks.size : -1),
               quads: (v.renderer.countQuads ? v.renderer.countQuads() : -1),
               frameP50: Math.round(p50 * 100) / 100, frameP95: Math.round(p95 * 100) / 100,
               frameWorst: Math.round(tAll[tAll.length - 1] * 100) / 100,
               workMs: Math.round(((v.renderer.stats.meshMs || 0) + (v.renderer.stats.uploadMs || 0))),
               meshUploadMs: Math.round(v.renderer.stats.uploadMs || 0),
               drawCalls, dcSolid: v.renderer.stats.dcSolid, dcNocull: v.renderer.stats.dcNocull,
               dcTrans: v.renderer.stats.dcTrans, slots: v.renderer.stats.slots,
               map2dMs: Math.round(m[2] * 100) / 100, map2dCells: cells,
               rawMB: Math.round(raw / 1e5) / 10 };
    `);
    if (info.__err) { fails.push(`${name}: eval 出错 ${info.__err}`); }
    const row = Object.assign({ name, stReady, firstMs, meshMs }, info);
    rows.push(row);
    if (!(row.firstMs >= 0 && row.firstMs <= budget.firstMs)) {
      fails.push(`${name}: 首帧 ${row.firstMs}ms > 预算 ${budget.firstMs}ms`);
    }
    if (!(row.workMs >= 0 && row.workMs <= budget.workMs)) {
      fails.push(`${name}: 建网格+上传工作量 ${row.workMs}ms > 预算 ${budget.workMs}ms`);
    }
    if (!(row.frameP50 <= budget.frameMs)) fails.push(`${name}: 环绕帧中位 ${row.frameP50}ms > 预算 ${budget.frameMs}ms`);
    if (!(row.drawCalls <= budget.drawCalls)) {
      fails.push(`${name}: draw call ${row.drawCalls}（solid ${row.dcSolid}/nocull ${row.dcNocull}/trans ${row.dcTrans}）> 预算 ${budget.drawCalls}`);
    }
    if (!(row.map2dMs <= budget.map2dMs)) fails.push(`${name}: 2D 重绘 ${row.map2dMs}ms > 预算 ${budget.map2dMs}ms`);
    await send('Page.captureScreenshot', { format: 'png' }).then((r) =>
      fs.writeFileSync(path.join(outDir, name.split(' ')[0] + '.png'), Buffer.from(r.data, 'base64')));
  }
  chrome.kill();

  console.log('\n视口性能（在线编辑器 / 大建筑）\n');
  console.log('用例        尺寸            方块      首帧 ms   网格齐 ms   工作量 ms   环绕中位/最差 ms   draw call   2D ms(格数)      /voxels 原始MB');
  for (const r of rows) {
    console.log(
      `${String(r.name).padEnd(11)} ` +
      `${String((r.size || []).join('x')).padEnd(15)} ` +
      `${String(r.blocks).padStart(8)} ` +
      `${String(r.firstMs).padStart(8)} ` +
      `${String(r.meshMs).padStart(10)} ` +
      `${String(r.workMs).padStart(10)} ` +
      `${String(r.frameP50 + '/' + r.frameWorst).padStart(17)} ` +
      `${String(r.drawCalls + ' (s' + r.dcSolid + '/t' + r.dcTrans + ')').padStart(18)} ` +
      `${String(r.map2dMs + ' (' + r.map2dCells + ')').padStart(15)} ` +
      `${String(r.rawMB).padStart(13)}`);
  }
  console.log(`\n截图：${outDir}\n`);
  if (errors.length) console.log('页面异常：', errors.slice(0, 3));
  if (fails.length) {
    console.log('FAILED:');
    for (const f of fails) console.log('  ' + f);
    process.exit(1);
  }
  console.log('ALL PASS（首帧/网格/环绕/2D 都在预算内）');
})();
