/* mcstudio UI 布局审计（headless Chrome + CDP，无第三方依赖）。
 *
 * 用法：
 *   python -m mcstudio serve --port 8617        # 先启动服务
 *   node tests/studio_ui_audit.js [chrome] [baseUrl] [outDir]
 *
 * 检查：页面无水平溢出、卡片与预览图、抽屉位置与内容、3D 弹窗 canvas 不越界、
 *       编辑器 2D 画布自适应、调色板/层滑块、运行时异常。
 * 截图默认存到 <repo>/.cache/shots/studio_ui（可 gitignore，不往 C:/tmp 里乱塞）。
 */
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const B = require('./_browser.js');
const chromePath = B.requireChrome(process.argv[2], 'studio_ui_audit.js');
const base = process.argv[3] || 'http://127.0.0.1:8617';
const outDir = B.shotsDir(process.argv[4], 'studio_ui');
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }
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
  await B.requireServer(base, 'studio_ui_audit.js');
  // 这个审计校验的是**模块库 UI**：116 项里绝大多数断言写死了具体模块
  // （modern_lobby 的尺寸/接口、roof_vent_1 的风道开口…），
  // 不是「随便建个空包」能满足的 —— 所以缺包时整脚本跳过，而不是硬跑出一堆假失败。
  // 只想跑「不需要资产包」的那部分编辑器冒烟，请用 tests/editor_tools_smoke.js。
  B.requirePacks('studio_ui_audit.js',
                 '这个审计需要真实资产包（断言里写死了 modern-arch / soviet-khrushchevka 的模块）。\n'
                 + '       把资产包放到 packs/ 下（见 README「仓库里有什么 / 没有什么」）。\n'
                 + '       不想建包就先跑： node tests/editor_tools_smoke.js');
  fs.mkdirSync(outDir, { recursive: true });
  const chrome = spawn(chromePath, ['--headless=new', '--no-sandbox', '--disable-dev-shm-usage',
    '--enable-unsafe-swiftshader', '--use-gl=angle', '--use-angle=swiftshader',
    '--remote-debugging-port=9377', `--user-data-dir=${B.profileDir('studio-ui')}`,
    '--window-size=1680,1050', 'about:blank'], { stdio: 'ignore' });
  let dbg = null;
  for (let i = 0; i < 60; i++) {
    await sleep(300);
    try { const l = await (await fetch('http://127.0.0.1:9377/json/list')).json();
      dbg = l.find((t) => t.type === 'page'); if (dbg) break; } catch (e) { }
  }
  const { send, errors } = await connect(dbg.webSocketDebuggerUrl);
  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1680, height: 1050, deviceScaleFactor: 1, mobile: false });
  const ev = async (expr) => {
    const r = await send('Runtime.evaluate', { expression: `(async () => { ${expr} })()`, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) return { __err: JSON.stringify(r.exceptionDetails).slice(0, 300) };
    return r.result.value;
  };
  const shot = async (name) => {
    const r = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(path.join(outDir, name), Buffer.from(r.data, 'base64'));
  };
  const results = [];
  const check = (n, ok, d) => { results.push({ n, ok }); console.log((ok ? 'PASS' : 'FAIL') + '  ' + n + '  ' + JSON.stringify(d)); };

  // ---------- library ----------
  await send('Page.navigate', { url: base + '/' });
  await sleep(4500);
  const lib = await ev(`
    const main = document.querySelector('main').getBoundingClientRect();
    const drawer = document.querySelector('#drawer').getBoundingClientRect();
    return {
      viewport: [innerWidth, innerHeight],
      doc: [document.documentElement.scrollWidth, document.documentElement.scrollHeight],
      cards: document.querySelectorAll('.card').length,
      imgsLoaded: [...document.querySelectorAll('.card .thumb img')].filter(i => i.naturalWidth > 0).length,
      imgsInView: [...document.querySelectorAll('.card .thumb img')]
        .filter((i) => i.getBoundingClientRect().top < innerHeight && i.getBoundingClientRect().bottom > 0).length,
      imgsLoadedInView: [...document.querySelectorAll('.card .thumb img')]
        .filter((i) => i.getBoundingClientRect().top < innerHeight && i.getBoundingClientRect().bottom > 0
                       && i.naturalWidth > 0).length,
      drawerRect: [Math.round(drawer.left), Math.round(drawer.top), Math.round(drawer.width)],
      mainRect: [Math.round(main.top), Math.round(main.height)],
      stats: document.querySelector('#stats').textContent,
      tags: document.querySelectorAll('.tag-row').length,
      grid: (() => {
        const g = document.querySelector('#module-grid');
        const cards = [...document.querySelectorAll('.card')];
        const cut = cards.filter((c) => c.scrollHeight > c.clientHeight + 1).length;
        const thumbOver = cards.filter((c) =>
          c.querySelector('.thumb').getBoundingClientRect().bottom > c.getBoundingClientRect().bottom + 1).length;
        g.scrollTop = 1e6;
        const last = cards.length ? cards[cards.length - 1].getBoundingClientRect() : null;
        const lastReachable = !!last && last.bottom <= innerHeight + 1 && last.top > 0;
        g.scrollTop = 0;
        const row0 = parseFloat(getComputedStyle(g).gridTemplateRows.split(' ')[0] || '0');
        const cardH = cards.length ? cards[0].getBoundingClientRect().height : 0;
        return { row0: Math.round(row0), cardH: Math.round(cardH), clientH: g.clientHeight,
                 scrollH: g.scrollHeight, scrollbarW: g.offsetWidth - g.clientWidth,
                 thumbOver, cut, scrollable: g.scrollHeight > g.clientHeight + 1, lastReachable,
                 rowsOk: cardH > 130 && Math.abs(row0 - cardH) <= 2 };
      })(),
      overflowing: [...document.querySelectorAll('body *')].filter(e => {
        const r = e.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && (r.right > innerWidth + 2) && getComputedStyle(e).position !== 'fixed';
      }).length,
    };
  `);
  check('库：无水平溢出', lib.doc[0] <= lib.viewport[0] + 2, { doc: lib.doc, viewport: lib.viewport, overflowing: lib.overflowing });
  // 预览图是 loading=lazy：只要求首屏（视口内）的图全部加载完成
  check('库：首屏卡片预览图全加载', lib.cards > 0 && lib.imgsInView > 0 && lib.imgsLoadedInView === lib.imgsInView,
        { cards: lib.cards, inView: lib.imgsInView, loadedInView: lib.imgsLoadedInView, loadedTotal: lib.imgsLoaded });
  check('库：抽屉隐藏时不越界', lib.drawerRect[0] >= lib.viewport[0] - 400, lib.drawerRect);
  // 回归：卡片是 overflow:hidden 滚动容器，行高若用默认 auto 会被摊平成容器高度 → 无滚动条 + 预览图裁切压叠
  check('库：网格行按内容排布且可拖动滚动', lib.grid.rowsOk && lib.grid.thumbOver === 0 && lib.grid.cut === 0
        && lib.grid.scrollable && lib.grid.scrollbarW > 0 && lib.grid.lastReachable, lib.grid);

  // 渲染预览工具栏：范围下拉（全部资产包 + 每个包）+ 两个选项 + 缺图计数与 /api/previews 一致
  const rb = await ev(`
    const api = await (await fetch('/api/previews')).json();
    const host = document.querySelector('#render-pack');
    const input = host.querySelector('.ss-input');
    input.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    input.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await new Promise(r => setTimeout(r, 400));
    const rows = host.querySelectorAll('.ss-pop .ss-row').length;
    const pop = host.querySelector('.ss-pop').getBoundingClientRect();
    const ir = input.getBoundingClientRect();
    const txt = (document.querySelector('#render-status') || {}).textContent || '';
    const label = (document.querySelector('#btn-render-missing') || {}).textContent || '';
    const all = (document.querySelector('#btn-render-all') || {}).textContent || '';
    const want = api.missing > 0 ? ('缺图 ' + api.missing) : '预览齐全';
    document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    return { packs: api.packs.length, total: api.total, missing: api.missing,
             rows, below: Math.round(pop.top - ir.bottom),
             status: txt, label, all,
             okStatus: txt.indexOf(want) >= 0,
             okLabel: label.indexOf('(' + api.missing + ')') >= 0 && all.indexOf('(' + api.total + ')') >= 0 };
  `);
  check('库：渲染预览工具栏（范围下拉可搜 + 缺图/全部 + 计数一致）',
        rb.rows === rb.packs + 1 && rb.below >= 0 && rb.below <= 6 && rb.okStatus && rb.okLabel, rb);

  // 回归：工具栏的“资产包 / 分类”换成可搜索下拉（输入即筛选、选中生效、× 清空），并且已删掉材料筛选
  const ss = await ev(`
    const host = document.querySelector('#pack-filter');
    const input = host.querySelector('.ss-input');
    const total0 = document.querySelectorAll('.card').length;
    input.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    input.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await new Promise(r => setTimeout(r, 250));
    const all = host.querySelectorAll('.ss-pop .ss-row').length;
    input.value = 'khrush';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    await new Promise(r => setTimeout(r, 400));
    const rows = [...host.querySelectorAll('.ss-pop .ss-row')];
    const labels = rows.map(r => r.querySelector('.ss-label').textContent);
    const pop = host.querySelector('.ss-pop').getBoundingClientRect();
    const ir = input.getBoundingClientRect();
    let cards = 0;
    if (rows.length) {
      rows[0].dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
      input.blur();
      await new Promise(r => setTimeout(r, 1200));
      cards = document.querySelectorAll('.card').length;
    }
    const shown = input.value;
    const clearVisible = !host.querySelector('.ss-x').classList.contains('hidden');
    host.querySelector('.ss-x').dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    await new Promise(r => setTimeout(r, 1400));
    const cleared = document.querySelectorAll('.card').length;
    return { all, total0, filtered: labels, cards, shown, clearVisible, cleared,
             matGone: !document.querySelector('#material-filter') && !document.querySelector('#material-list'),
             catSS: !!document.querySelector('#cat-filter .ss-input'),
             below: Math.round(pop.top - ir.bottom) };
  `);
  const ssHit = ss.filtered.length === 1 && /khrush/i.test(ss.filtered[0]);
  check('库：资产包下拉可搜索（超过 4 个包 + 输入即筛选 → 选中筛全库 → × 清空恢复）',
        ss.all >= 5 && ssHit && ss.shown === ss.filtered[0] && ss.clearVisible &&
        ss.cards > 0 && ss.cards < ss.total0 && ss.cleared === ss.total0 &&
        ss.below >= 0 && ss.below <= 6, ss);
  check('库：分类也是可搜索下拉 + “材料筛选”已移除',
        ss.catSS && ss.matGone, { catSS: ss.catSS, matGone: ss.matGone });

  // 回归：默认「按分组」下同时选两个同命名空间标签必须出结果（旧行为 AND 直接空列表且无提示）
  const facet = await ev(`
    const rows = [...document.querySelectorAll('.tag-row')]
      .filter((r) => parseInt(r.querySelector('.count').textContent) > 0);
    const ns = (n) => (n.includes(':') ? n.split(':')[0] : '(none)');
    const byNs = {};
    for (const r of rows) (byNs[ns(r.title)] = byNs[ns(r.title)] || []).push(r.title);
    const pair = Object.values(byNs).find((g) => g.length >= 2);
    if (!pair) return { skipped: true };
    const row = (t) => [...document.querySelectorAll('.tag-row')].find((r) => r.title === t);
    row(pair[0]).click(); await new Promise((r) => setTimeout(r, 900));
    const first = document.querySelectorAll('.card').length;
    row(pair[1]).click(); await new Promise((r) => setTimeout(r, 1200));
    const out = { tags: [pair[0], pair[1]], first,
      cards: document.querySelectorAll('.card').length,
      emptyShown: !document.querySelector('#grid-empty').classList.contains('hidden'),
      emptyText: document.querySelector('#grid-empty').textContent,
      mode: (document.querySelector('input[name=tagmode]:checked') || {}).value,
      counts: ['facet', 'and', 'or'].map((m) => (document.getElementById('cnt-' + m) || {}).textContent || '') };
    document.querySelector('#tag-clear').click(); await new Promise((r) => setTimeout(r, 900));
    out.afterClear = document.querySelectorAll('.card').length;
    return out;
  `);
  check('库：两个同组标签默认「按分组」有结果 + 三模式计数', !facet.skipped && facet.mode === 'facet'
        && facet.first > 0 && facet.cards >= facet.first && !facet.emptyShown
        && facet.counts.every((c) => c.startsWith('·')) && facet.afterClear === lib.cards, facet);
  await shot('01-library.png');

  // drawer open geometry
  await ev(`document.querySelector('.card .title').click(); return true;`);
  await sleep(1200);
  const dr = await ev(`
    const r = document.querySelector('#drawer').getBoundingClientRect();
    const m = document.querySelector('main').getBoundingClientRect();
    const img = document.querySelector('#drawer img.preview');
    return { rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
             main: [Math.round(m.top), Math.round(m.height)],
             img: img ? [img.naturalWidth, img.naturalHeight] : null,
             statsRows: document.querySelectorAll('#drawer .stat-list div').length };
  `);
  check('抽屉：在 main 内（不盖顶栏）', dr.rect[1] >= dr.main[0], dr);
  check('抽屉：预览图已加载 + 统计有行', dr.img && dr.img[0] > 0 && dr.statsRows > 0, { img: dr.img, rows: dr.statsRows });
  // 封面图 URL 必须带版本号：重渲染后 URL 变 → 浏览器才会拉新图
  //（否则「重渲染成功但模块库封面还是旧的」）
  const coverV = await ev(`
    const img = document.querySelector('.card .thumb img');
    const drawerImg = document.querySelector('#drawer img.preview');
    return { card: img ? img.getAttribute('src') : null,
             drawer: drawerImg ? drawerImg.getAttribute('src') : null };
  `);
  check('库：封面图 URL 带版本号（重渲染后不会被浏览器缓存钉住）',
        !!coverV.card && coverV.card.includes('?v=') &&
        !!coverV.drawer && coverV.drawer.includes('?v='), coverV);
  // 抽屉里的「复制 / 转移到别的资产包」（4 个包 → 至少能选到别的包）
  const xfer = await ev(`
    const sel = document.querySelector('#drawer .drawer-xfer select');
    const btns = [...document.querySelectorAll('#drawer .drawer-xfer button')]
      .map((b) => b.textContent);
    return { hasSel: !!sel, packs: sel ? [...sel.options].map((o) => o.value) : [],
             btns, curPack: document.querySelector('#drawer .kv b') ?
               document.querySelector('#drawer .kv b').textContent : null };
  `);
  check('库：抽屉可「复制 / 转移」到别的资产包（下拉里没有当前包）',
        xfer.hasSel && xfer.btns.join() === '复制到,转移到' &&
        xfer.packs.length >= 3 && !xfer.packs.includes(xfer.curPack), xfer);
  // 抽屉里的接口编辑器（模块库也能改）：行 + 面（带轴向）/类型（可搜索+可自写）/偏移/尺寸/保存
  const portEdit = await ev(`
    const list = document.querySelectorAll('#drawer .port-edit-row');
    const form = document.querySelector('#drawer .port-edit-form');
    const sel = document.querySelectorAll('#drawer .port-edit-form select');
    const faceOpts = [...document.querySelectorAll('#drawer .port-edit-form select option')]
      .map((o) => o.textContent).join(' | ');
    const nums = document.querySelectorAll('#drawer .port-edit-form input[type=number]');
    const btns = [...document.querySelectorAll('#drawer .port-edit-form button')].map((b) => b.textContent);
    const host = document.querySelector('#drawer-port-type');
    return { rows: list.length, hasForm: !!form, sels: sel.length, nums: nums.length, btns,
             faceOpts, typeSS: !!(host && host.querySelector('.ss input')),
             hasPortWord: document.querySelector('#drawer').innerText.includes('端口') };
  `);
  check('库：抽屉里接口可编辑（列表 + 面（带轴向）/形状/类型（可搜索可自写）/两点 + 保存）',
        !!portEdit.hasForm && portEdit.sels === 2 && portEdit.nums === 4 &&
        portEdit.btns.includes('保存接口') && portEdit.typeSS === true &&
        portEdit.faceOpts.includes('−X') && portEdit.hasPortWord === false, portEdit);

  // 标签编辑 = Obsidian 式属性表（默认 tag 属性 + 自定义属性 + `;` 分隔 + 可换行）
  const propsEd = await ev(`
    const ta = document.querySelector('#drawer textarea.props-text');
    if (!ta) return { err: '抽屉里没有属性表 textarea' };
    const before = ta.value;
    const parsed = App.parseProps('tag: a; b\\n尺寸: 9x6x8; 5x5');
    const round = App.propsText(['office', 'lobby', '尺寸:9x6x8']);
    ta.value = 'tag: probe_one; probe_two\\n楼层: 3';
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    const hint = (document.querySelector('#drawer .props-hint') || {}).textContent;
    ta.value = before;                       // 还原，不影响后续检查
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    return { multi: ta.tagName === 'TEXTAREA', rows: ta.rows, before, parsed, round, hint,
             hasDefaultTag: /^tag:/.test(before) };
  `);
  check('库：标签 = Obsidian 式属性表（默认 tag 属性 + 自定义属性 + ; 分隔 + 可换行 + 实时读数）',
        propsEd && !propsEd.err && propsEd.multi && propsEd.rows >= 3 &&
        propsEd.hasDefaultTag &&
        JSON.stringify(propsEd.parsed) === JSON.stringify(['a', 'b', '尺寸:9x6x8', '尺寸:5x5']) &&
        propsEd.round === 'tag: office; lobby\n尺寸: 9x6x8' &&
        /3 个标签/.test(propsEd.hint || '') && /1 个自定义属性/.test(propsEd.hint || ''), propsEd);

  const drawerCustom = await ev(`
    const host = document.querySelector('#drawer-port-type');
    const input = host.querySelector('.ss input');
    input.value = 'my_shaft';                       // 自己写的类型
    input.dispatchEvent(new Event('input', { bubbles: true }));
    await new Promise((r) => setTimeout(r, 60));
    const rows = [...host.querySelectorAll('.ss-row')].map((x) => x.textContent);
    return { first: rows.length ? rows[0] : '', n: rows.length };
  `);
  check('库：抽屉里的接口类型也能自己写（候选首行 = 自定义）',
        drawerCustom.first.includes('my_shaft'), drawerCustom);
  // 「在编辑器中打开」必须真的打开：抽屉给的 entry.path 是**包内相对**，
  // 直接发给 /api/structure/open 会被权限拦住（旧 bug：不允许访问: <包>/modules/…）
  const openFromLib = await ev(`
    const mid = document.querySelector('#drawer .head h3').textContent.trim();
    const btn = [...document.querySelectorAll('#drawer .tool-buttons button')]
      .find((b) => b.textContent.includes('在编辑器中打开'));
    if (!btn) return { err: '抽屉里没找到「在编辑器中打开」' };
    btn.click();
    for (let i = 0; i < 40; i++) {            // 等编辑器真的把结构加载进来
      await new Promise((res) => setTimeout(res, 500));
      const E = window.Editor.E;
      if (E.st && E.st.name === mid) break;
    }
    const E = window.Editor.E;
    const out = { mid, name: E.st ? E.st.name : null, path: E.st ? E.st.path : null,
                  status: document.querySelector('#editor-status').textContent,
                  toasts: [...document.querySelectorAll('.toast')].map((t) => t.textContent) };
    window.Editor.switchTab('library');      // 回到模块库，不影响后面的检查
    return out;
  `);
  check('库：「在编辑器中打开」真的打开（仓库相对路径，不再“不允许访问”）',
        openFromLib && !openFromLib.err && openFromLib.name === openFromLib.mid &&
        /^packs\//.test(openFromLib.path || '') &&
        !(openFromLib.toasts || []).some((t) => /不允许访问|打开失败/.test(t)),
        openFromLib);
  await shot('02-drawer.png');
  await ev(`document.querySelector('#drawer .head button').click(); return true;`);

  // ---------- 3D modal ----------
  await ev(`await App.viewStructure('packs/modern-arch/modules/rooms/modern_lobby.schem', { title: 'modern_lobby' }); return true;`);
  await sleep(10000);
  const vm = await ev(`
    const modal = document.querySelector('#modal-root .modal');
    const body = modal.querySelector('.body');
    const canvas = modal.querySelector('canvas');
    const mr = modal.getBoundingClientRect(), cr = canvas.getBoundingClientRect();
    return { rect: [Math.round(cr.left), Math.round(cr.top), Math.round(cr.width), Math.round(cr.height)],
             modal: [Math.round(mr.width), Math.round(mr.height)],
             canvas: [Math.round(cr.width), Math.round(cr.height)],
             bodyOverflowX: body.scrollWidth - body.clientWidth,
             fits: cr.right <= mr.right + 1 && cr.width <= body.clientWidth + 2,
             hud: modal.querySelector('.kv') ? modal.querySelector('.kv').textContent.slice(0, 80) : null };
  `);
  check('3D 弹窗：canvas 不超出弹窗', vm.fits && vm.bodyOverflowX <= 2, vm);
  await shot('03-viewer3d.png');
  await ev(`document.querySelectorAll('#modal-root .modal-mask').forEach(e => e.remove()); return true;`);

  // ---------- editor ----------
  await send('Page.navigate', { url: base + '/?view=editor&open=packs/modern-arch/modules/rooms/modern_lobby.schem' });
  await sleep(15000);
  const ed = await ev(`
    const c2 = document.querySelector('#layer2d');
    const r2 = c2.getBoundingClientRect();
    const gl = document.querySelector('#gl-canvas');
    const gr = gl.getBoundingClientRect();
    const panel = document.querySelector('.tool-panel').getBoundingClientRect();
    return {
      st: Editor.E.st ? [Editor.E.st.name, Editor.E.st.size] : null,
      zoom: Editor.E.zoom,
      canvas2d: [Math.round(c2.width), Math.round(c2.height), Math.round(r2.width), Math.round(r2.height)],
      glRect: [Math.round(gr.width), Math.round(gr.height)],
      panelRect: [Math.round(panel.width), Math.round(panel.height)],
      paletteRows: document.querySelectorAll('.palette-row').length,
      layerMax: document.querySelector('#layer-slider').max,
      layerLabel: document.querySelector('#layer2d-info').textContent,
      status: document.querySelector('#editor-status').textContent,
      buttons: [...document.querySelectorAll('.editor-toolbar button')].map(b => b.textContent.trim()),
      doc: [document.documentElement.scrollWidth, document.documentElement.scrollHeight],
    };
  `);
  check('编辑器：打开模块且工具栏完整', ed.st && ed.st[0] === 'modern_lobby' && ed.buttons.length >= 6, { st: ed.st, buttons: ed.buttons });
  check('编辑器：2D 画布自动适配（不拉伸）', ed.canvas2d[0] === ed.canvas2d[2] * 2 && ed.canvas2d[1] === ed.canvas2d[3] * 2 && ed.canvas2d[2] > 100,
        ed.canvas2d);
  check('编辑器：调色板 + 层滑块', ed.paletteRows >= 5 && Number(ed.layerMax) === ed.st[1][1] - 1, { rows: ed.paletteRows, layerMax: ed.layerMax, label: ed.layerLabel });

  // 结构编辑器：「打开结构」= 可搜索下拉（输入即搜仓库文件，选中即开；选完回到搜索框形态）
  const openSS = await ev(`
    const host = document.querySelector('#open-file');
    const input = host.querySelector('.ss-input');
    const total0 = document.querySelectorAll('#open-file .ss-pop .ss-row').length;
    input.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    input.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await new Promise(r => setTimeout(r, 900));
    const rows0 = [...host.querySelectorAll('.ss-pop .ss-row')];
    const pop = host.querySelector('.ss-pop').getBoundingClientRect();
    const ir = input.getBoundingClientRect();
    const below = Math.round(pop.top - ir.bottom);
    input.value = 'modern_lobby';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    await new Promise(r => setTimeout(r, 900));
    const rows = [...host.querySelectorAll('.ss-pop .ss-row')];
    const labels = rows.map(r => r.querySelector('.ss-label').textContent);
    if (rows.length) rows[0].dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    await new Promise(r => setTimeout(r, 6000));
    return { total0, recent: rows0.length, recentOk: rows0.every(r => /\.(schem|litematic)$/.test(r.querySelector('.ss-label').textContent)),
             hits: labels.length, labels: labels.slice(0, 3), below,
             name: Editor.E.st ? Editor.E.st.name : null,
             size: Editor.E.st ? Editor.E.st.size : null,
             inputVal: input.value,
             filesEndpoint: '/api/files?limit=40&q=' };
  `);
  check('编辑器：“打开结构”可搜索（向下展开最近文件 → 输入筛选 → 选中真打开）',
        openSS.recent >= 5 && openSS.recentOk && openSS.below >= 0 && openSS.below <= 6 &&
        openSS.hits >= 1 && openSS.labels[0].includes('modern_lobby') &&
        openSS.name === 'modern_lobby' && JSON.stringify(openSS.size) === JSON.stringify([9, 6, 8]) &&
        openSS.inputVal === '',
        openSS);
  await shot('04c-open-search.png');

  // 结构编辑器：模块装配里「资产包」筛选与模块搜索联合生效
  const modPack = await ev(`
    const host = document.querySelector('#mod-pack');
    const input = host.querySelector('.ss-input');
    input.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    input.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await new Promise(r => setTimeout(r, 400));
    const opts = [...host.querySelectorAll('.ss-pop .ss-row')].map(r => r.dataset.value);
    input.value = 'khrush';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    await new Promise(r => setTimeout(r, 400));
    const hit = host.querySelector('.ss-pop .ss-row');
    const wanted = hit ? hit.dataset.value : null;
    if (hit) hit.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    await new Promise(r => setTimeout(r, 2500));
    const rows = [...document.querySelectorAll('#mod-results .mod-result-row:not(.blank)')];
    const packs = [...new Set(rows.map(r => (r.querySelector('.mr-pack') || {}).textContent))];
    // 清掉筛选：结果应跨多个包（或至少换一批）
    host.querySelector('.ss-x').dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    await new Promise(r => setTimeout(r, 2500));
    const rows2 = [...document.querySelectorAll('#mod-results .mod-result-row:not(.blank)')];
    return { opts: opts.length, wanted, rows: rows.length, packs, cleared: rows2.length,
             nowValue: input.value };
  `);
  check('编辑器：模块装配的“资产包”筛选与搜索联合生效',
        modPack.opts >= 5 && modPack.rows > 0 && modPack.packs.length === 1 &&
        modPack.packs[0] === modPack.wanted && modPack.cleared > 0,
        modPack);
  // 建空画布只有「新建」一个入口：模块列表里不该再有一行「打开空白画布」
  const noBlank = await ev(`
    const rows = [...document.querySelectorAll('#mod-results .mod-result-row')];
    return { n: rows.length, ids: rows.map((r) => r.querySelector('.mr-id').textContent),
             hasNew: !!document.querySelector('#btn-new-canvas') };
  `);
  check('编辑器：模块装配列表里没有「打开空白画布」行（建画布走顶栏「新建」）',
        noBlank && noBlank.n > 0 && noBlank.hasNew &&
        !noBlank.ids.some((t) => /空白/.test(t)), noBlank);
  // 模块中心手柄要**略小于一格**：世界尺寸盒必须恰好 2×half（旧 bug 走格索引盒会被 +1 撑成 2r+1）
  const handleSize = await ev(`
    const v = Editor.E.viewer;
    const half = v.moduleHandleHalf(true);
    const [a, b] = v._worldCorners([10, 10, 10], half);
    const [ca, cb] = v._cellCorners([0, 0, 0, 0, 0, 0], 0.01);
    return { half, box: [b[0] - a[0], b[1] - a[1], b[2] - a[2]],
             cell: [cb[0] - ca[0], cb[1] - ca[1], cb[2] - ca[2]] };
  `);
  check('编辑器：模块中心手柄略小于一格（世界尺寸盒 = 2×half，不再被格索引 +1 撑大）',
        handleSize && handleSize.half <= 0.42 &&
        Math.abs(handleSize.box[0] - 2 * handleSize.half) < 1e-9 &&
        Math.abs(handleSize.cell[0] - 1.02) < 1e-9, handleSize);
  await shot('04d-mod-pack.png');
  check('编辑器：无页面溢出', ed.doc[0] <= lib.viewport[0] + 2, ed.doc);

  // ---------- 全站滚动条：写在 :root，靠继承覆盖所有滚动容器 ----------
  // 旧写法是一串白名单选择器（.grid/.tool-panel/…），弹窗 .modal .body、设置页
  // #view-settings、可搜索下拉 .ss .ss-pop、抽屉属性文本框、代码块 pre 全漏在外面，
  // 那些地方一直是系统浅色滚动条。
  const sb = await ev(`
    const want = 'rgb(70, 85, 107) rgb(26, 30, 35)';
    const real = (sel) => {
      const n = document.querySelector(sel);
      return n ? getComputedStyle(n).scrollbarColor : '(缺元素)';
    };
    // 弹窗 / 抽屉这些「现在还没建出来」的容器：放同层探针验证继承真的到得了
    const probe = (sel) => {
      const host = document.querySelector(sel) || document.body;
      const d = document.createElement('div');
      d.style.overflow = 'auto'; d.style.maxHeight = '8px';
      host.appendChild(d);
      const v = getComputedStyle(d).scrollbarColor;
      d.remove();
      return v;
    };
    const root = getComputedStyle(document.documentElement);
    return { want, root: root.scrollbarColor, scheme: root.colorScheme,
             tool: real('.tool-panel'), left: real('.left-panel'),
             settings: real('#view-settings'), pop: real('.ss .ss-pop'),
             grid: real('.grid'),
             modal: probe('#modal-root'), drawer: probe('#drawer'),
             deep: probe('#drawer .content') };
  `);
  const sbOK = (v) => v === sb.want;
  check('滚动条：全站深色（写在 :root，继承到弹窗/设置页/下拉/抽屉）',
        sbOK(sb.root) && sbOK(sb.tool) && sbOK(sb.left) && sbOK(sb.settings) &&
        sbOK(sb.pop) && sbOK(sb.grid) && sbOK(sb.modal) && sbOK(sb.drawer) &&
        sbOK(sb.deep),
        sb);
  check('滚动条：color-scheme: dark（原生下拉/控件的弹出层也跟着变深）',
        sb.scheme === 'dark', { scheme: sb.scheme });

  // ---------- 打开即实例：左上角「打开结构」进来的投影也是可拖实例 ----------
  const openedInst = await ev(`
    const E = Editor.E;
    return { n: E.placements.length,
             pack: E.placements[0] ? E.placements[0].pack : null,
             id: E.placements[0] ? E.placements[0].id : null,
             dirty: E.st.dirty, undo: E.st.can_undo,
             tool: E.tool, count: document.querySelector('#mod-count').textContent,
             rows: document.querySelectorAll('#mod-instances .mod-inst-row').length };
  `);
  check('编辑器：左上角「打开结构」打开的资产包模块就是可拖实例（不再是一盘散沙）',
        openedInst.n === 1 && openedInst.pack === 'modern-arch' &&
        openedInst.id === 'modern_lobby' && openedInst.tool === 'move' &&
        openedInst.rows === 1 && openedInst.count.includes('1'),
        openedInst);
  check('编辑器：打开文件不标「未保存」、不占撤销栈（markDirty/record = false）',
        openedInst.dirty === false && openedInst.undo === false, openedInst);

  // ---------- 叠加层只在移动/复制时画：切到放置就收起 ----------
  const ov0 = await ev(`
    const E = Editor.E, v = E.viewer;
    return { tool: E.tool, boxes: v.moduleBoxes.length, gizmo: !!v.gizmo,
             modWin: !document.querySelector('#mod-meta-win').classList.contains('hidden'),
             id: document.querySelector('#mod-meta-id').textContent,
             inst: document.querySelector('#mod-meta-inst').textContent };
  `);
  check('编辑器：移动工具下模块手柄 + 三箭头都在，左栏是该模块的属性',
        ov0.tool === 'move' && ov0.boxes === 1 && ov0.gizmo === true &&
        ov0.modWin === true && ov0.id.includes('modern_lobby') &&
        ov0.inst.includes('实例'), ov0);

  const ov1 = await ev(`
    document.querySelector('[data-tool=place]').click();
    await new Promise(r => setTimeout(r, 500));
    const E = Editor.E, v = E.viewer;
    return { tool: E.tool, boxes: v.moduleBoxes.length, gizmo: !!v.gizmo,
             sel: E.selectedPid,
             modWin: !document.querySelector('#mod-meta-win').classList.contains('hidden'),
             inst: document.querySelector('#mod-meta-inst').textContent,
             id: document.querySelector('#mod-meta-id').textContent };
  `);
  check('编辑器：切到「放置」→ 手柄/箭头/2D 脚框全收起（当普通方块用）',
        ov1.tool === 'place' && ov1.boxes === 0 && ov1.gizmo === false &&
        ov1.sel === null, ov1);
  check('编辑器：切工具后左栏回到「文件本身」的属性（实例行消失）',
        ov1.modWin === true && ov1.id.includes('modern_lobby') &&
        !ov1.inst.trim(), ov1);

  // 回「移动」→ 手柄回来；点中心小方块 → 又切回那个模块的属性
  const ov2 = await ev(`
    document.querySelector('[data-tool=move]').click();
    await new Promise(r => setTimeout(r, 600));
    const E = Editor.E, v = E.viewer;
    const back = { tool: E.tool, boxes: v.moduleBoxes.length };
    // 真·鼠标：在画布上找到能命中手柄的像素，在哪儿点一下
    const c = document.querySelector('#gl-canvas');
    const r = c.getBoundingClientRect();
    let pt = null;
    for (let y = 8; y < r.height - 8 && !pt; y += 4) {
      for (let x = 8; x < r.width - 8 && !pt; x += 4) {
        if (v.pickModuleHandle({ clientX: r.left + x, clientY: r.top + y })) pt = { x, y };
      }
    }
    if (!pt) return Object.assign(back, { err: '找不到手柄的屏幕位置' });
    const mk = (type) => new PointerEvent(type, {
      clientX: r.left + pt.x, clientY: r.top + pt.y, button: 0,
      buttons: type === 'pointerup' ? 0 : 1, bubbles: true,
      pointerId: 71, pointerType: 'mouse', isPrimary: true });
    c.dispatchEvent(mk('pointerdown')); c.dispatchEvent(mk('pointerup'));
    await new Promise(res => setTimeout(res, 1500));
    return Object.assign(back, {
      pt, sel: Editor.E.selectedPid, gizmo: !!v.gizmo,
      modWin: !document.querySelector('#mod-meta-win').classList.contains('hidden'),
      id: document.querySelector('#mod-meta-id').textContent,
      inst: document.querySelector('#mod-meta-inst').textContent,
      moveBox: !document.querySelector('#mod-move').classList.contains('hidden') });
  `);
  check('编辑器：切回「移动」→ 手柄回来；点中心小方块 → 左栏是该模块的属性',
        ov2.tool === 'move' && ov2.boxes === 1 && !ov2.err &&
        !!ov2.sel && ov2.gizmo === true && ov2.modWin === true &&
        ov2.id.includes('modern_lobby') && ov2.inst.includes('实例') &&
        ov2.moveBox === true, ov2);

  // ---------- 普通投影（不在资产包里）：打开也是整幅可拖实例，存盘后重开还在 ----------
  const selfWrap = await ev(`
    const E = Editor.E;
    const SELF = '.cache/mcstudio/_audit_self.schem';
    // 用 API 造一个普通结构（不是资产包模块），另存到 .cache/mcstudio（gitignore）
    const s = await App.post('/api/structure/new', { size: [10, 6, 10], name: '_audit_self' });
    await App.post('/api/structure/' + s.sid + '/ops',
      { ops: [{ type: 'set', x: 2, y: 2, z: 2, state: 'minecraft:stone' }] });
    await App.post('/api/structure/' + s.sid + '/save-as', { path: SELF });
    // 从左上角那一条路打开它
    await Editor.openPath(SELF);
    await new Promise(r => setTimeout(r, 2500));
    const p0 = Editor.E.placements[0] || {};
    const before = { n: Editor.E.placements.length, pack: p0.pack, id: p0.id,
                     dirty: Editor.E.st.dirty, undo: Editor.E.st.can_undo,
                     rows: document.querySelectorAll('#mod-instances .mod-inst-row').length,
                     tool: Editor.E.tool,
                     // 没有选中模块 + 不是资产包模块 → 左栏应该显示**当前投影属性**
                     projWin: !document.querySelector('#proj-meta-win').classList.contains('hidden'),
                     modWin: !document.querySelector('#mod-meta-win').classList.contains('hidden'),
                     projName: document.querySelector('#proj-meta-name').textContent,
                     projKV: document.querySelector('#proj-meta-kv').textContent };
    Editor.selectPlacement(null, false);      // 先取消选中，看「没点击」时显示什么
    await new Promise(r => setTimeout(r, 400));
    const noSel = {
      projWin: !document.querySelector('#proj-meta-win').classList.contains('hidden'),
      modWin: !document.querySelector('#mod-meta-win').classList.contains('hidden'),
    };
    Editor.selectPlacement(p0.pid, false);    // 再选回来，接着键盘搬
    await new Promise(r => setTimeout(r, 300));
    before.noSel = noSel;
    // 整幅搬 +3 X：用真·键盘（选中实例后 ←→ X、↑↓ Z、PgUp/PgDn Y）
    document.querySelector('[data-tool=move]').click();
    await new Promise(r => setTimeout(r, 300));
    Editor.selectPlacement(p0.pid, false);
    await new Promise(r => setTimeout(r, 300));    for (let i = 0; i < 3; i++) {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
      await new Promise(r => setTimeout(r, 700));
    }
    const st = await App.api('/api/structure/' + Editor.E.st.sid);
    const buf = new Uint16Array(await (await fetch('/api/structure/' + Editor.E.st.sid + '/voxels')).arrayBuffer());
    const [sx, sy, sz] = st.size;
    const at = (x, y, z) => st.palette[buf[(y * sz + z) * sx + x]];
    const p1 = Editor.E.placements[0] || {};
    return Object.assign(before, { moved: at(5, 2, 2), old: at(2, 2, 2),
                                   pos: p1.pos, dirtyAfter: Editor.E.st.dirty });
  `);
  check('编辑器：左上角打开普通投影 = 整幅实例（pack=@self），键盘能整体搬 +3 格',
        selfWrap.n === 1 && selfWrap.pack === '@self' &&
        selfWrap.id === '_audit_self' && selfWrap.dirty === false &&
        selfWrap.undo === false && selfWrap.rows === 1 &&
        selfWrap.tool === 'move' &&
        selfWrap.moved === 'minecraft:stone' && selfWrap.old === 'minecraft:air' &&
        JSON.stringify(selfWrap.pos) === JSON.stringify([3, 0, 0]) &&
        selfWrap.dirtyAfter === true, selfWrap);
  check('编辑器：普通投影的左栏 = 「当前投影属性」（选中整幅实例与否都是它）',
        selfWrap.projWin === true && selfWrap.modWin === false &&
        selfWrap.noSel && selfWrap.noSel.projWin === true &&
        selfWrap.noSel.modWin === false &&
        selfWrap.projName === '_audit_self' &&
        selfWrap.projKV.includes('尺寸') && selfWrap.projKV.includes('方块'),
        { noSel: selfWrap.noSel, name: selfWrap.projName, kv: selfWrap.projKV.slice(0, 120) });
  // 存盘 → 重开：体素不推两遍，而且**还是**可拖实例
  const selfReopen = await ev(`
    const SELF = '.cache/mcstudio/_audit_self.schem';
    await App.post('/api/structure/' + Editor.E.st.sid + '/save', {});
    const st = await App.post('/api/structure/open', { path: SELF });
    const buf = new Uint16Array(await (await fetch('/api/structure/' + st.sid + '/voxels')).arrayBuffer());
    const [sx, sy, sz] = st.size;
    const at = (x, y, z) => st.palette[buf[(y * sz + z) * sx + x]];
    const pl = await App.api('/api/structure/' + st.sid + '/modules');
    return { restored: st.restore.restored, placements: st.placements,
             pack: pl.placements[0] ? pl.placements[0].pack : null,
             dirty: st.dirty, moved: at(5, 2, 2), old: at(2, 2, 2) };
  `);
  check('编辑器：整幅实例存盘后重开——体素不推两遍、仍是一个可拖实例',
        selfReopen.restored === 1 && selfReopen.placements === 1 &&
        selfReopen.pack === '@self' && selfReopen.dirty === false &&
        selfReopen.moved === 'minecraft:stone' && selfReopen.old === 'minecraft:air',
        selfReopen);
  // 回到资产包模块 + 放置工具，后面的检查继续用
  await ev(`
    await Editor.openPath('packs/modern-arch/modules/rooms/modern_lobby.schem');
    await new Promise(r => setTimeout(r, 2500));
    document.querySelector('[data-tool=place]').click();
    return true;
  `);

  // 框选工具：**不用去选区面板点任何按钮**，3D 里左键点两个对角就成框（右键清除）
  const boxsel = await ev(`
    const E = Editor.E, v = E.viewer;
    document.querySelector('[data-tool=select]').click();
    await new Promise(r => setTimeout(r, 400));
    const c = v.canvas, r = c.getBoundingClientRect();
    const pts = [];
    for (let y = 20; y < r.height - 20 && pts.length < 2; y += 7) {
      for (let x = 20; x < r.width - 20 && pts.length < 2; x += 7) {
        const h = v.pick({ clientX: r.left + x, clientY: r.top + y });
        if (!h) continue;
        if (!pts.some(p => p.cell.join() === h.cell.join())) pts.push({ x, y, cell: h.cell.slice() });
      }
    }
    if (pts.length < 2) return { err: 'pick 不到两个格子' };
    const click = (type, x, y, button) => c.dispatchEvent(new PointerEvent(type, {
      clientX: r.left + x, clientY: r.top + y, button: button || 0,
      buttons: type === 'pointerup' ? 0 : (button === 2 ? 2 : 1),
      pointerId: 3, pointerType: 'mouse', bubbles: true, cancelable: true }));
    click('pointerdown', pts[0].x, pts[0].y); click('pointerup', pts[0].x, pts[0].y);
    const afterFirst = (Editor.AX.pickA || []).slice();
    click('pointerdown', pts[1].x, pts[1].y); click('pointerup', pts[1].x, pts[1].y);
    await new Promise(r2 => setTimeout(r2, 300));
    const sel = Editor.AX.sel ? Editor.AX.sel.slice() : null;
    click('pointerdown', pts[0].x, pts[0].y, 2); click('pointerup', pts[0].x, pts[0].y, 2);
    await new Promise(r2 => setTimeout(r2, 300));
    return { cells: pts.map(p => p.cell), afterFirst, sel, cleared: Editor.AX.sel,
             info: (document.querySelector('#ax-sel-info') || {}).textContent };
  `);
  check('编辑器：框选工具左键直接两点框选（不用先去面板点按钮），右键清除',
        boxsel && boxsel.afterFirst && boxsel.afterFirst.length === 3 &&
        Array.isArray(boxsel.sel) && boxsel.sel.length === 6 && boxsel.cleared === null,
        boxsel);

  // 「方块更新」开关：默认在放置工具下显示、勾选；换到不改方块的工具就隐藏
  const upd0 = await ev(`
    document.querySelector('[data-tool=place]').click();   // 打开模块后默认在「移动」，先切回放置
    const row = document.querySelector('#place-update');
    const box = document.querySelector('#place-update-on');
    const r = row.getBoundingClientRect();
    return {
      visible: !row.classList.contains('hidden') && r.height > 0,
      checked: box.checked,
      state: Editor.E.update,
      hasRecompute: !!document.querySelector('#btn-recompute'),
      rect: [Math.round(r.width), Math.round(r.height)],
    };
  `);
  check('编辑器：放置工具下有「方块更新」开关（默认开）',
        upd0.visible && upd0.checked && upd0.state === true && upd0.hasRecompute,
        upd0);

  const upd1 = await ev(`
    document.querySelector('#place-update-on').click();
    const off = Editor.E.update;
    document.querySelector('[data-tool=copy]').click();
    const hiddenCopy = document.querySelector('#place-update').classList.contains('hidden');
    document.querySelector('[data-tool=erase]').click();
    const shownErase = !document.querySelector('#place-update').classList.contains('hidden');
    document.querySelector('[data-tool=move]').click();
    const hiddenMove = document.querySelector('#place-update').classList.contains('hidden');
    document.querySelector('[data-tool=place]').click();
    const shownBack = !document.querySelector('#place-update').classList.contains('hidden');
    document.querySelector('#place-update-on').click();
    return { off, hiddenCopy, shownErase, hiddenMove, shownBack, on: Editor.E.update };
  `);
  check('编辑器：开关跟随工具显示/隐藏且状态同步',
        upd1.off === false && upd1.hiddenCopy && upd1.shownErase &&
        upd1.hiddenMove && upd1.shownBack && upd1.on === true, upd1);
  await shot('04b-editor-place-update.png');

  // ---------- 方块选择面板（色块网格 + 分类芯片）----------
  const pk0 = await ev(`
    await Editor.loadPicker();
    Editor.renderPicker();
    const cells = [...document.querySelectorAll('#blk-grid .blk')];
    const colors = new Set(cells.map((c) => {
      const i = c.querySelector('i');
      return i ? i.style.backgroundColor : '';
    }));
    return {
      cells: cells.length,
      colors: colors.size,
      fams: document.querySelectorAll('#blk-fams .blk-fam').length,
      info: (document.querySelector('#blk-info') || {}).textContent || '',
      count: (document.querySelector('#blk-count') || {}).textContent || '',
      hasDatalist: !!document.querySelector('#block-list'),
      gridScroll: (() => { const g = document.querySelector('#blk-grid');
        return g ? [g.clientHeight, g.scrollHeight] : null; })(),
    };
  `);
  check('方块面板：色块网格已渲染且有颜色/分类',
        pk0.cells >= 40 && pk0.colors >= 20 && pk0.fams >= 12 &&
        /\d+\/\d+/.test(pk0.count), pk0);
  check('方块面板：不再用原生 datalist', pk0.hasDatalist === false, pk0.hasDatalist);

  const pk1 = await ev(`
    // 点“玻璃”分类 → 只剩玻璃类
    const chip = [...document.querySelectorAll('#blk-fams .blk-fam')]
      .find((b) => b.textContent.trim() === '玻璃');
    chip.click();
    const names = [...document.querySelectorAll('#blk-grid .blk')]
      .map((c) => c.title);
    const allGlass = names.length > 0 && names.every((t) => t.includes('glass'));
    // 再点一个玻璃方块 → 当前方块变成它
    const pick = [...document.querySelectorAll('#blk-grid .blk')]
      .find((c) => c.title.startsWith('glass_pane')) ||
      [...document.querySelectorAll('#blk-grid .blk')][0];
    const want = pick.title.split('（')[0];
    pick.click();
    await new Promise((r) => setTimeout(r, 1200));
    return {
      n: names.length, allGlass,
      want, state: Editor.E.state,
      search: document.querySelector('#block-search').value,
      active: document.querySelectorAll('#blk-grid .blk.on').length,
      recent: (JSON.parse(localStorage.getItem('structworkshop.recentBlocks') || '[]'))[0] || '',
    };
  `);
  check('方块面板：分类芯片只留该类方块', pk1.allGlass && pk1.n > 0, { n: pk1.n, all: pk1.allGlass });
  check('方块面板：点色块选中方块（含记住最近）',
        pk1.state.startsWith('minecraft:' + pk1.want) && pk1.active === 1 &&
        pk1.recent === pk1.want, pk1);

  // 分类：材质家族只看贴图（磁石→石砖、红石火把→石砖），功能类方块以前压根没芯片。
  // 现在有「功能方块」，且「全部」一次把整本目录列出来（旧版只画前 300）。
  const pkFunc = await ev(`
    const btns = () => [...document.querySelectorAll('#blk-fams .blk-fam')];
    const chips = btns().map((b) => b.textContent.trim());
    const fn = btns().find((b) => b.textContent.trim() === '功能方块');
    if (!fn) return { err: '没有「功能方块」芯片', chips };
    fn.click();
    await new Promise((r) => setTimeout(r, 400));
    const names = [...document.querySelectorAll('#blk-grid .blk')]
      .map((c) => c.title.split('（')[0]);
    btns().find((b) => b.textContent.trim() === '灯具发光').click();
    await new Promise((r) => setTimeout(r, 400));
    const lights = [...document.querySelectorAll('#blk-grid .blk')]
      .map((c) => c.title.split('（')[0]);
    btns().find((b) => b.textContent.trim() === '全部').click();
    await new Promise((r) => setTimeout(r, 600));
    return { chips, n: names.length,
             hasObserver: names.includes('observer'),
             hasCrafter: names.includes('crafter'),
             hasBlast: names.includes('blast_furnace'),
             hasLodestone: names.includes('lodestone'),
             hasRedTorch: names.includes('redstone_torch'),
             hasRail: names.includes('rail'), hasAnvil: names.includes('anvil'),
             lights: lights.length,
             candles: lights.filter((n) => n === 'candle' || n.endsWith('_candle')).length,
             allCount: document.querySelectorAll('#blk-grid .blk').length,
             info: document.querySelector('#blk-info').textContent };
  `);
  check('方块面板：新增「功能方块」芯片（侦测器/合成器/高炉/磁石/红石火把/铁轨/铁砧）',
        pkFunc && !pkFunc.err && pkFunc.hasObserver && pkFunc.hasCrafter &&
        pkFunc.hasBlast && pkFunc.hasLodestone && pkFunc.hasRedTorch &&
        pkFunc.hasRail && pkFunc.hasAnvil && !pkFunc.chips.includes('细节家具'), pkFunc);
  check('方块面板：「灯具发光」含全部 17 种蜡烛',
        pkFunc && pkFunc.candles === 17, { lights: pkFunc && pkFunc.lights, candles: pkFunc && pkFunc.candles });
  check('方块面板：「全部」= 整本目录（不再只画前 300 个）',
        pkFunc && pkFunc.allCount > 1000 && !/前\s*300/.test(pkFunc.info || ''),
        { allCount: pkFunc.allCount, info: pkFunc.info });

  // 中文搜索：面板原本只认 id（搜「烟熏炉」一无所获），现在按中文别名也能搜
  const pkAlias = await ev(`
    const inp = document.querySelector('#block-search');
    const tryQuery = async (q) => {
      inp.value = q;
      inp.dispatchEvent(new Event('input', { bubbles: true }));
      await new Promise((r) => setTimeout(r, 500));
      const cells = [...document.querySelectorAll('#blk-grid .blk')];
      return { n: cells.length,
               names: cells.slice(0, 3).map((c) => c.title.split('（')[0]),
               title0: cells.length ? cells[0].title : '' };
    };
    const out = { smoker: await tryQuery('烟熏炉'), stairs: await tryQuery('楼梯'),
                  candles: await tryQuery('蜡烛'), obs: await tryQuery('侦测器') };
    inp.value = ''; inp.dispatchEvent(new Event('input', { bubbles: true }));
    return out;
  `);
  check('方块面板：中文搜得到（烟熏炉→smoker / 侦测器 / 楼梯 / 蜡烛 17 色）',
        pkAlias.smoker.n === 1 && pkAlias.smoker.names[0] === 'smoker' &&
        pkAlias.obs.names[0] === 'observer' && pkAlias.stairs.n > 30 &&
        pkAlias.candles.n === 17, pkAlias);

  const pk2 = await ev(`
    // 搜索框筛选 + 完整方块开关
    const inp = document.querySelector('#block-search');
    const chip = [...document.querySelectorAll('#blk-fams .blk-fam')]
      .find((b) => b.textContent.trim() === '全部');
    chip.click();
    inp.value = 'stairs';
    inp.dispatchEvent(new Event('input', { bubbles: true }));
    const stairs = [...document.querySelectorAll('#blk-grid .blk')].map((c) => c.title);
    const box = document.querySelector('#blk-full-only');
    box.checked = true;
    box.dispatchEvent(new Event('change', { bubbles: true }));
    inp.value = 'concrete';
    inp.dispatchEvent(new Event('input', { bubbles: true }));
    const fulls = [...document.querySelectorAll('#blk-grid .blk')].map((c) => c.title);
    box.checked = false;
    box.dispatchEvent(new Event('change', { bubbles: true }));
    inp.value = '';
    inp.dispatchEvent(new Event('input', { bubbles: true }));
    await new Promise((r) => setTimeout(r, 400));
    return {
      stairsN: stairs.length,
      stairsOk: stairs.length > 0 && stairs.every((t) => t.includes('stairs')),
      fullsN: fulls.length,
      fullsOk: fulls.length > 0 && fulls.every((t) => t.includes('（完整方块）')),
    };
  `);
  check('方块面板：搜索筛选', pk2.stairsOk && pk2.stairsN > 3, pk2);
  check('方块面板：「只看完整方块」生效', pk2.fullsOk && pk2.fullsN > 3, pk2);

  // ---------- 朝向 / 旋转 ----------
  const fc0 = await ev(`
    await Editor.chooseBlock('oak_stairs');
    await new Promise((r) => setTimeout(r, 1200));
    const box = document.querySelector('#face-ctl');
    const btns = [...box.querySelectorAll('.face-grid button')].map((b) => b.textContent.trim());
    return {
      hidden: box.classList.contains('hidden'),
      btns,
      on: [...box.querySelectorAll('.face-grid button.on')].map((b) => b.textContent.trim()),
      state: Editor.E.state,
      hasAuto: !!box.querySelector('#face-auto'),
      props: Editor.E.currentBlock ? Object.keys(Editor.E.currentBlock.properties || {}) : [],
    };
  `);
  check('朝向：楼梯显示四向按钮（+上/下行）且当前项高亮',
        !fc0.hidden && ['北', '东', '南', '西'].every((d) => fc0.btns.includes(d)) &&
        fc0.btns.includes('下半') && fc0.on.includes('北') && fc0.on.includes('下半') &&
        fc0.hasAuto && fc0.state.includes('facing='), fc0);

  const fc1 = await ev(`
    const box = document.querySelector('#face-ctl');
    [...box.querySelectorAll('.face-grid button')].find((b) => b.textContent.trim() === '东').click();
    const east = Editor.E.state;
    // R 键 = 顺时针一格（东 → 南）；Shift+R 反向
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'r', bubbles: true }));
    const rot = Editor.E.state;
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'R', shiftKey: true, bubbles: true }));
    const back = Editor.E.state;
    return { east, rot, back, auto: Editor.E.autoFace };
  `);
  check('朝向：点方向按钮 / R 旋转 / Shift+R 反向',
        fc1.east.includes('facing=east') && fc1.rot.includes('facing=south') &&
        fc1.back.includes('facing=east') && fc1.auto === false, fc1);

  const fc2 = await ev(`
    // 告示牌：16 档 rotation（0=南 / 4=西 / 8=北 / 12=东）
    await Editor.chooseBlock('oak_sign');
    await new Promise((r) => setTimeout(r, 1200));
    const box = document.querySelector('#face-ctl');
    const btns = [...box.querySelectorAll('.face-grid button')];
    [...btns].find((b) => b.textContent.trim() === '西').click();
    const west = Editor.E.state;
    const val = (box.querySelector('.face-val') || {}).textContent || '';
    const plus = [...box.querySelectorAll('.face-row button')].find((b) => b.textContent.includes('↻'));
    plus.click();
    return { west, after: Editor.E.state, val, n: btns.length };
  `);
  check('朝向：告示牌 16 档旋转（西=4，↻ +22.5°）',
        fc2.n >= 8 && fc2.west.includes('rotation=4') &&
        fc2.after.includes('rotation=5') && fc2.val.includes('西'), fc2);

    const fc3 = await ev(`
    // 跟随视角：相机看向不同方向时，自动算出的朝向跟着变
    // （相机约定：yaw=0 看向 −Z = 北；yaw=+π/2 → 东、−π/2 → 西、π → 南）
    const v = Editor.E.viewer;
    await Editor.chooseBlock('oak_stairs');
    await new Promise((r) => setTimeout(r, 1200));
    const out = {};
    for (const [yaw, want] of [[0, 'north'], [Math.PI / 2, 'east'],
                               [Math.PI, 'south'], [-Math.PI / 2, 'west']]) {
      v.cam.yaw = yaw;
      out[yaw.toFixed(2)] = Editor.autoProps({ normal: [0, 1, 0] }).facing;
      out[yaw.toFixed(2) + '_want'] = want;
    }
    // 墙上的东西：按点击面（北面 → facing=north）
    await Editor.chooseBlock('oak_wall_sign');
    await new Promise((r) => setTimeout(r, 1200));
    const wall = Editor.autoProps({ normal: [0, 0, -1] }).facing;
    // 容器类：朝向放置者（与视线相反）：看向北 → 箱子朝南
    await Editor.chooseBlock('chest');
    await new Promise((r) => setTimeout(r, 1200));
    v.cam.yaw = 0;
    const chest = Editor.autoProps({ normal: [0, 1, 0] }).facing;
    // 日志（axis）：按点击面
    await Editor.chooseBlock('oak_log');
    await new Promise((r) => setTimeout(r, 1200));
    const axis = Editor.autoProps({ normal: [1, 0, 0] }).axis;
    return { ...out, wall, chest, axis, viewDir: v.viewDir().map((x) => Number(x.toFixed(2))) };
  `);
  check('跟随视角：楼梯随视线方向（北/东/南/西）',
        fc3['0.00'] === 'north' && fc3['1.57'] === 'east' &&
        fc3['3.14'] === 'south' && fc3['-1.57'] === 'west', fc3);
  check('跟随视角：墙上牌子贴点击面、箱子朝放置者、原木按面定轴',
        fc3.wall === 'north' && fc3.chest === 'south' && fc3.axis === 'x', fc3);

  // 真的放一个方块：楼梯朝向应写进方块状态
  const fc4 = await ev(`
    await Editor.chooseBlock('oak_stairs');
    await new Promise((r) => setTimeout(r, 1200));
    Editor.E.autoFace = true;
    Editor.E.viewer.cam.yaw = Math.PI / 2;    // 看向东
    const st = Editor.stateForPlace({ normal: [0, 1, 0] });
    // 收尾：相机与当前方块还原（后面的 3D 拾取/放置检查依赖初始视角）
    Editor.E.viewer.cam.yaw = -0.65;
    if (Editor.E.viewer.frame) Editor.E.viewer.frame();
    Editor.E.state = 'minecraft:stone';
    return { st, cur: Editor.E.state, tool: Editor.E.tool,
             yaw: Number(Editor.E.viewer.cam.yaw.toFixed(2)) };
  `);
  check('跟随视角：放置时真的用上了视角朝向',
        fc4.st.includes('facing=east') && fc4.yaw === -0.65, fc4);

  // ---------- 掩码下拉：与其它选择栏同一套渲染 ----------
  const mk = await ev(`
    const inp = document.querySelector('#ax-mask');
    const btn = document.querySelector('#ax-mask-btn');
    const drop = document.querySelector('#ax-mask-drop');
    if (!inp || !btn || !drop) return { missing: true };
    const before = drop.classList.contains('hidden');
    btn.click();
    const items = [...drop.querySelectorAll('.it')].map((b) => b.textContent.trim());
    const shown = !drop.classList.contains('hidden');
    const hit = [...drop.querySelectorAll('.it')].find((b) => b.textContent.trim() === '!solid');
    hit.click();
    const picked = inp.value;
    inp.value = '';                    // 清干净：后面的 Axiom 检查不能被这里污染
    inp.dispatchEvent(new Event('input', { bubbles: true }));
    drop.classList.add('hidden');      // 别再压着下面的「应用」按钮
    return { before, shown, items: items.length, value: picked,
             closed: drop.classList.contains('hidden'),
             isDatalist: !!document.querySelector('#ax-mask-list'),
             styled: getComputedStyle(drop).position === 'absolute' };
  `);
  check('掩码：自定义下拉（非 datalist）且展开/选择正常',
        mk.before === true && mk.shown && mk.items >= 6 &&
        mk.value === '!solid' && mk.closed && !mk.isDatalist && mk.styled, mk);
  await shot('04c-editor-picker-face.png');

  // ---------- 搜索框只是筛选器：不能把分类点成 0 结果 ----------
  const sq = await ev(`
    const inp = document.querySelector('#block-search');
    inp.value = 'stone';
    inp.dispatchEvent(new Event('input', { bubbles: true }));
    const chip = [...document.querySelectorAll('#blk-fams .blk-fam')]
      .find((b) => b.textContent.trim() === '门窗');
    chip.click();
    const empty = {
      cells: document.querySelectorAll('#blk-grid .blk').length,
      info: document.querySelector('#blk-info').textContent,
      hasClear: !!document.querySelector('#blk-info .link-btn'),
    };
    // 点「清除搜索」→ 门窗方块应回来了
    document.querySelector('#blk-info .link-btn').click();
    const after = {
      cells: document.querySelectorAll('#blk-grid .blk').length,
      names: [...document.querySelectorAll('#blk-grid .blk')].slice(0, 2).map((c) => c.title),
      searchVal: inp.value,
    };
    // 选一个方块 → 搜索框应被清空（不再当隐藏过滤器）
    [...document.querySelectorAll('#blk-grid .blk')][0].click();
    await new Promise((r) => setTimeout(r, 900));
    const afterPick = { searchVal: inp.value, q: Editor.PICK.q,
                        state: Editor.E.state };
    // 吸管/调色板设状态也不该写进搜索框
    Editor.setState ? null : null;
    return { empty, after, afterPick };
  `);
  check('面板：搜索词与分类叠加时要说明原因 + 可一键清除',
        sq.empty.cells === 0 && /门窗|搜索词|石/.test(sq.empty.info) &&
        sq.empty.hasClear && sq.after.cells > 5 &&
        sq.after.names.every((t) => /(_door|_trapdoor)/.test(t)), sq);
  check('面板：选中方块后搜索框自动清空（不再当隐藏过滤器）',
        sq.afterPick.searchVal === '' && sq.afterPick.q === '' &&
        sq.afterPick.state.startsWith('minecraft:'), sq.afterPick);

  // ---------- 色块用真贴图 + 群系染色 ----------
  const tex = await ev(`
    const inp = document.querySelector('#block-search');
    inp.value = ''; inp.dispatchEvent(new Event('input', { bubbles: true }));
    // 先把分类回到「全部」（上一条检查停在「门窗」）
    [...document.querySelectorAll('#blk-fams .blk-fam')]
      .find((b) => b.textContent.trim() === '全部').click();
    await new Promise((r) => setTimeout(r, 300));
    const imgs = [...document.querySelectorAll('#blk-grid .blk i img.tex')];
    const loaded = imgs.filter((i) => i.naturalWidth > 0).length;
    const sample = imgs[0] ? { src: imgs[0].src, w: imgs[0].naturalWidth } : null;
    const cells0 = document.querySelectorAll('#blk-grid .blk').length;
    // 找一片树叶：应有贴图 + 群系染色层
    inp.value = 'oak_leaves'; inp.dispatchEvent(new Event('input', { bubbles: true }));
    await new Promise((r) => setTimeout(r, 2500));
    const cell = document.querySelector('#blk-grid .blk');
    const tint = cell ? cell.querySelector('i .tint') : null;
    const tintBg = tint ? getComputedStyle(tint).backgroundColor : '';
    const leaf = { hasTex: !!(cell && cell.querySelector('img.tex')),
                   tint: tintBg, blend: tint ? getComputedStyle(tint).mixBlendMode : '',
                   title: cell ? cell.title : '' };
    inp.value = ''; inp.dispatchEvent(new Event('input', { bubbles: true }));
    return { n: imgs.length, cells0, loaded, sample, leaf };
  `);
  check('面板：色块用真贴图（懒加载，已加载 > 0）',
        tex.cells0 >= 100 && tex.n >= 100 && tex.loaded > 0 &&
        /\/api\/asset\?/.test(tex.sample.src), tex);
  check('面板：草地/树叶类叠了群系染色（与出图一致）',
        tex.leaf.hasTex && tex.leaf.blend === 'multiply' &&
        /^rgb\(/.test(tex.leaf.tint) && tex.leaf.tint !== 'rgb(255, 255, 255)', tex.leaf);

  // ---------- 新建画布：不开文件也能动手 ----------
  const nc = await ev(`
    const before = Editor.E.st ? Editor.E.st.sid : null;
    const st = await Editor.newCanvas([12, 8, 10], '新建探针');
    await new Promise((r) => setTimeout(r, 500));
    const placed = Editor.E.voxels.length === 12 * 8 * 10;
    // 新画布上真的能画
    const c = document.querySelector('#layer2d');
    return { size: st.size, name: st.name, path: st.path, blocks: st.blocks,
             placed, before, sid: Editor.E.st.sid, palette: Editor.E.palette.slice(0, 2),
             dirty: Editor.E.st.dirty };
  `);
  check('新建：默认 16³ / 自定尺寸的空画布可直接用',
        JSON.stringify(nc.size) === JSON.stringify([12, 8, 10]) &&
        nc.path === null && nc.blocks === 0 && nc.palette[0] === 'minecraft:air', nc);

  const nc2 = await ev(`
    // 空画布上直接点 3D（没有实心块可打 → 应落到“射线进入画布的第一格”）
    document.querySelector('[data-tool=place]').click();
    const c = document.querySelector('#gl-canvas');
    const r = c.getBoundingClientRect();
    const x = r.left + r.width * 0.5, y = r.top + r.height * 0.5;
    const evp = (type, buttons) => new PointerEvent(type, {
      clientX: x, clientY: y, button: 0, buttons, bubbles: true,
      pointerId: 91, pointerType: 'mouse', isPrimary: true });
    const before = Editor.E.st.blocks;
    c.dispatchEvent(evp('pointermove', 0));
    const cursor = Editor.E.viewer.cursor
      ? { cell: Editor.E.viewer.cursor.cell.slice(), mode: Editor.E.viewer.cursor.mode } : null;
    c.dispatchEvent(evp('pointerdown', 1));
    c.dispatchEvent(evp('pointerup', 0));
    // 轮询等结果落地（固定 sleep 在冷缓存 / 慢机器上会抖：资源没下完时
    // POST /ops 可能要好几秒，旧写法等 2.5s 会误判成"放不上去"）
    for (let i = 0; i < 40; i++) {
      await new Promise((res) => setTimeout(res, 400));
      if (Editor.E.st.blocks > before) break;
    }
    return { before, after: Editor.E.st.blocks, cursor,
             palette: (Editor.E.palette || []).slice(0, 2) };
  `);
  check('新建：空画布上 3D 也能放第一块（准星=进画布首格）',
        nc2.cursor && nc2.cursor.mode === 'place' && nc2.after > nc2.before &&
        nc2.cursor.cell.every((v, i) => v >= 0 && v < [12, 8, 10][i]), nc2);

  await shot('04d-editor-new-canvas.png');

  // 回到原来的模块，别影响后面的检查
  await send('Page.navigate', { url: base + '/?view=editor&open=packs/modern-arch/modules/rooms/modern_lobby.schem' });
  await sleep(13000);

  // ---------- 点谁就是谁：面板选块绝不能退成“列表里第一个”----------
  const exact = await ev(`
    const out = {};
    for (const n of ['stone', 'glass', 'deepslate', 'sand', 'stone_slab',
                     'oak_stairs', 'oak_leaves']) {
      Editor.pickBlock(n);
      await new Promise((r) => setTimeout(r, 1500));
      out[n] = Editor.E.state;
    }
    return out;
  `);
  const badPick = Object.entries(exact).filter(([n, st]) =>
    !(st === 'minecraft:' + n || st.startsWith('minecraft:' + n + '[')));
  check('面板：点谁就是谁（stone 不再变成 blackstone）', badPick.length === 0, badPick);

  // ---------- 半砖上/下、楼梯上/下 ----------
  const orient = await ev(`
    const labels = () => [...document.querySelectorAll('#face-ctl .face-row')]
      .map((r) => r.textContent.trim());
    const click = (t) => {
      const b = [...document.querySelectorAll('#face-ctl .face-grid button')]
        .find((x) => x.textContent.trim() === t);
      if (b) b.click();
      return !!b;
    };
    Editor.pickBlock('stone_slab');
    await new Promise((r) => setTimeout(r, 1500));
    const slabRow = labels();
    const okTop = click('上半砖');
    const top = Editor.E.state;
    const okDouble = click('双层');
    const dbl = Editor.E.state;
    click('下半砖');
    Editor.pickBlock('oak_stairs');
    await new Promise((r) => setTimeout(r, 1500));
    const stairRow = labels();
    const okHalf = click('上半');
    const halfTop = Editor.E.state;
    click('下半');
    // 告示牌不该出现半砖/上下行
    Editor.pickBlock('oak_sign');
    await new Promise((r) => setTimeout(r, 1500));
    const signRow = labels();
    return { slabRow, top, dbl, okTop, okDouble, stairRow, okHalf, halfTop, signRow };
  `);
  check('朝向：半砖可切下/上/双层（type）',
        orient.slabRow.some((r) => /半砖/.test(r)) && orient.okTop &&
        orient.top.includes('type=top') && orient.dbl.includes('type=double'),
        { row: orient.slabRow, top: orient.top, dbl: orient.dbl });
  check('朝向：楼梯可切上/下半（half），告示牌不出现这两行',
        orient.stairRow.some((r) => /上下/.test(r)) && orient.okHalf &&
        orient.halfTop.includes('half=top') &&
        !orient.signRow.some((r) => /半砖|上下/.test(r)),
        { row: orient.stairRow, state: orient.halfTop, sign: orient.signRow });

  // ---------- AXIOM 工具面板：已从界面上下线（引擎与代码保留）----------
  const ex0 = await ev(`
    const panel = document.querySelector('#ax-panel');
    const st = {
      panelInDom: !!panel,
      panelHidden: !!(panel && panel.classList.contains('hidden')),
      panelVisible: !!(panel && panel.offsetParent),
      // 下线时不拉目录也不建工具按钮（axInit 被 AX_PANEL_ENABLED 拦住）
      toolButtons: document.querySelectorAll('.ax-tool').length,
      tabs: document.querySelectorAll('#ax-tabs .ax-tab').length,
      // 引擎还在：接口照旧能下发 27 个工具
      apiCount: (await App.api('/api/tools')).count,
      flag: Editor.AX_PANEL_ENABLED,
      // 选区面板（框选是移动/复制的前提）必须留着
      selPanelVisible: !!(document.querySelector('#ax-sel-pick') || {}).offsetParent,
    };
    // 编辑工具在面板下线后仍然后正常切换、开关跟随
    document.querySelector('[data-tool=erase]').click();
    st.erase = { tool: Editor.E.tool,
                 updateRowShown: !document.querySelector('#place-update').classList.contains('hidden') };
    document.querySelector('[data-tool=place]').click();
    st.place = Editor.E.tool;
    // 快捷键 1~6 全部映射到仍然存在的 6 个工具
    const keyMap = {};
    for (const k of ['1', '2', '3', '4', '5', '6']) {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true }));
      keyMap[k] = Editor.E.tool;
    }
    st.keyMap = keyMap;
    return st;
  `);
  check('AXIOM：面板已下线（DOM 保留 + hidden + 不加载工具目录）',
        ex0.panelInDom && ex0.panelHidden && !ex0.panelVisible &&
        ex0.toolButtons === 0 && ex0.tabs === 0 && ex0.flag === false, ex0);
  check('AXIOM：引擎仍在（/api/tools 照样返回 29 个工具）', ex0.apiCount === 29, ex0.apiCount);
  check('AXIOM：下线后编辑工具与方块更新开关仍正常（擦除=笔刷类 → 开关显示）',
        ex0.erase.tool === 'erase' && ex0.erase.updateRowShown === true &&
        ex0.place === 'place', ex0);
  check('AXIOM：选区面板保留（移动/复制要靠它框选）', ex0.selPanelVisible === true, ex0);
  check('快捷键 1~6 = 放置/擦除/替换/移动/复制/框选',
        ex0.keyMap['1'] === 'place' && ex0.keyMap['2'] === 'erase' &&
        ex0.keyMap['3'] === 'replace' && ex0.keyMap['4'] === 'move' &&
        ex0.keyMap['5'] === 'copy' && ex0.keyMap['6'] === 'select', ex0.keyMap);

  // ---------- 面板：服务端旧版（没有 /api/blocks/picker）时的退化 ----------
  const fb = await ev(`
    const orig = App.api;
    App.api = async (url, ...rest) => {
      if (String(url).includes('/api/blocks/picker')) throw new Error('404 没有路由');
      return orig(url, ...rest);
    };
    Editor.PICK.data = null; Editor.PICK.mode = ''; Editor.PICK.q = ''; Editor.PICK.fam = 'all';
    await Editor.loadPicker(); Editor.renderFams(); Editor.renderPicker();
    const res = {
      mode: Editor.PICK.mode,
      cells: document.querySelectorAll('#blk-grid .blk').length,
      chips: document.querySelectorAll('#blk-fams .blk-fam').length,
      note: document.querySelector('#blk-note').textContent,
      fullRowHidden: document.querySelector('#blk-full-row').classList.contains('hidden'),
    };
    const chip = [...document.querySelectorAll('#blk-fams .blk-fam')]
      .find((b) => b.textContent.trim() === '半砖');
    chip.click();
    const names = [...document.querySelectorAll('#blk-grid .blk')].map((c) => c.title);
    res.byNameChip = names.length;
    res.byNameOk = names.length > 3 && names.every((t) => /(_slab|_stairs)$/.test(t));
    const inp = document.querySelector('#block-search');
    inp.value = 'copper'; inp.dispatchEvent(new Event('input', { bubbles: true }));
    res.searchN = document.querySelectorAll('#blk-grid .blk').length;
    res.searchOk = res.searchN > 3 &&
      [...document.querySelectorAll('#blk-grid .blk')].every((c) => c.title.includes('copper'));
    // 还原：回到新服务端的完整面板
    App.api = orig;
    inp.value = ''; inp.dispatchEvent(new Event('input', { bubbles: true }));
    Editor.PICK.data = null; Editor.PICK.mode = ''; Editor.PICK.fam = 'all';
    await Editor.loadPicker(); Editor.renderFams(); Editor.renderPicker();
    res.restored = { mode: Editor.PICK.mode, note: document.querySelector('#blk-note').textContent,
                     chips: document.querySelectorAll('#blk-fams .blk-fam').length };
    return res;
  `);
  check('面板：旧服务端也能出面板（名单模式 + 警告 + 按名分类 + 搜索）',
        fb.mode === 'names' && fb.cells >= 40 && fb.chips >= 12 &&
        /重启/.test(fb.note) && fb.fullRowHidden &&
        fb.byNameOk && fb.searchOk, fb);
  check('面板：接口恢复后回到完整模式（颜色/分类）',
        fb.restored && fb.restored.mode === 'full' && fb.restored.note === '' &&
        fb.restored.chips >= 15, fb.restored);
  await shot('04-editor.png');

  // draw check on 2D canvas then screenshot
  await ev(`
    const c = document.querySelector('#layer2d');
    const r = c.getBoundingClientRect();
    const opts = { clientX: r.left + 8, clientY: r.top + 8, button: 0, buttons: 1, bubbles: true, pointerId: 3, pointerType: 'mouse', isPrimary: true };
    c.dispatchEvent(new PointerEvent('pointerdown', opts));
    c.dispatchEvent(new PointerEvent('pointerup', opts));
    return true;
  `);
  await sleep(2500);
  await shot('05-editor-edited.png');

  // ---------- Axiom 工具面板 ----------
  // 面板当前**从界面上下线**（index.html 的 #ax-panel 加 hidden + editor.js 的
  // AX_PANEL_ENABLED=false，引擎与接线全部保留）。把这两处改回去，下面 4 项
  // 面板测试就会照原样继续跑 —— 所以这里只是按开关跳过，不是删掉测试。
  const AX_ON = await ev(`return Editor.AX_PANEL_ENABLED === true;`);
  check('Axiom 面板：已从界面下线（面板测试按开关跳过）', AX_ON === false, AX_ON);
  const ax0 = AX_ON ? await ev(`
    const panel = document.querySelector('.tool-panel');
    return {
      tabs: document.querySelectorAll('.ax-tab').length,
      tools: document.querySelectorAll('.ax-tool').length,
      count: (document.querySelector('#ax-count') || {}).textContent || '',
      params: document.querySelectorAll('#ax-params .ax-param').length,
      spec: (Editor.AX && Editor.AX.spec) ? Editor.AX.spec.id : null,
      toolMode: Editor.E.tool,          // 未选工具时必须是 'place'
      brushShapes: document.querySelectorAll('#ax-brush-shape option').length,
      scrollOk: panel.scrollHeight >= panel.clientHeight,
    };
  `) : null;
  check('Axiom 面板：分组/工具/参数已渲染（下线时跳过）',
        !AX_ON || (ax0.tabs >= 5 && ax0.tools >= 2 && ax0.brushShapes >= 5 &&
                   ax0.spec === null && ax0.toolMode === 'place'), ax0);

  if (AX_ON) {
  await ev(`
    const t = [...document.querySelectorAll('.ax-tab')].find(x => x.textContent.includes('绘制'));
    t.click();
    return true;
  `);
  await sleep(300);
  await ev(`
    const b = [...document.querySelectorAll('.ax-tool')].find(x => x.textContent.includes('噪声绘制'));
    b.click();
    return true;
  `);
  await sleep(300);
  }
  const axp = AX_ON ? await ev(`
    const set = (id, v) => { const n = document.getElementById(id); n.value = v;
      n.dispatchEvent(new Event('change', { bubbles: true })); };
    set('ax-sx0', 0); set('ax-sy0', 0); set('ax-sz0', 0);
    set('ax-sx1', 8); set('ax-sy1', 5); set('ax-sz1', 8);
    return { spec: Editor.AX.spec.id, sel: Editor.AX.sel,
             mode: Editor.E.tool,
             params: document.querySelectorAll('#ax-params .ax-param').length };
  `) : null;
  check('Axiom 面板：切工具 + 设选区 + 参数表单（下线时跳过）',
        !AX_ON || (axp.spec === 'noise_painter' && axp.mode === 'ax' &&
                   Array.isArray(axp.sel) && axp.sel[3] === 8 && axp.params >= 8), axp);

  let axr = null;
  if (AX_ON) {
    await ev(`document.getElementById('ax-apply').click(); return true;`);
    await sleep(6000);
    axr = await ev(`
      return { status: document.querySelector('#ax-status').textContent,
               dirty: Editor.E.st.dirty, undo: Editor.E.st.can_undo,
               blocks: Editor.E.st.blocks };
    `);
  }
  check('Axiom 面板：应用工具（噪声绘制）且可撤销（下线时跳过）',
        !AX_ON || (axr.undo === true && /格/.test(axr.status)), axr);
  const axgap = await ev(`
    const v = Editor.E.viewer;
    const gaps = v.resourceGaps(Editor.E.palette);
    // 画一帧再读像素：确认 3D 里真的画出了东西（新方块类型会让 mesher 丢 chunk → 全黑）
    v.draw();
    const gl = v.gl;
    const w = gl.drawingBufferWidth, h = gl.drawingBufferHeight;
    const buf = new Uint8Array(w * h * 4);
    gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, buf);
    let bright = 0;
    for (let i = 0; i < buf.length; i += 4) {
      if (buf[i] + buf[i + 1] + buf[i + 2] > 120) bright++;
    }
    return { gaps, bright, total: w * h,
             ratio: Math.round(bright / (w * h) * 1000) / 10 };
  `);
  check('Axiom 面板：新方块类型已补资源且 3D 仍出画',
        axgap && axgap.gaps && axgap.gaps.length === 0 && axgap.ratio > 1,
        axgap);
  await shot('06-axiom-tool.png');

  // 画笔放一个调色板里没有的方块：同样要补资源（回归：曾整块 chunk 丢网格 → 黑屏）
  const axnew = await ev(`
    document.querySelector('[data-tool=place]').click();
    Editor.E.state = 'minecraft:diamond_block';
    const c = document.querySelector('#layer2d');
    const r = c.getBoundingClientRect();
    const o = { clientX: r.left + 8, clientY: r.top + 8, button: 0, buttons: 1,
                bubbles: true, pointerId: 31, pointerType: 'mouse', isPrimary: true };
    c.dispatchEvent(new PointerEvent('pointerdown', o));
    c.dispatchEvent(new PointerEvent('pointerup', o));
    await new Promise(r2 => setTimeout(r2, 3000));
    const v = Editor.E.viewer;
    v.draw();
    const gl = v.gl, w = gl.drawingBufferWidth, h = gl.drawingBufferHeight;
    const buf = new Uint8Array(w * h * 4);
    gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, buf);
    let bright = 0;
    for (let i = 0; i < buf.length; i += 4) {
      if (buf[i] + buf[i + 1] + buf[i + 2] > 120) bright++;
    }
    return { gaps: v.resourceGaps(Editor.E.palette),
             ratio: Math.round(bright / (w * h) * 1000) / 10 };
  `);
  check('画笔放新方块也补资源（不黑屏）',
        axnew && axnew.gaps && axnew.gaps.length === 0 && axnew.ratio > 1, axnew);

  await ev(`document.getElementById('btn-undo').click(); return true;`);
  await sleep(3000);
  const axu = await ev(`return { canRedo: Editor.E.st.can_redo };`);
  check('Axiom 面板：撤销工具改动', axu.canRedo === true, axu);

  const axsel = await ev(`
    document.querySelector('[data-tool=select]').click();
    const c = document.querySelector('#layer2d');
    const r = c.getBoundingClientRect();
    const mk = (type, x, y) => new PointerEvent(type, {
      clientX: r.left + x, clientY: r.top + y, button: 0, buttons: 1,
      bubbles: true, pointerId: 9, pointerType: 'mouse', isPrimary: true });
    c.dispatchEvent(mk('pointerdown', 8, 8));
    c.dispatchEvent(mk('pointermove', 40, 40));
    c.dispatchEvent(mk('pointerup', 40, 40));
    return { sel: Editor.AX.sel, info: document.querySelector('#ax-sel-info').textContent };
  `);
  check('Axiom 面板：2D 拖框选区',
        Array.isArray(axsel.sel) && axsel.sel.length === 6 && axsel.sel[3] > axsel.sel[0],
        axsel);

  // 3D 笔刷涂抹：光标压在模型上按一下 → 应产生一次笔刷应用
  await ev(`
    const t = [...document.querySelectorAll('.ax-tab')].find(x => x.textContent.includes('绘制'));
    t.click();
    return true;
  `);
  await sleep(300);
  await ev(`
    const b = [...document.querySelectorAll('.ax-tool')].find(x => x.textContent.includes('实心绘制'));
    b.click();
    return true;
  `);
  await sleep(300);
  await ev(`
    document.getElementById('ax-sel-none').click();   // 清掉上一步的框选
    Editor.E.state = 'minecraft:glass';       // 换个显眼的方块，便于验证真的写进去了
    const c = document.querySelector('#gl-canvas');
    const r = c.getBoundingClientRect();
    const mk = (type, x, y, buttons) => new PointerEvent(type, {
      clientX: r.left + x, clientY: r.top + y, button: 0, buttons,
      bubbles: true, pointerId: 11, pointerType: 'mouse', isPrimary: true });
    const cx = r.width / 2, cy = r.height / 2;
    c.dispatchEvent(mk('pointerdown', cx, cy, 1));
    c.dispatchEvent(mk('pointermove', cx + 5, cy + 3, 1));
    c.dispatchEvent(mk('pointerup', cx + 5, cy + 3, 0));
    return true;
  `);
  await sleep(7000);
  const ax3d = AX_ON ? await ev(`
    const s0 = document.querySelector('#ax-status').textContent;
    const m = /[0-9][0-9,]*/.exec(s0);        // 状态里的第一个数字 = 改动格数
    return { status: document.querySelector('#ax-status').textContent,
             spec: Editor.AX.spec.id, dirty: Editor.E.st.dirty,
             wrote: m ? Number(m[0].replace(/,/g, '')) : 0,
             last: Editor.AX.last, brush: Editor.AX.brush };
  `) : null;
  check('Axiom 面板：3D 笔刷涂抹真的改到方块（下线时跳过）',
        !AX_ON || (ax3d.spec === 'painter' && ax3d.wrote > 0 && !!ax3d.last), ax3d);
  await shot('07-axiom-brush.png');

  // 回归：移动工具右键不应改方块（旧行为：右键直接擦除了光标下的方块）
  const mv = await ev(`
    document.querySelector('[data-tool=move]').click();
    const c = document.querySelector('#gl-canvas');
    const r = c.getBoundingClientRect();
    const before = Editor.E.st.blocks;
    const mk = (type, buttons) => new PointerEvent(type, {
      clientX: r.left + r.width / 2, clientY: r.top + r.height / 2,
      button: 2, buttons, bubbles: true, pointerId: 21, pointerType: 'mouse',
      isPrimary: true });
    c.dispatchEvent(mk('pointerdown', 2));
    c.dispatchEvent(mk('pointerup', 0));
    await new Promise(res => setTimeout(res, 1200));
    const v2 = await App.api('/api/structure/' + Editor.E.st.sid + '/voxels');
    return { tool: Editor.E.tool, before, after: Editor.E.st.blocks };
  `);
  check('编辑器：移动工具右键不擦除方块',
        mv.tool === 'move' && mv.after === mv.before, mv);

  // 操作日志：有记录、能点着回退（Axiom 式 History）
  const hist = await ev(`
    // Axiom 面板下线后这里不会再冒出「工具·噪声绘制」那种条目：先用普通编辑
    // 补两步，再验日志本身（日志功能与具体工具无关）
    for (let n = 0; n < 2; n++) {
      await App.post('/api/structure/' + Editor.E.st.sid + '/ops',
        { ops: [{ type: 'set', x: 1 + n, y: 1, z: 1, state: 'minecraft:gold_block' }],
          update: false });
    }
    for (let i = 0; i < 20; i++) {          // 等日志刷新完（避免时序抖动）
      await Editor.refreshHistory();
      if ((Editor.E.hist || {}).total >= 2) break;
      await new Promise(r => setTimeout(r, 250));
    }
    const rows = [...document.querySelectorAll('#hist-list .ax-hist-row')];
    return { rows: rows.length, info: (document.querySelector('#hist-info') || {}).textContent || '',
             cursor: (Editor.E.hist || {}).cursor, total: (Editor.E.hist || {}).total,
             labels: rows.slice(0, 4).map(r => r.querySelector('.hi-lb').textContent) };
  `);
  check('编辑器：操作日志有条目（每步一句中文标签）',
        hist.rows >= 2 && hist.cursor === hist.rows && hist.labels.length >= 2 &&
        hist.labels.every((x) => !!x &&
          /绘制|擦除|替换|移动|复制|保存范围|模块|装配/.test(x)),
        hist);
  const histJump = await ev(`
    const before = Editor.E.hist.cursor;
    document.querySelectorAll('#hist-list .ax-hist-row')[0].click();   // 回到第 1 步
    await new Promise(res => setTimeout(res, 2500));
    const mid = Editor.E.hist.cursor;
    return { before, mid, canRedo: Editor.E.st.can_redo,
             undoneRows: document.querySelectorAll('#hist-list .ax-hist-row.undone').length };
  `);
  check('编辑器：点日志可回退（已撤销行变暗）',
        histJump.mid === 1 && histJump.canRedo && histJump.undoneRows > 0, histJump);
  const histBack = await ev(`
    document.getElementById('hist-redo').click();
    await new Promise(res => setTimeout(res, 2500));
    return { cursor: Editor.E.hist.cursor, total: Editor.E.hist.total,
             canUndo: Editor.E.st.can_undo };
  `);
  check('编辑器：日志窗口的重做按钮生效',
        histBack.cursor === histJump.mid + 1 && histBack.canUndo, histBack);
  await shot('08-history.png');

  // 回归：3D 放置「指哪放哪」——准星格子 = 落点格子，且落点紧贴命中面
  const place3d = await ev(`
    document.querySelector('[data-tool=place]').click();
    const v = Editor.E.viewer;
    const c = document.querySelector('#gl-canvas');
    const r = c.getBoundingClientRect();
    const [sx, sy, sz] = Editor.E.st.size;
    Editor.E.state = 'minecraft:gold_block';
    // 找一个「放置位在画布内」的屏幕像素（避开边界，保证是普通放置而不是扩容）
    let spot = null;
    for (let y = 30; y < r.height - 30 && !spot; y += 13) {
      for (let x = 30; x < r.width - 30; x += 13) {
        const h = v.pick({ clientX: r.left + x, clientY: r.top + y });
        if (!h) continue;
        const p = h.place;
        if (p[0] <= 0 || p[1] <= 0 || p[2] <= 0 ||
            p[0] >= sx || p[1] >= sy || p[2] >= sz) continue;
        spot = { x, y, hit: { cell: h.cell.slice(), place: p.slice(), normal: h.normal.slice() } };
        break;
      }
    }
    if (!spot) return { err: '整屏都没有可放置像素' };
    const evp = (type, buttons) => new PointerEvent(type, {
      clientX: r.left + spot.x, clientY: r.top + spot.y, button: 0, buttons,
      bubbles: true, pointerId: 51, pointerType: 'mouse', isPrimary: true });
    c.dispatchEvent(evp('pointermove', 0));                      // 悬停 → 准星
    const cursor = v.cursor ? { cell: v.cursor.cell.slice(), mode: v.cursor.mode } : null;
    const bi = spot.hit.place;
    const at = (p) => Editor.E.voxels[(p[1] * sz + p[2]) * sx + p[0]];
    const before = at(bi);
    c.dispatchEvent(evp('pointerdown', 1));
    c.dispatchEvent(evp('pointerup', 0));
    await new Promise(res => setTimeout(res, 1500));
    return { spot: spot.hit, cursor, before, after: Editor.E.palette[at(bi)] || null,
             pending: Editor.E.pending.length };
  `);
  // 中键单击 = 吸取方块（与吸管工具等价，但任何工具下都能用）；拖动仍是平移
  const mmbPick = await ev(`
    const E = window.Editor.E, v = E.viewer;
    const c = document.querySelector('#gl-canvas');
    const r = c.getBoundingClientRect();
    document.querySelector('[data-tool=place]').click();
    E.state = 'minecraft:red_concrete';
    let spot = null;
    for (let y = 20; y < r.height - 20 && !spot; y += 7) {
      for (let x = 20; x < r.width - 20; x += 7) {
        const h = v.pick({ clientX: r.left + x, clientY: r.top + y });
        if (h && h.stateIndex > 0 && E.palette[h.stateIndex] !== 'minecraft:red_concrete') {
          spot = { x, y, want: E.palette[h.stateIndex] };
          break;
        }
      }
    }
    if (!spot) return { err: '找不到可吸取的像素' };
    const evp = (type, buttons, extra) => new PointerEvent(type, Object.assign({
      clientX: r.left + spot.x, clientY: r.top + spot.y, button: 1, buttons,
      bubbles: true, pointerId: 61, pointerType: 'mouse', isPrimary: true }, extra || {}));
    c.dispatchEvent(evp('pointerdown', 4));
    c.dispatchEvent(evp('pointerup', 0));
    await new Promise((res) => setTimeout(res, 700));
    const picked = E.state;
    const status = document.querySelector('#editor-status').textContent;
    // 拖动 (>4px) 不该吸取，只平移
    E.state = 'minecraft:red_concrete';
    const cam0 = v.cam.target.slice();
    c.dispatchEvent(evp('pointerdown', 4));
    c.dispatchEvent(evp('pointermove', 4, { clientX: r.left + spot.x + 40, clientY: r.top + spot.y + 20 }));
    c.dispatchEvent(evp('pointerup', 0, { clientX: r.left + spot.x + 40, clientY: r.top + spot.y + 20 }));
    await new Promise((res) => setTimeout(res, 400));
    return { want: spot.want, picked, status,
             afterDrag: E.state,
             camMoved: JSON.stringify(cam0) !== JSON.stringify(v.cam.target) };
  `);
  check('编辑器：中键单击吸取方块（拖动仍是平移，不会误吸）',
        mmbPick && !mmbPick.err && mmbPick.picked === mmbPick.want &&
        /中键吸取/.test(mmbPick.status || '') &&
        mmbPick.afterDrag === 'minecraft:red_concrete' && mmbPick.camMoved, mmbPick);

  check('编辑器：3D 放置指哪放哪（准星=落点=命中面外侧，方块真写进去）',
        !place3d.err && place3d.cursor && place3d.cursor.mode === 'place' &&
        JSON.stringify(place3d.cursor.cell) === JSON.stringify(place3d.spot.place) &&
        JSON.stringify(place3d.spot.place) ===
          JSON.stringify(place3d.spot.cell.map((c, i) => c + place3d.spot.normal[i])) &&
        place3d.before === 0 && place3d.after === 'minecraft:gold_block' &&
        place3d.pending === 0,
        place3d);
  await shot('08b-place-3d.png');

  // 模块 × 编辑工具不再互斥：压在模块实心处的笔刷**就地改在模块实例上**
  const punch = await ev(`
    const E = Editor.E, v = E.viewer;
    if (!E.placements || !E.placements.length) return { err: '画布上没有装配实例' };
    if (!v.pick) return { err: '没有 viewer.pick' };
    document.querySelector('[data-tool=place]').click();
    E.state = 'minecraft:gold_block';
    const [sx, sy, sz] = E.st.size;
    let cell = null;
    for (let y = 0; y < sy && !cell; y++)
      for (let z = 0; z < sz && !cell; z++)
        for (let x = 0; x < sx && !cell; x++)
          if (E.voxels[(y * sz + z) * sx + x] !== 0) cell = [x, y, z];
    if (!cell) return { err: '画布上没有实心块' };
    const before = E.placements[0].edits || 0;
    const c = document.querySelector('#gl-canvas');
    const r = c.getBoundingClientRect();
    const real = v.pick.bind(v);
    v.pick = () => ({ cell: cell.slice(), place: cell.slice(), normal: [0, 1, 0], stateIndex: 1 });
    const evp = (type, buttons) => new PointerEvent(type, {
      clientX: r.left + Math.round(r.width / 2), clientY: r.top + Math.round(r.height / 2),
      button: 0, buttons, bubbles: true, pointerId: 63, pointerType: 'mouse', isPrimary: true });
    c.dispatchEvent(evp('pointerdown', 1));
    c.dispatchEvent(evp('pointerup', 0));
    await new Promise((res) => setTimeout(res, 1400));
    v.pick = real;
    const idx = (cell[1] * sz + cell[2]) * sx + cell[0];
    const painted = E.palette[E.voxels[idx]];
    // 就地修改也进撤销栈：undo → 模块自己那块回来；redo → 又是画上去的
    const beforeUndo = (E.hist.undo || []).length;
    await Editor.undo();
    const undone = E.palette[E.voxels[idx]];
    await Editor.redo();
    const redone = E.palette[E.voxels[idx]];
    return { cell, before, edits: (E.placements[0].edits || 0),
             painted, undone, redone, want: 'minecraft:gold_block',
             redoLeft: (E.hist.redo || []).length, beforeUndo,
             toasts: [...document.querySelectorAll('.toast')].map((t) => t.textContent) };
  `);
  check('编辑器：压在模块上的笔刷就地改在模块实例上（不再“会被模块覆盖”+ 可撤销/重做）',
        punch && !punch.err && punch.edits > punch.before &&
        punch.painted === punch.want && punch.undone !== punch.want &&
        punch.redone === punch.want && punch.redoLeft === 0 &&
        (punch.toasts || []).some((t) => /就地改在模块/.test(t)), punch);

  // 回归：−X/−Y/−Z 方向（画布原点那一面）放不上去 → 准星标红 + 状态栏说明
  // （几何随机，直接用 stub 把「place 在画布外」的拾取结果喂给编辑器）
  const neg = await ev(`
    document.querySelector('[data-tool=place]').click();
    const v = Editor.E.viewer;
    const c = document.querySelector('#gl-canvas');
    const r = c.getBoundingClientRect();
    const real = v.pick.bind(v);
    v.pick = () => ({ cell: [0, 5, 5], place: [-1, 5, 5], normal: [-1, 0, 0], stateIndex: 1 });
    const evp = (type, buttons) => new PointerEvent(type, {
      clientX: r.left + Math.round(r.width / 2), clientY: r.top + Math.round(r.height / 2),
      button: 0, buttons, bubbles: true, pointerId: 61, pointerType: 'mouse', isPrimary: true });
    c.dispatchEvent(evp('pointermove', 0));
    const cursor = v.cursor ? { cell: v.cursor.cell.slice(), mode: v.cursor.mode } : null;
    const blocks = Editor.E.st.blocks;
    c.dispatchEvent(evp('pointerdown', 1));
    c.dispatchEvent(evp('pointerup', 0));
    await new Promise(res => setTimeout(res, 800));
    const out = { cursor, blocks, blocksAfter: Editor.E.st.blocks,
                  pending: Editor.E.pending.length,
                  status: document.querySelector('#editor-status').textContent };
    v.pick = real;
    v.setCursor(null);
    return out;
  `);
  check('编辑器：负方向放不上去时准星标红 + 状态栏明说 + 不发无效请求',
        neg.cursor && neg.cursor.mode === 'blocked' &&
        JSON.stringify(neg.cursor.cell) === JSON.stringify([0, 5, 5]) &&
        /放不上去/.test(neg.status) && neg.pending === 0 &&
        neg.blocksAfter === neg.blocks,
        neg);

  // 回归：缩小画布框不裁数据（只改框，框外内容留着，可在历史里撤销）
  const frame1 = await ev(`
    const sliders = [...document.querySelectorAll('#size-sliders input[type=range]')];
    const before = {
      size: Editor.E.st.size.slice(), blocks: Editor.E.st.blocks,
      frame: (Editor.E.st.frame || []).slice(),
      canvasW: document.querySelector('#layer2d').style.width,
      hist: Editor.E.hist.total,
    };
    const want = before.size.map((v) => Math.max(2, Math.floor(v / 2)));
    sliders.forEach((s, i) => {
      s.value = String(want[i]);
      s.dispatchEvent(new Event('input', { bubbles: true }));
    });
    document.querySelector('#btn-size-apply').click();
    await new Promise(r => setTimeout(r, 2000));
    return {
      before, want,
      size: Editor.E.st.size.slice(), frame: (Editor.E.st.frame || []).slice(),
      blocks: Editor.E.st.blocks, outside: Editor.E.st.outside,
      canvasW: document.querySelector('#layer2d').style.width,
      status: document.querySelector('#editor-status').textContent,
      info: document.querySelector('#layer2d-info').textContent,
      hist: Editor.E.hist.total,
      histLabels: (Editor.E.hist.undo || []).slice(-2).map(x => x.label),
      frameBox: Editor.E.viewer.frameBox ? Editor.E.viewer.frameBox.slice() : null,
    };
  `);
  check('编辑器：缩小画布框只改框（数据范围/方块数/2D 画布都不变）',
        JSON.stringify(frame1.size) === JSON.stringify(frame1.before.size) &&
        frame1.blocks === frame1.before.blocks &&
        JSON.stringify(frame1.frame) === JSON.stringify(frame1.want) &&
        frame1.canvasW === frame1.before.canvasW &&
        frame1.frameBox !== null && /画布框/.test(frame1.status) &&
        /框外/.test(frame1.status + frame1.info),
        frame1);
  check('编辑器：保存范围变化进操作日志（名为「保存范围」）',
        frame1.hist === frame1.before.hist + 1 &&
        frame1.histLabels.includes('保存范围'),
        { hist: `${frame1.before.hist}→${frame1.hist}`, labels: frame1.histLabels });

  // 回归：框外还能继续放东西（画布框不动，数据写在框外）
  const frame2 = await ev(`
    document.querySelector('[data-tool=place]').click();
    Editor.E.state = 'minecraft:gold_block';
    const c = document.querySelector('#layer2d');
    const r = c.getBoundingClientRect();
    const [sx, sy, sz] = Editor.E.st.size;
    const [fx, fy, fz] = Editor.E.st.frame;
    const y = Editor.E.layer;
    let spot = null;
    for (let z = 0; z < sz && !spot; z++) {
      for (let x = fx; x < sx; x++) {
        if (!Editor.E.voxels[(y * sz + z) * sx + x]) { spot = { x, z }; break; }
      }
    }
    if (!spot) return { err: '框外找不到空格' };
    const o = { clientX: r.left + spot.x * Editor.E.zoom + Editor.E.zoom / 2,
                clientY: r.top + spot.z * Editor.E.zoom + Editor.E.zoom / 2,
                button: 0, buttons: 1, bubbles: true, pointerId: 71,
                pointerType: 'mouse', isPrimary: true };
    const before = { frame: Editor.E.st.frame.slice(), blocks: Editor.E.st.blocks,
                     outside: Editor.E.st.outside };
    c.dispatchEvent(new PointerEvent('pointerdown', o));
    c.dispatchEvent(new PointerEvent('pointerup', o));
    await new Promise(res => setTimeout(res, 1500));
    const idx = Editor.E.voxels[(y * sz + spot.z) * sx + spot.x];
    return { spot, y, before, frame: Editor.E.st.frame.slice(),
             blocks: Editor.E.st.blocks, outside: Editor.E.st.outside,
             state: Editor.E.palette[idx] || null,
             status: document.querySelector('#editor-status').textContent };
  `);
  check('编辑器：画布框外还能继续放置（框不动，格写到框外）',
        !frame2.err && frame2.state === 'minecraft:gold_block' &&
        frame2.blocks === frame2.before.blocks + 1 &&
        frame2.outside === frame2.before.outside + 1 &&
        JSON.stringify(frame2.frame) === JSON.stringify(frame2.before.frame),
        frame2);

  // 另存为弹窗要提醒「会按画布框裁剪」
  const frame3 = await ev(`
    document.querySelector('#btn-save-as').click();
    await new Promise(r => setTimeout(r, 400));
    const body = document.querySelector('#modal-root .modal .body');
    const text = body ? body.textContent : '';
    const btns = [...document.querySelectorAll('#modal-root .modal .foot button')];
    const cancel = btns.find(b => b.textContent.includes('取消'));
    if (cancel) cancel.click();
    await new Promise(r => setTimeout(r, 200));
    return { text: text.slice(0, 160), closed: !document.querySelector('#modal-root .modal') };
  `);
  check('编辑器：另存为弹窗提醒按画布框裁剪',
        /画布框/.test(frame3.text) && /裁剪/.test(frame3.text) && frame3.closed,
        frame3);

  // 画布框这一步可撤销（回到原来的框）
  const frame4 = await ev(`
    const before = Editor.E.st.frame.slice();
    for (let i = 0; i < 12; i++) {          // 撤到「保存范围」那一步（中间有放置）
      if (!Editor.E.st.can_undo) break;
      document.getElementById('btn-undo').click();
      await new Promise(r => setTimeout(r, 900));
      if (JSON.stringify(Editor.E.st.frame) !== JSON.stringify(before)) break;
    }
    return { before, frame: Editor.E.st.frame.slice(),
             size: Editor.E.st.size.slice(), blocks: Editor.E.st.blocks };
  `);
  check('编辑器：撤销能回到原来的画布框（数据不丢）',
        JSON.stringify(frame4.frame) !== JSON.stringify(frame4.before) &&
        JSON.stringify(frame4.size) === JSON.stringify(frame1.before.size) &&
        frame4.frame[0] === frame1.before.frame[0],
        frame4);

  // 背景切换：编辑器视口（黑/白/透明）+ 渲染队列背景（白/黑/透明）
  const bg = await ev(`
    const sel = document.querySelector('#view-bg');
    const opts = [...sel.options].map(o => o.value);
    sel.value = 'white';
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    await new Promise(r => setTimeout(r, 500));
    const v = Editor.E.viewer;
    v.draw();
    const gl = v.gl;
    const buf = new Uint8Array(4);
    gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, buf);   // 左下角 = 背景
    const white = [buf[0], buf[1], buf[2]];
    sel.value = 'transparent';
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    await new Promise(r => setTimeout(r, 300));
    const tbuf = new Uint8Array(4);
    v.draw();
    gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, tbuf);
    const checker = document.querySelector('.viewport-wrap').classList.contains('bg-checker');
    sel.value = 'dark';
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    const rsel = document.querySelector('#render-bg');
    return { opts, white, transparent: [tbuf[0], tbuf[1], tbuf[2], tbuf[3]], checker,
             bg: v.bg, renderOpts: [...rsel.options].map(o => o.value),
             stored: localStorage.getItem('structworkshop.background') };
  `);
  check('编辑器：视口背景可切黑/白/透明 + 渲染背景可选',
        ['black', 'white', 'transparent'].every((m) => bg.opts.includes(m))
        && bg.white[0] > 245 && bg.white[1] > 245 && bg.white[2] > 245
        && bg.transparent[3] === 0 && bg.checker
        && ['black', 'white', 'transparent'].every((m) => bg.renderOpts.includes(m)),
        bg);
  await shot('09-background.png');

  // ---------- 「重算连接」端到端（换一个连接状态过期的模块）----------
  await send('Page.navigate', { url: base + '/?view=editor&open=packs/modern-arch/modules/aero/aero_mech_band.schem' });
  await sleep(15000);
  // 打开模块后内容是**装配实例**（可拖动）；「重算连接」作用于基地层，
  // 所以先「固化装配」把实例化成体素（这也是改模块内容的标准前一步）。
  await ev(`
    const b = document.querySelector('#btn-mod-bake');
    if (b) b.click();
    await new Promise((r) => setTimeout(r, 4000));
    return true;
  `);
  await sleep(2000);
  const before = await ev(`
    const pal = Editor.E.palette || [];
    const bar = pal.find((s) => s.includes('iron_bars['));
    return {
      name: Editor.E.st ? Editor.E.st.name : null,
      barsWithDir: pal.filter((s) => s.includes('iron_bars[') && s.includes('north=')).length,
      sample: bar || '',
      hist: Editor.E.hist ? Editor.E.hist.total : -1,
    };
  `);
  const after = await ev(`
    document.querySelector('#btn-recompute').click();
    await new Promise((r) => setTimeout(r, 8000));
    const pal = Editor.E.palette || [];
    return {
      barsWithDir: pal.filter((s) => s.includes('iron_bars[') && s.includes('north=')).length,
      barsTrue: pal.filter((s) => s.includes('iron_bars[') && s.includes('=true')).length,
      dirty: Editor.E.st.dirty,
      hist: Editor.E.hist ? Editor.E.hist.total : -1,
      toasts: [...document.querySelectorAll('.toast')].map((t) => t.textContent).join(' | '),
    };
  `);
  check('编辑器：「重算连接」真的补上过期连接状态',
        after.barsWithDir > before.barsWithDir && after.barsTrue > 0 &&
        after.hist === before.hist + 1,
        { name: before.name, before: before.barsWithDir, after: after.barsWithDir,
          hist: `${before.hist}→${after.hist}`, dirty: after.dirty,
          toast: after.toasts.slice(0, 60) });
  check('编辑器：「重算连接」后 3D 不报错且渲染正常',
        !after.__err && errors.length === 0,
        { err: after.__err || null, errors: errors.slice(0, 2) });
  await shot('10-recompute.png');

  // ---------- 编辑器渲染：明暗 / 隐形线框 / 特判方块 / 液体贴图 ----------
  const rfix = await ev(`
    const v = Editor.E.viewer;
    const r = v.renderer;
    const R3 = window.McRender3D;
    const inv = (r && r.invisibleBlocksMesh) ? r.invisibleBlocksMesh.lineVertices() : 0;
    return { mode: v.rendererMode, ownRenderer: !!(r && r.setEnv && r.pump),
             faceShade: R3 ? R3.FACE_SHADE : null,
             aoLevels: R3 ? R3.AO_LEVELS : null,
             invisibleLines: inv,
             chunks: r && r.chunks ? r.chunks.size : -1,
             // 画布切块上界：负 chunk key 被夹到 0 会让同一份网格进多个 key（8 倍重复绘制）
             chunkGrid: (() => { const s = v.size || [0, 0, 0];
               return Math.ceil(s[0] / 16) * Math.ceil(s[1] / 16) * Math.ceil(s[2] / 16); })(),
             quads: r && r.countQuads ? r.countQuads() : -1,
             bakedStates: r && r.mesher ? r.mesher.baked.size : -1,
             missing: r && r.mesher ? r.mesher.missing : -1,
             meshMs: v.meshMs || -1,
             palette: Editor.E.palette.length,
             extraTextures: (Editor.E.extraTextures || []).length,
             semantics: Object.keys((McStudio3D.semantics || {}).semanticsByName || {}).length };
  `);
  check('编辑器：默认用自己的 mesher（renderer3d.js），且真的建出了网格',
        rfix.ownRenderer === true && rfix.mode === 'own' && rfix.chunks > 0 && rfix.quads > 0,
        rfix);
  check('编辑器：面明暗 = vanilla 四档（上1.0/下0.5/南北0.8/东西0.6），AO 四档表与 mcrender 一致',
        Array.isArray(rfix.faceShade) &&
        JSON.stringify(rfix.faceShade) === JSON.stringify([0.6, 0.6, 1.0, 0.5, 0.8, 0.8]) &&
        rfix.aoLevels.every((v, i) => Math.abs(v - [0.45, 0.62, 0.8, 1][i]) < 0.002), rfix);
  check('编辑器：chunk 数不超画布切块数（负 chunk 不许夹到 0 → 一份网格进 8 个 key）',
        rfix.chunkGrid > 0 && rfix.chunks > 0 && rfix.chunks <= rfix.chunkGrid, rfix);
  check('编辑器：几何按调色板状态缓存烘焙（baked ≤ 调色板数）+ 没有隐形线框',
        rfix.bakedStates > 0 && rfix.bakedStates <= rfix.palette && rfix.invisibleLines === 0, rfix);

  // 旗帜图案层贴图（NBT → entity/banner/<pattern>）+ AO 表来源
  const wiring = await ev(`
    const refs = McStudio3D.bannerPatternTextures([
      [0, 0, 0, 'minecraft:white_banner', { patterns: [{ color: 'red', pattern: 'stripe_top' }] }],
      [1, 0, 0, 'minecraft:chest', {}],
    ]);
    const info = await App.post('/api/palette-info', {
      states: ['minecraft:stone'], version: Editor.E.st.version });
    return { refs, aoLevels: info.aoLevels, localAo: McRender3D.AO_LEVELS };
  `);
  check('编辑器：旗帜图案层贴图由 NBT 推出（entity/banner/<pattern>）',
        wiring.refs && wiring.refs.length === 1 && wiring.refs[0] === 'entity/banner/stripe_top', wiring);
  check('编辑器：AO 四档表来自 Python（与 mcrender 同源）',
        Array.isArray(wiring.aoLevels) && wiring.aoLevels.length === 4 &&
        wiring.aoLevels.every((v, i) => Math.abs(v - wiring.localAo[i]) < 0.002), wiring);

  // 浏览器里的真实延迟：改一格 → 只重建 ±1 chunk
  const perf = await ev(`
    const v = Editor.E.viewer;
    const st = Editor.E.st;
    const x = Math.floor(st.size[0] / 2), y = 1, z = Math.floor(st.size[2] / 2);
    const r = v.renderer;
    const t0 = performance.now();
    v.setVoxel(x, y, z, 1);
    r.flush();
    const dt = performance.now() - t0;
    v.draw();
    const gl = v.gl;
    let bright = 0;
    const buf = new Uint8Array(gl.drawingBufferWidth * gl.drawingBufferHeight * 4);
    gl.readPixels(0, 0, gl.drawingBufferWidth, gl.drawingBufferHeight, gl.RGBA, gl.UNSIGNED_BYTE, buf);
    for (let i = 0; i < buf.length; i += 4) if (buf[i] + buf[i+1] + buf[i+2] > 120) bright += 1;
    return { dt: Math.round(dt * 10) / 10, bright };
  `);
  check('编辑器：改一格 → ±1 chunk 增量重建 < 60ms，且 3D 画面正常',
        perf.dt >= 0 && perf.dt < 60 && perf.bright > 1000, perf);

  // 液体/方块实体：浴室模块有 water + water_cauldron（旧行为：水侧面是品红/黑棋盘）
  await send('Page.navigate', { url: base + '/?view=editor&open=packs/soviet-khrushchyovka/modules/props/bath_soviet.schem' });
  await sleep(15000);
  const water = await ev(`
    const v = Editor.E.viewer;
    const id = deepslate.Identifier.parse('block/water_flow');
    const uv = v.resources.atlas.getTextureUV(id);
    v.draw();
    const gl = v.gl, w = gl.drawingBufferWidth, h = gl.drawingBufferHeight;
    const buf = new Uint8Array(w * h * 4);
    gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, buf);
    let magenta = 0, drawn = 0;
    for (let i = 0; i < buf.length; i += 4) {
      const r = buf[i], g = buf[i + 1], b = buf[i + 2];
      if (r > 180 && g < 90 && b > 180) magenta++;
      if (r + g + b > 60) drawn++;
    }
    return { uv, extra: Editor.E.extraTextures || [], magenta,
             drawn: Math.round(drawn / (w * h) * 1000) / 10 };
  `);
  check('编辑器：液体流面贴图真的取到（水侧面不再是品红棋盘）',
        water.extra.includes('block/water_flow') &&
        (water.uv[0] > 0 || water.uv[1] > 0) && water.magenta === 0 && water.drawn > 1,
        water);

  // 模块属性面板：打开资产包模块 → **左侧**「预览图 + 简介/属性」栏（不是模块时整栏收起）
  const modPanel = await ev(`
    const win = document.querySelector('#mod-meta-win');
    const aside = document.querySelector('#mod-meta-panel');
    const d = await Editor.refreshModulePanel();
    const img = document.querySelector('#mod-meta-preview');
    const hints = {
      refFromModule: Editor.moduleRefFromPath('packs/modern-arch/modules/rooms/modern_lobby.schem'),
      refFromPackRel: Editor.moduleRefFromPath('soviet-khrushchyovka/modules/props/bath_soviet.schem'),
      refFromBuild: Editor.moduleRefFromPath('builds/现代摩天大楼/现代摩天楼.schem'),
      refFromNull: Editor.moduleRefFromPath(null),
    };
    return {
      visible: !win.classList.contains('hidden'),
      asideShown: !!aside && !aside.classList.contains('hidden'),
      asideRect: aside
        ? [Math.round(aside.getBoundingClientRect().left),
           Math.round(aside.getBoundingClientRect().right),
           Math.round(aside.getBoundingClientRect().width)] : null,
      viewportLeft: Math.round(document.querySelector('.viewport-wrap')
        .getBoundingClientRect().left),
      id: document.querySelector('#mod-meta-id').textContent,
      previewSrc: img && img.getAttribute('src'),
      previewShown: !!img && !img.classList.contains('hidden'),
      desc: document.querySelector('#mod-meta-desc').value,
      tags: document.querySelector('#mod-meta-tags').value,
      kv: document.querySelector('#mod-meta-kv').textContent,
      ports: document.querySelectorAll('#mod-meta-ports tr').length,
      hints, entryId: d && d.entry && d.entry.id,
    };
  `);
  check('编辑器：打开模块时**左侧**出现「模块属性」（预览图 + 简介/属性）',
        modPanel.visible && modPanel.asideShown &&
        modPanel.asideRect && modPanel.asideRect[1] <= modPanel.viewportLeft + 1 &&
        modPanel.asideRect[2] >= 200 &&
        modPanel.entryId === 'bath_soviet' &&
        /bath_soviet/.test(modPanel.id) && !!modPanel.previewSrc &&
        modPanel.previewShown && modPanel.desc.length > 0 &&
        modPanel.tags.length > 0 && /×/.test(modPanel.kv), modPanel);
  check('编辑器：模块路径识别（packs/… 与包内相对路径都认；builds/ 与 null 不算模块）',
        !!modPanel.hints.refFromModule && modPanel.hints.refFromModule.id === 'modern_lobby' &&
        !!modPanel.hints.refFromPackRel && modPanel.hints.refFromPackRel.id === 'bath_soviet' &&
        modPanel.hints.refFromBuild === null && modPanel.hints.refFromNull === null,
        modPanel.hints);
  // 打开的不是模块（builds/… 或空画布）时，左栏换成**当前投影属性**（窗口不消失、
  // 两个窗口互斥）；回到模块又换成模块属性。
  const modCollapse = await ev(`
    const E = window.Editor.E, aside = document.querySelector('#mod-meta-panel');
    const keep = E.st.path, keepPid = E.selectedPid;
    Editor.selectPlacement(null, false);     // 看「没选中」时的左栏
    await new Promise(r => setTimeout(r, 300));
    const shot = () => ({ aside: !aside.classList.contains('hidden'),
                          win: !document.querySelector('#mod-meta-win').classList.contains('hidden'),
                          proj: !document.querySelector('#proj-meta-win').classList.contains('hidden'),
                          projKV: document.querySelector('#proj-meta-kv').textContent.slice(0, 80) });
    E.st.path = 'builds/现代摩天大楼/现代摩天楼.schem';
    await Editor.refreshModulePanel();
    const off = shot();
    E.st.path = null;
    await Editor.refreshModulePanel();
    const off2 = shot();
    E.st.path = keep;
    Editor.selectPlacement(keepPid, false);
    await Editor.refreshModulePanel();
    return { off, off2, back: shot() };
  `);
  check('编辑器：不是模块时左栏切到「投影属性」（模块属性收起，两个窗口互斥）',
        modCollapse && !modCollapse.off.win && modCollapse.off.proj &&
        !modCollapse.off2.win && modCollapse.off2.proj &&
        modCollapse.off.aside && modCollapse.off2.aside &&
        modCollapse.back.aside && modCollapse.back.win &&
        !modCollapse.back.proj && modCollapse.off.projKV.includes('尺寸'),
        modCollapse);

  const modSave = await ev(`
    const mid = Editor.moduleRefFromPath(Editor.E.st.path).id;
    const before = await App.api('/api/modules/' + mid);
    const desc0 = before.spec.description || '';
    const tags0 = (before.spec.tags || []).join(', ');
    document.querySelector('#mod-meta-desc').value = '审计写入的描述';
    document.querySelector('#mod-meta-tags').value = 'audit-probe, 测试';
    document.querySelector('#mod-meta-save').click();
    await new Promise((r) => setTimeout(r, 3000));
    const after = await App.api('/api/modules/' + mid);
    // 还原：这是仓库里受版本控制的 .module.json，测试不能留痕
    document.querySelector('#mod-meta-desc').value = desc0;
    document.querySelector('#mod-meta-tags').value = App.propsText(before.spec.tags || []);
    document.querySelector('#mod-meta-save').click();
    await new Promise((r) => setTimeout(r, 3000));
    const back = await App.api('/api/modules/' + mid);
    return { mid, desc0, tags0, afterDesc: after.spec.description,
             afterTags: after.spec.tags, backDesc: back.spec.description,
             backTags: (back.spec.tags || []).join(', ') };
  `);
  check('编辑器：模块属性可保存（写回 .module.json，旧逗号格式粘贴也认）且测试已还原',
        modSave.afterDesc === '审计写入的描述' &&
        JSON.stringify(modSave.afterTags) === JSON.stringify(['audit-probe', '测试']) &&
        modSave.backDesc === modSave.desc0 && modSave.backTags === modSave.tags0, modSave);
  await shot('09a-mod-meta-panel.png');

  // 回归：方块更新重算出的**新连接状态**必须还能画（旧 bug：在墙旁边放一块 → 墙变透明）
  //   两个原因，缺一不可：
  //   1) env 里的 blockStates/semantics 是**按引用**拿的，换数组后 mesher 还在查旧数组
  //      → 新下标越界 → stateAt() 退回 air；
  //   2) 资源集只取了「当前状态对应变体」的模型，新状态要的模型（east=tall →
  //      cobblestone_wall_side_tall）没下载 → getMesh 抛异常 → **整块 chunk 消失**。
  await send('Page.navigate', { url: base + '/?view=editor&open=tests/fixtures/wall_state_probe.schem' });
  {
    let ready = false;
    for (let i = 0; i < 40 && !ready; i++) {
      await sleep(1000);
      ready = await ev(`const v = window.Editor.E.viewer;
        return !!(window.Editor.E.st && v && v.renderer && v.renderer.mesher);`) === true;
    }
    const errBefore = errors.length;
    const wall = await ev(`
      const E = window.Editor.E, v = E.viewer;
      const [sx, sy, sz] = E.st.size;
      const at = (x, y, z) => E.voxels[(y * sz + z) * sx + x];
      const bakedQuads = (i) => {
        const b = v.renderer.mesher.baked.get(i);
        return b ? b.quads.length : null;
      };
      const before = { palette: E.palette.length, wallIdx: at(4, 1, 4),
                       wallQuads: bakedQuads(at(4, 1, 4)),
                       updateOn: document.querySelector('#place-update-on').checked };
      E.state = 'minecraft:stone';
      document.querySelector('[data-tool=place]').click();
      const c = document.querySelector('#gl-canvas');
      const r = c.getBoundingClientRect();
      const want = [[5,1,4],[3,1,4],[4,1,5],[4,1,3]];
      let spot = null;
      for (let y = 10; y < r.height - 10 && !spot; y += 6) {
        for (let x = 10; x < r.width - 10; x += 6) {
          const h = v.pick({ clientX: r.left + x, clientY: r.top + y });
          if (!h) continue;
          if (want.some((w) => w[0] === h.place[0] && w[1] === h.place[1] && w[2] === h.place[2])) {
            spot = { x, y, place: h.place.slice() };
            break;
          }
        }
      }
      if (!spot) return { err: '没找到紧挨墙的可放置像素（夹具变了？）' };
      const evp = (type, buttons) => new PointerEvent(type, {
        clientX: r.left + spot.x, clientY: r.top + spot.y, button: 0, buttons,
        bubbles: true, pointerId: 73, pointerType: 'mouse', isPrimary: true });
      c.dispatchEvent(evp('pointermove', 0));
      c.dispatchEvent(evp('pointerdown', 1));
      c.dispatchEvent(evp('pointerup', 0));
      await new Promise((res) => setTimeout(res, 3500));
      const wi = at(4, 1, 4);
      const env = v.renderer.env;
      return {
        before, palette: E.palette.length, blockStates: v.blockStates.length,
        envStates: env.blockStates.length, envSem: env.semantics.length,
        envIsSameArray: env.blockStates === v.blockStates,
        wallIdx: wi, wallState: E.palette[wi], wallQuads: bakedQuads(wi),
        // 扫描顺序受视口宽窄影响，四个紧邻格子里哪个被点到都对——别写死 (5,1,4)
        neighbors: want.map((w) => [w.join(','), E.palette[at(w[0], w[1], w[2])]]),
        neighbor: E.palette[at(5, 1, 4)],
        bakeFailures: v.renderer.mesher.bakeFailures
          ? [...v.renderer.mesher.bakeFailures.keys()] : [],
      };
    `);
    await shot('09-wall-state-update.png');
    check('编辑器：方块更新重算出新连接状态后墙不会消失（env 同步 + 新状态有模型）',
          wall && !wall.err && wall.before.wallQuads > 0 &&
          (wall.neighbors || []).some((n) => n[1] === 'minecraft:stone') &&
          wall.palette === wall.before.palette + 1 &&
          wall.envStates === wall.palette && wall.envSem === wall.palette &&
          wall.envIsSameArray && wall.wallQuads > 0 &&
          (wall.bakeFailures || []).length === 0 && errors.length === errBefore, wall);
  }

  // 回归：3D 地面网格必须**被建筑遮挡**（旧写法关深度测试画线 → 从上看下去
  // 一格格的网格线盖在模型上，看不清建筑）。用专门的夹具（8×4×8 整块红混凝土，
  // 顶面天然均匀红）：正上方俯视，看顶面矩形内缩 6px 后还有没有网格灰像素。
  await send('Page.navigate', { url: base + '/?view=editor&open=tests/fixtures/grid_depth_probe.schem' });
  {
    let ready = false;
    for (let i = 0; i < 40 && !ready; i++) {
      await sleep(1000);
      ready = await ev(`const v = window.Editor.E.viewer;
        return !!(window.Editor.E.st && v && v.renderer && v.renderer.mesher);`) === true;
    }
  }
  const gridDepth = await ev(`
    const E = window.Editor.E, v = E.viewer;
    const [sx, sy, sz] = E.st.size;
    v.cam = { yaw: 0, pitch: 1.5707, dist: Math.max(sx, sz, 8) * 3.2,
              target: [sx / 2, 0.5, sz / 2] };
    // 坐标轴箭头是**故意的覆盖层**（不吃深度，永远看得见），量「网格有没有被
    // 模型挡住」时要先关掉它，否则它的彩线会被当成网格线/撑大红色包围盒。
    const keepArrows = v.renderer.showAxisArrows;
    const keepBoxes = v.moduleBoxes, keepGizmo = v.gizmo;
    v.renderer.showAxisArrows = false;
    v.moduleBoxes = []; v.gizmo = null;   // 模块手柄/三箭头也是覆盖层，一起关掉
    v.draw();
    const gl = v.gl, W = gl.drawingBufferWidth, H = gl.drawingBufferHeight;
    const buf = new Uint8Array(W * H * 4);
    gl.readPixels(0, 0, W, H, gl.RGBA, gl.UNSIGNED_BYTE, buf);
    const isRed = (i) => buf[i] > 90 && buf[i] - buf[i+1] > 40;
    let x0 = W, x1 = -1, y0 = H, y1 = -1, reds = 0;
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        if (!isRed((y * W + x) * 4)) continue;
        reds++;
        if (x < x0) x0 = x; if (x > x1) x1 = x;
        if (y < y0) y0 = y; if (y > y1) y1 = y;
      }
    }
    if (!reds) return { err: '俯视看不到红色顶面' };
    let bad = 0, grid = 0, total = 0;
    for (let y = y0 + 6; y <= y1 - 6; y++) {        // 内缩 6px 排除轮廓线
      for (let x = x0 + 6; x <= x1 - 6; x++) {
        const i = (y * W + x) * 4;
        total++;
        if (!isRed(i)) bad++;
        if (Math.abs(buf[i]-buf[i+1]) < 14 && Math.abs(buf[i+1]-buf[i+2]) < 14 && buf[i] > 150) grid++;
      }
    }
    v.renderer.showAxisArrows = keepArrows;
    v.moduleBoxes = keepBoxes; v.gizmo = keepGizmo;
    return { reds, total, bad, grid, pct: Math.round(bad / Math.max(1, total) * 1000) / 10 };
  `);
  check('编辑器：3D 地面网格被模型遮挡（正上方俯视，模型顶面里没有网格线）',
        gridDepth && gridDepth.reds > 1000 && gridDepth.grid === 0 && gridDepth.pct < 1,
        gridDepth);
  await shot('09b-grid-depth.png');

  // ---------- 设置页：备份策略（不备份 / 按天留存 / 保留最近 n 次） ----------
  // 注意：这段会真的改一次设置文件（.cache/mcstudio/settings.json），结束时按读到的原值还原。
  const setPage = await ev(`
    const tabs = [...document.querySelectorAll('.tabs .tab')];
    const names = tabs.map(t => t.dataset.view);
    tabs.find(t => t.dataset.view === 'settings').click();
    await new Promise(r => setTimeout(r, 1200));
    const view = document.querySelector('#view-settings');
    const cur = await App.api('/api/settings');
    return {
      names, order: names.indexOf('settings') === names.indexOf('editor') + 1,
      active: view.classList.contains('active'),
      modes: [...document.querySelectorAll('#set-modes .set-mode')].map(b => b.dataset.mode),
      activeMode: (document.querySelector('#set-modes .set-mode.active') || {}).dataset.mode,
      count: document.querySelector('#set-count').value,
      days: document.querySelector('#set-days').value,
      statsRows: document.querySelectorAll('#set-stats .set-srow').length,
      root: document.querySelector('#set-root').textContent,
      note: document.querySelector('#set-note').textContent,
      saved: cur.backup,
      overflow: document.documentElement.scrollWidth - innerWidth,
    };
  `);
  check('设置页：tab 紧跟在「结构编辑器」后面且能切过去',
        setPage.order && setPage.active && setPage.names.includes('settings'), setPage.names);
  check('设置页：三种模式 + 数字框已回填 + 现状统计/目录已渲染',
        JSON.stringify(setPage.modes) === JSON.stringify(['off', 'count', 'daily']) &&
        setPage.activeMode === setPage.saved.mode &&
        Number(setPage.count) === setPage.saved.count &&
        Number(setPage.days) === setPage.saved.days &&
        setPage.statsRows >= 5 && /目录：/.test(setPage.root) &&
        setPage.note.length > 0 && setPage.overflow <= 2,
        setPage);

  const setSave = await ev(`
    const before = (await App.api('/api/settings')).backup;
    const target = before.mode === 'daily' ? 'count' : 'daily';
    document.querySelector('#set-modes .set-mode[data-mode="' + target + '"]').click();
    const daysRowOff = document.querySelector('#set-days-row').classList.contains('off');
    const daysDisabled = document.querySelector('#set-days').disabled;
    document.querySelector('#btn-set-save').click();
    await new Promise(r => setTimeout(r, 1500));
    const after = (await App.api('/api/settings')).backup;
    const state = (await App.api('/api/settings')).stats.kinds.structures;
    // 还原成测试前读到的值
    document.querySelector('#set-modes .set-mode[data-mode="' + before.mode + '"]').click();
    document.querySelector('#set-count').value = String(before.count);
    document.querySelector('#set-days').value = String(before.days);
    document.querySelector('#btn-set-save').click();
    await new Promise(r => setTimeout(r, 1500));
    const restored = (await App.api('/api/settings')).backup;
    return { before, target, after, restored, daysRowOff, daysDisabled, files: state.files };
  `);
  check('设置页：改模式保存后服务端真的变了（且测试已还原）',
        setSave.after.mode === setSave.target &&
        JSON.stringify(setSave.restored) === JSON.stringify(setSave.before), setSave);
  check('设置页：切到按天留存时天数框生效、次数框灰掉',
        setSave.daysRowOff === false && setSave.daysDisabled === false, setSave);

  // ---------- 设置页：打开上限（文件大小 / 格数 / 方块数 / 告警阈值） ----------
  const setLim = await ev(`
    const before = (await App.api('/api/settings')).structure;
    const ids = ['#set-max-mb', '#set-max-cells', '#set-max-blocks', '#set-warn-cells'];
    const have = ids.map(s => !!document.querySelector(s));
    const inp = document.querySelector('#set-max-blocks');
    const target = Number(before.values.max_blocks) === 1500000 ? 2500000 : 1500000;
    inp.value = String(target);
    inp.dispatchEvent(new Event('input', { bubbles: true }));
    document.querySelector('#btn-set-limits-save').click();
    await new Promise(r => setTimeout(r, 1500));
    const after = (await App.api('/api/settings')).structure;
    // 还原成测试前保存的值
    inp.value = String(before.saved.max_blocks);
    inp.dispatchEvent(new Event('input', { bubbles: true }));
    document.querySelector('#btn-set-limits-save').click();
    await new Promise(r => setTimeout(r, 1500));
    const restored = (await App.api('/api/settings')).structure;
    return { have, before: before.values, after: after.values,
             savedAfter: after.saved, restored: restored.saved,
             note: document.querySelector('#set-limits-note').textContent };
  `);
  check('设置页：打开上限四项可改，保存后服务端生效（并已还原）',
        setLim.have.every(Boolean) &&
        Number(setLim.savedAfter.max_blocks) !== Number(setLim.before.max_blocks) &&
        Number(setLim.restored.max_blocks) === Number(setLim.before.max_blocks) &&
        /格|块/.test(setLim.note), setLim);
  await shot('11-settings.png');

  // 端口：加载已有端口（画在 3D 里 + 列表）→ 端口工具两点取矩形 → 扫开口 → 保存/删除
  // 用现代建筑包自带端口的模块（只读）+ roof_vent_1（有 2×2 风道开口，保存后还原）
  await send('Page.navigate', { url: base + '/?view=editor&open=packs/modern-arch/modules/aero/aero_core.schem' });
  {
    let ready = false;
    for (let i = 0; i < 40 && !ready; i++) {
      await sleep(1000);
      ready = await ev(`const v = window.Editor.E.viewer;
        return !!(window.Editor.E.st && v && v.renderer && v.renderer.mesher);`) === true;
    }
    const portLoad = await ev(`
      await Editor.refreshModulePanel();
      const E = window.Editor.E;
      return { ports: E.ports.map((p) => [p.id, p.face, p.size]),
               drawn: (E.viewer.ports || []).length,
               rows: document.querySelectorAll('#port-list .port-row').length,
               formShown: !document.querySelector('#port-form').classList.contains('hidden'),
               faceOpts: document.querySelectorAll('#port-face option').length,
               typeOpts: Editor.portTypeOptions().length,
               typeIsSS: !!document.querySelector('#port-type .ss input') };
    `);
    check('接口：打开模块时已加载并画出已有接口（列表 + 3D；面 6 档带轴向）',
          portLoad.ports.length >= 2 && portLoad.drawn === portLoad.ports.length &&
          portLoad.rows === portLoad.ports.length &&
          portLoad.formShown && portLoad.faceOpts === 6 && portLoad.typeOpts >= 17 &&
          portLoad.typeIsSS === true, portLoad);
    // 端口工具：在 3D 里点两个角 → 表单拿到矩形（与 assemble 同一套轴向）
    const twoClick = await ev(`
      const E = window.Editor.E, v = E.viewer;
      const [sx] = E.st.size;
      const c = document.querySelector('#gl-canvas');
      const r = c.getBoundingClientRect();
      document.querySelector('[data-tool=port]').click();
      const picks = [];
      for (let y = 8; y < r.height - 8 && picks.length < 2; y += 5) {
        for (let x = 8; x < r.width - 8; x += 5) {
          const h = v.pick({ clientX: r.left + x, clientY: r.top + y });
          if (!h) continue;
          const f = (() => { const [nx, ny, nz] = h.normal;
            const ax = Math.abs(nx), ay = Math.abs(ny), az = Math.abs(nz);
            if (ay >= ax && ay >= az) return ny > 0 ? 'up' : 'down';
            if (ax >= az) return nx > 0 ? 'east' : 'west';
            return nz > 0 ? 'south' : 'north'; })();
          if (f !== 'east' || h.cell[0] !== sx - 1) continue;   // 只取东面边界层
          if (picks.some((p) => p.cell[1] === h.cell[1] && p.cell[2] === h.cell[2])) continue;
          picks.push({ px: x, py: y, cell: h.cell.slice() });
          if (picks.length === 2 &&
              (picks[0].cell[1] === picks[1].cell[1] || picks[0].cell[2] === picks[1].cell[2])) {
            /* 两个角至少一个轴不同即可 */
          }
          if (picks.length >= 2) break;
        }
      }
      if (picks.length < 2) return { err: '找不到东面两个像素' };
      for (const p of picks) {
        const evp = (type, buttons) => new PointerEvent(type, {
          clientX: r.left + p.px, clientY: r.top + p.py, button: 0, buttons,
          bubbles: true, pointerId: 77, pointerType: 'mouse', isPrimary: true });
        c.dispatchEvent(evp('pointerdown', 1));
        c.dispatchEvent(evp('pointerup', 0));
        await new Promise((res) => setTimeout(res, 250));
      }
      const want = Editor.portFromCells('east', picks[0].cell, picks[1].cell);
      const b1 = [Number(document.querySelector('#port-b1').value),
                  Number(document.querySelector('#port-b2').value)];
      return { cells: picks.map((p) => p.cell), want,
               origin: [Number(document.querySelector('#port-a1').value),
                        Number(document.querySelector('#port-a2').value)],
               size: [b1[0] - Number(document.querySelector('#port-a1').value) + 1,
                      b1[1] - Number(document.querySelector('#port-a2').value) + 1],
               face: document.querySelector('#port-face').value,
               picks: E.portPicks.length, hint: document.querySelector('#port-hint').textContent };
    `);
    check('接口：接口工具点两个角 → 表单得到矩形（点①/点② 两点）',
          twoClick && !twoClick.err && twoClick.face === 'east' &&
          JSON.stringify(twoClick.origin) === JSON.stringify(twoClick.want.origin) &&
          JSON.stringify(twoClick.size) === JSON.stringify(twoClick.want.size) &&
          twoClick.picks === 2, twoClick);
    // 端口矩阵换算：两点 → origin/size → bbox，六个面都要自洽（与 mccore.assemble 同一套约定）
    const portMath = await ev(`
      const E = window.Editor.E, [sx, sy, sz] = E.st.size;
      const out = {};
      for (const face of ['east', 'west', 'north', 'south', 'up', 'down']) {
        const bbox = Editor.portBBox(face, [1, 1], [2, 2]);
        const c1 = [bbox[0], bbox[1], bbox[2]], c2 = [bbox[3], bbox[4], bbox[5]];
        out[face] = { bbox, back: Editor.portFromCells(face, c1, c2) };
      }
      return { size: [sx, sy, sz], out };
    `);
    const facesOk = ['east', 'west', 'north', 'south', 'up', 'down'].every((f) => {
      const back = portMath.out[f].back;
      return back && JSON.stringify(back.origin) === '[1,1]' && JSON.stringify(back.size) === '[2,2]';
    });
    check('接口：两点 ↔ bbox 换算六个面都自洽（与装配同一套轴向）', facesOk, portMath);
    await shot('09d-port-panel.png');
  }
  // roof_vent_1：扫开口 + 保存 + 删除（写完还原，不留痕迹）
  await send('Page.navigate', { url: base + '/?view=editor&open=packs/soviet-khrushchyovka/modules/roof/roof_vent_1.schem' });
  {
    let ready = false;
    for (let i = 0; i < 40 && !ready; i++) {
      await sleep(1000);
      ready = await ev(`const v = window.Editor.E.viewer;
        return !!(window.Editor.E.st && v && v.renderer && v.renderer.mesher);`) === true;
    }
    const scan = await ev(`
      const E = window.Editor.E;
      const pts = () => ({
        origin: [Number(document.querySelector('#port-a1').value),
                 Number(document.querySelector('#port-a2').value)],
        corner2: [Number(document.querySelector('#port-b1').value),
                  Number(document.querySelector('#port-b2').value)],
      });
      const sizeOf2 = () => { const p = pts(); return [p.corner2[0] - p.origin[0] + 1,
                                                      p.corner2[1] - p.origin[1] + 1]; };
      await Editor.refreshModulePanel();
      document.querySelector('[data-tool=port]').click();
      document.querySelector('#port-shape button[data-shape=rect]').click();
      // 「整个面」：整个面当一个接口（柱子分段对接用）
      document.querySelector('#port-face').value = 'down';
      document.querySelector('#port-whole').click();
      const whole = { face: document.querySelector('#port-face').value,
                      origin: pts().origin, size: sizeOf2(),
                      type: Editor.portTypeValue() };
      // 「从框选」：框选两个对角 → 投到面上
      const [sx, sy, sz] = E.st.size;
      E.viewer && E.viewer.setPortPreview(null);
      Editor.AX.sel = [0, 1, 0, 1, 2, 3];      // 任意一段框选
      document.querySelector('#port-face').value = 'east';
      document.querySelector('#port-from-sel').click();
      const fromSel = { origin: pts().origin, size: sizeOf2(),
                        type: Editor.portTypeValue() };
      // 「扫空洞」：面上最大的非实心连通块
      document.querySelector('#port-face').value = 'up';
      document.querySelector('#port-scan').click();
      const scanned = { origin: pts().origin, size: sizeOf2(),
                        preview: !!(E.viewer.portPreview),
                        type: Editor.portTypeValue() };
      // 圆形：切形状 → 直径字段出现，圆心/直径 ⇄ 两点 互搬
      document.querySelector('#port-shape button[data-shape=circle]').click();
      const circ = { rectHidden: document.querySelector('#port-rect-fields').classList.contains('hidden'),
                     circShown: !document.querySelector('#port-circle-fields').classList.contains('hidden'),
                     dia: Number(document.querySelector('#port-dia').value),
                     center: [Number(document.querySelector('#port-c1').value),
                              Number(document.querySelector('#port-c2').value)],
                     shape: Editor.portShape(),
                     b2Hidden: document.querySelector('#port-b2').closest('.p-field')
                       ? document.querySelector('#port-b2').closest('.p-field').classList.contains('hidden')
                       : null }
      ;
      // 圆形回写：圆心 + 直径 → 存盘的 origin/size；再读回来要一致
      const round = Editor.portBoxOf({ face: 'up', shape: 'circle',
                                       origin: [5, 5], size: [3, 3] });
      const back = Editor.fillPortBox('up', [4, 4], [3, 3], { shape: 'circle' });
      const after = { center: [Number(document.querySelector('#port-c1').value),
                               Number(document.querySelector('#port-c2').value)],
                      dia: Number(document.querySelector('#port-dia').value) };
      document.querySelector('#port-shape button[data-shape=rect]').click();
      document.querySelector('#port-face').value = 'up';
      document.querySelector('#port-scan').click();     // 把表单还原成 2×2 vent
      const restored = { origin: pts().origin, size: sizeOf2(),
                         type: Editor.portTypeValue() };
      return { size: [sx, sy, sz], whole, fromSel, scanned, circ, round, after, restored,
               preview: !!(E.viewer.portPreview), type: scanned.type };
    `);
    check('接口：「整个面」把一个面当接口 + 「从框选」投到面上',
          scan && JSON.stringify(scan.whole.origin) === '[0,0]' &&
          JSON.stringify(scan.whole.size) === JSON.stringify([scan.size[0], scan.size[2]]) &&
          scan.whole.type === 'interface' &&
          JSON.stringify(scan.fromSel.origin) === '[1,0]' &&
          scan.fromSel.size[0] === 2 &&
          scan.fromSel.size[1] === Math.min(3, scan.size[2] - 1) + 1 &&
          scan.fromSel.type === 'interface', scan);
    check('接口：「扫空洞」填出顶面 2×2 风道口 + 预览框',
          scan && (scan.scanned.size[0] > 0) && scan.scanned.preview &&
          scan.scanned.type === 'vent' &&
          JSON.stringify(scan.restored.size) === JSON.stringify(scan.scanned.size), scan);
    check('接口：形状可切圆形（矩形字段收起 / 圆心 + 直径 + 两点互搬）+ 圆形 bbox 换算',
          scan && scan.circ.rectHidden && scan.circ.circShown &&
          scan.circ.shape === 'circle' && scan.circ.dia >= 1 &&
          scan.circ.b2Hidden !== false &&
          JSON.stringify(scan.round) === JSON.stringify([[4, 4], [3, 3]]) &&
          JSON.stringify(scan.after) === JSON.stringify({ center: [5, 5], dia: 3 }), scan);
    const saved = await ev(`
      const E = window.Editor.E;
      document.querySelector('#port-id').value = 'audit_vent';
      document.querySelector('#port-apply').click();
      await new Promise((r) => setTimeout(r, 1500));
      const d = await App.api('/api/modules/roof_vent_1');
      const now = (d.entry.ports || []).find((p) => p.id === 'audit_vent') || null;
      // 还原（这是仓库里的模块，不能留痕迹）
      await App.patch('/api/modules/roof_vent_1', { ports: (d.entry.ports || []).filter((p) => p.id !== 'audit_vent') });
      const after = await App.api('/api/modules/roof_vent_1');
      return { now, left: (after.entry.ports || []).length,
               rows: document.querySelectorAll('#port-list .port-row').length };
    `);
    check('接口：保存写进 .module.json（且测试已还原）',
          saved && saved.now && saved.now.face === 'up' &&
          JSON.stringify(saved.now.size) === '[2,2]' && saved.left === 0, saved);
    await shot('09e-port-tool.png');
  }

  // 回归：方块实体（旗帜/箱子/潜影盒/头颅/装饰罐/床/告示牌）都真的画出来。
  // 旧 bug：Python 把「从特判表拿的几何」也算进 has_elements → 客户端看到 has_elements=true
  // 就不跑特判渲染、也不下载 entity 贴图 → 橙色旗帜放下去**什么也看不到**。
  await send('Page.navigate', { url: base + '/?view=editor&open=tests/fixtures/special_blocks.schem' });
  {
    let ready = false;
    for (let i = 0; i < 40 && !ready; i++) {
      await sleep(1000);
      ready = await ev(`const v = window.Editor.E.viewer;
        return !!(window.Editor.E.st && v && v.renderer && v.renderer.mesher);`) === true;
    }
    const sp = await ev(`
      const E = window.Editor.E, v = E.viewer, m = v.renderer.mesher;
      const [sx, sy, sz] = E.st.size;
      const rows = [], seen = new Set();
      for (let y = 0; y < sy; y++) for (let z = 0; z < sz; z++) for (let x = 0; x < sx; x++) {
        const i = E.voxels[(y * sz + z) * sx + x];
        if (!i || seen.has(i)) continue;
        seen.add(i);
        const b = m.baked.get(i);
        const sem = v.renderer.env.semAt(i);
        rows.push({ state: E.palette[i].replace('minecraft:', ''), quads: b ? b.quads.length : -1,
                    missing: b ? b.missing : -1, special: sem ? sem.special : null,
                    hasElements: sem ? sem.has_elements : null });
      }
      return { rows, failures: m.bakeFailures ? [...m.bakeFailures.keys()] : [],
               extras: (E.extraTextures || []).length };
    `);
    const zero = (sp.rows || []).filter((r) => r.quads <= 0);
    check('编辑器：方块实体都画出几何（旗帜/箱子/潜影盒/床/告示牌/钟…）',
          sp && sp.rows && sp.rows.length >= 8 && zero.length === 0 &&
          (sp.failures || []).length === 0 && sp.extras > 0,
          { 状态数: sp.rows && sp.rows.length, 零面: zero,
            失败: sp.failures, 额外贴图: sp.extras });
    const banner = (sp.rows || []).find((r) => r.state.startsWith('orange_banner[rotation=8'));
    check('编辑器：橙色旗帜 rotation=8（用户报的那个）真的画出来',
          !!banner && banner.quads > 0 && banner.special === 'banner' &&
          banner.hasElements === false, banner);
    await shot('09c-special-blocks.png');
  }

  console.log('page errors:', errors.slice(0, 3));

  // ---------- 没开文件时**不**自动建画布（编辑器保持空，提示去打开/新建）----------
  await send('Page.navigate', { url: base + '/?view=editor' });
  await sleep(15000);
  const auto = await ev(`
    return {
      has: !!Editor.E.st,
      status: document.querySelector('#editor-status').textContent,
      tool: Editor.E.tool,
    };
  `);
  check('编辑器：没开文件时不自动建画布（状态栏提示去打开/新建）',
        auto.has === false && /未打开结构/.test(auto.status) && auto.tool === 'place', auto);
  await shot('12-empty-editor.png');
  // 自建（「新建」）仍然可用
  const manual = await ev(`
    const st = await Editor.newCanvas([12, 8, 10], '手动画布');
    return { size: st.size, name: st.name, path: st.path, tool: Editor.E.tool };
  `);
  check('编辑器：「新建」按钮仍能建空画布',
        !!manual && JSON.stringify(manual.size) === JSON.stringify([12, 8, 10]) &&
        manual.path === null, manual);
  const failed = results.filter((r) => !r.ok).length;
  console.log(`\n${results.length - failed}/${results.length} PASS`);
  chrome.kill();
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error('AUDIT ERR', e); process.exit(2); });
