/* mcstudio 模块库：标签管理、检索、批量操作、导入、3D 预览、编辑器入口 */
/* global McStudio3D */
(function (global) {
  'use strict';

  // ---------------------------------------------------------------- helpers
  function el(tag, attrs, ...children) {
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
  }
  const $ = (sel) => document.querySelector(sel);
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

  async function api(url, body, method) {
    const opts = { method: method || (body ? 'POST' : 'GET') };
    if (body !== undefined && body !== null) {
      opts.headers = { 'Content-Type': 'application/json' };
      opts.body = JSON.stringify(body);
    }
    const r = await fetch(url, opts);
    let data = null;
    try { data = await r.json(); } catch (e) { data = {}; }
    if (!r.ok) throw new Error(data.error || r.statusText);
    return data;
  }
  const post = (url, body) => api(url, body || {});
  const patch = (url, body) => api(url, body || {}, 'PATCH');

  // ---------------------------------------------------------------- 可搜索下拉
  /** 把「一长串 <select>」换成「输入框 + 向下展开的结果」。
   *
   * opts: {
   *   options: [{value,label,hint}]（也接受纯字符串＝ value 与 label 相同）,
   *   value, placeholder, emptyLabel（给了才在列表首行显示「不限」）,
   *   allowEmpty（显示 × 清空）, max（最多显示多少条，默认 60）,
   *   onPick(value) 选中/清空回调, onQuery(q) -> Promise<options>（服务端搜索，非空查询优先）,
   *   debounce（服务端搜索防抖 ms，默认 180）, title,
   * }
   * 返回 { node, input, value(), set(v), setOptions(list), options(), open(), close(), focus() }
   *
   * 交互：回车/点击选中；↑↓ 选择、Esc 关闭（allowEmpty 时空查询再按 Esc 清空）；
   * 点输入框外收起。**不回敲未选中的输入文字**，避免筛选条件跟着半边词乱跳。
   */
  function searchSelect(opts) {
    opts = opts || {};
    const S = { opts: [], value: opts.value === undefined ? '' : opts.value,
                rows: [], active: -1, open: false, seq: 0, timer: null };

    const norm = (o) => (typeof o === 'string'
      ? { value: o, label: o, hint: '' }
      : { value: String(o.value), label: String(o.label === undefined ? o.value : o.label),
          hint: String(o.hint || '') });
    const input = el('input', {
      class: 'ss-input', type: 'text', autocomplete: 'off', spellcheck: 'false',
      placeholder: opts.placeholder || '搜索…',
      title: opts.title || opts.placeholder || '',
    });
    const clearBtn = el('button', { class: 'ss-x hidden', type: 'button', title: '清除' }, '×');
    const pop = el('div', { class: 'ss-pop hidden' });
    const node = el('div', { class: 'ss' + (opts.class ? ' ' + opts.class : '') },
                    input, clearBtn, pop);

    function labelOf(v) {
      const hit = S.opts.find((o) => o.value === v);
      return hit ? hit.label : v;
    }

    function display() {
      if (document.activeElement !== input) input.value = S.value ? labelOf(S.value) : '';
    }

    function syncClear() {
      clearBtn.classList.toggle('hidden',
        !(opts.allowEmpty && (S.value || input.value)));
    }

    function pick(v) {
      S.value = v === undefined || v === null ? '' : String(v);
      S.open = false;
      pop.classList.add('hidden');
      input.value = S.value
        ? (opts.echoPick === false ? '' : labelOf(S.value)) : '';
      syncClear();
      if (opts.onPick) opts.onPick(S.value);
      else if (opts.onChange) opts.onChange(S.value);
    }

    /** 客户端过滤 + 可选的本地行（不限 / 自写 / 无匹配）。
     *
     * ``allowCustom``：允许「列表里没有的值」——输入框里敲的新词会作为第一行
     * 「使用 “xxx”」候选（回车即采用），所以既是选择器也是可自由填写的输入框。
     * 接口类型就用它：内置 + 已用过的类型随便挑，也能自己写一个新类型。
     */
    function localRows(q) {
      const qq = (q || '').trim().toLowerCase();
      const rows = [];
      if (!qq && opts.emptyLabel) {
        rows.push({ value: '', label: opts.emptyLabel, hint: '', empty: true });
      }
      const typed = (q || '').trim();
      if (opts.allowCustom && typed &&
          !S.opts.some((o) => o.value.toLowerCase() === typed.toLowerCase())) {
        rows.push({ value: typed, label: typed, hint: '自定义', custom: true });
      }
      const max = opts.max || 60;
      for (const o of S.opts) {
        const hay = `${o.label} ${o.value} ${o.hint}`.toLowerCase();
        if (!qq || hay.includes(qq)) rows.push(o);
        if (rows.length >= max + (opts.emptyLabel && !qq ? 1 : 0)) break;
      }
      return rows;
    }

    function renderRows(rows, note) {
      S.rows = rows;
      S.active = rows.length ? 0 : -1;
      pop.innerHTML = '';
      if (note) pop.append(el('div', { class: 'ss-note' }, note));
      rows.forEach((o, i) => {
        pop.append(el('div', {
          class: 'ss-row' + (i === S.active ? ' active' : '')
            + (o.value === S.value ? ' cur' : '') + (o.empty ? ' empty-row' : ''),
          'data-value': o.value,
          onmousedown: (e) => { e.preventDefault(); pick(o.value); },
          onmouseenter: () => { S.active = i; markActive(); },
        }, el('span', { class: 'ss-label' }, o.label),
           o.hint ? el('span', { class: 'ss-hint' }, o.hint) : null));
      });
      if (!rows.length) pop.append(el('div', { class: 'ss-note' }, '没有匹配项'));
    }

    function markActive() {
      [...pop.querySelectorAll('.ss-row')].forEach((r, i) => {
        r.classList.toggle('active', i === S.active);
      });
      const cur = pop.querySelector('.ss-row.active');
      if (cur && cur.scrollIntoView) cur.scrollIntoView({ block: 'nearest' });
    }

    function show() {
      S.open = true;
      pop.classList.remove('hidden');
      const q = input.value;
      const local = localRows(q);
      renderRows(local);
      if (!opts.onQuery) return;
      const seq = ++S.seq;
      clearTimeout(S.timer);
      S.timer = setTimeout(async () => {
        try {
          const rows = await opts.onQuery(input.value.trim());
          if (seq !== S.seq || !S.open) return;      // 过期结果直接丢
          renderRows((rows || []).map(norm));
        } catch (e) { /* 搜索失败就保留本地结果 */ }
      }, q ? (opts.debounce === undefined ? 180 : opts.debounce) : 0);
    }

    input.addEventListener('focus', show);
    input.addEventListener('click', show);
    input.addEventListener('mousedown', show);   // 窗口未激活时 focus 不一定进来，点一下就要开
    input.addEventListener('input', () => { syncClear(); show(); });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        if (!S.open) { show(); return; }
        if (!S.rows.length) return;
        S.active = (S.active + (e.key === 'ArrowDown' ? 1 : -1) + S.rows.length)
          % S.rows.length;
        markActive();
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (S.open && S.rows[S.active]) pick(S.rows[S.active].value);
      } else if (e.key === 'Escape') {
        if (S.open) { S.open = false; pop.classList.add('hidden'); }
        else if (opts.allowEmpty && (S.value || input.value)) pick('');
      } else if (e.key === 'Tab') {
        S.open = false; pop.classList.add('hidden');
        display();
      }
    });
    clearBtn.addEventListener('mousedown', (e) => { e.preventDefault(); pick(''); });
    if (opts.allowEmpty) input.addEventListener('blur', () => setTimeout(display, 0));
    const outside = (e) => {
      if (!node.contains(e.target)) {
        S.open = false;
        pop.classList.add('hidden');
        display();
      }
    };
    document.addEventListener('mousedown', outside);

    const api = {
      node, input, pop,
      value: () => S.value,
      options: () => S.opts.slice(),
      /** 用户真的敲进去的文字（与显示中的选中项标签相同则视为“没敲”）。
       *
       * ``allowCustom`` 的控件必须区分这两者：``set('vent')`` 会把输入框填成
       * 「风口 / 风道」(label)，如果拿 input.value 当值，存下去的就是标签而不是
       * ``vent``（接口类型会被存成一整句话）。
       */
      typed() {
        const t = String(input.value || '').trim();
        if (!t) return '';
        if (S.value && labelOf(S.value) === t) return '';
        return t;
      },
      set(v, o) {
        S.value = v === undefined || v === null ? '' : String(v);
        if (o && o.options) S.opts = o.options.map(norm);
        input.value = S.value ? (opts.echoPick === false ? '' : labelOf(S.value)) : '';
        syncClear();
      },
      setOptions(list) { S.opts = (list || []).map(norm); display(); syncClear(); },
      open: show,
      close() { S.open = false; pop.classList.add('hidden'); },
      focus() { input.focus(); },
    };
    api.setOptions(opts.options);
    api.set(S.value);
    if (opts.echoPick === false) input.placeholder = opts.placeholder || '搜索…';
    return api;
  }

  function toast(msg, kind, ms) {
    const t = el('div', { class: 'toast ' + (kind || '') }, msg);
    $('#toasts').append(t);
    setTimeout(() => t.remove(), ms || 4200);
  }

  function modal(title, bodyNode, buttons, opts) {
    opts = opts || {};
    const root = $('#modal-root');
    const box = el('div', { class: 'modal' + (opts.wide ? ' wide' : '') });
    const close = () => mask.remove();
    box.append(el('div', { class: 'head' }, title));
    box.append(el('div', { class: 'body' }, bodyNode));
    const foot = el('div', { class: 'foot' });
    for (const b of buttons || [{ label: '关闭' }]) {
      foot.append(el('button', {
        class: b.class || '',
        onclick: async () => {
          if (b.onClick) {
            const keep = await b.onClick(close);
            if (keep !== false) close();
          } else close();
        },
      }, b.label));
    }
    box.append(foot);
    const mask = el('div', { class: 'modal-mask', onclick: (e) => {
      if (e.target === mask && !opts.sticky) close();
    } }, box);
    root.append(mask);
    return { close, box, mask };
  }

  function formRow(label, input) {
    return el('div', { class: 'form-row' }, el('label', {}, label), input);
  }

  // -------------------------------------------------- 标签 / 属性（Obsidian 式）
  // 模块的「标签」不是一堆逗号分隔的字符串，而是**属性表**：
  //   tag: office; lobby; glass      ← 默认属性：纯标签（`;` 分隔）
  //   尺寸: 9x6x8; 5x5                ← 自定义属性：落成命名空间标签 「尺寸:9x6x8」
  // 一行一个属性，空行忽略，`#` 开头是注释。值里用 `;`（或 `,`）分隔多个。
  // 这样既有 Obsidian 的「默认 tag + 自定义属性」手感，又**不动存储**：
  // spec.tags 仍是扁平列表，`key:value` 就是现有的命名空间语义（tag_tree 照样分组）。
  const TAG_KEYS = new Set(['tag', 'tags', '标签']);

  /** 一行属性值 → 条目数组（`;` 或 `,` 分隔，去空）。 */
  function splitList(text) {
    return String(text === undefined || text === null ? '' : text)
      .split(/[;,\n]/).map((s) => s.trim()).filter(Boolean);
  }

  /** 属性表文本 → 标签数组（`tag:` 行为纯标签，其余行落成 `属性:值`）。
   *
   * 没写属性名的行（例如直接贴了一行 `office; lobby`）也当纯标签，容错。
   * `tag` 行里允许出现冒号（`尺寸:9x6x8` 原样当纯标签，旧语义保留）。
   */
  function parseProps(text) {
    const out = [];
    const seen = new Set();
    const push = (t) => { const v = String(t).trim(); if (v && !seen.has(v)) { seen.add(v); out.push(v); } };
    for (const raw of String(text === undefined || text === null ? '' : text).split(/\r?\n/)) {
      const line = raw.trim();
      if (!line || line.startsWith('#')) continue;
      const i = line.indexOf(':');
      if (i <= 0) { splitList(line).forEach(push); continue; }
      const key = line.slice(0, i).trim();
      // 属性名里出现 `;` / `,` → 整行其实是**一列普通标签**
      // （旧格式 `office, lobby, 尺寸:9x6x8` 粘进来不会被当成一个属性名吞掉）
      if (/[;,]/.test(key)) { splitList(line).forEach(push); continue; }
      const items = splitList(line.slice(i + 1));
      if (TAG_KEYS.has(key.toLowerCase())) items.forEach(push);
      else items.forEach((v) => push(`${key}:${v}`));
    }
    return out;
  }

  /** 标签数组 → 属性表文本（第一行永远是默认属性 `tag`）。
   *
   * 命名空间标签按**首次出现顺序**分组（`尺寸:9x6x8` → 一行 `尺寸: 9x6x8`）。
   */
  function propsText(tags) {
    const plain = [];
    const groups = new Map();
    for (const t of tags || []) {
      const s = String(t);
      const i = s.indexOf(':');
      if (i <= 0) { plain.push(s); continue; }
      const key = s.slice(0, i).trim();
      const val = s.slice(i + 1).trim();
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(val);
    }
    const lines = ['tag: ' + plain.join('; ')];
    for (const [key, vals] of groups) lines.push(`${key}: ${vals.join('; ')}`);
    return lines.join('\n');
  }

  /** 属性表小控件：多行框 + 实时「N 个标签 / M 个属性」读数。 */
  function propsEditor(value, opts) {
    opts = opts || {};
    const ta = el('textarea', {
      rows: opts.rows || 4, spellcheck: 'false', autocomplete: 'off',
      placeholder: opts.placeholder || 'tag: office; lobby\n尺寸: 9x6x8',
    });
    ta.value = value || '';
    ta.classList.add('props-text');
    const hint = el('div', { class: 'hint2 props-hint' });
    const sync = () => {
      const tags = parseProps(ta.value);
      const props = new Set(tags.filter((t) => t.includes(':')).map((t) => t.split(':')[0]));
      hint.textContent = tags.length
        ? `→ ${tags.length} 个标签 · ${props.size} 个自定义属性`
        : '（空：这个模块没有标签）';
    };
    ta.addEventListener('input', sync);
    sync();
    const node = el('div', { class: 'props-editor' }, ta, hint);
    return { node, input: ta, tags: () => parseProps(ta.value), hint };
  }

  // ---------------------------------------------------------------- state
  const state = {
    modules: [],
    filtered: [],
    tags: [],
    packs: [],
    categories: [],
    selected: new Set(),
    drawerId: null,
    filters: { tags: new Set(), tagMode: 'facet', q: '', pack: '', category: '',
               untagged: false, sort: 'id' },
    tagCounts: null,   // 三种标签模式下的结果数 {facet, and, or}
  };
  const TAG_MODE_LABEL = { facet: '按分组', and: '全选', or: '任一' };

  function filtersToQuery() {
    const q = new URLSearchParams();
    for (const t of state.filters.tags) q.append('tag', t);
    q.set('tagMode', state.filters.tagMode);
    if (state.filters.q) q.set('q', state.filters.q);
    if (state.filters.pack) q.set('pack', state.filters.pack);
    if (state.filters.category) q.set('category', state.filters.category);
    if (state.filters.untagged) q.set('untagged', '1');
    q.set('sort', state.filters.sort);
    return q.toString();
  }

  // ---------------------------------------------------------------- render
  function renderStats(stats) {
    $('#stats').textContent =
      `${stats.modules} 模块 · ${stats.tags} 标签 · ${stats.packs} 资产包 · ` +
      `${stats.previews} 有预览`;
  }

  function renderTagList() {
    const list = $('#tag-list');
    list.innerHTML = '';
    const needle = ($('#tag-search').value || '').toLowerCase();
    const groups = new Map();
    for (const t of state.tags) {
      if (needle && !t.name.toLowerCase().includes(needle)) continue;
      const ns = t.namespace || '·';
      if (!groups.has(ns)) groups.set(ns, []);
      groups.get(ns).push(t);
    }
    for (const [ns, tags] of groups) {
      list.append(el('div', { class: 'tag-ns' }, ns === '·' ? '常用' : ns));
      for (const t of tags) {
        const on = state.filters.tags.has(t.name);
        const row = el('div', {
          class: 'tag-row' + (on ? ' on' : ''),
          title: t.name,
          onclick: () => {
            if (on) state.filters.tags.delete(t.name);
            else state.filters.tags.add(t.name);
            renderTagList();
            loadModules();
          },
        }, el('span', { class: 'name' }, t.leaf),
           el('span', { class: 'count' }, String(t.count)));
        list.append(row);
      }
    }
    if (!list.children.length) list.append(el('div', { class: 'muted' }, '没有标签'));
    const dl = $('#tag-list-all');
    dl.innerHTML = '';
    for (const t of state.tags) dl.append(el('option', { value: t.name }));
  }

  function chip(text, ns) {
    return el('span', { class: 'chip' + (ns ? ' ns' : '') }, text);
  }

  function renderGrid() {
    const grid = $('#module-grid');
    grid.innerHTML = '';
    const rows = state.filtered;
    renderEmptyHint();
    for (const m of rows) {
      const selected = state.selected.has(m.id);
      const card = el('div', {
        class: 'card' + (selected ? ' selected' : ''),
        onclick: (e) => { if (!e.target.closest('.check')) openDrawer(m.id); },
      });
      const thumb = el('div', { class: 'thumb' });
      if (m.preview_urls && m.preview_urls.thumb) {
        thumb.append(el('img', { src: m.preview_urls.thumb, loading: 'lazy',
                                 alt: m.id }));
      } else thumb.append('无预览');
      const chk = el('label', { class: 'check' }, el('input', {
        type: 'checkbox', checked: selected ? 'checked' : null,
        onclick: (e) => {
          e.stopPropagation();
          if (e.target.checked) state.selected.add(m.id);
          else state.selected.delete(m.id);
          renderGrid();
        },
      }));
      const body = el('div', { class: 'body' },
        el('div', { class: 'title' }, m.id),
        el('div', { class: 'meta' },
           el('span', {}, m.category),
           el('span', {}, (m.size || []).join('×')),
           el('span', {}, m.blocks + ' 块'),
           m.ports && m.ports.length ? el('span', {}, m.ports.length + ' 口') : null,
           m.errors && m.errors.length ? el('span', { class: 'danger-text' }, 'spec!') : null),
        el('div', { class: 'chips' }, ...(m.tags || []).slice(0, 6).map(
          (t) => chip(t, t.includes(':'))))); 
      card.append(chk, thumb, body);
      if (m.packs_hint) card.append(el('div', { class: 'badge' }, m.packs_hint));
      grid.append(card);
    }
    renderBatchBar();
  }

  function renderBatchBar() {
    const n = state.selected.size;
    $('#batch-bar').classList.toggle('hidden', n === 0);
    $('#sel-count').textContent = n ? `已选 ${n} 个模块` : '';
  }

  async function loadModules() {
    const data = await api('/api/modules?' + filtersToQuery());
    state.filtered = data.modules;
    state.tagCounts = null;
    renderGrid();
    await refreshTagModeCounts();
  }

  // 三种标签模式各有多少结果：让"多选标签为空"可解释、可一键切换
  async function refreshTagModeCounts() {
    const hasTags = state.filters.tags.size > 0;
    let counts = null;
    if (hasTags) {
      try { counts = await api('/api/modules/counts?' + filtersToQuery()); }
      catch (e) { counts = null; }
    }
    state.tagCounts = counts;
    for (const mode of ['facet', 'and', 'or']) {
      const span = $('#cnt-' + mode);
      if (!span) continue;
      const n = counts ? counts[mode] : null;
      span.textContent = n === null ? '' : '·' + n;
      span.classList.toggle('zero', n === 0);
    }
    renderEmptyHint();
    return counts;
  }

  function switchTagMode(mode) {
    state.filters.tagMode = mode;
    document.querySelectorAll('input[name=tagmode]').forEach((r) => {
      r.checked = r.value === mode;
    });
    loadModules();
  }

  function renderEmptyHint() {
    const box = $('#grid-empty');
    if (!box) return;
    if (state.filtered.length) {
      box.classList.add('hidden');
      box.innerHTML = '';
      return;
    }
    box.classList.remove('hidden');
    box.innerHTML = '';
    const counts = state.tagCounts;
    if (!state.filters.tags.size || !counts) {
      box.append(el('div', {}, '没有匹配的模块'));
      return;
    }
    box.append(el('div', {}, '没有模块同时满足这些标签'));
    box.append(el('div', { class: 'modes' },
      ['facet', 'and', 'or'].map((m) =>
        `${TAG_MODE_LABEL[m]} ${counts[m]}`).join(' · ')));
    const best = ['facet', 'or', 'and'].find((m) => m !== state.filters.tagMode && counts[m] > 0);
    if (best) {
      box.append(el('button', {
        class: 'suggest primary',
        onclick: () => switchTagMode(best),
      }, `改用「${TAG_MODE_LABEL[best]}」查看 ${counts[best]} 个模块`));
    }
  }

  // 工具栏上的可搜索下拉（资产包 / 分类 / 渲染范围）：懒创建，refreshAll 只换 options。
  let packSS = null;
  let catSS = null;
  let renderPackSS = null;

  function mountSearchSelects() {
    if (!packSS && $('#pack-filter')) {
      packSS = searchSelect({
        placeholder: '全部资产包', emptyLabel: '全部资产包', allowEmpty: true,
        onPick: (v) => { state.filters.pack = v; loadModules(); },
      });
      $('#pack-filter').replaceWith(packSS.node);
      packSS.node.id = 'pack-filter';
    }
    if (!catSS && $('#cat-filter')) {
      catSS = searchSelect({
        placeholder: '全部分类', emptyLabel: '全部分类', allowEmpty: true,
        onPick: (v) => { state.filters.category = v; loadModules(); },
      });
      $('#cat-filter').replaceWith(catSS.node);
      catSS.node.id = 'cat-filter';
    }
    if (!renderPackSS && $('#render-pack')) {
      renderPackSS = searchSelect({
        placeholder: '全部资产包', emptyLabel: '全部资产包', allowEmpty: true,
        onPick: () => refreshRenderStatus(),
      });
      $('#render-pack').replaceWith(renderPackSS.node);
      renderPackSS.node.id = 'render-pack';
    }
  }

  async function refreshAll() {
    const st = await api('/api/state');
    state.tags = st.tags;
    state.packs = st.packs;
    state.categories = st.categories;
    state.interfaceTypes = st.interface_types || [];
    renderStats(st.stats);
    mountSearchSelects();
    if (packSS) {
      packSS.setOptions(st.packs.map((p) => ({
        value: p.id, label: p.name || p.id, hint: `${p.modules} 个模块`,
      })));
    }
    if (catSS) {
      catSS.setOptions(st.categories.map((c) => ({ value: c, label: c })));
    }
    if (renderPackSS) {
      renderPackSS.setOptions(st.packs.map((p) => ({
        value: p.id, label: p.name || p.id, hint: `${p.modules} 个模块`,
      })));
    }
    renderTagList();
    await loadModules();
    refreshOpenFiles();
    await refreshRenderStatus();
  }

  // ---------------------------------------------------------------- drawer
  function closeDrawer() {
    $('#drawer').classList.remove('open');
    state.drawerId = null;
  }

  function openDrawer(id) {
    state.drawerId = id;
    const entry = state.filtered.find((m) => m.id === id);
    if (!entry) return;
    const drawer = $('#drawer');
    drawer.innerHTML = '';
    drawer.append(el('div', { class: 'head' },
      el('h3', {}, entry.id),
      el('button', { class: 'link-btn', onclick: closeDrawer }, '✕')));
    const content = el('div', { class: 'content' });
    drawer.append(content);

    if (entry.preview_urls && entry.preview_urls.thumb) {
      content.append(el('img', { class: 'preview', src: entry.preview_urls.thumb }));
    }
    content.append(el('div', { class: 'kv' },
      el('span', {}, '包 ', el('b', {}, entry.pack)),
      el('span', {}, '分类 ', el('b', {}, entry.category)),
      el('span', {}, '尺寸 ', el('b', {}, (entry.size || []).join('×'))),
      el('span', {}, '方块 ', el('b', {}, String(entry.blocks)))));

    const desc = el('textarea', { rows: 3 });
    const cat = el('input', { value: entry.category });
    const tagsEd = propsEditor(propsText(entry.tags || []));
    const tagsInput = tagsEd.input;
    content.append(el('div', { class: 'field' }, el('label', {}, '描述'), desc));
    content.append(el('div', { class: 'field' }, el('label', {}, '分类'), cat));
    content.append(el('div', { class: 'field' },
      el('label', { title: '一行一个属性；值用 ; 分隔；tag 是默认属性（其余属性落成「属性:值」标签）' },
         '属性（标签）'),
      tagsEd.node));

    // 接口：可编辑（点行填表单 → PATCH /api/modules/<id>；服务端会校验面/尺寸/类型）
    // 面：**带轴向指示**（与编辑器同一套措辞）；类型：**可搜索 + 可自写**
    const portFaces = [['west', '西面（−X）'], ['east', '东面（+X）'],
                       ['north', '北面（−Z）'], ['south', '南面（+Z）'],
                       ['up', '顶面（+Y）'], ['down', '底面（−Y）']];
    const portTypes = [['passage', '通道'], ['door', '门'], ['window', '窗'], ['vent', '风口'],
                       ['stair_up', '楼梯上'], ['stair_down', '楼梯下'], ['shaft', '竖井'],
                       ['fluid_in', '进液'], ['fluid_out', '排液'], ['item_in', '进料'],
                       ['item_out', '出料'], ['redstone_in', '红石入'], ['redstone_out', '红石出'],
                       ['power_in', '电力入'], ['power_out', '电力出'],
                       ['anchor', '锚点'], ['interface', '通用接口'], ['light', '采光']];
    let ports = (entry.ports || []).map((x) => Object.assign({}, x));
    let portSel = null;
    const portList = el('div', { class: 'port-edit-list' });
    const pFace = el('select', {}, ...portFaces.map(([v, t]) => el('option', { value: v }, t)));
    // 类型下拉：内置推荐 + 仓库里已用过的 + 自己写的新类型
    const typeSeen = new Map(portTypes);
    for (const t of state.interfaceTypes || []) if (!typeSeen.has(t)) typeSeen.set(t, '已用过');
    for (const p of ports) if (p.type && !typeSeen.has(p.type)) typeSeen.set(p.type, '本模块已用');
    const pTypeSS = searchSelect({
      options: [...typeSeen].map(([value, label]) => ({ value, label })),
      value: 'passage', allowCustom: true,
      placeholder: '选已有类型，或直接写一个新的…',
      title: '类型可以自由填写；自写的类型也会出现在下拉里',
    });
    pTypeSS.node.id = 'drawer-port-type';
    const pId = el('input', { placeholder: 'id' });
    const pTags = el('input', { placeholder: '标签，分号分隔' });
    // 形状：矩形（两点）/ 圆形（圆心 + 直径）——与编辑器同一套存储
    // （圆形：origin = 圆心、size = [直径, 直径]；引擎匹配仍按外接矩形）
    const pShape = el('select', {},
      el('option', { value: 'rect' }, '矩形（两点）'),
      el('option', { value: 'circle' }, '圆形（圆心 + 直径）'));
    const faceAxes = (face) => (face === 'west' || face === 'east') ? ['Y', 'Z']
      : (face === 'north' || face === 'south') ? ['Y', 'X'] : ['X', 'Z'];
    const pNums = {};
    const pLbls = {};
    const numField = (key, min, dflt) => {
      const n = el('input', { type: 'number', min, value: dflt });
      pNums[key] = n;
      const lbl = el('span', { class: 'p-lbl' });
      pLbls[key] = lbl;
      return el('label', { class: 'p-field' }, lbl, n);
    };
    /** 形状 → 标签名（矩形叫点①/点②，圆形叫圆心/直径）与 b2 的显隐。 */
    function syncPortShape() {
      const circ = pShape.value === 'circle';
      const ax = faceAxes(pFace.value);
      pLbls.a1.textContent = circ ? `圆心 ${ax[0]}` : `点① ${ax[0]}`;
      pLbls.a2.textContent = circ ? `圆心 ${ax[1]}` : `点① ${ax[1]}`;
      pLbls.b1.textContent = circ ? '直径（格）' : `点② ${ax[0]}`;
      pLbls.b2.textContent = `点② ${ax[1]}`;
      pNums.b2.parentElement.classList.toggle('hidden', circ);
    }
    const pForm = el('div', { class: 'port-edit-form' },
      el('div', { class: 'port-2col' }, pFace, pShape),
      el('div', { class: 'port-2col' }, pTypeSS.node),
      el('div', { class: 'port-2col' }, numField('a1', 0, 0), numField('a2', 0, 0)),
      el('div', { class: 'port-2col' }, numField('b1', 0, 0), numField('b2', 0, 0)),
      pId, pTags,
      el('div', { class: 'row-btns' },
        el('button', { onclick: async () => {
          const p = readPort();
          const next = ports.filter((x) => x.id !== p.id && (!portSel || x.id !== portSel.id));
          next.push(p);
          await savePorts(next, `已保存接口 ${p.id}`);
        } }, '保存接口'),
        el('button', { onclick: () => { portSel = null;
                                        fillPort({ origin: [0, 0], size: [1, 1],
                                                   shape: pShape.value });
                                        renderPorts(); } }, '清空'),
        el('button', { title: '整个面当一个接口', onclick: () => {
          const sizeOf = (face, axis) => {
            const [sx, sy, sz] = (entry.size || [1, 1, 1]);
            const ax = (face === 'west' || face === 'east') ? ['y', 'z']
              : (face === 'north' || face === 'south') ? ['y', 'x'] : ['x', 'z'];
            const v = axis === 0 ? ax[0] : ax[1];
            return v === 'x' ? sx : v === 'y' ? sy : sz;
          };
          const face = pFace.value;
          portSel = null;
          fillPortBox(face, [0, 0], [sizeOf(face, 0), sizeOf(face, 1)], { type: 'interface' });
          renderPorts();
        } }, '整个面')));

    function readPort() {
      const n = (k, d) => { const v = Number(pNums[k].value); return Number.isFinite(v) ? Math.round(v) : d; };
      const face = pFace.value;
      const id = pId.value.trim() || `${face}_${n('a1', 0)}_${n('a2', 0)}`;
      // 类型：先看用户**真的敲进去**的原文（支持自写），否则用下拉选中值
      // （不能直接读 input.value —— set() 会把标签写进输入框）
      let type = pTypeSS.typed() || pTypeSS.value() || 'passage';
      if (type.startsWith('minecraft:')) type = type.slice(10);
      const tags = pTags.value.split(/[;,]/).map((s) => s.trim()).filter(Boolean);
      const shape = pShape.value === 'circle' ? 'circle' : 'rect';
      let origin, size;
      if (shape === 'circle') {
        const d = Math.max(1, n('b1', 3));
        origin = [Math.max(0, n('a1', 0)), Math.max(0, n('a2', 0))];
        size = [d, d];
      } else {
        const a = [n('a1', 0), n('a2', 0)], b = [n('b1', 0), n('b2', 0)];
        origin = [Math.min(a[0], b[0]), Math.min(a[1], b[1])];
        size = [Math.abs(a[0] - b[0]) + 1, Math.abs(a[1] - b[1]) + 1];
      }
      const p = { id, type, face, origin, size, tags };
      if (shape === 'circle') p.shape = 'circle';
      return p;
    }

    /** 面内包围盒 → 表单（圆形取短边当直径、圆心取正中）。 */
    function fillPortBox(face, origin, size, meta) {
      const shape = (meta && meta.shape) || pShape.value;
      const d = Math.max(1, Math.min(size[0], size[1]));
      const off = Math.floor((d - 1) / 2);
      fillPort({
        face, shape, id: (meta && meta.id) || '',
        type: (meta && meta.type) || (pTypeSS.value() || 'passage'),
        origin: shape === 'circle' ? [origin[0] + off, origin[1] + off] : origin,
        size: shape === 'circle' ? [d, d] : size,
        tags: (meta && meta.tags) || [],
      });
    }

    function fillPort(p) {
      const shape = (p.shape || 'rect') === 'circle' ? 'circle' : 'rect';
      pShape.value = shape;
      pFace.value = p.face || 'east';
      pTypeSS.set(p.type || 'passage');
      const o = p.origin || [0, 0], s = p.size || [1, 1];
      if (shape === 'circle') {
        pNums.a1.value = o[0]; pNums.a2.value = o[1];
        pNums.b1.value = Math.max(1, Math.min(s[0], s[1]));
      } else {
        pNums.a1.value = o[0]; pNums.a2.value = o[1];
        pNums.b1.value = o[0] + Math.max(1, s[0]) - 1;
        pNums.b2.value = o[1] + Math.max(1, s[1]) - 1;
      }
      pId.value = p.id || '';
      pTags.value = (p.tags || []).join('; ');
      syncPortShape();
    }

    function renderPorts() {
      portList.innerHTML = '';
      if (!ports.length) {
        portList.append(el('div', { class: 'hint2' },
          '还没有接口：选形状 → 填两个角（或圆心 + 直径）→ 「保存接口」。' +
          '接口只是装配/AI 的参考，里面实心还是空心无所谓。'));
      }
      for (const p of ports) {
        const circ = (p.shape || 'rect') === 'circle';
        const row = el('div', { class: 'port-edit-row' + (portSel && portSel.id === p.id ? ' on' : '') },
          el('span', {}, circ ? '⭘' : '▭'), el('span', {}, p.id), el('span', {}, p.face),
          el('span', {}, p.type),
          el('span', {}, circ ? `⌀${p.size[0]}` : (p.size || []).join('×')),
          el('button', { class: 'link-btn', title: '删除', onclick: async (ev) => {
            ev.stopPropagation();
            if (!confirm(`删除接口 ${p.id}？`)) return;
            await savePorts(ports.filter((x) => x.id !== p.id), `已删除 ${p.id}`);
          } }, '✕'));
        row.onclick = () => { portSel = p; fillPort(p); renderPorts(); };
        portList.append(row);
      }
    }

    async function savePorts(next, msg) {
      try {
        const out = await patch('/api/modules/' + id, { ports: next });
        ports = ((out.entry && out.entry.ports) || next).map((x) => Object.assign({}, x));
        portSel = null;
        renderPorts();
        toast(msg, 'ok');
        await refreshAll();
      } catch (e) { toast('接口保存失败: ' + e.message, 'err'); }
    }

    content.append(el('div', { class: 'field' },
      el('label', {}, '接口（可编辑：点一行 = 编辑；接口只是装配/AI 的参考）'), portList, pForm));
    pShape.addEventListener('change', syncPortShape);
    pFace.addEventListener('change', syncPortShape);
    syncPortShape();
    renderPorts();

    const statBox = el('div', { class: 'stat-list' }, '加载方块统计…');
    content.append(el('div', { class: 'field' }, el('label', {}, '方块构成'), statBox));

    // 复制 / 转移到别的资产包（结构 + spec + 预览图一起走；move 会备份并删源）
    const packSel = el('select');
    for (const p of state.packs) {
      if (p.id !== entry.pack) packSel.append(el('option', { value: p.id }, p.name));
    }
    const doTransfer = async (mode) => {
      const to = packSel.value;
      if (!to) return;
      if (mode === 'move' &&
          !confirm(`把 ${id} 转移到 ${to}？源模块会先备份到 .cache/backups 再删除。`)) return;
      try {
        const r = await post('/api/modules/' + id + '/transfer', { to_pack: to, mode });
        toast(`${mode === 'move' ? '已转移' : '已复制'}到 ${to}：新 id ${r.id}` +
              (r.renamed_from ? `（原名 ${r.renamed_from} 已占用）` : ''), 'ok', 6000);
        closeDrawer();
        await refreshAll();
      } catch (e) { toast(e.message, 'err'); }
    };
    if (state.packs.length > 1) {
      content.append(el('div', { class: 'field' },
        el('label', {}, '复制 / 转移到别的资产包'),
        el('div', { class: 'drawer-xfer' }, packSel,
           el('button', { onclick: () => doTransfer('copy') }, '复制到'),
           el('button', { onclick: () => doTransfer('move') }, '转移到'))));
    }

    // 「3D 查看 / 在编辑器中打开」要**仓库相对**路径（packs/…）：entry.path 是包内相对，
    // 直接发过去会被权限拦住（旧 bug：报「不允许访问: modern-arch/modules/…」）。
    const repoPath = entry.file || ('packs/' + String(entry.path || '').replace(/^\/+/, ''));
    const btnRow = el('div', { class: 'tool-buttons' },
      el('button', { onclick: async () => {
        try {
          await patch('/api/modules/' + id, {
            description: desc.value, category: cat.value,
            tags: tagsEd.tags(),
          });
          toast('已保存 spec', 'ok');
          await refreshAll();
        } catch (e) { toast(e.message, 'err'); }
      } }, '保存 spec'),
      el('button', { onclick: () => viewStructure(repoPath, { title: entry.id, ports: entry.ports }) }, '3D 查看'),
      el('button', { onclick: () => global.Editor.openPath(repoPath) }, '在编辑器中打开'),
      el('button', { onclick: () => renderPreview(id) }, '重渲染预览'),
      el('button', { class: 'danger', onclick: async () => {
        if (!confirm(`删除模块 ${id}？（备份到 .cache/backups）`)) return;
        try {
          await api('/api/modules/' + id, null, 'DELETE');
          toast('已删除 ' + id, 'ok');
          closeDrawer();
          await refreshAll();
        } catch (e) { toast(e.message, 'err'); }
      } }, '删除'));
    content.append(btnRow);
    drawer.classList.add('open');

    // async detail
    api('/api/modules/' + id).then((d) => {
      desc.value = d.spec.description || '';
      cat.value = d.spec.category || entry.category;
      tagsInput.value = propsText(d.spec.tags || []);
      tagsInput.dispatchEvent(new Event('input'));   // 刷新「N 个标签」读数
      ports = ((d.entry && d.entry.ports) || []).map((x) => Object.assign({}, x));
      renderPorts();
      statBox.innerHTML = '';
      for (const [name, n] of Object.entries(d.blocks || {})) {
        if (name === 'air') continue;
        statBox.append(el('div', {}, el('span', {}, name), el('span', {}, String(n))));
      }
    }).catch((e) => { statBox.textContent = e.message; });
  }

  async function renderPreview(id) {
    try {
      const r = await post(`/api/modules/${id}/preview`, { background: renderBg() });
      toast(`渲染预览中… (${r.job})`);
      const job = await waitJob(r.job);
      if (job.state === 'done') {
        const rep = job.result || {};
        toast(`预览完成：新渲染 ${(rep.rendered || []).length} 个`, 'ok');
        await refreshAll();
      } else toast('预览失败: ' + (job.error || '').split('\n')[0], 'err');
    } catch (e) { toast(e.message, 'err'); }
  }

  async function waitJob(jid, timeoutMs) {
    const t0 = Date.now();
    for (;;) {
      const job = await api('/api/jobs/' + jid);
      if (job.state !== 'running' && job.state !== 'queued') return job;
      if (Date.now() - t0 > (timeoutMs || 180000)) return job;
      await new Promise((r) => setTimeout(r, 800));
    }
  }

  // 渲染/视口背景：白底 / 黑底 / 透明（localStorage 记忆）
  // 键名随项目改名（structworkshop.*）；旧键 mcforge.background 读到就搬过来（不丢偏好）。
  const BG_KEY = 'structworkshop.background';
  const BG_KEY_LEGACY = 'mcforge.background';
  function loadBg() {
    try {
      const v = localStorage.getItem(BG_KEY);
      if (v !== null) return v;
      const old = localStorage.getItem(BG_KEY_LEGACY);
      if (old !== null) { localStorage.setItem(BG_KEY, old); return old; }
    } catch (e) { /* 隐私模式忽略 */ }
    return 'dark';
  }
  function saveBg(mode) {
    try { localStorage.setItem(BG_KEY, mode); } catch (e) { /* 隐私模式忽略 */ }
  }
  function renderBg() {
    const sel = $('#render-bg');
    return (sel && sel.value) || loadBg();
  }
  function viewBg() {
    const sel = $('#view-bg');
    return (sel && sel.value) || loadBg();
  }

  // ---------------------------------------------------------------- 渲染预览队列
  // 预览图是 mcrender 子进程渲染的，一个模块几秒到几十秒；工具栏把任务提交到
  // 服务端的 preview 通道排队（一次只跑一个），这里负责显示进度/队列与取消排队。
  const render = { timer: null };

  function scopeSummary(packs, packId) {
    const list = packId ? packs.filter((p) => p.id === packId) : packs;
    return {
      total: list.reduce((n, p) => n + p.total, 0),
      missing: list.reduce((n, p) => n + p.missing, 0),
    };
  }

  async function refreshRenderStatus() {
    let d;
    try { d = await api('/api/previews'); } catch (e) { return; }
    // 范围下拉的选项就用 /api/previews 的实时缺图统计（比 /api/state 更新）
    if (renderPackSS) {
      const keep = renderPackSS.value();
      renderPackSS.setOptions(d.packs.map((p) => ({
        value: p.id, label: p.name || p.id, hint: `缺 ${p.missing}/${p.total}`,
      })));
      if (!(keep && d.packs.some((p) => p.id === keep))) renderPackSS.set('');
    }
    const s = scopeSummary(d.packs, renderPackSS ? renderPackSS.value() : '');
    $('#btn-render-missing').textContent = `为缺图的渲染 (${s.missing})`;
    $('#btn-render-all').textContent = `全部重新渲染 (${s.total})`;
    $('#btn-render-missing').disabled = s.missing === 0;
    $('#btn-render-all').disabled = s.total === 0;
    const active = (d.jobs || []).filter(
      (j) => j.state === 'running' || j.state === 'queued');
    if (!active.length) {
      $('#render-fill').style.width = '0%';
      $('#render-status').textContent = s.missing
        ? `${s.total} 个模块 · 缺图 ${s.missing}`
        : `${s.total} 个模块 · 预览齐全`;
    }
    renderRenderQueue(active);
    if (active.length && !render.timer) {
      render.timer = setInterval(pollRender, 900);
    }
  }

  function renderRenderQueue(active) {
    const qbox = $('#render-queue');
    qbox.innerHTML = '';
    if (!active || !active.length) return;
    const run = active.find((j) => j.state === 'running') || active[0];
    const pr = run.progress || {};
    const pct = pr.total ? Math.round((pr.done / pr.total) * 100) : 0;
    $('#render-fill').style.width = pct + '%';
    $('#render-status').textContent =
      (run.state === 'running' ? '渲染中 ' : '排队中 ') +
      `${pr.done || 0}/${pr.total || '?'}` + (pr.label ? ` · ${pr.label}` : '');
    const queued = active.filter((j) => j.state === 'queued');
    if (queued.length) {
      qbox.append(el('span', { class: 'rb-idle' }, `队列 ${queued.length}`));
      queued.forEach((j, i) => qbox.append(el('button', {
        class: 'rb-chip', title: '取消排队',
        onclick: () => cancelRenderJob(j.id),
      }, `✕ ${i + 1}`)));
    }
  }

  async function queueRender(scope) {
    const pack = renderPackSS ? renderPackSS.value() : '';
    const background = renderBg();
    saveBg(background);
    const btns = ['#btn-render-missing', '#btn-render-all'].map($);
    btns.forEach((b) => { b.disabled = true; });
    try {
      const r = await post('/api/previews/render', { pack, scope, background });
      toast(r.queued_ahead
        ? `已加入渲染队列（前面还有 ${r.queued_ahead} 个）`
        : `已开始渲染（${r.job}）`, 'ok');
      if (!render.timer) render.timer = setInterval(pollRender, 900);
      pollRender();
    } catch (e) { toast(e.message, 'err'); }
    setTimeout(refreshRenderStatus, 400);   // 恢复按钮状态（缺图 0 时仍禁用）
  }

  async function cancelRenderJob(jid) {
    try { await post(`/api/jobs/${jid}/cancel`); toast('已取消排队', 'ok'); }
    catch (e) { toast(e.message, 'err'); }
  }

  async function pollRender() {
    let jobs;
    try {
      jobs = (await api('/api/jobs')).jobs.filter((j) => j.channel === 'preview');
    } catch (e) { return; }
    const active = jobs.filter(
      (j) => j.state === 'running' || j.state === 'queued');
    renderRenderQueue(active);
    if (active.length) return;
    clearInterval(render.timer);
    render.timer = null;
    const last = jobs[jobs.length - 1];
    await refreshAll();                       // 状态 + 卡片缩略图
    if (last && last.state === 'done') {
      const r = last.result || {};
      const bad = (r.failed || []).length;
      toast(`渲染完成：新渲染 ${(r.rendered || []).length}` +
            (r.skipped ? ` · 跳过 ${r.skipped}` : '') +
            (bad ? ` · 失败 ${bad}` : ''), bad ? 'err' : 'ok', 6000);
    } else if (last && last.state === 'error') {
      toast('渲染失败: ' + (last.error || '').split('\n')[0], 'err', 8000);
    }
  }

  // ---------------------------------------------------------------- 3D modal
  function colorMaps(info) {
    const colors = {}, flags = {};
    for (const [s, v] of Object.entries(info || {})) {
      colors[s] = v.color;
      // flags 整份带过去：编辑器 mesher / 特判渲染器补丁要用 layer·special·has_elements
      flags[s] = Object.assign({}, v);
    }
    return { colors, flags };
  }

  async function openSession(path) {
    const st = await post('/api/structure/open', { path });
    const r = await fetch(`/api/structure/${st.sid}/voxels`);
    const buf = await r.arrayBuffer();
    const pi = await post('/api/palette-info',
      { states: st.palette, version: st.version, dataVersion: st.data_version });
    const maps = colorMaps(pi.info);
    return { st, voxels: new Uint16Array(buf), ...maps, extraTextures: pi.textures || [] };
  }

  async function viewStructure(path, opts) {
    opts = opts || {};
    const canvas = el('canvas');
    const hud = el('div', { class: 'kv' }, '加载中…');
    const layer = el('input', { type: 'range', min: 0, max: 0, value: 0 });
    const only = el('input', { type: 'checkbox' });
    const bgSel = el('select', { class: 'ax-select' },
      ...[['dark', '深底'], ['black', '黑底'], ['white', '白底'],
          ['transparent', '透明']].map(([v, t]) => el('option', { value: v }, t)));
    bgSel.value = loadBg();
    const holder = el('div', { class: 'viewer-modal' }, canvas, hud,
      el('div', { class: 'form-row' },
         el('label', {}, '层'),
         layer, el('label', {}, '只显示'), only,
         el('label', {}, '背景'), bgSel));
    const m = modal(opts.title || '3D 预览', holder, [
      { label: '在编辑器中打开', onClick: () => { global.Editor.openSessionRef(m && m.st); } },
      { label: '关闭' },
    ], { sticky: true, wide: true });
    const viewer = new McStudio3D.VoxelViewer(canvas, { title: opts.title });
    viewer.observeResize();
    viewer.setBackground(bgSel.value);
    bgSel.addEventListener('change', () => {
      saveBg(bgSel.value);
      viewer.setBackground(bgSel.value);
      const v = $('#view-bg');
      if (v) v.value = bgSel.value;
    });
    try {
      const s = await openSession(path);
      const mode = await viewer.load(
        { size: s.st.size, palette: s.st.palette, version: s.st.version,
          colors: s.colors, flags: s.flags, extraTextures: s.extraTextures },
        s.voxels);
      viewer.setPorts(opts.ports || []);
      viewer.fit();
      hud.innerHTML = `${s.st.size.join('×')} · ${s.st.blocks} 块 · ` +
        `${s.st.palette.length} 状态 · ${mode === 'texture' ? '贴图' : '色彩'}模式`;
      layer.max = s.st.size[1] - 1;
      layer.value = Math.floor(s.st.size[1] / 2);
      const apply = () => viewer.setLayerRange(Number(layer.value),
        Number(layer.value), only.checked);
      layer.addEventListener('input', apply);
      only.addEventListener('change', apply);
      m.st = s.st;
      if (viewer.mode === 'view') {
        viewer.opts.onPick = (hit) => {
          if (!hit) return;
          const name = s.st.palette[hit.stateIndex] || '?';
          hud.innerHTML = `(${hit.cell.join(', ')}) ${esc(name)}`;
        };
      }
    } catch (e) {
      hud.textContent = '加载失败: ' + e.message;
    }
  }

  // ---------------------------------------------------------------- import
  function importDialog(files) {
    const pack = el('select');
    for (const p of state.packs) pack.append(el('option', { value: p.id }, p.name));
    const category = el('input', { value: 'imported' });
    const tags = el('input', { placeholder: 'tag: imported; facade（分号分隔）' });
    const desc = el('input', { placeholder: '统一描述（可选）' });
    const trim = el('input', { type: 'checkbox', checked: 'checked' });
    const list = el('div', { class: 'muted' },
      `待导入 ${files.length} 个文件：` + files.map((f) => f.name).join('、'));
    const body = el('div', {},
      list,
      formRow('资产包', pack),
      formRow('分类', category),
      formRow('标签', tags),
      formRow('描述', desc),
      formRow('裁空气', trim));
    modal('导入模块', body, [
      { label: '取消' },
      { label: '开始导入', class: 'primary', sticky: true,
        onClick: async () => {
          let ok = 0, fail = 0, skip = 0;
          for (const f of files) {
            const q = new URLSearchParams({
              filename: f.name, pack: pack.value, category: category.value,
              tags: tags.value, description: desc.value,
              trim: trim.checked ? '1' : '0',
            });
            try {
              const r = await fetch('/api/import/upload?' + q.toString(),
                { method: 'POST', body: f });
              const rep = await r.json();
              if (!r.ok) throw new Error(rep.error || r.statusText);
              ok += (rep.imported || []).length;
              skip += (rep.skipped || []).length;
              fail += (rep.failed || []).length;
            } catch (e) { fail += 1; }
          }
          toast(`导入完成：成功 ${ok} · 跳过 ${skip} · 失败 ${fail}`,
                fail ? 'warn' : 'ok');
          await refreshAll();
          return true;
        } },
    ]);
  }

  // ---------------------------------------------------------------- editor hook
  async function refreshOpenFiles() {
    // “打开结构”现在是可搜索下拉（服务端按 q 搜），列表不再预加载，交给编辑器初始化即可。
    if (global.Editor && global.Editor.refreshFiles) {
      try { await global.Editor.refreshFiles(); } catch (e) { /* ignore */ }
    }
  }

  // ---------------------------------------------------------------- events
  function bind() {
    document.querySelectorAll('.tab').forEach((t) => {
      t.addEventListener('click', () => {
        document.querySelectorAll('.tab').forEach((x) => x.classList.remove('active'));
        t.classList.add('active');
        document.querySelectorAll('.view').forEach((v) => v.classList.remove('active'));
        $('#view-' + t.dataset.view).classList.add('active');
        if (t.dataset.view === 'editor') global.Editor.onShow();
        if (t.dataset.view === 'settings' && global.Settings) global.Settings.onShow();
      });
    });
    $('#tag-search').addEventListener('input', renderTagList);
    document.querySelectorAll('input[name=tagmode]').forEach((r) => {
      r.addEventListener('change', () => {
        state.filters.tagMode = r.value;
        loadModules();
      });
    });
    $('#tag-clear').addEventListener('click', () => {
      state.filters.tags.clear();
      renderTagList();
      loadModules();
    });
    $('#tag-manage').addEventListener('click', tagManager);
    let timer = null;
    $('#search').addEventListener('input', (e) => {
      clearTimeout(timer);
      timer = setTimeout(() => { state.filters.q = e.target.value; loadModules(); }, 200);
    });
    $('#sort').addEventListener('change', (e) => {
      state.filters.sort = e.target.value; loadModules();
    });
    $('#untagged').addEventListener('change', (e) => {
      state.filters.untagged = e.target.checked; loadModules();
    });
    $('#btn-import').addEventListener('click', () => $('#import-input').click());
    $('#btn-render-missing').addEventListener('click', () => queueRender('missing'));
    $('#btn-render-all').addEventListener('click', () => queueRender('all'));
    const rbg = $('#render-bg');
    if (rbg) {
      rbg.value = loadBg();
      rbg.addEventListener('change', () => saveBg(rbg.value));
    }
    const vbg = $('#view-bg');
    if (vbg) vbg.addEventListener('change', () => {
      saveBg(vbg.value);
      if (global.Editor && global.Editor.setViewBackground) {
        global.Editor.setViewBackground(vbg.value);
      }
    });
    $('#import-input').addEventListener('change', (e) => {
      if (e.target.files.length) importDialog([...e.target.files]);
      e.target.value = '';
    });
    ['dragover', 'drop'].forEach((ev) => {
      document.addEventListener(ev, (e) => {
        if (!e.dataTransfer || !e.dataTransfer.types.includes('Files')) return;
        e.preventDefault();
        if (ev === 'drop' && e.dataTransfer.files.length) {
          importDialog([...e.dataTransfer.files]);
        }
      });
    });

    const batchTags = () => splitList($('#batch-tags').value);
    const afterBatch = async (fn) => {
      if (!state.selected.size) return;
      try {
        await fn([...state.selected]);
        toast('批量操作完成', 'ok');
        state.selected.clear();
        await refreshAll();
      } catch (e) { toast(e.message, 'err'); }
    };
    $('#batch-add').addEventListener('click', () => afterBatch((ids) =>
      post('/api/modules/batch', { ids, addTags: batchTags() })));
    $('#batch-remove').addEventListener('click', () => afterBatch((ids) =>
      post('/api/modules/batch', { ids, removeTags: batchTags() })));
    $('#batch-cat').addEventListener('click', () => {
      const cat = prompt('新分类名：');
      if (!cat) return;
      afterBatch((ids) => post('/api/modules/batch', { ids, category: cat }));
    });
    $('#batch-del').addEventListener('click', () => {
      if (!confirm(`删除选中的 ${state.selected.size} 个模块？`)) return;
      afterBatch((ids) => post('/api/modules/batch', { ids, delete: true }));
    });
    $('#batch-clear').addEventListener('click', () => {
      state.selected.clear();
      renderGrid();
    });
  }

  function tagManager() {
    const rows = state.tags.map((t) => el('tr', {},
      el('td', {}, t.name),
      el('td', {}, String(t.count)),
      el('td', {}, el('button', {
        class: 'link-btn',
        onclick: async () => {
          const nw = prompt(`把标签 ${t.name} 重命名为：`, t.name);
          if (!nw || nw === t.name) return;
          try {
            const r = await post('/api/tags/rename', { old: t.name, new: nw });
            toast(`重命名完成，影响 ${r.affected} 个模块`, 'ok');
            state.filters.tags.delete(t.name);
            state.filters.tags.add(nw);
            await refreshAll();
          } catch (e) { toast(e.message, 'err'); }
        },
      }, '重命名'),
        el('button', {
          class: 'link-btn',
          onclick: async () => {
            const dst = prompt(`把标签 ${t.name} 合并到：`);
            if (!dst) return;
            try {
              const r = await post('/api/tags/merge', { src: t.name, dst });
              toast(`合并完成，影响 ${r.affected} 个模块`, 'ok');
              state.filters.tags.delete(t.name);
              await refreshAll();
            } catch (e) { toast(e.message, 'err'); }
          },
        }, '合并'),
        el('button', {
          class: 'link-btn danger-text',
          onclick: async () => {
            if (!confirm(`从全部模块移除标签 ${t.name}？`)) return;
            try {
              const r = await post('/api/tags/delete', { tag: t.name });
              toast(`已移除，影响 ${r.affected} 个模块`, 'ok');
              state.filters.tags.delete(t.name);
              await refreshAll();
            } catch (e) { toast(e.message, 'err'); }
          },
        }, '删除'))));
    const table = el('table', { class: 'ports' },
      el('tr', {}, el('th', {}, '标签'), el('th', {}, '数量'), el('th', {}, '操作')),
      rows);
    modal('标签管理', table, [{ label: '关闭' }]);
  }

  // ---------------------------------------------------------------- boot
  async function init() {
    bind();
    try {
      await refreshAll();
      const params = new URLSearchParams(location.search);
      if (params.get('view') === 'editor') global.Editor.switchTab('editor');
      const openPath = params.get('open');
      if (openPath) await global.Editor.openPath(openPath);
      if (params.get('view') === 'editor') global.Editor.switchTab('editor');
    } catch (e) {
      toast('初始化失败: ' + e.message, 'err', 8000);
    }
  }

  global.App = {
    init, state, toast, modal, api, post, patch, viewStructure, openSession,
    searchSelect, propsEditor, parseProps, propsText, splitList,
    colorMaps, refreshAll, loadModules, refreshOpenFiles, waitJob,
    viewStructureFromSession,
  };

  // used by the editor: open the 3D modal from an already-open session
  async function viewStructureFromSession(st, voxels, colors, flags, ports, extraTextures) {
    const canvas = el('canvas');
    const hud = el('div', { class: 'kv' }, '加载中…');
    const holder = el('div', { class: 'viewer-modal' }, canvas, hud);
    const m = modal('3D 预览 — ' + st.name, holder, [{ label: '关闭' }],
                    { sticky: true, wide: true });
    const viewer = new McStudio3D.VoxelViewer(canvas);
    viewer.observeResize();
    const mode = await viewer.load(
      { size: st.size, palette: st.palette, version: st.version,
        colors, flags, extraTextures: extraTextures || [] }, voxels);
    viewer.setPorts(ports || []);
    viewer.fit();
    hud.textContent = `${st.size.join('×')} · ${mode === 'texture' ? '贴图' : '色彩'}模式`;
    return { viewer, modal: m };
  }
})(window);
