# 任务：核查当前仓库（HEAD 530fdbb）仍真实存在的问题

## 背景（已核实）
当前 HEAD 是 `530fdbb`（fix(step01/step02a): 复核第三轮）。此前已提交：
- `e4712c7` 已知缺陷修复（T1/T2 完成，T3 冻结待裁决）
- `8c2a214` 夹具内容无关化
- `b942098` Seedance 官方提示词实践接入
- `ae26d11` M01–M03/M06 收口（9600+ 行文档/工件）
- `38217a4` 复核第二轮
- `1237553` OpenCode Go 端点迁移
- `530fdbb` 复核第三轮

收口文档 `docs/step01-layered-execution/M09-M10-delivery.md` 自报了 4 个 GATE 待决策、5 个未完成项、7 条未验证清单。但**文档是上一轮执行者自报，可能乐观或与代码脱节**。本次任务是独立核查：文档说的"缺口/风险"在当前代码里是否真的存在，并发现文档没提到的真实问题。

## 你的工作（只读，不要修改任何文件、不要提交、不要跑真实外部调用）
1. 用 git/grep/Read 核实以下每一项在**当前代码**里是否真实存在（给文件:行号证据，不要只信文档）：
   - **A. `ADDED_DIALOGUE` 生产死码**：`step01_script_gen/script_findings.py` 的 `scan_script` 新增了 `source_text` 入参，但生产调用方（`build_shadow_report` / `deterministic_checks` / 其他 `scan_script(` 调用点）是否真的传了 `source_text`？grep 所有 `scan_script(` 调用，列出哪些传了、哪些没传。结论：该码在真实审计路径是否仍不产出。
   - **B. `framing_jump` 子串匹配**：复核第三轮声称已改"最长匹配"。读 `step01_script_gen/constraints/framing_jump.py` 与 `validation.py`，确认 `shot_size_level` / 相关匹配是否真的改成最长匹配，还是仍可能 `大远景` 先命中 `远景` 导致被改回。给当前实际代码证据。
   - **C. 参考清单→prepare 映射缺口（M06）**：`step02_video_gen/prompt_builder.py` 构建 prompt 时，是否能拿到**最终定稿**的参考清单（materials/purpose 列表）？还是仍早于参考清单定稿？核实 `build_m06_reference_purpose` / `_build_r2v_purpose_list` 的数据来源时机。
   - **D. 声音两态拆分（P7）**：`step03_tts_audio/main.py` / `shared/voice_preparation.py` 是否做了"准备态 / 视频后确认态"两态？还是仍是单一探测逻辑？
   - **E. `issue_ledger` 是否接进提交门**：grep 提交门入口，确认 `step01_script_gen/issue_ledger.py` 的 `closed` 是否真的没被任何提交/采纳流程调用（文档说"本包不动"）。
   - **F. 未提交文件**：`tools/build_m02_u6_candidate.py` 有 +26 行未提交改动（投影后残余检查）。读它的 diff（`git diff -- tools/build_m02_u6_candidate.py`），判断这改动是收口工作的合理延续还是误留，给出"建议提交 / 建议丢弃 / 存疑"的结论与理由。
   - **G. 当前测试红绿**：在受控环境下跑最相关的几个测试目录（避免全仓触发 safe-delete 批量门导致误红），报告真实 failed/skipped 数量。重点：`tests/step01_script_gen`、`tests/step02a_frame_gen`、`tests/step02_video_gen`、`tests/shared`。若全仓跑会撞环境问题，请改成小批独立重跑并说明。
   - **H. 文档未覆盖的真实问题**：扫一遍 step01/step02a/step02b 关键路径，找文档 M09-M10 未列出、但你判断是真实隐患的点（如：跨镜状态继承、时间轴恢复、并发、TTS 时长窗口、参考图顺序与 prompt 指代一致性等），每条给文件:行号与一句话风险。

2. 产出一份**优先级排序的"当前开放问题"清单**（高/中/低），每条含：问题描述、文件:行号证据、是否文档已自报、建议下一步。不要写代码，只给结论与证据。

## 输出
把结论写入 `C:/myFloders/git/aiVideo/.pytest_tmp/dsh_tasks/remaining_problems_result_20261113.md`，并在末尾附"文档自报 vs 代码实测"对照表（自报缺口哪些被证实、哪些已修复但文档未更新、哪些文档夸大了）。

## 停止条件
纯只读分析。若发现某文档声称的修复在代码里根本不存在（重大脱节），明确标红报告，但不要自行改代码。
