/* mcstudio 设置页：备份策略（不备份 / 按天留存 / 保留最近 N 次）。
   策略存在服务端 <仓库>/.cache/mcstudio/settings.json，mctools CLI 也读它。 */
/* global App */
(function (global) {
  'use strict';

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

  const MODE_LABEL = {
    off: ['不备份', '保存直接覆盖旧文件（已有备份保留，不新建）'],
    count: ['保留最近 N 次', '同一个文件按次数留底，超过 N 份就删最旧的'],
    daily: ['按天留存', '同一个文件每天最多留一份（当天再存就更新当天那份），超过天数就删'],
  };

  //: 打开上限的四行（key / 输入框 / 环境变量徽标）
  const LIMIT_ROWS = [
    ['max_mb', '#set-max-mb', '#set-max-mb-env'],
    ['max_cells', '#set-max-cells', '#set-max-cells-env'],
    ['max_blocks', '#set-max-blocks', '#set-max-blocks-env'],
    ['warn_cells', '#set-warn-cells', '#set-warn-cells-env'],
  ];

  const S = { data: null, busy: false, dirty: false };

  function fmtBytes(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1024 / 1024).toFixed(2) + ' MB';
  }
  function fmtStamp(s) {
    if (!s || s.length < 13) return s || '—';
    return `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)} ${s.slice(9, 11)}:${s.slice(11, 13)}`;
  }

  /** 表单里的当前值（未保存也算）。backup = 备份策略，structure = 打开上限。 */
  function formValue() {
    const num = (id, fallback) => {
      const raw = Number(($(id) || {}).value);
      return Number.isFinite(raw) ? raw : fallback;
    };
    const d = (S.data && S.data.structure && S.data.structure.defaults) || {};
    return {
      backup: {
        mode: (S.data && S.data.backup && S.data.backup.mode) || 'count',
        count: num('#set-count', 20) || 1,
        days: num('#set-days', 7) || 1,
      },
      structure: {
        max_mb: num('#set-max-mb', d.max_mb),
        max_cells: num('#set-max-cells', d.max_cells),
        max_blocks: num('#set-max-blocks', d.max_blocks),
        warn_cells: num('#set-warn-cells', d.warn_cells),
      },
    };
  }

  const fmtNum = (n) => {
    n = Number(n);
    if (!Number.isFinite(n)) return '—';
    return n.toLocaleString('zh-CN', { maximumFractionDigits: 1 });
  };

  /** 「2000 万格」大概多大：给个人话参考，不至于拍脑袋填。 */
  function humanLimits(v) {
    const parts = [];
    const cells = Number(v.max_cells), blocks = Number(v.max_blocks);
    if (Number.isFinite(cells) && cells > 0) {
      const side = Math.floor(Math.cbrt(cells));
      parts.push(`${fmtNum(cells)} 格 ≈ ${side}×${side}×${side} 的立方体`);
    }
    if (Number.isFinite(blocks) && blocks > 0) {
      parts.push(`${fmtNum(blocks)} 块 ≈ ${fmtNum(Math.round(blocks / 590000 * 10) / 10)} 个「太空探索者纪念中心」`);
    }
    return parts.join('；');
  }

  function renderModes() {
    const box = $('#set-modes');
    if (!box) return;
    const cur = formValue().backup.mode;
    box.innerHTML = '';
    for (const m of (S.data && S.data.modes) || ['off', 'count', 'daily']) {
      const [label, hint] = MODE_LABEL[m] || [m, ''];
      box.append(el('button', {
        class: 'set-mode' + (m === cur ? ' active' : ''),
        'data-mode': m, title: hint,
        onclick: () => {
          if (!S.data) return;
          S.data.backup.mode = m;
          S.dirty = true;
          renderModes();
          syncRows();
          renderNote();
        },
      }, el('span', { class: 'sm-name' }, label), el('span', { class: 'sm-hint' }, hint)));
    }
    const lim = (S.data && S.data.limits) || {};
    if ($('#set-count')) $('#set-count').max = String(lim.count || 500);
    if ($('#set-days')) $('#set-days').max = String(lim.days || 365);
  }

  /** 只有当前模式的数字才有意义：另一行灰掉。 */
  function syncRows() {
    const mode = formValue().backup.mode;
    const cRow = $('#set-count-row');
    const dRow = $('#set-days-row');
    if (cRow) cRow.classList.toggle('off', mode !== 'count');
    if (dRow) dRow.classList.toggle('off', mode !== 'daily');
    if ($('#set-count')) $('#set-count').disabled = mode !== 'count';
    if ($('#set-days')) $('#set-days').disabled = mode !== 'daily';
  }

  /** 打开上限：把「已生效的值」填回输入框 + 标出被环境变量锁定的项。 */
  function fillLimitInputs() {
    const spec = (S.data && S.data.structure) || null;
    if (!spec || !$('#set-max-mb')) return;
    const vals = spec.values || {};
    const src = spec.source || {};
    for (const [key, id, envId] of LIMIT_ROWS) {
      const inp = $(id);
      if (!inp) continue;
      const range = (spec.range || {})[key];
      if (range) { inp.min = String(range[0]); inp.max = String(range[1]); }
      inp.value = String(vals[key]);
      const fromEnv = src[key] === 'env';
      inp.disabled = fromEnv;
      inp.title = fromEnv ? `被环境变量 ${(spec.env || {})[key]} 固定，改了不生效` : '';
      const badge = $(envId);
      if (badge) {
        badge.textContent = fromEnv ? `环境变量锁定：${(spec.env || {})[key]}=${vals[key]}` : '';
        badge.classList.toggle('on', fromEnv);
      }
      const row = $(id + '-row');
      if (row) row.classList.toggle('off', fromEnv);
    }
  }

  /** 打开上限的说明文字（输入时就该更新，**不要**回填输入框）。 */
  function renderLimitsNote() {
    const spec = (S.data && S.data.structure) || null;
    const note = $('#set-limits-note');
    if (!spec || !note) return;
    const src = spec.source || {};
    const v = formValue().structure;
    const locked = LIMIT_ROWS.filter(([k]) => src[k] === 'env')
      .map(([k]) => (spec.labels || {})[k] || k);
    const parts = [`当前：${humanLimits(v)}。`];
    parts.push('上限对下一次「打开结构 / 新建画布」生效，已打开的画布不受影响。');
    if (locked.length) parts.push(`以下项被环境变量锁定，设置页改不了：${locked.join('、')}。`);
    if (S.dirty) parts.push('（有未保存的改动）');
    note.textContent = parts.join(' ');
  }

  function renderLimits() {
    fillLimitInputs();
    renderLimitsNote();
  }

  /** 打开上限恢复默认（只改表单，点保存才落盘）。 */
  function resetLimits() {
    const spec = (S.data && S.data.structure) || null;
    if (!spec) return;
    for (const [key, id] of LIMIT_ROWS) {
      const inp = $(id);
      if (inp && !inp.disabled) inp.value = String((spec.defaults || {})[key]);
    }
    S.dirty = true;
    renderLimitsNote();
    App.toast('已填回默认值，点「保存上限」生效', 'ok');
  }

  function renderNote() {
    const note = $('#set-note');
    if (!note || !S.data) return;
    const b = S.data.backup;
    const pruned = S.data.pruned;
    const parts = [];
    if (b.mode === 'off') {
      parts.push('不备份：保存时直接覆盖旧文件；已有备份不会被删（要腾地方点「清空已有备份」）。');
    } else if (b.mode === 'count') {
      parts.push(`保留最近 ${b.count} 次：同一个文件最多留 ${b.count} 份旧版本，超出的最旧份数自动删掉。`);
    } else {
      parts.push(`按天留存：同一个文件每天最多留一份，只保留最近 ${b.days} 天。`);
    }
    if (pruned && pruned.removed) {
      parts.push(`本次保存设置已清理 ${pruned.removed} 份旧备份（释放 ${fmtBytes(pruned.bytes)}）。`);
    }
    if (S.dirty) parts.push('（有未保存的改动）');
    note.textContent = parts.join(' ');
  }

  function renderStats() {
    const box = $('#set-stats');
    if (!box || !S.data) return;
    const st = S.data.stats || { kinds: {}, total: {} };
    box.innerHTML = '';
    const rows = [
      ['structures', '结构保存 / 另存为'],
      ['tools', '工具原地改（mctools --in-place）'],
      ['modules', '删模块时挪走的原件'],
      ['packs', '资产包 --force 覆盖挪走的旧包'],
    ];
    for (const [k, label] of rows) {
      const d = (st.kinds || {})[k] || {};
      box.append(el('div', { class: 'set-srow' + (d.managed ? '' : ' extra') },
        el('span', { class: 'sr-name' }, label),
        el('span', { class: 'sr-num' }, (d.files || 0) + ' 份'),
        el('span', { class: 'sr-num' }, fmtBytes(d.bytes)),
        el('span', { class: 'sr-when' }, d.newest ? fmtStamp(d.newest) : '—'),
        el('span', { class: 'sr-tag' }, d.managed ? '受策略管' : '不受策略管')));
    }
    const t = st.total || {};
    box.append(el('div', { class: 'set-srow head' },
      el('span', { class: 'sr-name' }, '合计'), el('span', { class: 'sr-num' }, `${t.files || 0} 份`),
      el('span', { class: 'sr-num' }, fmtBytes(t.bytes)), el('span', { class: 'sr-when' }, '')));
    const root = $('#set-root');
    if (root) root.textContent = st.root ? `目录：${st.root}` : '';
  }

  function renderAll() {
    if (!S.data) return;
    const b = S.data.backup || {};
    if ($('#set-count')) $('#set-count').value = String(b.count || 20);
    if ($('#set-days')) $('#set-days').value = String(b.days || 7);
    renderModes();
    syncRows();
    renderLimits();
    renderNote();
    renderStats();
  }

  async function load() {
    try {
      S.data = await App.api('/api/settings');
      S.dirty = false;
      renderAll();
    } catch (e) {
      App.toast('读取设置失败: ' + e.message, 'err');
    }
  }

  async function save() {
    if (S.busy) return;
    S.busy = true;
    try {
      const v = formValue();
      S.data = await App.post('/api/settings',
        { backup: v.backup, structure: v.structure, prune: true });
      S.dirty = false;
      renderAll();
      const pr = S.data.pruned || {};
      App.toast(pr.removed
        ? `设置已保存；按新策略清理了 ${pr.removed} 份旧备份（释放 ${fmtBytes(pr.bytes)}）`
        : '设置已保存', 'ok');
    } catch (e) {
      App.toast('保存设置失败: ' + e.message, 'err');
    } finally { S.busy = false; }
  }

  function clearDialog() {
    if (!S.data) return;
    const st = S.data.stats || {};
    const files = ((st.kinds || {}).structures || {}).files +
      ((st.kinds || {}).tools || {}).files;
    const bytes = ((st.kinds || {}).structures || {}).bytes +
      ((st.kinds || {}).tools || {}).bytes;
    App.modal('清空已有备份？', el('div', {},
      el('div', {}, `将删除 ${st.root || '.cache/backups'} 下 ` +
        `structures / tools 两个类别的全部备份：${files} 份，${fmtBytes(bytes)}。`),
      el('div', { class: 'muted' }, '这些只是旧版本留底，删掉不影响当前文件。' +
        '「删模块 / 资产包覆盖」挪走的原件不在清理范围内（避免误删）。')),
      [{ label: '取消' },
       { label: '清空', class: 'danger', onClick: async () => {
         try {
           S.data = await App.post('/api/settings/clear', {});
           renderAll();
           const c = S.data.cleared || {};
           App.toast(`已清空 ${c.removed} 份备份（释放 ${fmtBytes(c.bytes)}）`, 'ok');
         } catch (e) { App.toast('清空失败: ' + e.message, 'err'); }
       } }]);
  }

  function init() {
    const saveBtn = $('#btn-set-save');
    if (saveBtn) saveBtn.addEventListener('click', save);
    const limSave = $('#btn-set-limits-save');
    if (limSave) limSave.addEventListener('click', save);
    const limReset = $('#btn-set-limits-reset');
    if (limReset) limReset.addEventListener('click', resetLimits);
    const reload = $('#btn-set-reload');
    if (reload) reload.addEventListener('click', load);
    const clear = $('#btn-set-clear');
    if (clear) clear.addEventListener('click', clearDialog);
    for (const id of ('#set-count #set-days #set-max-mb #set-max-cells #set-max-blocks #set-warn-cells').split(' ')) {
      const inp = $(id);
      if (inp) inp.addEventListener('input', () => {
        S.dirty = true;
        renderLimitsNote();       // 不要 renderLimits()：那会把输入框回填成已保存值
        renderNote();
      });
    }
  }

  global.Settings = { init, onShow: load, load, save, S };
  document.addEventListener('DOMContentLoaded', init);
})(window);
