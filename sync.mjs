#!/usr/bin/env node
/**
 * dsh-ocgo-sync — 让 DSH（CLI 运行时 / DSH Desktop）快速同步 OpenCode Go 模型清单。
 *
 * 背景：DSH 的模型清单不是配置项，而是 pi-ai 包内的一份数据文件
 *   @earendil-works/pi-ai/dist/providers/data/opencode-go.json
 *   - CLI 运行时（~/.dsh-runtime）每次启动会用自带脚本刷新它；
 *   - DSH Desktop 自带的这份从出厂起就不再更新，所以新模型
 *     （例如 deepseek-v4.1-flash）在桌面版模型选择器里永远不出现。
 *
 * 本工具会找到机器上所有这份文件（跨 Junction/Symlink 自动去重），
 * 用「网关模型列表 + models.dev 元数据」重建并就地替换，覆盖前备份、
 * 覆盖后校验，并保留既有描述符中的 compat / thinkingLevelMap 等字段。
 *
 * 零依赖，Node >= 20。
 */
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const VERSION = '1.0.1';
const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, '..');

const PROVIDER = 'opencode-go';
const PROTOCOLS = ['anthropic-messages', 'openai-completions', 'openai-responses'];
const NPM_OPENAI = '@ai-sdk/openai';
const NPM_ANTHROPIC = '@ai-sdk/anthropic';
const NPM_COMPATIBLE = '@ai-sdk/openai-compatible';
const PROTOCOL_BY_NPM = {
  [NPM_OPENAI]: 'openai-responses',
  [NPM_ANTHROPIC]: 'anthropic-messages',
  [NPM_COMPATIBLE]: 'openai-completions',
};
const GATEWAY_MODELS_URL = 'https://opencode.ai/zen/go/v1/models';
const MODELS_DEV_URL = 'https://models.dev/api.json';
const DEFAULT_SNAPSHOT = path.join(REPO_ROOT, 'catalog', 'opencode-go.json');
const DEFAULT_SNAPSHOT_URL = 'https://api.github.com/repos/M1012-w0917/dsh-ocgo-sync/contents/catalog/opencode-go.json';

// 网关 id 与 models.dev 元数据 id 不一致时的兜底映射
const FALLBACK_META_ID = { 'deepseek-flash': 'deepseek-v4.1-flash', 'hy3-preview': 'hy3' };

const MAX_DEPTH = 12;
const PRUNE_DIRS = new Set([
  '.git', '.cache', 'cache', 'Cache', 'Code Cache', 'GPUCache', 'DawnGraphiteCache',
  'DawnWebGPUCache', 'blob_storage', 'Local Storage', 'Network', 'Dictionaries',
  'Shared Dictionary', 'logs', 'sessions', 'storages', '.generations', 'trash', 'temp', 'tmp',
]);
// ---------------------------------------------------------------- 命令行解析

function parseArgs(argv) {
  const o = {
    roots: [], dryRun: false, offline: false, prune: false, list: false,
    snapshot: null, from: null, emit: null, yes: false, help: false, version: false,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    const val = (name) => {
      const v = argv[i + 1];
      if (v === undefined || v.startsWith('--')) throw new Error(name + ' 需要一个参数');
      i += 1;
      return v;
    };
    if (a === '-h' || a === '--help') o.help = true;
    else if (a === '-v' || a === '--version') o.version = true;
    else if (a === '--dry-run') o.dryRun = true;
    else if (a === '--offline') o.offline = true;
    else if (a === '--prune') o.prune = true;
    else if (a === '--list') o.list = true;
    else if (a === '-y' || a === '--yes') o.yes = true;
    else if (a === '--snapshot') o.snapshot = val(a);
    else if (a === '--from') o.from = val(a);
    else if (a === '--emit') o.emit = val(a);
    else if (a === '--root') o.roots.push(val(a));
    else if (a.startsWith('--snapshot=')) o.snapshot = a.slice(11);
    else if (a.startsWith('--from=')) o.from = a.slice(7);
    else if (a.startsWith('--emit=')) o.emit = a.slice(7);
    else if (a.startsWith('--root=')) o.roots.push(a.slice(7));
    else throw new Error('未知参数: ' + a);
  }
  return o;
}

function printHelp() {
  const lines = [
    '',
    'dsh-ocgo-sync v' + VERSION,
    '让 DSH（CLI 运行时 / DSH Desktop）同步 OpenCode Go 模型清单',
    '',
    '用法:',
    '  npx dsh-ocgo-sync [选项]',
    '',
    '选项:',
    '  --list              只列出找到的目录文件与模型数，不做修改',
    '  --dry-run           显示将要发生的变更，但不写盘',
    '  --offline           不联网，改用仓库内的 catalog 快照',
    '  --snapshot <file>   指定快照文件（配合 --offline）',
    '  --from <url>        指定快照 URL',
    '  --prune             删除网关已下线且快照里也没有的模型（默认保留）',
    '  --emit <file>       只生成快照文件（供仓库 catalog/ 使用）',
    '  --root <dir>        追加搜索根目录（可重复）',
    '  -y, --yes           非交互',
    '  -h, --help          显示帮助',
    '  -v, --version       显示版本',
    '',
    '示例:',
    '  npx dsh-ocgo-sync --list',
    '  npx dsh-ocgo-sync --dry-run',
    '  npx dsh-ocgo-sync',
    '  npx dsh-ocgo-sync --offline',
    '',
  ];
  console.log(lines.join('\n'));
}
// ---------------------------------------------------------------- 目标发现

function defaultRoots() {
  const home = os.homedir();
  const roots = [path.join(home, '.dsh-runtime')];
  if (process.platform === 'win32') {
    if (process.env.LOCALAPPDATA) roots.push(path.join(process.env.LOCALAPPDATA, 'Programs'));
    if (process.env.APPDATA) roots.push(path.join(process.env.APPDATA, 'dsh-desktop'));
  } else if (process.platform === 'darwin') {
    roots.push('/Applications/DSH Desktop.app');
    roots.push(path.join(home, 'Library', 'Application Support', 'dsh-desktop'));
  } else {
    roots.push(path.join(home, '.config', 'dsh-desktop'));
    roots.push('/opt/DSH Desktop');
  }
  return roots.filter((dir) => fs.existsSync(dir));
}

function looksLikeCatalogPath(file) {
  const segs = file.split(/[\\/]/).map((s) => s.toLowerCase());
  const i = segs.lastIndexOf('pi-ai');
  return i >= 0
    && segs[i + 1] === 'dist'
    && segs[i + 2] === 'providers'
    && segs[i + 3] === 'data'
    && segs[segs.length - 1] === 'opencode-go.json';
}

function walk(root, out, depth) {
  const level = depth || 0;
  if (level > MAX_DEPTH) return;
  let entries;
  try { entries = fs.readdirSync(root, { withFileTypes: true }); } catch { return; }
  for (const e of entries) {
    const child = path.join(root, e.name);
    if (e.isDirectory()) {
      if (PRUNE_DIRS.has(e.name)) continue;
      // pnpm store 里只关心 pi-ai，避免遍历上千个无关包
      if (path.basename(root) === '.pnpm' && !e.name.startsWith('@earendil-works+pi-ai')) continue;
      walk(child, out, level + 1);
    } else if (e.isFile() && e.name === 'opencode-go.json' && looksLikeCatalogPath(child)) {
      out.push(child);
    }
  }
}

/** 找到所有目录文件，并按真实物理路径去重（Junction / Symlink 会折叠成一个） */
function discoverTargets(roots) {
  const hits = [];
  for (const r of roots) walk(r, hits, 0);
  const uniq = new Map();
  for (const f of hits) {
    let real = f;
    try { real = fs.realpathSync.native(f); } catch { /* 保留原路径 */ }
    const key = process.platform === 'win32' ? real.toLowerCase() : real;
    if (!uniq.has(key)) uniq.set(key, { file: real, refs: [] });
    uniq.get(key).refs.push(f);
  }
  return Array.from(uniq.values());
}

// ---------------------------------------------------------------- 目录文件读写

function readCatalog(file) {
  const raw = fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '');
  return JSON.parse(raw);
}

function catalogIds(cat) {
  const ids = [];
  for (const p of PROTOCOLS) {
    if (cat && cat[p] && typeof cat[p] === 'object') ids.push(...Object.keys(cat[p]));
  }
  return Array.from(new Set(ids));
}

function descriptorIndex(cat) {
  const idx = new Map();
  for (const p of PROTOCOLS) {
    if (!cat || !cat[p]) continue;
    for (const [id, d] of Object.entries(cat[p])) idx.set(id, { protocol: p, descriptor: d });
  }
  return idx;
}

function emptyCatalogLike(base) {
  const out = {};
  for (const k of Object.keys(base || {})) if (PROTOCOLS.indexOf(k) < 0) out[k] = base[k];
  for (const p of PROTOCOLS) out[p] = {};
  return out;
}
// ---------------------------------------------------------------- 生成逻辑

function num(v) { return typeof v === 'number' && Number.isFinite(v) ? v : 0; }

/** 由 models.dev 的 reasoning_options 推导 pi-ai 的 thinkingLevelMap */
function thinkingLevelMapFrom(meta) {
  const opts = (meta && meta.reasoning_options) || [];
  const found = opts.find((o) => o && o.type === 'effort');
  const values = found && found.values;
  if (!Array.isArray(values) || values.length === 0) return null;
  const map = { minimal: null, low: null, medium: null, high: null, max: null };
  for (const v of values) if (Object.prototype.hasOwnProperty.call(map, v)) map[v] = v;
  return map;
}

/** 为新模型挑一个同族老模型，用来继承 compat 等字段（按最长公共前缀） */
function pickSibling(id, idx) {
  let best = null;
  let bestLen = 0;
  for (const [otherId, entry] of idx) {
    if (!entry.descriptor || !entry.descriptor.compat) continue;
    const n = Math.min(otherId.length, id.length);
    let k = 0;
    while (k < n && otherId[k] === id[k]) k += 1;
    if (k > bestLen) { bestLen = k; best = entry.descriptor; }
  }
  return bestLen >= 4 ? best : null;
}

/** 用「网关模型列表 + models.dev 元数据」重建目录 */
function buildFromLive(base, opts) {
  const ids = opts.ids;
  const meta = opts.meta || {};
  const providerMeta = opts.providerMeta;
  const prune = !!opts.prune;
  const baseIdx = descriptorIndex(base);
  const out = emptyCatalogLike(base);
  const firstEntry = baseIdx.values().next();
  const fallbackBaseUrl = firstEntry.done ? 'https://opencode.ai/zen/go/v1' : firstEntry.value.descriptor.baseUrl;
  const baseUrl = (providerMeta && providerMeta.api) || fallbackBaseUrl || 'https://opencode.ai/zen/go/v1';
  const providerNpm = (providerMeta && providerMeta.npm) || NPM_COMPATIBLE;
  const added = [];
  const changed = [];

  for (const id of ids) {
    const cur = baseIdx.get(id);
    const m = meta[id] || meta[FALLBACK_META_ID[id]] || null;
    const npm = (m && m.provider && m.provider.npm) || providerNpm;
    const api = PROTOCOL_BY_NPM[npm] || (cur && cur.protocol) || 'openai-completions';
    let d;
    if (cur && cur.protocol === api) {
      d = Object.assign({}, cur.descriptor);
    } else {
      const sibling = pickSibling(id, baseIdx);
      d = {
        id,
        name: (m && m.name) || id,
        api,
        provider: PROVIDER,
        baseUrl,
        reasoning: !!(m && m.reasoning),
        input: ['text'],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 1000000,
        maxTokens: 128000,
      };
      if (sibling && sibling.compat) d.compat = Object.assign({}, sibling.compat);
      if (sibling && sibling.thinkingLevelMap) d.thinkingLevelMap = Object.assign({}, sibling.thinkingLevelMap);
    }

    if (m) {
      if (m.name) d.name = m.name;
      if (typeof m.reasoning === 'boolean') d.reasoning = m.reasoning;
      const modalities = m.modalities && m.modalities.input;
      if (Array.isArray(modalities)) {
        const input = ['text'];
        if (m.attachment && modalities.indexOf('image') >= 0) input.push('image');
        d.input = input;
      }
      if (m.cost) {
        d.cost = {
          input: num(m.cost.input),
          output: num(m.cost.output),
          cacheRead: num(m.cost.cache_read),
          cacheWrite: num(m.cost.cache_write),
        };
      }
      if (m.limit && m.limit.context) d.contextWindow = m.limit.context;
      if (m.limit && m.limit.output) d.maxTokens = m.limit.output;
      const tlm = thinkingLevelMapFrom(m);
      if (tlm && !d.thinkingLevelMap) d.thinkingLevelMap = tlm;
    }

    d.id = id;
    d.api = api;
    d.provider = PROVIDER;
    d.baseUrl = baseUrl;

    if (!cur) added.push(id);
    else if (JSON.stringify(cur.descriptor) !== JSON.stringify(d)) changed.push(id);
    out[api][id] = d;
  }

  const removed = [];
  const keep = new Set(ids);
  for (const [id, entry] of baseIdx) {
    if (keep.has(id)) continue;
    if (prune) { removed.push(id); continue; }
    out[entry.protocol][id] = entry.descriptor;
  }
  return { catalog: out, added, changed, removed };
}

/** 离线模式：以快照为准套用，保留目标已有的 compat / thinkingLevelMap */
function buildFromSnapshot(base, snapshot, prune) {
  const baseIdx = descriptorIndex(base);
  const snapIdx = descriptorIndex(snapshot);
  const out = emptyCatalogLike(base);
  const added = [];
  const changed = [];

  for (const [id, entry] of snapIdx) {
    const cur = baseIdx.get(id);
    if (cur && cur.protocol === entry.protocol) {
      const merged = Object.assign({}, entry.descriptor);
      if (cur.descriptor.compat) merged.compat = cur.descriptor.compat;
      if (cur.descriptor.thinkingLevelMap && !merged.thinkingLevelMap) {
        merged.thinkingLevelMap = cur.descriptor.thinkingLevelMap;
      }
      if (JSON.stringify(cur.descriptor) !== JSON.stringify(merged)) changed.push(id);
      out[entry.protocol][id] = merged;
    } else {
      out[entry.protocol][id] = Object.assign({}, entry.descriptor);
      added.push(id);
    }
  }

  const removed = [];
  for (const [id, entry] of baseIdx) {
    if (snapIdx.has(id)) continue;
    if (prune) { removed.push(id); continue; }
    out[entry.protocol][id] = entry.descriptor;
  }
  return { catalog: out, added, changed, removed };
}
// ---------------------------------------------------------------- 远端取数

async function fetchJson(url, timeoutMs, label) {
  // GitHub contents API 默认返回 base64 包装，必须显式要 raw
  const isGithubApi = url.indexOf('api.github.com') >= 0;
  const res = await fetch(url, {
    headers: {
      'user-agent': 'dsh-ocgo-sync/' + VERSION,
      accept: isGithubApi ? 'application/vnd.github.raw' : 'application/json',
    },
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!res.ok) throw new Error(label + ' 请求失败: HTTP ' + res.status);
  return res.json();
}

async function loadLive() {
  const pair = await Promise.all([
    fetchJson(GATEWAY_MODELS_URL, 30000, 'opencode 网关'),
    fetchJson(MODELS_DEV_URL, 60000, 'models.dev'),
  ]);
  const gateway = pair[0];
  const modelsDev = pair[1];
  const providerMeta = (modelsDev && modelsDev[PROVIDER]) || null;
  const ids = ((gateway && gateway.data) || []).map((m) => m && m.id).filter(Boolean);
  if (ids.length === 0) throw new Error('网关没有返回任何模型，已中止（避免把清单清空）');
  return { kind: 'live', ids, meta: (providerMeta && providerMeta.models) || {}, providerMeta };
}

async function loadSnapshot(opts) {
  if (opts.snapshot) return readCatalog(opts.snapshot);
  if (opts.from) return await fetchJson(opts.from, 30000, '快照');
  if (fs.existsSync(DEFAULT_SNAPSHOT)) return readCatalog(DEFAULT_SNAPSHOT);
  throw new Error('离线模式不会访问网络，但本地没有可用快照。请用 --snapshot <file> 指定快照，或使用包含 catalog/ 的 npm/仓库版本');
}

// ---------------------------------------------------------------- 写盘

function stamp() {
  const d = new Date();
  const p2 = (n) => String(n).padStart(2, '0');
  return '' + d.getFullYear() + p2(d.getMonth() + 1) + p2(d.getDate()) + '-' + p2(d.getHours()) + p2(d.getMinutes()) + p2(d.getSeconds());
}

async function writeCatalog(target, catalog, dryRun) {
  const text = JSON.stringify(catalog, null, 2) + '\n';
  if (dryRun) return { bytes: Buffer.byteLength(text), backup: null };
  const backup = target + '.bak-' + stamp();
  await fsp.copyFile(target, backup);

  try {
    await fsp.writeFile(target, text, 'utf8');
    const check = readCatalog(target);
    if (JSON.stringify(check) !== JSON.stringify(catalog)) {
      throw new Error('写入后的目录内容与预期不一致');
    }
    return { bytes: Buffer.byteLength(text), backup };
  } catch (e) {
    let restored = false;
    try {
      await fsp.copyFile(backup, target);
      restored = true;
    } catch { /* 保留原始错误；备份仍在 */ }
    throw new Error('写入校验失败: ' + e.message + (restored ? '；已自动恢复备份' : '；请从备份恢复: ' + backup));
  }
}

// ---------------------------------------------------------------- 主流程

function describeTargets(targets) {
  for (const t of targets) {
    let count = '?';
    try { count = catalogIds(readCatalog(t.file)).length; } catch (e) { count = '解析失败'; }
    const extra = t.refs.length > 1 ? '（' + t.refs.length + ' 个路径指向它，已按物理文件去重）' : '';
    console.log('  - ' + t.file);
    console.log('      当前模型数: ' + count + extra);
  }
}

async function main() {
  let opts;
  try { opts = parseArgs(process.argv.slice(2)); }
  catch (e) { console.error('参数错误: ' + e.message); process.exitCode = 2; return; }
  if (opts.help) { printHelp(); return; }
  if (opts.version) { console.log(VERSION); return; }

  const roots = defaultRoots().concat(opts.roots);
  const targets = discoverTargets(roots);

  if (opts.emit) {
    // 以现有目录为基表：优先 emit 目标文件，其次仓库快照，最后机器上找到的目标。
    // 这样快照才能带上 compat 等字段，以及网关已下线但仍在用的历史模型。
    let base = {};
    if (fs.existsSync(opts.emit)) base = readCatalog(opts.emit);
    else if (fs.existsSync(DEFAULT_SNAPSHOT)) base = readCatalog(DEFAULT_SNAPSHOT);
    else if (targets.length > 0) {
      try { base = readCatalog(targets[0].file); } catch (e) { base = {}; }
    }
    const live = await loadLive();
    console.log('网关 ' + live.ids.length + ' 个模型，models.dev 元数据 ' + Object.keys(live.meta).length + ' 条');
    const built = buildFromLive(base, Object.assign({}, live, { prune: opts.prune }));
    await fsp.writeFile(opts.emit, JSON.stringify(built.catalog, null, 2) + '\n', 'utf8');
    console.log('已生成快照 ' + opts.emit + '：' + catalogIds(built.catalog).length + ' 个模型');
    if (built.added.length) console.log('  新增: ' + built.added.sort().join(', '));
    if (built.removed.length) console.log('  移除: ' + built.removed.sort().join(', '));
    return;
  }

  if (targets.length === 0) {
    console.log('没有找到 DSH 的 OpenCode Go 模型目录文件。');
    console.log('搜索根目录: ' + roots.join(', '));
    console.log('如果装在别的位置，用 --root <dir> 指定。');
    return;
  }

  console.log('发现 ' + targets.length + ' 个目录文件:');
  describeTargets(targets);
  if (opts.list) return;

  const source = opts.offline
    ? Object.assign({ kind: 'offline' }, { snapshot: await loadSnapshot(opts) })
    : await loadLive();
  console.log(source.kind === 'offline'
    ? '离线模式: 快照里 ' + catalogIds(source.snapshot).length + ' 个模型'
    : '在线模式: 网关 ' + source.ids.length + ' 个模型，元数据 ' + Object.keys(source.meta).length + ' 条');

  let wrote = 0;
  for (const t of targets) {
    console.log('');
    console.log('> ' + t.file);
    let base = {};
    try { base = readCatalog(t.file); } catch (e) { console.log('  跳过: JSON 解析失败'); continue; }
    const built = source.kind === 'offline'
      ? buildFromSnapshot(base, source.snapshot, opts.prune)
      : buildFromLive(base, Object.assign({}, source, { prune: opts.prune }));

    console.log('  ' + catalogIds(base).length + ' -> ' + catalogIds(built.catalog).length + ' 个模型');
    if (built.added.length) console.log('  新增: ' + built.added.sort().join(', '));
    if (built.changed.length) console.log('  更新: ' + built.changed.sort().join(', '));
    if (built.removed.length) console.log('  移除: ' + built.removed.sort().join(', '));
    if (opts.dryRun) { console.log('  [dry-run] 未写入'); continue; }

    try {
      const w = await writeCatalog(t.file, built.catalog, false);
      console.log('  已写入 ' + (w.bytes / 1024).toFixed(1) + ' KB，备份 ' + path.basename(w.backup));
      wrote += 1;
    } catch (e) {
      console.log('  写入失败: ' + e.message);
      console.log('  提示: macOS 装在 /Applications 时可能需要 sudo；Windows 请先完全退出 DSH Desktop。');
      process.exitCode = 1;
    }
  }

  if (wrote > 0) {
    console.log('');
    console.log('完成。请重启对应的 DSH 让新清单生效:');
    console.log('  DSH Desktop: 完全退出后重新打开（并确认没有残留的 harness 进程）');
    console.log('  CLI 运行时 : 重新运行启动脚本即可');
  }
}

main().catch((e) => {
  console.error('失败: ' + (e && e.message ? e.message : e));
  process.exitCode = 1;
});