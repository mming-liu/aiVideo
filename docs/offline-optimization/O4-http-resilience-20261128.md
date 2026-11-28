# O4 统一 HTTP 传输韧性层与故障注入（2026-11-28）

> 承接 O3（见 `O3-run-context-20261128.md`）。本包**不改变** `RULE_SOURCE_FINGERPRINT`；
> 全部验证用离线桩，零真实请求、正式 `output/` 零写入。

## 1. 交付范围

| 计划要求 | 落地 | 状态 |
| --- | --- | --- |
| 新增 `shared/http_resilience.py` | 错误三分类 + 内容审核拒收识别 + `ManagedSession` | ✅ |
| 统一错误分类 | `RETRYABLE_TRANSPORT`（SSL / 连接重置 / 超时）/ `RETRYABLE_HTTP`（429/500/502/503/504）/ `NON_RETRYABLE_CLIENT`（4xx） | ✅ |
| `ManagedSession`：脏连接池重建 + 指数退避 + 抖动 | `close()`→重建，`backoff_delay()` 指数+抖动，优先 `Retry-After` | ✅ |
| 每次尝试上报 `outbound_guard` | `charge_attempt(phase)`（未安装守卫 = no-op；触顶抛 `SendBlocked`） | ✅ |
| 不可重试错误 0 重试 | 4xx 直接返回响应，不重试 | ✅ |
| 故障注入测试（6 类） | `tests/shared/test_http_resilience_fault_injection.py`（31 用例） | ✅ |
| **渐进接入**到生图/生视频/视觉 QC provider | **本包未接入**（见 §4） | ⚠️ 未完成 |

## 2. 关键设计

1. **不第二套账本**：重试次数只由 `max_retries` 控制；预算只由 `outbound_guard` 管。
   `ManagedSession` 每次尝试前调 `charge_attempt()`，触顶时 `SendBlocked` 直接冒泡
   （`BaseException`，不会被 `except RuntimeError` 吞掉）。
2. **不改变对外契约**：返回的响应对象原样交给调用方；4xx **不**转异常（由业务层决定），
   只有传输异常在重试耗尽后原样抛出（不包装类型）。
3. **脏连接池**：`SSLError` / `ConnectionResetError` / 超时命中后置脏，下一次尝试前
   `close()` 旧会话并重建；`rebuilds` 计数可观测。
4. **可测性**：`transport` / `sleep` / `session_factory` 全部可注入，测试不依赖真实时间与网络。

## 3. 故障注入覆盖（31 用例）

| # | 故障 | 断言 |
| --- | --- | --- |
| 1 | 429 + `Retry-After: 7` | 只重试一次，等待 7s（头优先于退避），账目 2 次尝试 |
| 2 | 502→503→504 连续 | 重试到上限（3 次尝试）后返回最后一次响应，不抛 |
| 3 | `ssl.SSLError` | 旧会话 `close()` 1 次、新会话重建、重试成功、`rebuilds==1` |
| 4 | 读超时 | 判为可重试传输错误，退避后重试成功 |
| 5 | 400/401/403/404/422 | 各只发 1 次，0 重试 |
| 6 | 预算耗尽（阶段上限 0） | `SendBlocked`，transport 0 次调用，`attempts==0` |
| 附 | 退避序列 / 状态与异常分类 / 内容审核标记 / 账目计数 | 指数序列 `0.5→1→2`；分类表 14 项；审核标记 3 正 1 负 |

## 4. 未完成项与理由（如实记账）

**provider 接入延后。** 计划要求「优先接入 `_image_gen/volcengine_provider.py`、
`_video_gen/volcengine_provider.py`、`_video_gen/agent_plan_provider.py` 与视觉 QC 客户端，
保持对外异常类型与返回值契约不变」。本包只交付模块与测试，未改任何 provider 发送路径，
理由：

1. 三个视频/生图 provider 各自带业务重试语义（403 换模型、5xx 交恢复、`SUBMIT_UNKNOWN`
   禁止自行重发），用统一韧性层替换会改变这些**业务契约**，不是「保持契约不变」。
2. 视觉 QC 的既有重试契约测试 `tests/shared/test_image_qc_retry.py` **6 个用例本来就是红的**：
   它们仍在 monkeypatch `requests.post`，而实现已改为 `_get_qc_session().post`，于是被
   `tools/probe_isolation.py` 的探针隔离拦下（`ProbeIsolationError`）。接入前必须先修这批
   stale 契约测试，否则改完无法区分「接线错」与「用例过期」。
3. 在离线阶段（零真实调用）把韧性层接进真实发送路径，收益无法用真实故障验证，
   风险却由后续真实跑量承担。

**建议的后续顺序**（留待真实跑量前）：先修 `test_image_qc_retry.py` 的 6 个 stale 用例 →
用 `ManagedSession` 替换 QC 客户端重试循环 → 再按 provider 逐个接入（每个单独回归）。

## 5. 门禁

| 项 | 结果 |
| --- | --- |
| 新增单测 | 31 个用例全过 |
| `ruff_gate` | PASS（读数不变） |
| 指纹 | 不变 |
| 正式 `output/` | 不变（656 文件 / 401,740,834 字节） |
