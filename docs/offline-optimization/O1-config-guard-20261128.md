# O1 配置单源收口与跨键冲突启动守卫（2026-11-28）

> 承接 O0（`a641d77`）。基线 `6a4a3e7`；本包零真实模型调用、正式 `output/` 零写入。
> 取证产物：`.tmp/o1_junit/`、`.tmp/o0_20261128/`（均为本地未跟踪目录）。

## 1. 目的与完成判据

1. `.env.content` 里残留的历史硬覆盖清掉（三项视频画幅留空、`IMAGE_MAX_REFS` 注释化），
   并把「留空 = 按分镜画幅推导」在 provider 里真正实现（否则留空会透传空画幅）。
2. 把 `shared/env_guard.py` 从「shell 覆盖检测」升级为**跨键一致性预检**，供单测、
   `tools/print_effective_config.py --check` 与五个阶段入口共用。
3. 补掉 `6a4a3e7` 的 `RENDER_FPS <= 0` 边界（`shared/style_prefix.py`）。
4. 完成判据：`--stage all --check` 0 error/0 warning；新增规则单测 ≥16；全量离线测试
   **0 新增失败**；ruff 棘轮门禁转绿；正式产物零变化。

## 2. 改动清单

| 文件 | 变更 |
| --- | --- |
| `shared/config.py` | `BAILIAN_VIDEO_RATIO` 默认 `"9:16"` → `""`（空=推导）；新增 `parse_size()` / `size_ratio()` 画幅解析单源 |
| `_video_gen/bailian_provider.py` | 新增 `_ratio_from_canvas()` / `_resolve_ratio()`：显式 override → `frame_canvas_ratio()` → `req.size` → `9:16`；三处 `"ratio": self._ratio` 改走推导；`self._ratio` 只表示显式 override |
| `shared/style_prefix.py` | `frame_canvas_ratio()` 改用 `shared.config.size_ratio()`；`get_global_render_config()` 的 fps override 增加 `<= 0` 保护（非数字仍保留默认） |
| `.env.content` | `BAILIAN_VIDEO_RATIO` / `VOLCENGINE_VIDEO_RATIO` / `AGENT_PLAN_VIDEO_RATIO` 留空；`IMAGE_MAX_REFS=3` 注释化（留空=按模型能力，seedream-5-0-pro 原生 10 张） |
| `shared/model_caps.py` | `active_video_model()` / `active_video_supports_audio()` 增加可选 `env` 映射（默认进程环境），供预检在不写 `os.environ` 的前提下按同一口径解析模型 |
| `shared/env_guard.py` | 新增 `ConfigIssue` / `ConfigConsistencyError` / `validate_config_consistency()` / `assert_config_consistent()` / `format_config_issues()` 与 8 类规则 |
| `tools/print_effective_config.py` | 新增 `--check`：报告附 `consistency_issues` / `consistency_ok`，有 error 退出码 2 |
| `step01_script_gen/orchestrator/runtime.py` | 模块级接入 `assert_config_consistent(stage="step01")`（沿用既有 env_guard 调用点） |
| `step02a/step02b/step03/step04 main.py` | `main()` 起始处接入对应 stage 的预检（默认只记录，`CONFIG_GUARD_STRICT=1` 才阻断） |
| `_text_gen/service.py` | 修 F821：`_cid = new_call_id()` 前移到章节额度扣减之前（原顺序会在 `STEP01_CHAPTER_QUOTA_FILE` 生效且 provider 非 `agent_plan` 时抛 `UnboundLocalError`） |
| `tests/shared/test_env_guard_consistency.py` | 新增：8 规则正反例、阶段过滤、strict/非 strict、CLI `--check` 退出码（64 个用例） |
| `tests/step02b_video_gen/test_bailian_ratio.py` | 新增：留空推导、显式覆盖、非法值回退、payload 不为空串、配置默认值 |
| `tests/shared/test_config_single_source.py` | 增补：`RENDER_FPS=0/-5/0.0` 保持默认 24；`parse_size`/`size_ratio` 单源断言 |

## 3. 关键实现口径（与计划的偏差及依据）

1. **`AUDIO_MODE_VOICE_CONFLICT` 按实际声音合同收窄。** 计划写「`VIDEO_MODEL_VOICE=0`
   且 `MERGE_AUDIO_MODE` 只留原声会直接产出静音成片」，但 `shared/audio_policy.resolve_audio_policy()`
   在 voice=0 或模型无声时**一律回落 tts_mix**，step04 也走该函数取 effective mode，
   该静音路径在当前代码里不存在。实际风险是「配置字面与实际执行不一致」与「模式名写错被
   静默当成 native」，因此实现为：
   - `error`：`MERGE_AUDIO_MODE` 不在 `{native, tts_mix}`（会被静默按 native 处理）；
   - `warning`：`native` + `VIDEO_MODEL_VOICE=0`（声音合同实际会回落 tts_mix，step03 必须跑）。
   规则代码与键集合不变。
2. **`VIDEO_MAX_RETRIES` 允许 0**，只有负数算 error：`range(max_retries + 1)` 下 0 = 不重试
   但仍会尝试一次，是合法配置；计划里「<=0 一律非法」会误报。
3. **新增 `shared.config.parse_size()` / `size_ratio()`。** 画幅解析原先在
   `style_prefix.frame_canvas_ratio`、`vimax_adapters._canvas_ratio`、各 provider 各有一份；
   本次三处新消费方（env_guard、bailian、style_prefix）复用同一实现，避免三份解析漂移。
4. **`--check` 只在显式传入时计算**，默认输出保持原样（不破坏既有 CLI 输出用例）。

## 4. 指纹：本包确实改变了 `RULE_SOURCE_FINGERPRINT`（计划预期不符，已定位）

| 时点 | 本机工作树值 |
| --- | --- |
| O1 前（`6a4a3e7` + O0 取证） | `6f612fa3d96482bb` |
| O1 后 | `3cb3049c25224ad3` |

**根因（实测，非推断）**：做过一次可复现的对照实验——把 `shared/style_prefix.py` 临时还原成
`HEAD` 版本再算指纹，得回 `6f612fa3d96482bb`；换回 O1 版本得 `3cb3049c25224ad3`，
文件字节恢复后校验一致。即漂移**只**来自 `shared/style_prefix.py`，与本包其它改动无关
（其余改动文件都不在指纹清单里：`shared/config.py`、`shared/model_caps.py`、
`shared/env_guard.py`、`_text_gen/service.py`、各 `main.py`、`tools/*`）。

机制：`_STEP01_STYLE_PREFIX_SYMBOLS` 含 `inject_global_config_into_script`，其 AST 闭包
卷入 `get_global_render_config` / `frame_canvas_ratio` / `_GLOBAL_RENDER_DEFAULTS`；本包
两处修正都落在这条闭包里，因此必然改变符号摘要。

结论：**O1 验收里「指纹不应变化（与 6a4a3e7 一致）」与本包要求的 `RENDER_FPS` 边界修复
不可同时成立**——两者改的是同一段被纳入指纹的代码。按计划排序，O2 本来就承担「把非判定
元数据从指纹里解耦」并在 O2 记录新的稳定值；中间状态不会导致额外刷新（O6 才落盘一次），
所以本包按原指令完成修复并在此记账，不做绕过指纹的写法。

**给 O2 的实测事实**：逐个根符号算闭包后，只有 `inject_global_config_into_script`
会拉到渲染配置簇（`get_global_render_config` / `_GLOBAL_RENDER_DEFAULTS` /
`frame_canvas_ratio`）；其余 8 个符号（`sanitize_script_shots` / `validate_sensitive_words` /
`sanitize_prompt` / `detect_lf_midstate` / `detect_ff_multi_action` / `normalize_ff_desc` /
`validate_fps_consistency` / `is_empty_scene`）的闭包都不含它们。所以 O2 只需把
`inject_global_config_into_script` 移出清单即可达到预期效果，无需再拆其它符号。

## 5. 生效配置与验收读数

`python tools/print_effective_config.py --stage all --check`（本机工作树）：

- `consistency_ok = true`，`consistency_issues = []`（0 error / 0 warning），退出码 0。
- `.env.content` 三项画幅生效值均为 `''`（来源 `.env.content`），`IMAGE_MAX_REFS` 未设置
  （来源不再是 `.env.content`）。
- 画幅推导实测：`VIDEO_SIZE=1440*2560` → `9:16`；`2560*1440` → `16:9`；`1280*1280` → `1:1`；
  `1080*1350` → `9:16`（落到最近竖桶）；`RENDER_FPS=0/-5` → 24，`30` → 30。

**改完再跑一次只读门禁预演**（`--refresh-gate --print-refresh`，两章）：
`ok=true` / `status=PASS` / `blocking=false` / 新指纹 `3cb3049c25224ad3` /
`reverified_from_fingerprint=e5460e55f36d1d08`。即本包的指纹变化**不改变门禁裁决**
（两章仍无真实 blocking），正式目录零写入。

## 6. 测试与静态门禁

| 项 | 基线（O0） | O1 后 |
| --- | --- | --- |
| 全量分组测试 | 8304 项：7916 passed / **75 failed** / 0 error / 313 skipped | 8368 项：7981 passed / **74 failed** / 0 error / 313 skipped |
| 新增失败 | — | **0**（逐用例比对 `.tmp/o0_junit_baseline` vs `.tmp/o1_junit`） |
| 新增通过 | — | 1：`tests.shared.test_s27_boat_composition::test_seedream_5_0_pro_and_flash_caps_and_ref_limits`（`get_ref_limit(seedream-5-0-pro)=10` 不再被 `.env.content` 的 `IMAGE_MAX_REFS=3` 压到 3——正是本包第 2 项要恢复的模型能力） |
| `tools/ruff_gate.py` | FAIL（F821=1，`_text_gen/service.py` `_cid`） | **PASS**（`{'F401': 199, 'F811': 15}`，F821=0；比存档基线少 37 条） |
| 新增单测 | — | 64 个用例 100% 通过（规则、阶段过滤、strict、CLI） |

`tools/ruff_gate.py` 提示「比基线少 37 条，可 `--update` 下调」：本包未顺手改
`tools/ruff_baseline.json`（避免把无关计数变化混进 O1 提交），留待 O6 收口时统一处理。

## 7. 残余与下一步

1. 本包的指纹中间值 `3cb3049c25224ad3` **不是**最终值：O2 会把
   `inject_global_config_into_script` 移出符号清单并改为 AST 归一化，届时重算并锁定。
2. `print_effective_config --check` 依赖 `.env.content` 的生产加载语义（这是验收口径本身）；
   若在 shell 里另设了相关键，会按 shell 优先——与各阶段入口一致。
3. 未在真实生成入口跑过（本包承诺为离线）：`bailian` 的 ratio 推导只经单测与 payload 断言，
   尚无真实百炼视频请求佐证——按计划红线，这类验证留到真实样片阶段。
