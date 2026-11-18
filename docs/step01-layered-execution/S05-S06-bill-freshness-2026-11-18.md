# S05 账单修正 + S06 账单时效（2026-11-18）

对应 2026-11-18 复核：S02 缓存修复、shot 2 隔离门禁、S04 的 5 单元/114 字符保留；
本轮只补 S05 与 S06。**0 外部模型 / 0 生图 / 0 视频 / 0 TTS 调用。**

## 1. S05：就绪待生成与缺帧阻断分开

`tools/cache_bill.py` 升到 `cache_bill.v2`：

| 项 | 读数 |
|---|---|
| 就绪子集 | shot 1 `auto`（first_last）5s / 2 refs；shot 2 `first_only` 7s / 1 ref |
| 就绪待生成 | **[1, 2]** |
| 缺帧阻断 | **[3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]** |
| 命中 | **0**（逐镜 `cache_decision`，两镜均 `absent`：视频产物缺失） |
| 合计 | **2 次生成 / 12s / 3 张参考帧** |
| 策略 | `{"1":"auto","2":"first_only"}` |
| request_key | shot1 `7e661be3…`；shot2 `b8234b4b…` |
| 稿件 revision | `0afa8074…` |
| 章节 scope | chapter=1 / book_index=1 / book_name=杀青州 |

实现要点：

- 不再把 `cache_relation == absent` 的所有镜头都塞进 `pending`；只有**就绪子集**
  里未命中的镜头才进 `cache.ready_pending`；
- 缺帧镜头单列 `cache.blocked_missing_frames`，不计入待生成；
- 命中数由 `step02_video_gen.submit_poll.cache_decision()` 逐镜计算：
  - `frozen_request` 用 plan 的 `request_key` 构造最小引用；
  - `shot_hash` 按该镜 plan 的 `media_policy` 计算（`_get_shot_hash(..., media_policy=...)`）；
  - `require_exact=(policy != "auto")`；
  - 结果连同 `hit/reason/relation` 写入 `cache.decisions`。
- 账单额外记录 `script_revision` / `policies` / `request_keys` / `plan_paths` /
  `chapter_scope`，供 S06 核对。

产物：`_enhancement/s05_bill/cache_bill.json/.md`（已重算）。

## 2. S06：展示前核对账单是否仍适用

`tools/studio_server.py::_bill_freshness()` 在 `cost_prep()` 返回前核对：

| 项 | 判据 | 失配原因 |
|---|---|---|
| 章节 | 账单 `chapter` 绝对路径 vs 当前 Studio chapter | `chapter_mismatch` |
| 稿件 revision | 账单 `script_revision` vs 当前 `compute_script_revision(script)` | `script_revision_mismatch` |
| 素材策略 | 账单 `policies` vs 当前 `step02a_quality.json` 的 `scope.policies` | `policy_mismatch:shotN` |
| 请求 key | 账单 `request_keys` vs 账单 `plan_paths` 里当前 plan 的 `request_key` | `request_key_mismatch:shotN` |
| plan 缺失 | 账单记录的 plan 文件不存在 | `plan_missing:shotN` |

返回 `freshness = {state: fresh|stale|unknown, reasons, checked, note}`。
Studio UI 费用区新增“账单时效 / 时效原因 / 核对摘要”三行；stale 时明确提示
“账单已过期或不适用于当前稿，请重新生成后再审批”。

实测（生产章 + 重算账单）：

```text
GET /api/cost
ok=true / state=fresh / reasons=[]
ready=2 / pending=[1,2] / blocked=[3..13] / hits=0
rev=0afa8074 / policies={"1":"auto","2":"first_only"}
```

单测 `tests/tools/test_cache_bill.py` **2 passed**（就绪/阻断拆分、真实缓存命中）；
`tests/tools/test_studio_server.py` **25 passed**，覆盖 fresh 以及
chapter / revision / policy / request_key 四类失配。
`tests/step02_video_gen tests/step02b_video_gen tests/tools` 合计 **373 passed / 0 failed**。

## 3. V02 审批对象（待确认）

| 镜头 | 策略 | 时长 | 参考帧 | 生成次数 |
|---|---|---|---|---|
| shot 1 | auto（first_last） | 5s | 2（ff+lf） | 1 |
| shot 2 | first_only | 7s | 1（ff） | 1 |
| 合计 |  | **12s** | **3** | **2** |

- 单价：`pending_provider_quote`（未编造金额）。
- 自动重试配置：`VIDEO_MAX_RETRIES=3`；**人工批准尝试上限仍为 1**，不因自动重试
  配置放宽。
- **运行根**：shot 2 的隔离门禁证据对应 `.tmp/s03_isolated` 的请求
  （历史 key `58c35558…`）；本账单记录的是**生产根** O2 门禁后的 plan
  （key `b8234b4b…`）。两者不可混用。若 V02 使用生产根，则 shot 2 证据以
  生产 `step02a_quality.json`（PASS，policies `{"1":"auto","2":"first_only"}`）和本账单
  `plan_paths` 为准；若坚持使用隔离证据，必须切到与 `58c35558…` 匹配的隔离根，
  并重新生成账单后再审批。
- 本轮未发起任何真实生成；V02 仍需用户确认单价与付费尝试上限后另行执行。
