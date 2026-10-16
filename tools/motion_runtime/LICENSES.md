# 第三方组件与许可清单（A 包动效渲染）

本目录的 **HTML/SVG 模板与 `shoot.mjs`、`templates/motion.js` 均为本仓库原创**，
不含第三方代码、示例或素材。运行时用到的第三方组件如下（均未在本轮安装）：

| 组件 | 版本 | 许可 | 说明 |
|---|---|---|---|
| Playwright (npm) | 1.56.0 | Apache-2.0 | 见 `node_modules/playwright/LICENSE`；锁在 `package-lock.json`（含 integrity） |
| Playwright core | 1.56.0 | Apache-2.0 | 同上 |
| Chromium | build 1194 / 141.0.7390.37 | BSD-3-Clause + 第三方组件（详见分发内 `LICENSE` 与 `credits`） | 由 `playwright install chromium` 下载到 `.browsers/` |
| Noto Sans SC / Noto Serif SC（可变字体，系统副本） | Windows 自带 `C:\Windows\Fonts\NotoSansSC-VF.ttf` / `NotoSerifSC-VF.ttf` | SIL Open Font License 1.1（OFL-1.1） | **仅作测试/样片素材只读使用**，摘要写进 fixture 的 `assets` |

## 未完成项（本轮如实记录）

- **仓库内没有可分发字体**：样片与测试用的是 Windows 系统里的 Noto 字体副本（OFL），
  只在本地隔离 fixture 里复制 + 记摘要，**未纳入本仓库**，也未核对再分发条款。
  正式交付前需要**冻结一份许可清楚、可分发的中文字体**并把它登记进 `assets`
  （`path/sha256/license_source` 三项都由代码重算校验）。
- Chromium 分发包许可随版本变化，安装后应把 `LICENSE` / `credits` 一并保留在
  `.browsers/` 内，不在本清单里逐条复述。
