# M09–M10：职责迁移影子评估与统一交付（2026-11-12）

本文件是统一收口包的收口文档：**逐项回答「承诺做到哪一步」**，并把不能在本包内
完成的部分明确标成待办，不冒充已完成。

## 1. 总判据与结论

计划的总完成条件是：

> 修订链能够正确修改，执行链能够忠实消费，参考与请求能够对应，时间轴能够离线验证。

| 判据 | 结论 | 依据 |
|---|---|---|
| 修订链能够正确修改 | **是**（离线、桩响应） | M02：5 条路径经实际入口通过；U6 候选 `84ff4112… → abfd217d…`，`accepted=true` |
| 执行链能够忠实消费 | **是**（既有 S03/S04 链，回归通过） | `tests/step02a_frame_gen`、`tests/step02_video_gen` 回归 |
| 参考与请求能够对应 | **部分** | 首帧用途口径已修（M06）；提示词仍读不到**最终**参考清单（结构性缺口，见 §3 M06） |
| 时间轴能够离线验证 | **是**（既有 S06 链，回归通过） | `tests/step04_merge`、样片 `artifacts/s06/sample.mp4` |

## 2. 逐模块闭合矩阵

| 模块 | 状态 | 交付物 / 证据 | 未完成部分 |
|---|---|---|---|
| **M01** 冻结证据与归因 | **完成（离线）** | `M01-evidence-and-attribution.md`、`tools/build_step01_call_index.py`、`artifacts/m01/call_index.{json,md}`、`tests/tools/test_step01_call_index.py`（4 条） | 当前 `time_ellipsis_legal` 未再真实发送 → 模型表现记为未验证 |
| **M02** 修订入口 | **完成（离线）** | `M02-revision-entry.md`、`repair_fields.frame_layout_companions`、台账 `authorization`/`postprocess_reverted`、`tests/step01_script_gen/test_m02_revision_entry.py`（7 条）、`tools/build_m02_u6_candidate.py`、`artifacts/m02/*` | 真实模型遵从、多轮收敛未验证 |
| **M03** 裁决与残余 | **完成（离线、影子）** | `M03-issue-ledger.md`、`step01_script_gen/issue_ledger.py`、`artifacts/m03/issue_status.{json,md}`、`tests/step01_script_gen/test_issue_ledger.py`（9 条）；**复核 P4**：`execution_semantics` 不再因缺「行动」标签拒收合法省略 | 未接进提交门（准入语义变更，走 §4 GATE） |
| **M04** 共用执行绑定 | **既有能力，回归通过** | `shared/execution_contract.py`、`shared/execution_semantics.py`、`tests/shared/test_execution_contract.py` / `test_execution_semantics.py` | 未新增；绑定字段未覆盖 M02 新台账（两者职责不同，未合并） |
| **M05** 画面计划与素材 | **部分（复核后已补主缺口）** | 画面计划：`step02a_frame_gen/execution_plan.py`（S03）；**计划已成为生成参数来源**（`execution.unit` + `verify_plan_execution` + `gen_all_frames` 消费，见 `review-round2-2026-11-12.md` §2）；素材 bank：`shared/assets_bank.py` | **缺素材导入工具**：无「来源/许可/摘要/用途/继承与排除/派生/候选状态」记录（§3 M05） |
| **M06** 参考绑定与提示词 | **部分** | 首帧用途口径修正 + `tests/step02_video_gen/test_m06_reference_purpose.py`（3 条） | 提示词构建**早于**参考清单定稿，读不到最终列表（§3 M06） |
| **M07** 声音与时长 | **既有能力，回归通过** | `shared/voice_preparation.py`、`step03_tts_audio/main.py`、`tests/step03_tts_audio/test_voice_preparation.py` | 真实 TTS 音色/质量未验证 |
| **M08** 合并与恢复 | **既有能力，回归通过** | `step04_merge/timeline.py`、`tests/step04_merge/test_timeline_acceptance_s06.py`、`artifacts/s06/sample.mp4` | 真实成片、发布中断/并发事务未验证 |
| **M09** 影子迁移 | **部分** | 本文 §3 影子表 | 正式门禁未切换（§4 GATE） |
| **M10** 统一交付 | **部分** | 本文 §5 交付索引、§6 未验证清单 | 视频模型选型与真实效果比较（按计划后置） |

## 3. 影子评估表（逐项）

格式：`原检查 → 新责任阶段 → 实际入口 → 正例 → 反例 → 失败回传 → 是否具备切换条件`。

| # | 原检查 | 新责任阶段 | 实际入口 | 正例 | 反例 | 失败回传 | 可切换 |
|---|---|---|---|---|---|---|---|
| 1 | 重复文本（逐字） | Step01 确定性（未迁移） | `script_findings._duplicate_findings` | `test_script_findings` 重复正例 | 非相邻不报 | 影子报告 `matched_findings` | 否（本就不在门禁） |
| 2 | 道具状态链 | Step01 确定性（未迁移） | `_prop_chain_findings` | 脱手后回手 | 显式时间跳跃豁免 | 影子报告 | 否 |
| 3 | 图片实际可见性 | Step02a | `execution_plan.verify_frame_observations` | `test_execution_plan` 在场正例 | 画外人物被判在场 | 计划 `unverified` 列表 | **是**（已有入口） |
| 4 | 动作要求进入冻结请求 | Step02b | `execution_requirements` | 片段事件齐备 | 事件丢失/顺序错 | 四类路由 | **是** |
| 5 | 声音实际长度进入预算 | Step03 | `voice_preparation` | 实测长度入窗口 | 音频长于窗口 | 定向反馈到声音单元 | **是** |
| 6 | 最终时长与同步 | Step04 | `build_timeline_acceptance` | 样片 6.17s 通过 | 越界/缺轨/超长 | 合并前阻塞 + 合成后复核 | **是** |
| 7 | **「本次没报出」=「已解决」** | Step01 提交门（拟迁移） | `issue_ledger.build_issue_ledger` | 4 条仍报出、0 条可关闭 | 未报出只记 `not_detected` | 台账 `status` | **否**：会改变准入语义，须 §4 GATE |
| 8 | **审计修复 vs 景别跨度约束** | Step01 后处理（拟迁移/裁决） | `constraints/framing_jump.py` | 修 prompt 后不再跳变 | 修复被改回原值（`postprocess_reverted`） | 新台账 `postprocess_reverted` | **否**：两条判据冲突，须裁决 |

第 7、8 行是本包**新发现**的两处冲突，都只记账、未改判据：

- **#7**：`resolved = before_keys - after_keys` 让「模型沉默」等于「修好」。M03 的台账
  已在离线上证明这条会把 3/7 确认缺陷读成「消失」。
- **#8**：`framing_jump._shot_size_level` 按词表顺序做**子串匹配**，`大远景` 先命中
  `远景`；与下一镜 `中近景` 差 4 档触发改写，`replace("远景","特写")` 作用在
  「大远景」上**恰好还原成「大特写」**——把审计刚修好的 prompt 改回原值，且此前无记录。

## 4. 需要裁决的事项（GATE）

| 事项 | 依据 | 具体批准对象 | 解锁条件 |
|---|---|---|---|
| 把 `issue_ledger.closed` 接进提交门 | 准入语义变化：从「本次没报出」改为「有证据才算关闭」 | 提交点接受条件新增一项 | 用本包 `artifacts/m03` 的正反例 + 一轮真实运行对照 |
| `framing_jump` 的景别匹配改成长词优先 / 或让约束跳过本轮已授权字段 | 判据冲突（一致性 vs 跨度） | 约束行为变更 | 影子对照：改前后同一稿件的 `postprocess_reverted` 归零且不新增告警 |
| 提示词改为「先定稿参考、再构建正文」 | 结构性缺口（M06） | `prompt_builder` 调用顺序 | 见 §3 M06 的改造范围 |
| 正式候选采纳 | 产物采纳 | 采纳动作本身 | 本包未做 |

## 5. 交付索引

| 交付物 | 位置 |
|---|---|
| 调用索引（8 次冻结 + 20 次复跑） | `artifacts/m01/call_index.{json,md}` |
| M01 归因修正与计数重算 | `M01-evidence-and-attribution.md` |
| M02 修订入口与五条路径 | `M02-revision-entry.md` |
| U6 离线修订候选（可读差异） | `artifacts/m02/diff.md`、`candidate.revised.json`、`repair_diag.json` |
| M03 已确认问题台账 | `artifacts/m03/issue_status.{json,md}` |
| M06 首帧用途口径 | `tests/step02_video_gen/test_m06_reference_purpose.py` |
| 既有执行链产物 | `artifacts/s03/visual_plan.json`、`s04/video_request.json`、`s05/voice_preparation.json`、`s06/sample.mp4`、`s06/timeline_acceptance.json`、`s08/*` |

## 6. 未验证清单（明确不冒充通过）

1. 当前 `time_ellipsis_legal` 的真实模型表现（夹具已改版，未再发送）。
2. 修订轮真实模型遵从：桩响应是按 S01 裁决**构造**的合法修法，不是模型输出。
3. 多轮修订收敛（本包只跑 `max_rounds=1`）。
4. 提示词删减的画风/起点质量收益（无真实对照；官方文档链接未在本轮材料内）。
5. R2V 首帧锁定：代码层**没有**保证，本包只把提示词口径改成「待验证」。
6. 真实 TTS 音色、真实成片、发布中断/并发事务。
7. 视频模型选型与效果比较（按计划后置）。

## 7. 运行记录

```powershell
# M01 索引
python tools/build_step01_call_index.py
# -> calls=28, by_phase={audit_ab:6, audit_ab_rerun:20, repair:1, final_audit:1},
#    drift={checked:6, match:4, drift:2}

# M02 U6 离线候选（桩响应，外部数据集显式声明）
python tools/build_m02_u6_candidate.py
# -> accepted=true, targets=[shot_1, shot_11, shot_6, shot_7], touched=3,
#    revision 84ff4112… -> abfd217d…, changed_fields=5,
#    postprocess_reverted={shot_1:[prompt], shot_6:[prompt]}

# M03 台账
python tools/build_m03_issue_status.py
# -> confirmed=7, still_reported=4, not_detected=3, closed=0

# 回归（本机，2026-11-12）
python -m pytest tests/step01_script_gen tests/tools/test_step01_call_index.py -q
# -> 4255 passed / 278 skipped / 4 xfailed / 0 failed
python -m pytest tests/shared/test_execution_contract.py tests/shared/test_execution_semantics.py `
  tests/step02a_frame_gen/test_execution_plan.py tests/step02_video_gen/test_execution_requirements.py `
  tests/step03_tts_audio/test_voice_preparation.py tests/step04_merge/test_timeline_acceptance_s06.py `
  tests/integration/test_s07_shadow_and_candidate_revalidation.py -q
# -> 84 passed
python -m pytest tests/step02a_frame_gen tests/step03_tts_audio tests/step04_merge `
  tests/integration/test_s07_shadow_and_candidate_revalidation.py -q
# -> 205 passed / 4 skipped
python -m pytest tests/step02_video_gen tests/step02b_video_gen -q
# -> 152 passed / 2 failed（均为既有问题，见下）
```

已知既有失败（**与本包改动无关**，已用 `git stash` 在 HEAD 上复现）：

- `tests/step02b_video_gen/test_production_trace.py::test_step02b_entry_keeps_success_and_cache_semantics`
  在 HEAD `b942098` 上同样失败（`assert task is not None`）。
- `tests/step02b_video_gen/test_step02b_report_wp5.py::test_step02b_report_is_produced_by_the_real_writer`
  单独运行通过，合并运行失败 → 顺序依赖，属既有问题。
