# O5 全链路跨阶段统一计量与成本台账（2026-11-28）

> 承接 O4（`e119b9f`）。只读工具，零模型调用；`RULE_SOURCE_FINGERPRINT` 不变。

## 1. 交付

`tools/chapter_pipeline_report.py`：

```
python tools/chapter_pipeline_report.py --chapter-dir <path> [--json | --markdown]
python tools/chapter_pipeline_report.py --book-dir <path>    [--json | --markdown]
```

- 单章 schema `pipeline.chapter_report/1`，多章 `pipeline.book_report/1`；
- 五阶段各自 `complete` / `partial` / `not_run`（**缺阶段不崩**）；
- Step02a/02b 直接复用 `summarize_step02a_events` / `summarize_step02b_events`，不另写指标；
- 跨阶段总账：`total_paid_requests_by_provider_model`、`logical_requests`、
  `retry_amplification_ratio`、`top_failure_classes`、`parse_warnings`；
- 多章汇总按各章 `totals` 相加（不重复扫事件，避免加和失配）。

## 2. 口彩与口径（重要）

1. **「真实付费请求」事件集**：`request_sent`（旧账本）/ `http_attempt`（新账本）/
   `video_submit` / `qc_http_attempt`；`pre_send_failed` 的行**不计**（发送前就被拦）。
2. **通道缺失时标 `unknown/<kind>`**：真实 `chapter_001/step02_events.jsonl` 的 22 条
   `request_sent` 没有 `provider`/`model_id` 字段——报表如实归到 `unknown/image`，
   不冒充已知通道，也不直接丢弃。
3. `retry_amplification_ratio = 付费尝试数 / 逻辑请求数`（按 `request_id` 去重）；
   逻辑数为 0 时为 `null`（不编造 1.0）。
4. 坏 JSONL 行只跳过该行并记 `parse_warnings`（含文件名与行号），其余行照常统计。

## 3. 真实验证（只读）

`output/锦衣夜行/part_01_杀青州`（两章，均只跑到 step02a）：

| 章 | step01 | step02a | step02b/03/04 | 真实请求 |
| --- | --- | --- | --- | --- |
| chapter_001 | complete，14 镜 / 86.86s / PASS | complete（74 事件 / 15 帧） | not_run | 22（`unknown/image`，重试放大 1.0） |
| chapter_002 | complete，14 镜 / 116.5s / PASS | not_run | not_run | 0 |

失败分布（chapter_001）：`transport_failed×9`、`qc_rejected×8`、`qc_call_failed×3`。

## 4. 测试

`tests/tools/test_chapter_pipeline_report.py`（7 用例）：完整五阶段夹具、只跑一半、
坏 JSONL 行（含非对象行）、空章节目录、多章加和守恒（`5 = 2 + 3` 与按通道求和）、
Markdown 关键行、CLI `--json` / `--markdown` / 错误路径退出码 2。

## 5. 残余

1. Step03 的 `VOICE_TIER_POLICY` 命中与防截音策略（`fail`/`pad`/`atempo`/`trim`）分布
   尚未解析——`step03_duration_audit.json` 里有实测时长，但策略分布字段要先在 O6 里
   把报告口径定下来（O6 会更新 `voice_unit_report`）。
2. Step04 的「硬门 vs 影子告警」条数取自 `step04_quality.json` 的 `media` 字段；
   当前两章都没跑到 step04，所以只有 `not_run` 路径被真实数据覆盖过。
3. 未做 ffprobe 实测（避免在只读报表里引入媒体探测开销）；成片分辨率/帧率读报告字段。
