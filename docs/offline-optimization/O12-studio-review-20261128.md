# O12 studio_server 审片看板与全链路 API（2026-11-28）

> 承接 O9（`457c661`）。只读；零模型调用。前端改动为**增量**（新状态条 + 新页签），
> 不改动既有渲染函数；视觉复核留给用户在本机打开页面确认。

## 1. 新增三个只读 API

| 路由 | 数据来源 | 关键字段 |
| --- | --- | --- |
| `GET /api/chapter/<id>/pipeline_report` | 直接复用 O5 `tools/chapter_pipeline_report.build_chapter_report()` | `stage_status`、`totals.paid_requests_total`、`parse_warnings`；补 `chapter`（数字）与 `chapter_name` |
| `GET /api/chapter/<id>/shots_review` | `script_result.json` + 既有 `review()`/`video_table()`/`voice_table()` + 面板侧车 | 逐镜：帧/关键帧/参考图/`qc`/视频/字幕/`audio_over_window`，以及三个筛选位 |
| `GET /api/system/config_health` | O1 的 `shared.env_guard.validate_config_consistency`（与 `--check` 同源）+ `step01_commit.json` 的指纹 | `consistency{ok,error_count,warning_count,issues}`、`fingerprint{live,adopted,matches,adopted_missing}` |

**章节 id 白名单**：`(?:chapter_)?\d{1,4}` 且必须等于当前打开章节的章号；
`999` 之类返回 404 `chapter_not_open`，非数字返回 400 `bad_chapter_id`，
`..`（含 `%2e%2e`）一律拒绝——不依赖路径归一化。

## 2. 前端（`tools/studio_ui/index.html`，增量）

- 顶部新增 **状态条** `#healthBar`：画风、配置一致性（0 error / N warning）、门禁指纹
  是否与采纳版本一致；每次刷新时拉一次 `config_health`，失败只在状态条内提示。
- 新增 **「审片」页签**：一次性拉 `shots_review`，顶部显示各筛选计数，四个筛选按钮
  （全部 / 仅看 QC 拒收 / 仅看未生成 / 仅看音频超窗），逐镜卡片显示帧状态、关键帧数、
  参考图数、QC 状态、视频状态与字幕。
- 既有函数（`renderStep01`…）未改动；新代码包在 `try` 之外的独立函数里，接口失败
  只影响该页签。

## 3. 测试

扩展 `tests/tools/test_studio_server.py`（+7 用例，共 32）：逐镜清单完整字段、QC 拒收
标记与原因、配置健康（指纹一致 + 一致性字段）、台账包装（`chapter` 数字 + `stage_status`）、
HTTP 三个新路由、越权/穿越拦截（404/400/404/400/404）、首页引用新接口。

回归：全量分组跑见下节数字。

## 4. 残余

1. **前端未做浏览器级视觉复核**：本包只保证接口与静态引用（首页含新路由字符串），
   滤镜/布局在真实浏览器里的观感需你打开 `python tools/studio_server.py --receipt ...` 确认。
2. `shots_review.qc` 是**面板侧车启发式**（`*hallucination*.json` / `*qc*.json` 的
   status/reason/person_count），不是 QC 服务的原始判据源；没有侧车时如实标 `not_checked`。
3. 逐镜「时长偏差」暂未接 ffprobe 实测（避免每次打开页面探测全部视频）；当前给
   `video.state` 与字节数，实测偏差留给后续按需点击探测。
