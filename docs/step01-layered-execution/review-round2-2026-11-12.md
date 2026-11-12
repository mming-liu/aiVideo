# 复核第二轮：按实际消费路径修正（2026-11-12）

针对复核意见的七项，逐条给出**独立核对结果 + 本包处置**。原则不变：只把有入口证据的
写成已修；需要改准入语义的只记账、走裁决。

## 1. 逐项对照

| # | 复核意见 | 独立核对 | 本包处置 |
|---|---|---|---|
| P1 | 基线盘点不完整，会重复建设 | **成立**：`shared/execution_contract.py`、`shared/execution_semantics.py`、`step02a_frame_gen/execution_plan.py`、`build_timeline_acceptance`、`verify_rendered_output` 均已存在且已接主入口 | **采纳**：M04/M05/M08 一律按「现有实现的差距修复」处理，本包未新增第二套协议 |
| P2 | 画面计划未成为实际生成参数来源 | **成立**：`gen_all_frames` 只做 `validate_plan_identity`，随后 `tasks_spec.append((idx, shot, ...))` 用的是原稿 `shot` | **已修**（§2） |
| P3 | BLOCK 候选无合法下游路径 | **成立**：Step02a 入口要求语义门通过 + `COMMITTED` manifest；U6 是 BLOCK | **部分**：给出两条路径的边界与只读准备入口的要求（§3），未接线 |
| P4 | 下游语义检查会重新阻断合法省略 | **成立且已复现**：`起因/结果` 无 `行动` 标签 → `causal_gap / reject`，且 `context_bridge` 不在时间标记读取范围 | **已修**（§4） |
| P5 | 提示词改造漏掉实际生效分支 | **成立**：Seedance 走 `_build_seedance_concise_prompt`、`first_last+image` 走 `build_motion_prompt`、r2v 另有用途块与 provider 压缩 | **部分**：只改了 r2v 首帧口径；全分支到 prepare 的验收未做（§5） |
| P6 | 一镜多片段下游接不住 | **成立**：`(kind, shot_index)` 重复身份检查 + `video_map` 二元组映射 | **未做**：列为独立施工项（§6） |
| P7 | 声音前置缺准备态/最终策略两态 | **成立**：Step03 先探测现有音轨再定策略；合并候选要求声音与视频 manifest 同现 | **未做**：给出两态定义（§7） |

## 2. P2：计划成为生成参数的唯一来源（已修）

### 改动

1. `step02a_frame_gen/execution_plan.py`
   - 每个镜头条目新增 `execution.unit`：**合并候选之后**的整份单元（生成侧要消费的那一份）。
   - 帧条目新增 `kind`（`ff`/`lf`/`kf`），使「计划里的最终提示」可被复算。
   - 新增 `verify_plan_execution(plan, *, characters, check_reference_files=True)`：
     - 逐帧用 `execution.unit` 重算 `compose_frame_prompt`，与计划记录的 `prompt` 比对；
     - 逐参考重算文件 `sha256`，与计划记录比对（文件缺失也算失配）。
2. `step02a_frame_gen/main.py`
   - `validate_plan_identity` 之后追加 `verify_plan_execution`，不一致即 `SystemExit(2)`；
   - 任务列表改为 `_plan_units.get(idx, shot)`——**生成消费计划里的单元**，不再回读原稿。

### 证据

`tests/step02a_frame_gen/test_m05_plan_consumption.py`（4 条）：

| 用例 | 断言 |
|---|---|
| `test_generation_consumes_the_planned_execution_unit` | 用候选改 `ff_desc` 建计划 → 桩生成器收到的 `ff_desc` **是计划值**，原稿未被污染 |
| `test_plan_prompt_drift_is_refused_before_send` | 改计划里的 `frames[].prompt` → `plan_prompt_drift`，`gen_all_frames` 抛 `SystemExit`，桩**零调用** |
| `test_missing_reference_file_is_reported` | 参考文件缺失 → `plan_reference_missing` |
| `test_plan_without_execution_block_is_refused` | 旧计划缺执行参数 → `plan_execution_missing` |

**仍未覆盖**：参考**拼图/重排**后的身份（计划记录的是收集阶段的 sha256；provider 侧
若再拼表，需在 prepare 结果上复核）。这一条并入 P5。

## 3. P3：BLOCK 候选的下游路径（边界已定，未接线）

两条路径必须分开，不能拿「候选输出根」当绕过上游准入的依据：

| 路径 | 输入 | 入口 | 产物 | 本包状态 |
|---|---|---|---|---|
| A. 下游合同与媒体链验证 | 受控合法样本 + 桩审计 | **真实提交入口**（隔离 `OUTPUT_BASE`）→ `COMMITTED` → Step02a/02b/03/04 | 隔离提交 + 样片 | 既有 S03–S06 链已具备 |
| B. U6 修订候选 | 冻结 BLOCK 候选 + 桩响应 | 修订入口 → 投影 → 残余评阅 | 离线候选（不进入制作链） | M02 已交付 `artifacts/m02/` |

需要「未提交候选只做准备」时，应新增**显式只读准备入口**（例如
`--plan-only --candidate-root` 已有形态），与生成、采纳入口隔离；本包未新增该入口。

## 4. P4：合法省略在下游被重新阻断（已修）

### 复现（修前）

```python
# 起因：甲与乙约定赴城 / 结果：乙抵达城门；两镜各有两段动作；衔接字段写「次日，城门」
check_execution_semantics(script)
# -> [{"code": "causal_gap", "severity": "reject", ...}]
```

### 根因（两处）

1. `_check_causal_and_time` 只看 `causal_chain` 的**角色标签**，从不看镜头里有没有过程；
2. `_has_temporal_marker` 不读 `context_bridge`。

### 改动

| 文件 | 改动 |
|---|---|
| `shared/execution_semantics.py` | 新增 `_process_carriers`（动作分段/关键帧，结构判据）；`causal_gap` 降为 **advisory** 并带 `carrier_evidence`；`time_ellipsis_lost` 只在**无过程载体且无时间标记**时拒收；`_has_temporal_marker` 纳入 `context_bridge` / `time_notes` |
| `shared/execution_contract.py` | `attach_semantic_checks` 尊重 finding 的 severity：advisory 进 `binding.advisories`，不再写进 `unresolved`、不再把 checks 判 `fail` |
| `step02a_frame_gen/execution_plan.py` | `plan.status` 只由**阻塞级** finding 决定 |

### 证据

- `tests/shared/test_execution_semantics.py`：`causal_gap` 必须是 advisory，其余 code 仍必须 reject（防止 advisory 被扩大）。
- `tests/step02a_frame_gen/test_execution_plan.py::test_legal_ellipsis_is_not_blocked_by_the_frame_plan_entry`：
  同一合法样本 → `plan.status == "planned"`、无阻塞级 finding、`causal_gap` 作为 advisory
  记录、`validate_plan_identity` 通过。
- 变异样本（删掉行动 + 时间标记）仍产出 `time_ellipsis_lost` 并 blocked——**判别力保留**。

## 5. P5：提示词分支（部分）

已修：r2v 下首帧用途不再声称「画面从该静态瞬间开始、完全一致」，改为「可对齐什么 +
不锁定首帧，属待验证」（`tests/step02_video_gen/test_m06_reference_purpose.py`）。

未做：把验收终点固定到 **provider 无提交 prepare 结果**，并分别覆盖三条分支
（Seedance 精简正文 / 普通完整提示 / `first_last+image` 的 `build_motion_prompt`）。
现有测试只覆盖了片段（`test_seedance_prompt_practices.py`、`test_prepared_frozen.py`），
没有一条断言「最终 prepared 文本里的图N 指代 = 实际 materials 顺序」。

## 6. P6：一镜多片段（未做，独立施工项）

现状：`step04_merge/timeline.py` 用 `(kind, shot_index)` 判重复身份，候选解析用它构造
`video_map`。同一镜的第二个片段会触发 `duplicate_clip_identity`，或覆盖同一映射位。

首个贯通版本维持**一镜头一视频片段**；需要拆分时明确报「尚未支持」，不靠新增一个
片段字段宣称完成。独立施工项须覆盖：片段身份与顺序、语义镜头→片段映射、候选解析/
缓存/恢复、声音跨片段连续性、字幕窗口与合并时间轴。

## 7. P7：声音前置的两态（未做）

| 状态 | 输入 | 允许产出 | 不允许 |
|---|---|---|---|
| 视频生成前 | 语义稿 + 文本 + 说话人 | 独立 TTS/旁白的准备结果与实际时长 | 宣称最终声音来源已确定 |
| 视频存在后 | 视频音轨 + 候选 | 声音策略确认与时间轴绑定 | 用提前准备覆盖实际音轨 |

Step03 当前「先探测现有音轨再定策略」在无视频时探测为未知；合并候选解析又要求声音
候选同时提供视频 manifest。两态拆分后，提前生成才谈得上「沿现有入口完成采纳链」。

## 8. 验证读数

```powershell
python -m pytest tests/step02a_frame_gen tests/shared -q
# -> 1475 passed / 5 skipped / 0 failed
python -m pytest tests/step02a_frame_gen/test_m05_plan_consumption.py -q
# -> 4 passed
python -m pytest tests/shared/test_execution_semantics.py tests/shared/test_execution_contract.py `
  tests/step02a_frame_gen/test_execution_plan.py -q
# -> 63 passed
```

## 9. 与修订后施工顺序的对应

| 修订顺序 | 本包落点 |
|---|---|
| 1. M01 重新盘点 | 本文 §1；`M01-evidence-and-attribution.md` |
| 2. M02＋M03 先贯通修订入口与共用判据 | M02 已交付；**M03 增补 P4**（本文 §4） |
| 3. M04＋M05 让画面计划进入发送参数 | **P2 已修**（本文 §2） |
| 4. M06 全部分支到 prepare | 仅首帧口径；全分支未做（§5） |
| 5. M07＋M08 一镜一片段的声音/合并/恢复 | 既有链回归通过；两态未拆（§7） |
| 6. 片段拆分能力 | 未做（§6） |
| 7. M09＋M10 影子与统一评阅 | 影子表见 `M09-M10-delivery.md` §3 |
