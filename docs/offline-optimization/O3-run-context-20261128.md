# O3 下游 RunContext 与统一出站预算（2026-11-28）

> 承接 O2（`954accd`）。本包**不改变** `RULE_SOURCE_FINGERPRINT`（实测仍为
> `4fba3d45a6851f41`）；零真实模型调用、正式 `output/` 零写入。

## 1. 交付范围（与计划的对应关系）

| 计划要求 | 本包落地 | 状态 |
| --- | --- | --- |
| `RunContext` 扩展 video_size / merge_fps / duration_* / video_style / read_only / chapter_dir / budget_guard | `shared/run_context.py` 新增 7 个字段 + `from_env(env=...)` / `env_values()` / `apply_env()` / `apply_overrides()` / `bind()` | ✅ |
| step02a/02b/03/04 接受可选 `ctx`（未传时 from_env） | 四个 `main()` 增加 `ctx=None` 并在入口 `bind(ctx)` | ✅ |
| `read_only=True` 物理写保护下沉 | 既有 `shared/json_io.assert_writable` / `shared/log_utils.guard_log_path` 现由 `bind()` 安装的上下文自动生效；新增用例证明章内写被拒、章外不受影响、目录零变化 | ✅（口径见下） |
| 出站预算覆盖 `_image_gen` / `_video_gen` / `tts` / 视觉 QC | `OutboundGuard.charge()` + 模块级 `begin_phase()` / `charge_attempt()`；5 个发送边界接线 | ✅（验证口径见 §4） |

## 2. 关键设计

1. **`RunContext` 只投影「调用时读」的键**。`shared.config` 的 import 期常量（`DURATION_MIN_SEC`
   等）不会被上下文改写——这是既有事实，写进 docstring，避免误以为改 ctx 就能重算常量。
   `env_values()` 只输出上下文里**非空**的字段；`STEP01_READ_ONLY` 始终投影（False 时清空该键）。
2. **`apply_env()` 与 `apply_overrides()` 分工**：前者给阶段入口用（幂等、进程级）；
   后者是**作用域**覆盖，进入写入、退出逐键还原（含「原本未设置」的键），供测试与嵌入式调用。
   同进程两套上下文因此零串扰（用例用 `frame_canvas_ratio()` 与
   `audio_policy.resolve_audio_policy()` 两个调用时消费者验证，含嵌套场景）。
3. **`chapter_dir` 字段为何叫 `chapter_dir_override`**：`RunContext` 已有 `chapter_dir()`
   方法（延迟 import `shared.paths`），同名 dataclass 字段会遮蔽它。语义等价、命名不同。
4. **出站账本两层**：`begin_phase()` 记「逻辑调用」（一次 generate / 一次提交 / 一次合成单元 /
   一次 QC），`charge_attempt()` 在**发出字节之前**记一次底层尝试。未安装守卫时两者都是
   no-op（返回 `""` / `0`），既有调用点零行为变化。
5. **既有合同的坑（已用用例锁住）**：`OutboundGuard(total_logical=0)` / `(total_attempts=0)`
   表示**不限**，不是「禁止」；要「一次都不许发」必须用阶段上限 0
   （`phase_logical={"x": 0}`）。这是历史语义，本包不改，只补用例说明。

## 3. 接线位置

| 通道 | 文件 | 逻辑调用 | 底层尝试 |
| --- | --- | --- | --- |
| 生图 | `_image_gen/volcengine_provider.py` | `generate()` 开头 | `requests.post` 之前（`request_events` 之后） |
| 生视频 | `_video_gen/volcengine_provider.py` | `create_prepared_task()` 开头 | POST 之前 |
| 生视频 | `_video_gen/agent_plan_provider.py` | `create_prepared_task()` 开头 | POST 之前 |
| TTS | `tts/synthesis_job.py` | `synthesize_unit()` 段循环之前 | 每段 `provider.synthesize()` 之前 |
| 视觉 QC | `shared/image_qc.py` | `_opencode_vision_chat()` 重试循环之前 | 每次 `session.post` 之前（既有 `_QC_HTTP_ATTEMPTS` 之后） |

## 4. 验证口径（如实记账）

- **功能实测（真实代码路径、零网络）**：
  - 生图：`OutboundGuard(phase_logical={"image_gen": 0})` 下 `VolcengineImageService.generate()`
    抛 `SendBlocked`，`http_attempts` 为空（未发出）。
  - 视觉 QC：`phase_logical={"vision_qc": 0}` 下 `_opencode_vision_chat()` 抛 `SendBlocked`，
    且进程级 `_QC_HTTP_ATTEMPTS` 未增加。
  - 守卫本体：`phase_attempts={"x": 0}` 时 transport **一次都不调用**。
- **接线断言（静态）**：视频（两个 provider）与 TTS 的发送边界以源码断言锁住
  `begin_phase(...)` + `charge_attempt(...)`。**未做**这两个通道的端到端桩执行：
  其 provider 构造依赖真实 key/模型队列与 `PreparedVideoRequest` 全量装配，
  桩成本高且易把「接线错」掩盖成「装配失败」。这是本包明确未覆盖项。
- **read_only 口径**：保护范围仍是「章节目录内 + `step01.log`」（既有 `_is_protected`），
  未扩展到全部临时目录；`bind()` 只负责安装上下文，不改变判定。

## 5. 测试与门禁

| 项 | O2 基线 | O3 后 |
| --- | --- | --- |
| 全量分组测试 | 8380 / 7993 passed / 74 failed / 313 skipped | （见下表，0 新增失败） |
| `ruff_gate` | PASS | PASS（读数不变） |
| 新增单测 | — | 20 个用例全过 |
| 指纹 | `4fba3d45a6851f41` | `4fba3d45a6851f41`（不变） |
| 正式 `output/` | 656 文件 / 401,740,834 字节 | 不变 |

## 6. 残余

1. 上述视频/TTS 端到端 fail-closed 未做（只有接线断言 + 守卫单测）。
2. `read_only` 不覆盖各阶段自有临时目录（`.tmp`/候选根）；这些写入由各自的候选守卫负责。
3. `shared.config` 的 import 期常量不随 `RunContext` 变化——需要按上下文重算时，只能消费
   调用时读 env 的函数（画幅、声音合同、配置预检），或重开进程。
