/*!
 * motion.js —— aiVideo 动效渲染引擎（原创，本仓库自有；与 tools/motion_runtime 同许可）。
 *
 * 契约（计划 §A1/§A2/§15）：
 *   1. 动画只是 `seek(t_sec)` 的纯函数：本文件**没有** requestAnimationFrame、
 *      Date/performance.now/setTimeout、随机数、网络请求或外部资源加载。
 *   2. 字符索引是 **Unicode code point**：所有按字推进都用 `Array.from(text)`，
 *      **不允许**用 UTF-16 偏移的 `String.prototype.slice` 冒充同一索引。
 *      `__motion_measure()` 会把浏览器看到的 code point 数与 Python 侧声明的
 *      cp_len 对比，不一致即拒收。
 *   3. 行切分由 Python 侧唯一实现（`shared/motion_render/validate.py::layout_spec`），
 *      这里只负责按给定的行/坐标绘制，并**权威测量**这些行在该字体下是否放得下。
 *
 * 页面就绪后 `window.__motion_ready` resolve；取帧方按需调用 `window.__motion_seek(t)`。
 */
(function () {
  "use strict";

  var MODEL = JSON.parse(document.getElementById("motion-model").textContent);
  var stage = document.getElementById("stage");
  var FONT_FAMILY = "motion-font";
  var SVG_NS = "http://www.w3.org/2000/svg";

  var R = MODEL.color[0], G = MODEL.color[1], B = MODEL.color[2], A0 = MODEL.color[3];
  var FONT_PX = MODEL.font_size_px;
  var LINE_H = MODEL.line_height_px;
  var STATIC = MODEL.mode === "static";

  function clamp01(v) { return v < 0 ? 0 : (v > 1 ? 1 : v); }
  function smooth(t) { t = clamp01(t); return t * t * (3 - 2 * t); }
  function rgba(alpha) { return "rgba(" + R + "," + G + "," + B + "," + (A0 * clamp01(alpha)).toFixed(6) + ")"; }
  /** code point 真切片（禁 UTF-16 偏移） */
  function cps(text) { return Array.from(text); }
  function cpSlice(text, n) { return cps(text).slice(0, n).join(""); }

  // ---------------------------------------------------------------- 字体
  var fontStyle = document.createElement("style");
  fontStyle.textContent =
    "@font-face{font-family:'" + FONT_FAMILY + "';" +
    "src:url('" + MODEL.font_url + "') format('truetype');font-display:block;}";
  document.head.appendChild(fontStyle);

  // ---------------------------------------------------------------- DOM
  var textNodes = [];   // {el, seg, line, cpStart, cpEnd, full}
  var edgeShapes = [];  // {line, head, from, to, edge}
  var nodeEls = [];     // {disc, label, node}

  var svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("width", String(MODEL.width));
  svg.setAttribute("height", String(MODEL.height));
  stage.appendChild(svg);

  MODEL.segments.forEach(function (seg) {
    seg.lines.forEach(function (line, li) {
      var el = document.createElement("div");
      el.className = "text-line";
      el.style.left = seg.x + "px";
      el.style.top = (seg.y + li * LINE_H) + "px";
      el.style.fontFamily = "'" + FONT_FAMILY + "'";
      el.style.fontSize = FONT_PX + "px";
      el.style.color = rgba(1);
      el.textContent = line.text;
      stage.appendChild(el);
      textNodes.push({
        el: el, seg: seg, line: line,
        cpStart: (line.cp_start === undefined ? 0 : line.cp_start),
        cpEnd: (line.cp_end === undefined ? cps(line.text).length : line.cp_end),
        full: line.text
      });
    });
  });

  MODEL.nodes.forEach(function (node) {
    var disc = document.createElementNS(SVG_NS, "circle");
    disc.setAttribute("cx", String(node.x));
    disc.setAttribute("cy", String(node.y));
    disc.setAttribute("r", String(Math.max(4, Math.round(FONT_PX * 0.32))));
    disc.setAttribute("fill", rgba(1));
    svg.appendChild(disc);
    var el = document.createElement("div");
    el.className = "text-line";
    el.style.left = node.x + "px";
    el.style.top = (node.y - FONT_PX * 1.6) + "px";
    el.style.transform = "translateX(-50%)";
    el.style.fontFamily = "'" + FONT_FAMILY + "'";
    el.style.fontSize = FONT_PX + "px";
    el.style.color = rgba(1);
    el.textContent = node.lines.join("\n");
    stage.appendChild(el);
    nodeEls.push({ disc: disc, label: el, node: node });
  });

  MODEL.edges.forEach(function (edge) {
    var from = null, to = null;
    MODEL.nodes.forEach(function (n) {
      if (n.node_id === edge.from_node) { from = n; }
      if (n.node_id === edge.to_node) { to = n; }
    });
    if (!from || !to) { return; }
    var line = document.createElementNS(SVG_NS, "line");
    line.setAttribute("stroke", rgba(1));
    line.setAttribute("stroke-width", String(Math.max(2, Math.round(FONT_PX * 0.12))));
    line.setAttribute("stroke-linecap", "round");
    svg.appendChild(line);
    var head = document.createElementNS(SVG_NS, "polygon");
    head.setAttribute("fill", rgba(1));
    svg.appendChild(head);
    edgeShapes.push({ line: line, head: head, from: from, to: to, edge: edge });
  });

  // ---------------------------------------------------------------- 时间模型
  /** title：整片淡入 → 保持 → 淡出（§A2）。 */
  function globalEnvelope(t) {
    var d = MODEL.duration_sec;
    var fade = Math.min(0.6, d * 0.2);
    if (t <= 0 || d <= 0) { return 0; }
    if (t < fade) { return smooth(t / fade); }
    if (t > d - fade) { return smooth((d - t) / fade); }
    return 1;
  }

  /** 单个 segment 在自身 [start_sec, end_sec) 内的软边可见度。 */
  function segmentAlpha(seg, t) {
    var start = seg.start_sec, end = seg.end_sec;
    if (t < start || t >= end) { return 0; }
    var edge = Math.min(0.12, Math.max(0.001, (end - start) / 4));
    if (t < start + edge) { return smooth((t - start) / edge); }
    if (t > end - edge) { return smooth((end - t) / edge); }
    return 1;
  }

  function edgeProgress(edge, t) {
    var span = edge.end_sec - edge.start_sec;
    if (span <= 0) { return t >= edge.end_sec ? 1 : 0; }
    return smooth((t - edge.start_sec) / span);
  }

  // ---------------------------------------------------------------- seek
  function seek(t) {
    var gt = Number(t);
    if (!isFinite(gt)) { throw new Error("seek 需要有限时间参数: " + t); }

    textNodes.forEach(function (node) {
      var seg = node.seg;
      var alpha, shown;
      if (STATIC) {
        alpha = 1;
        shown = node.cpEnd - node.cpStart;
      } else if (MODEL.template === "letter") {
        alpha = segmentAlpha(seg, gt);
        // 逐 code point 显现：按本行在 segment 内的 code point 区间裁剪
        var span = Math.max(0.001, seg.end_sec - seg.start_sec);
        var progress = clamp01((gt - seg.start_sec) / span);
        var revealed = Math.floor(progress * seg.cp_len);
        shown = Math.max(0, Math.min(node.cpEnd - node.cpStart, revealed - node.cpStart));
      } else {
        alpha = segmentAlpha(seg, gt) * (MODEL.template === "title" ? globalEnvelope(gt) : 1);
        shown = node.cpEnd - node.cpStart;
      }
      node.el.style.display = alpha > 0 ? "block" : "none";
      node.el.style.color = rgba(alpha);
      var want = STATIC ? node.full : cpSlice(node.full, shown);
      if (node.el.textContent !== want) { node.el.textContent = want; }
    });

    nodeEls.forEach(function (item) {
      var alpha = STATIC ? 1 : segmentAlpha({ start_sec: 0, end_sec: MODEL.duration_sec }, gt);
      item.disc.setAttribute("fill", rgba(alpha));
      item.label.style.color = rgba(alpha);
      item.label.style.display = alpha > 0 ? "block" : "none";
    });

    edgeShapes.forEach(function (item) {
      var p = STATIC ? 1 : edgeProgress(item.edge, gt);
      var x0 = item.from.x, y0 = item.from.y, x1 = item.to.x, y1 = item.to.y;
      var xh = x0 + (x1 - x0) * p, yh = y0 + (y1 - y0) * p;
      item.line.setAttribute("x1", String(x0));
      item.line.setAttribute("y1", String(y0));
      item.line.setAttribute("x2", String(xh));
      item.line.setAttribute("y2", String(yh));
      item.line.setAttribute("opacity", p <= 0 ? "0" : "1");
      var len = Math.sqrt((xh - x0) * (xh - x0) + (yh - y0) * (yh - y0));
      if (len < 1 || p <= 0) {
        item.head.setAttribute("points", "0,0 0,0 0,0");
        return;
      }
      var ux = (xh - x0) / len, uy = (yh - y0) / len;
      var size = Math.max(6, FONT_PX * 0.45);
      var bx = xh - ux * size, by = yh - uy * size;
      var px = -uy, py = ux;
      item.head.setAttribute("points",
        xh + "," + yh + " " +
        (bx + px * size * 0.42) + "," + (by + py * size * 0.42) + " " +
        (bx - px * size * 0.42) + "," + (by - py * size * 0.42));
    });
    return true;
  }

  // ---------------------------------------------------------------- 测量 / 校验
  /**
   * 权威测量：这些行在**当前加载的字体**下是否放得进 rect 宽；
   * 并把浏览器看到的 code point 数与 Python 侧 cp_len 对比。
   */
  function measure() {
    var report = { overflow: [], cp_mismatch: [], font_loaded: null, template: MODEL.template };
    try {
      report.font_loaded = document.fonts.check(FONT_PX + "px '" + FONT_FAMILY + "'");
    } catch (e) {
      report.font_loaded = null;
    }
    var canvas = document.createElement("canvas");
    var ctx = canvas.getContext("2d");
    ctx.font = FONT_PX + "px '" + FONT_FAMILY + "'";
    var rectW = MODEL.rect[2];
    var seen = {};
    function checkText(text, where) {
      var w = ctx.measureText(text).width;
      if (w > rectW + 0.5) {
        report.overflow.push({ where: where, text: text, needed_px: w, available_px: rectW });
      }
      return w;
    }
    MODEL.segments.forEach(function (seg) {
      var want = seg.cp_len;
      var got = cps(seg.lines.map(function (l) { return l.text; }).join("")).length;
      if (want !== got) {
        report.cp_mismatch.push({
          where: seg.segment_id, python_cp_len: want, browser_cp_len: got,
          note: "code point 数与 Python 侧不一致（禁止用 UTF-16 偏移冒充同一索引）"
        });
      }
      seg.lines.forEach(function (line) { checkText(line.text, seg.segment_id); });
    });
    MODEL.nodes.forEach(function (node) {
      node.lines.forEach(function (line) { checkText(line, "node:" + node.node_id); });
    });
    return report;
  }

  window.__motion_seek = seek;
  window.__motion_measure = measure;
  window.__motion_model = MODEL;

  var ready = null;
  try {
    ready = document.fonts.load(FONT_PX + "px '" + FONT_FAMILY + "'", "汉字Ab")
      .then(function () { return document.fonts.ready; })
      .then(function () {
        seek(0);
        window.__motion_ready = true;
        return true;
      })
      .catch(function (err) {
        window.__motion_ready = "error:" + (err && err.message ? err.message : String(err));
        return false;
      });
  } catch (e) {
    window.__motion_ready = "error:" + String(e);
    ready = Promise.resolve(false);
  }
  window.__motion_ready_promise = ready;
  seek(0);
})();
