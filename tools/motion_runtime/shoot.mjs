/**
 * shoot.mjs —— aiVideo 动效取帧驱动（原创，本仓库自有）。
 *
 * 契约（计划 §A1）：
 *   * 只做三件事：打开**本地文件** HTML → 逐个 t 调 `window.__motion_seek(t)` → 截图；
 *   * 页面/驱动都不联网：字体等素材通过 `page.route` 从**冻结的本地文件**兑现；
 *   * 截图固定 `omitBackground: true`（RGBA 透明、全画幅），帧号即请求顺序；
 *   * 取帧顺序即请求顺序——**支持乱序 seek**（同一 t 的 PNG 必须逐字节一致）；
 *   * 浏览器侧做**权威测量**（版式溢出 / code point 数），拒收时 result.ok=false，
 *     由 Python 侧转成结构化问题，不在这里吞掉。
 *
 * 输入：--plan <plan.json>
 *   { html, out_dir, width, height, fps, mode, browsers_path, assets{url->abs}, times[] }
 * 输出：<out_dir>/result.json
 *   { ok, frames[], measure{}, error? }
 *
 * 退出码：0 正常（ok=true/false 都可能）；非 0 表示驱动自身失败（result.json 仍尽量写出）。
 */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const require_ = createRequire(import.meta.url);

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--plan") { out.plan = argv[++i]; }
  }
  return out;
}

/** Chromium 启动参数：确定性渲染（颜色配置/字体栅格/合成阶段）。 */
const DETERMINISTIC_ARGS = [
  "--force-color-profile=srgb",
  "--font-render-hinting=none",
  "--disable-font-subpixel-positioning",
  "--disable-lcd-text",
  "--hide-scrollbars",
  "--deterministic-mode",
  "--disable-threaded-animation",
  "--disable-checker-imaging",
];

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args.plan) {
    console.error("usage: node shoot.mjs --plan plan.json");
    return 2;
  }
  const plan = JSON.parse(fs.readFileSync(args.plan, "utf8"));
  const outDir = plan.out_dir;
  fs.mkdirSync(outDir, { recursive: true });
  const resultPath = path.join(outDir, "result.json");
  const fail = (code, error, measure) => {
    fs.writeFileSync(resultPath, JSON.stringify({ ok: false, error: code, detail: String(error), measure: measure || {} }));
  };
  if (plan.browsers_path) { process.env.PLAYWRIGHT_BROWSERS_PATH = plan.browsers_path; }

  let chromium;
  try {
    ({ chromium } = require_("playwright"));
  } catch (err) {
    fail("playwright_module_missing", err && err.message);
    return 3;
  }

  let browser = null;
  try {
    browser = await chromium.launch({ args: DETERMINISTIC_ARGS });
  } catch (err) {
    fail("chromium_launch_failed", err && err.message);
    return 3;
  }
  const frames = [];
  try {
    const context = await browser.newContext({
      viewport: { width: plan.width, height: plan.height },
      deviceScaleFactor: 1,
      colorScheme: "light",
      reducedMotion: "reduce",
    });
    const page = await context.newPage();
    // 素材只从冻结的本地文件兑现（页面自身不联网）
    await page.route("**/assets/**", (route) => {
      const url = route.request().url();
      const rel = decodeURIComponent(url.split("/assets/").pop() || "");
      const abs = (plan.assets || {})["assets/" + rel];
      if (!abs || !fs.existsSync(abs)) {
        return route.fulfill({ status: 404, body: "asset not frozen: " + rel });
      }
      const ext = path.extname(abs).toLowerCase();
      const type = ext === ".ttf" ? "font/ttf" : (ext === ".otf" ? "font/otf" : "application/octet-stream");
      return route.fulfill({ status: 200, body: fs.readFileSync(abs), contentType: type });
    });
    const externalRequests = [];
    page.on("request", (req) => {
      const u = req.url();
      if (!u.startsWith("file:") && !u.startsWith("data:") && !u.includes("/assets/")) {
        externalRequests.push(u);
      }
    });
    await page.goto("file://" + path.resolve(plan.html.replace(/^file:\/\//, "")), { waitUntil: "load" });
    if (externalRequests.length) {
      fail("external_request_blocked", externalRequests.join(", "));
      return 7;
    }
    const ready = await page.evaluate(() => {
      const p = window.__motion_ready_promise;
      if (!p) { return Promise.resolve("no_ready_hook"); }
      return p.then(() => window.__motion_ready);
    });
    if (ready === false || (typeof ready === "string" && ready.startsWith("error"))) {
      fail("font_ready_failed", String(ready));
      return 4;
    }
    const measure = await page.evaluate(() => window.__motion_measure());
    if ((measure.overflow && measure.overflow.length) || (measure.cp_mismatch && measure.cp_mismatch.length)) {
      fail("measure_rejected", "browser measure rejected", measure);
      return 5;
    }
    for (let i = 0; i < plan.times.length; i++) {
      await page.evaluate((t) => window.__motion_seek(t), plan.times[i]);
      const file = path.join(outDir, "f_" + String(i).padStart(5, "0") + ".png");
      await page.screenshot({ path: file, omitBackground: true, type: "png" });
      frames.push(file);
    }
    fs.writeFileSync(resultPath, JSON.stringify({ ok: true, frames, measure }));
    return 0;
  } catch (err) {
    fail("shoot_failed", (err && err.stack) || String(err));
    return 6;
  } finally {
    if (browser) { await browser.close().catch(() => {}); }
  }
}

main().then((code) => process.exit(code)).catch((err) => {
  console.error(String((err && err.stack) || err));
  process.exit(9);
});
