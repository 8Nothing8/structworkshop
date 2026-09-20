/* headless 浏览器审计脚本的共用件：找 Chrome、定输出目录、查可选数据。
 *
 * 为什么要有它：这些脚本原来把 **Chrome 的绝对路径**（`C:/Program Files/...`）和
 * **输出目录**（`C:/tmp/...`）写死在每一份文件里 —— 换台机器（macOS / Linux、
 * 或者 Chrome 装在别处）就跑不起来。这里收成一处：
 *
 *   const B = require('./_browser.js');
 *   const chrome = B.chromePath(process.argv[2]);   // argv > CHROME_PATH > 三平台常见位置 > PATH
 *   const outDir = B.shotsDir(process.argv[4], 'ab');  // 默认 <repo>/.cache/shots/ab（已 gitignore）
 *   const profile = B.profileDir('ab');              // 放系统临时目录，不污染仓库
 *   if (!B.hasData('packs/xxx')) { B.skip('需要一个资产包…'); }
 *
 * 用法（都是可选的，参数缺省时自动推断）：
 *   node tests/<script>.js [chrome] [baseUrl] [outDir]
 */
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');

/** 仓库根（本文件在 tests/ 下）。 */
const REPO = path.resolve(__dirname, '..');

/** 各平台常见安装位置（按可能性排序）。 */
const CHROME_CANDIDATES = {
  win32: [
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
    'C:/Program Files/Chromium/Application/chrome.exe',
    'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/BraveSoftware/Brave-Browser/Application/brave.exe',
  ],
  darwin: [
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
    '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
    '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser',
  ],
  linux: [
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    '/snap/bin/chromium',
    '/usr/bin/microsoft-edge',
    '/opt/google/chrome/chrome',
  ],
};

/** PATH 里找可执行文件（不引第三方依赖）。 */
function which(name) {
  const dirs = (process.env.PATH || '').split(path.delimiter).filter(Boolean);
  const exts = process.platform === 'win32'
    ? (process.env.PATHEXT || '.EXE;.CMD;.BAT').split(';') : [''];
  for (const d of dirs) {
    for (const ext of exts) {
      const p = path.join(d, name + ext);
      try { if (fs.statSync(p).isFile()) return p; } catch (e) { /* 继续找 */ }
    }
  }
  return null;
}

/**
 * 定位浏览器可执行文件。
 * 顺序：显式参数 > `CHROME_PATH` 环境变量 > 本平台常见位置 > PATH 里的常见名字。
 * @returns {string|null} 找到的绝对路径；都没有返回 null（交给调用方 skip）。
 */
function chromePath(explicit) {
  const tries = [explicit, process.env.CHROME_PATH,
    ...(CHROME_CANDIDATES[process.platform] || [])];
  for (const p of tries) {
    if (!p) continue;
    // 只在含路径分隔符时 stat：纯名字交给 which 处理（Windows 的 `chrome`）
    if (p.includes('/') || p.includes('\\')) {
      if (fs.existsSync(p)) return p;
    } else {
      const found = which(p);
      if (found) return found;
    }
  }
  for (const n of ['google-chrome', 'google-chrome-stable', 'chromium',
    'chromium-browser', 'chrome', 'msedge']) {
    const found = which(n);
    if (found) return found;
  }
  return null;
}

/** 取不到浏览器时的统一退出：退出码 2（= 跳过，不是失败）。 */
function requireChrome(explicit, scriptName) {
  const p = chromePath(explicit);
  if (p) return p;
  console.error(
    `[skip] 没找到 Chrome/Chromium/Edge，跳过 ${scriptName}。\n` +
    '       指定方式（任选其一）：\n' +
    `         node tests/${scriptName} "<chrome 可执行文件>"\n` +
    '         设环境变量 CHROME_PATH');
  process.exit(2);
}

/**
 * 这些审计都要先起本地工作台。连不上就直接跳过（退出码 2），
 * 而不是连上 Chrome 之后卡在页面导航里等超时。
 *
 * @returns {Promise<void>} 连不上时不会返回（process.exit(2)）
 */
async function requireServer(base, scriptName) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 4000);
  try {
    const r = await fetch(base, { signal: ctl.signal });
    clearTimeout(timer);
    if (r.ok || r.status < 500) return;
    throw new Error('HTTP ' + r.status);
  } catch (e) {
    clearTimeout(timer);
    console.error(
      `[skip] 连不上工作台 ${base}（${e.message}）。\n` +
      '       先起服务再跑这个脚本：\n' +
      '         python -m mcstudio serve --port 8617\n' +
      `       （tests/${scriptName}）`);
    process.exit(2);
  }
}

/**
 * 截图/审计产物目录。默认 `<repo>/.cache/shots/<name>`（`.gitignore` 已忽略 `.cache/`），
 * 这样在别人的机器上也不会往 `C:/tmp` 里乱塞东西。
 */
function shotsDir(explicit, name) {
  const d = explicit || path.join(REPO, '.cache', 'shots', name || 'shots');
  fs.mkdirSync(d, { recursive: true });
  return path.resolve(d);
}

/** 浏览器 user-data-dir：放系统临时目录，避免污染仓库 / 撞已有 profile。 */
function profileDir(name) {
  const d = path.join(os.tmpdir(), 'structworkshop-chrome-' + (name || 'audit'));
  fs.mkdirSync(d, { recursive: true });
  return d;
}

/** 仓库里有没有这份数据（`packs/` / `builds/` 等可选内容不在代码仓库里）。 */
function hasData(rel) {
  return fs.existsSync(path.join(REPO, rel));
}

/** `packs/` 里有没有至少一个真正的资产包（有 pack.json 的目录）。 */
function hasPacks() {
  const d = path.join(REPO, 'packs');
  try {
    return fs.readdirSync(d).some(
      (n) => fs.existsSync(path.join(d, n, 'pack.json')));
  } catch (e) {
    return false;
  }
}

/** 模块库 / 抽屉 / 接口表单这类断言需要真实资产包，没有就跳过。
 *
 * @param {string} scriptName
 * @param {string} [detail] 这个脚本到底要哪些包（不传就给个通用说法）
 */
function requirePacks(scriptName, detail) {
  if (hasPacks()) return;
  skip('`packs/` 里没有任何资产包（本地内容，代码仓库不附带）—— '
       + (detail || '模块库/抽屉/接口相关的断言跑不了。\n'
                  + '       建一个： python -m mccore.pack create demo'),
       scriptName);
}

/**
 * 需要「可选数据」的审计脚本的统一跳过口。
 * `packs/` `builds/` `kb/` 是可选的本地内容，代码仓库里没有 —— 缺了就该跳过而不是报错。
 */
function skip(reason, scriptName) {
  console.error(`[skip] ${reason}\n` +
    `       这个脚本需要仓库里的可选数据，纯代码 checkout 里没有。\n` +
    (scriptName ? `       （tests/${scriptName}）` : ''));
  process.exit(2);
}

/** 缺数据就跳过：把一组相对路径全部检查一遍。 */
function requireData(paths, scriptName) {
  const missing = [].concat(paths).filter((p) => !hasData(p));
  if (missing.length) {
    skip(`缺这些数据：${missing.join(', ')}`, scriptName);
  }
}

module.exports = {
  REPO,
  hasData,
  hasPacks,
  requirePacks,
  requireData,
  skip,
  which,
  chromePath,
  requireChrome,
  requireServer,
  shotsDir,
  profileDir,
};
