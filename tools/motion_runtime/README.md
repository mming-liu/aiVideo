# tools/motion_runtime — 动效渲染的隔离 Node 工作目录

本目录是 A 包（动效协议、模板与修订，计划 §A1/§A5/§15）的**运行底座**：
固定 HTML/SVG 模板 + Playwright 定时 seek 截图 + FFmpeg 编码。它**不是** Python 依赖，
宿主不 import 任何 Node 包；Python 侧只通过 `subprocess` 参数列表调用 `shoot.mjs`。

## 目录内容

| 文件 | 说明 |
|---|---|
| `package.json` / `package-lock.json` | 依赖锁：`playwright@1.56.0`（含 `integrity` 摘要，`lockfileVersion: 3`） |
| `shoot.mjs` | 取帧驱动：加载本地 HTML → 逐 t 调 `window.__motion_seek(t)` → `omitBackground` 截图 |
| `templates/spec.html` | 原创模板骨架（画布/字体/透明背景由 Python 构建时注入） |
| `templates/motion.js` | 原创 seek 引擎：全部动画是 `seek(t)` 的纯时间函数，无计时器/随机/联网 |
| `LICENSES.md` | 第三方组件与字体许可清单 |

`node_modules/` 与 `.browsers/` 是**运行时目录**，不进版本库（本仓库不提交、不打包）。

## 本机现状（2026-10-03 实测）

```
node      : v24.18.0（C:\Program Files\nodejs\node.exe）
playwright: 未安装（<本目录>/node_modules/playwright 不存在）
chromium  : 未安装（<本目录>/.browsers/ 不存在，%LOCALAPPDATA%\ms-playwright 亦不存在）
ffmpeg    : 8.1.2-full_build-www.gyan.dev（PATH 可见）
```

因此 `shared/motion_render.render.runtime_status()` 当前返回
`available=false, missing=["playwright_module","chromium_browser"]`，
所有渲染调用**如实记 `not_run`/`unavailable`**，不伪造成功、不自动下载。

## 安装（需另行授权；本仓库不代跑）

安装位置固定在本目录 + 本目录 `.browsers/`，**不污染宿主全局 Node / 浏览器缓存**：

```powershell
cd tools/motion_runtime
npm ci --ignore-scripts
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.browsers"
npx playwright install chromium
```

Python 侧通过两个环境变量指向隔离位置（也有默认值，见 `render.py`）：

| 变量 | 默认 | 作用 |
|---|---|---|
| `MOTION_PLAYWRIGHT_BROWSERS_PATH` | `tools/motion_runtime/.browsers` | Chromium 安装位置 |
| `MOTION_NODE_BIN` | PATH 上的 `node` | Node 可执行文件 |
| `MOTION_TEMPLATE_DIR` | `tools/motion_runtime/templates` | 模板目录覆盖 |

## 下载来源与体积（**元数据核实，未下载**）

下表数字于 2026-10-03 通过 npm registry 元数据与 CDN `HEAD`（Content-Length）核实，
不是估算，也**不代表安装后的磁盘占用**（解压、Chromium 增量更新目录会另占空间）：

| 组件 | 版本/构建 | 大小 | 来源 |
|---|---|---|---|
| `playwright` (npm, unpacked) | 1.56.0 | 3,876,497 B（≈3.7 MiB） | `https://registry.npmjs.org/playwright/-/playwright-1.56.0.tgz` |
| `playwright-core` (npm, unpacked) | 1.56.0 | 8,228,434 B（≈7.8 MiB） | `https://registry.npmjs.org/playwright-core/-/playwright-core-1.56.0.tgz` |
| Chromium (win64 zip) | build 1194 / Chrome 141.0.7390.37 | 156,153,766 B（≈148.9 MiB） | `https://cdn.playwright.dev/dbazure/download/playwright/builds/chromium/1194/chromium-win64.zip`（307 → `playwright.download.prss.microsoft.com`） |

`npx playwright install chromium` 默认还会取 `chromium-headless-shell`（同 revision 1194）；
其分发包大小本次未核实（该路径 HEAD 返回 400），安装时应以实际下载量为准并回填此处。

结论：**新增下载量约 160 MB 量级**（压缩包口径），落地后更大；是否安装由用户按
授权与费用边界决定，代码侧不代跑、不联网。

## 判定与失败语义

- `runtime_status()` 只**探测**：`node` 可执行 → `node_modules/playwright/package.json`
  → `.browsers/chromium*/chrome-win*/chrome.exe`；缺任一项即 `available=false` 并列出缺项。
- 渲染函数在缺底座时返回 `status="unavailable"`（`not_run`），manifest 的 `checks.render`
  记 `not_run`；消费者必须用 `required_checks` 声明自己要的是真渲染结果。
- 浏览器侧做**权威测量**：行宽是否放得下、浏览器看到的 code point 数是否与 Python
  声明一致；两者任一不通过即**拒收**（`layout_overflow` / `cp_len_mismatch`），
  驱动把 `result.json` 的 `ok=false` 交回 Python，不静默通过。
- 页面若发起任何非 `file:` / 非 `data:` / 非 `assets/` 的请求，驱动直接判失败
  （`external_request_blocked`）——禁止联网加载素材/字体。

## 与主项目的关系

- 模板与驱动是**原创**（本仓库自有），不引入 HyperFrames SDK 或其它渲染框架；
- `shared/media_contract.MERGE_FPS=24` 是唯一帧率来源，模板不写死第二份帧率；
- 帧数量化只走 `shared.media_contract.quantize_frame_count`；
- 透明产物是 RGBA PNG 全画幅序列，MP4 只是单片展示件（alpha 直通合成到显式背景）。
