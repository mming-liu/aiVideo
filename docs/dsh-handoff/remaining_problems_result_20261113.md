# 当前仓库开放问题独立核查（HEAD `530fdbb`）

- 核查时间：2026-11-13
- 仓库：`C:\myFloders\git\aiVideo`
- HEAD：`530fdbb27dff427b657658a2f173db0e1158df7a`（`fix(step01/step02a): 复核第三轮`）
- 工作方式：只读代码、只读 git、受控离线测试；没有发起任何真实模型/TTS/视频外部调用。
- 工作树：核查前已有 `tools/build_m02_u6_candidate.py` 的 +26 行未提交改动；核查后仍只有这一处已跟踪改动（另有既有未跟踪文件）。本次只新增本报告及测试临时产物，没有改源码、没有提交。

## 0. 结论摘要

当前代码里**真实存在**的高优先问题有三个：

1. **M03 的 `issue_ledger` 仍未接进提交门**：提交门仍以 `before_keys - after_keys` 判“已解决”，文档所说的准入语义变更没有发生。
2. **Step02a 的 `verify_plan_coverage` 在“全量运行”时存在覆盖洞**：`only_indices=None` 时它只遍历计划里已有的条目，计划漏掉的镜头不会被发现；随后主入口又把 `only_indices` 收窄为计划镜头，漏镜被静默跳过。
3. **Step02b 的 `fit_refs` 仍把参考拼表写进生产 `shots/<idx>/`**：`--plan-only` 没有生产快照守卫；候选模式虽然有快照守卫，但写入发生在守卫之前，发现时已经污染正式目录。该问题在 r2v 且参考数超模型上限时触发。

另外确认：

- `ADDED_DIALOGUE` 在真实审计/影子报告路径**仍不产出**；M03 文档已自报，但 M09-M10 没有列。
- `framing_jump` 的“大远景被当成远景”具体缺陷**已修**（最长匹配生效，测试通过）；但匹配没有锚定到声明景别，且 `近景` 不在词表、多样性检查仍是首个子串匹配。
- 提示词构建并没有“早于参考清单选择”：`_build_r2v_purpose_list` 拿到的是 `_collect_r2v_references` 已定稿的请求级列表。真正的残余缺口是 **provider `prepare_request` 之后仍可能拼表/丢弃素材**，而提示词的“图N/视频N”不会跟着重算；文档把这一点描述成“提示词构建早于参考清单”，不准确。
- 声音仍是单一 `build_voice_preparation` 逻辑，没有“准备态 / 视频后确认态”状态机。
- 未提交的 `tools/build_m02_u6_candidate.py` 是复核第三轮收口的合理延续，**建议提交**，不是误留。

## 1. A：`ADDED_DIALOGUE` 生产死码

### 结论

**真实存在。** `scan_script` 新增了 `source_text` 参数，但生产调用方没有传；`ADDED_DIALOGUE` 在真实审计/影子路径不会产出。

### 证据

- 新增检查：`step01_script_gen/script_findings.py:1096-1139`，`_added_dialogue_findings` 在 `source_text` 不是非空字符串时直接返回 `[]`（`:1101-1102`）。
- `scan_script` 签名与调用：`step01_script_gen/script_findings.py:1146-1160`，只有显式传入 `source_text` 才调用 `_added_dialogue_findings`。
- 生产调用点 1：`step01_script_gen/audit_adjudication.py:206-210`，`deterministic_checks` 在 `findings is None` 时调用 `scan_script(script)`，没有 `source_text`。
- 生产调用点 2：`step01_script_gen/audit_adjudication.py:571-576`，`build_shadow_report` 调用 `scan_script(script)`，没有 `source_text`。
- 影子报告入口：`step01_script_gen/main.py:2959` 调 `_write_audit_adjudication(payload, script)`；`:2969-2982` 的 `_write_audit_adjudication` 不接受原文，最终 `:2981` 调 `build_shadow_report(audit, script, ...)`，没有传 `source_text`。

### 所有 `scan_script(` 调用分类

`grep -n "scan_script("` 全仓 40 处（含 baselines 历史副本）。当前源码中的有效调用：

| 调用位置 | 是否传 `source_text` | 性质 |
|---|---|---|
| `step01_script_gen/audit_adjudication.py:208` | 否 | 生产影子裁决 fallback |
| `step01_script_gen/audit_adjudication.py:575` | 否 | 生产影子报告 |
| `tests/step01_script_gen/test_script_findings.py:81, 90, 96, 102, 118, 129, 137, 152, 161, 169, 179, 195, 206, 228, 240, 245, 259, 265, 275, 287, 299, 306, 313, 319, 334` | 否 | 测试，覆盖其他 finding |
| `tests/step01_script_gen/test_script_findings.py:339, 350, 355, 365` | **是** | 仅测试 ADDED_DIALOGUE 正例 |
| `tests/step01_script_gen/narrative_family_builder.py:286` | 否 | 测试构造器 |
| `tests/step01_script_gen/test_narrative_family_genericity.py:133, 147, 175` | 否 | 测试 |

`baselines/2026-11-12-pre-layered/source_bc5c50e/script_findings.py:935` 是旧副本，不参与当前运行。

### 影响

`ADDED_DIALOGUE` 是诊断/影子码，不直接改变 blocking（`script_findings.py:23-31` 明确 shadow-only），所以不是“会放行坏稿”的高危门禁缺陷；但它让新增确定性检查在真实路径上**永远零产出**，会让报告看起来覆盖了“新增台词”，实际没有。

### 文档是否自报

- `docs/step01-layered-execution/M03-issue-ledger.md:71` 已明确写：“`scan_script` 的 `ADDED_DIALOGUE` 在两条生产调用点都不传 `source_text` → 生产死码。”
- `M09-M10-delivery.md` 没有列这一条。因此是 **M03 已自报、M09-M10 漏列**，不是文档完全没提。

### 建议下一步

把 `novel_text`/规划原文从 `_write_audit_adjudication` 传入 `build_shadow_report`，并让 `deterministic_checks` 接受同一 `source_text`；补一条“真实 `_write_audit_adjudication` 入口产出 ADDED_DIALOGUE”的入口测试，而不是只测 `scan_script` 函数。

## 2. B：`framing_jump` 的景别匹配

### 结论

**具体缺陷已修，但匹配仍有残余问题。**

- “`大远景` 先命中 `远景`”这一条已经不会再发生：`_shot_size_level` 现在按长度取最长匹配（`validation.py:5303-5317`），`[大远景]` 返回 `大远景`，`[大特写]` 返回 `大特写`；对应测试 `tests/step01_script_gen/constraints/test_m02_postprocess_no_revert.py:29-34` 通过。
- 但“最长匹配”是在**整段 prompt 任意位置**取最长词，不是锚定在声明景别处；合法 prompt 里同时出现“远景转中景”时，会被判成 `中景`。
- `近景` 不在官方 9 级词表，`_shot_size_level({"prompt": "[近景] ..."})` 返回 `None`，相邻景别跳跃检查直接跳过。
- `_check_shot_size_diversity` 仍按词表顺序取**第一个**子串，不是最长匹配。

### 证据

- `step01_script_gen/validation.py:5303-5317`：
  - 注释说“prompt 开头格式「[景别]」”；
  - 实际 `prompt = shot.get("prompt", "")`，遍历整段，`if sz in prompt and ... len(sz) > len(best)`。
- `step01_script_gen/constraints/framing_jump.py:92-110`：取 `lv1/lv2` 后 `prompt.replace(lv1, new_lv1, 1)`；如果 `lv1` 出现在动作描述中而不是开头景别，仍会替换第一处。
- 词表缺口：`step01_script_gen/validation.py:5293-5295` 的 `_SHOT_SIZE_ORDER` 没有 `近景`；`storyboard_refs.py:10-20` 的官方 `SHOT_SIZES` 也没有 `近景`；但 `step01_script_gen/split_visuals.py:30` 明确把 `近景` 当合法景别，`constraints/framing_prefix_fill.py:38-40` 也承认它是合法词。
- 当前冻结样本确实有 `[近景]`：`baselines/2026-11-12-pre-layered/step01_outputs/run_review_20261013/script_result.json:766` 与 `:1497`。
- 多样性检查首匹配：`step01_script_gen/validation.py:585-589`，按 `storyboard_refs.SHOT_SIZES` 顺序 `break`；`[大远景]` 会先命中 `远景`。

### 实测探针

```text
[近景] 张十三 => None
近景，船头 => None
[大远景] 荷塘 => 大远景
远景转中景，横移跟拍 => 中景
```

（`_shot_size_level` 直接调用；无外部依赖。）

### 影响

- `framing_jump` 仍可能把动作描述里的景别词当成当前景别，或在含多个景别词的合法 prompt 上判错。
- `近景` 镜头不参与 `_check_shot_size_jump` / `framing_jump`，当前冻结稿已有实例。
- 目前这些检查是 warning/约束级，不是 blocking；影响是“该报的景别跳跃不报/报错方向”，不是直接放行语义错误。

### 文档是否自报

- `M09-M10-delivery.md:53-55` 记录了旧的子串匹配缺陷；`review-round3-2026-11-12.md` 说已改最长匹配。
- 代码证明“大远景/大特写”这一具体反例已修；但文档没有提“未锚定声明景别、近景缺失、多样性首匹配”这三个残余。

### 建议下一步

按 prompt 的显式 `[景别]` 或文首声明位置解析，而不是全段最长；把 `近景` 映射到 `中近景`/`特写` 之间的明确档位（或明确声明不支持并统一过滤）；让 `_check_shot_size_diversity` 复用同一个解析函数。

## 3. C：参考清单 → prepare 的映射缺口

### 结论

文档“提示词构建早于参考清单定稿，读不到最终列表”这个表述**不准确**。`prompt_builder` 内部确实先完成参考选择，再生成用途清单；真正未闭合的是 **provider `prepare_request` 之后**的素材改写没有回写/复核到提示词。

### 证据

- `step02_video_gen/prompt_builder.py:1509-1515`：`_collect_r2v_references(...)` 收集参考，随后追加视频参考，得到请求级 `reference_paths`。
- 同一函数 `:1637-1650`：在 `reference_paths` 已确定后，才调用 `_build_r2v_purpose_list(..., ref_paths=reference_paths, ...)`，并拼到 `full_prompt`。
- `_collect_r2v_references` 末尾 `:1930-1948` 调 `fit_refs`，拼表结果就是返回给 `_build_r2v_purpose_list` 的列表；所以实际顺序是「选择/拼表 → 用途清单」，不是文档所说的「用途清单早于定稿」。
- 真正的时间顺序：`step02_video_gen/submit_poll.py:1450` 先 `make_request`（内部构建 prompt），`:1474` 才 `svc.prepare_request(req, ...)`。
- provider 在 prepare 内仍可能改素材：`_video_gen/volcengine_provider.py:331-335` 调 `_effective_prompt` + `_plan_content`；`:474-551` 对超限参考图 `compose`，对超限/不支持视频 `dropped`；`_video_gen/agent_plan_provider.py:232-233` 同构。
- 冻结核对只比较 prepared 与 frozen：`step02_video_gen/request_plan.py:267-299`，没有检查 `prepared.prompt` 里的“图N/视频N”是否仍对应 `prepared.materials`。
- `build_m06_reference_purpose` 在仓库中**不存在**（全仓 grep 无匹配）；实际实现名是 `_build_r2v_purpose_list`。

### 影响

- 请求级列表和提示词用途在正常路径下是对齐的；文档把“prompt builder 顺序”当成缺口，夸大了问题。
- 但在 provider 拼表/丢视频/能力降级后，提示词可能仍写“图N/视频N”，实际 material 已变；`verify_prepared_frozen` 只保证 prepared 与 frozen 一致，不保证 prompt 的指代与 prepared 素材一致。
- 当前 `.env.content` 是 `VIDEO_REFERENCE_MODE=image`，该路径不是当前主路径；r2v 或未来切换时才是活跃风险。

### 文档是否自报

`M09-M10-delivery.md:16, 28, 63` 自报“提示词仍读不到最终参考清单”；`M01-evidence-and-attribution.md:162-164` 又说 `fit_refs` 是定稿点。两处口径不一致。代码实测更接近后者，前者应改为“prepare 后素材改写未回写提示词”。

### 建议下一步

在 `prepare_request` 之后用 `prepared.materials` 重建/校验“图N/视频N”块；至少增加 `prompt_reference_material_mismatch` 检查。若保留 prompt builder 现有顺序，应把文档改为“请求级列表已定稿，prepare 级仍未闭合”。

## 4. D：声音“准备态 / 视频后确认态”两态

### 结论

**没有两态拆分，仍是单一探测/构建逻辑。**

### 证据

- `shared/voice_preparation.py:129-270` 只有一个 `build_voice_preparation`；通过是否给 `audio_paths` 区分“计划/实测”，但没有状态字段或状态机。
- `:160-165`：`measured` 拿不到时回退 `declared`；`:185-187` 只把 `verified` 记成 false；`:261` 的 `status` 只看 `conflicts`，不看 `unverified`。
- `:201-215`：`native_dialogue` 没有 `verified=true` 时直接记 `native_dialogue_unverified`。
- `step03_tts_audio/main.py:150-165`：合成后调一次 `build_voice_preparation` 并写 attempt；`:390-427`：plan-only 调同一个函数写 plan 目录。
- 没有“视频返回后把 native_dialogue 标记为已确认”的代码路径；`grep native_dialogue_unverified` 只有 `shared/voice_preparation.py` 和 `shared/execution_semantics.py` 的检测。

### 影响

- 原生对白在视频生成前无法确认，plan 阶段必然 `native_dialogue_unverified`；视频生成后也没有确认态回填，报告会持续把“未确认”当阻塞/未验证。
- TTS 音频探测失败时，`actual_sec` 回退声明值，`status` 仍可能 `ready`；`unverified` 只是列表，不参与 `status`。Step04 的 `timeline_acceptance` 会在音轨实测阶段再拦，但 Step03 报告本身可能被读成通过。

### 文档是否自报

- `review-round3-2026-11-12.md:116` 已写“声音「准备态 / 视频后确认态」两态拆分：本包未完成项”。
- `M09-M10-delivery.md:29` 只写“既有能力，回归通过”，没有强调两态未做；两处口径不一致。

### 建议下一步

明确两个状态：`prepared`（只有声明/预算）与 `confirmed_after_video`（有实际音轨、说话人、时长、窗口核对）；`native_dialogue` 只在后者可关闭。`unverified` 应参与消费端 gate，或至少让 `status` 区分 `ready_unverified`。

## 5. E：`issue_ledger` 是否接进提交门

### 结论

**没有接。** `issue_ledger` 目前只有离线工具/测试消费者，提交门仍用旧的 `resolved = before_keys - after_keys`。

### 证据

- `step01_script_gen/issue_ledger.py:91-213` 是纯函数；`:49` 定义 `_CLOSED_STATUSES`，`:201` 汇总 `closed`。
- 生产提交门：`step01_script_gen/main.py:6639-6713` 的 `_repair_acceptance_decision`，`:6670-6673` 计算 `before_keys/after_keys/resolved`，`:6703-6712` 的接受条件没有任何 ledger 输入。
- 最终提交：`step01_script_gen/main.py:4178-4244` 只按 `blocking or not audit_ok` 分支；`:4260-4262` / `:4301-4303` 用 `require_semantic_gate`，没有 `build_issue_ledger`。
- 全仓 `.closed` 调用：只有 `tests/step01_script_gen/test_issue_ledger.py` 和 `tools/build_m03_issue_status.py:46` 的 `build_issue_ledger`；没有提交/采纳流程。
- `tools/build_m03_issue_status.py` 是唯一离线消费者（`:35, :46, :61-63`）。

### 影响

同一份未变稿在两次审计之间少报几条，仍可能被提交门判成“resolved”；M03 文档自己说的“模型沉默=已解决”风险仍然成立。这是准入语义问题，不是普通诊断。

### 文档是否自报

- `M09-M10-delivery.md:61` 把它列为 GATE。
- `M03-issue-ledger.md:81-84` 明确写“尚未接进提交门”。
- 结论：文档自报属实，代码未接线。

### 建议下一步

按 GATE 裁决后，把 `step01_audit.blocked.json`、人工裁决集、`fixed_evidence`/`withdrawn` 喂给 `build_issue_ledger`，把 `closed` 作为提交附加条件；在此之前不要把它写成已生效门禁。

## 6. F：未提交的 `tools/build_m02_u6_candidate.py`

### 结论

**建议提交。** 这 +26 行是复核第三轮“投影后残余”验证的合理延续，不是误留。

### 证据

- `git diff -- tools/build_m02_u6_candidate.py`：仅新增 26 行（无删除）。
- 新增内容：
  - `tools/build_m02_u6_candidate.py:167-182`：对 `_diff_rows` 的每个变化字段，在 `m.project_for_commit(revised, novel)` 之后逐字段比对，记录 `proj_residual`。
  - `:228-241`：把“投影后残余”写进 `diff.md`；无残余时明确写“全部成立”。
- 文档引用：`review-round3-2026-11-12.md:93,130` 和 `M09-M10-delivery.md:24` 都以 `tools/build_m02_u6_candidate.py` 的“投影 7/7 成立”作为证据；如果工具改动不提交，这个证据不可复现。
- 改动只影响离线工具输出，不触碰生产路径；没有新增外部调用。
- 小注意：`proj_errors` 非空时只写进报告，不把工具判失败；这是诊断工具口径，不是生产缺陷。若希望工具在投影错误时退出非零，可另立小改动，但不影响“建议提交”。

### 建议下一步

提交该工具改动，并把 `.pytest_tmp/m02_u6_out/diff.md` 的结果作为复核第三轮的离线证据；不要把它和源码修复混在一个语义不清的提交里。

## 7. G：当前测试红绿

### 受控环境

- Python：`C:\Users\ee54\AppData\Local\Python\pythoncore-3.14-64\python.exe`（pytest 9.1.1）。
- 每个目录单独 `--basetemp`、`-p no:cacheprovider`。
- 先按原样跑了一次；再在测试进程内设 `CODEBUDDY_SAFE_DELETE_ENABLED=0` / `CODEBUDDY_SAFE_DELETE_SANDBOX=0` 重跑。**只关闭测试进程的 safe-delete shim，不改仓库文件策略、不删生产文件。**
- 所有测试均走仓库自带 `_no_external_transport` 离线护栏；没有真实外部调用。

### 读数

| 范围 | 结果 | 备注 |
|---|---|---|
| `tests/step01_script_gen`（原样） | **4219 passed / 47 failed / 278 skipped / 4 xfailed**（305s） | 47 条几乎全是 `SAFE_DELETE_BULK_CONFIRM_REQUIRED`（`SystemExit: 1`），另有 adoption receipt 的 `chapter_dir_mismatch` 级联；不是业务断言失败 |
| `tests/step01_script_gen`（关闭 safe-delete shim） | **4266 passed / 0 failed / 278 skipped / 4 xfailed**（270s） | 真实绿；比文档的 4255 多 11 条（新增测试/收集差异） |
| `tests/step02a_frame_gen` | **102 passed / 4 skipped / 0 failed**（43s） | 4 skip 为 `test_wp3_58c1b6bd...` 外部数据集缺失 |
| `tests/step02_video_gen` | **71 passed / 0 failed**（17s） | 文档写的 152 应是 step02+step02b 合并口径 |
| `tests/shared` | **1377 passed / 1 skipped / 0 failed**（40s） | 1 skip 为 `U3_EVIDENCE_DIR` 未指定 |
| `tests/step02b_video_gen` | **82 passed / 1 failed / 0 skipped**（69s） | 失败：`test_production_trace.py::test_step02b_entry_keeps_success_and_cache_semantics` |
| `tests/step03_tts_audio` | **46 passed / 0 failed**（20s） | 仅作 D 的回归背景，非用户点名范围 |

### 关于 step01 的 47 条原样失败

失败栈的共同点是：

```text
C:\Program Files\WorkBuddy\...\sitecustomize.py: _check_bulk_delete_guard
[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] ...
SystemExit: 1
```

这是测试进程累计删除数达到 safe-delete 批量阈值后的环境门，不是 `step01` 断言失败；关闭该 shim 后 47 条全部转绿。另有几条 adoption receipt 的 `chapter_dir_mismatch`，是测试只 monkeypatch `m.CHAPTER_DIR`、未同步 `shared.paths` 的测试隔离问题，也不是生产代码失败。

### 关于 step02b 的 1 条失败

- `tests/step02b_video_gen/test_production_trace.py:164-169` 的 shot 只有 `motion_desc`，没有 `ff_desc`/`lf_desc`。
- `step02_video_gen/execution_requirements.py:216-218` 因此产生 `start_end_state_mismatch`。
- `step02_video_gen/submit_poll.py:1510-1519` 的 `validate_requirements` 把它当阻塞，`_process_one_shot_skip_frames` 返回 `None`，测试的 `assert task is not None` 失败。
- 这更像**测试夹具未跟上新的执行要求门**，不是产品行为错误；但它说明文档把它称为“既有失败、与本包改动无关”并不准确。
- 文档列的第二条 `test_step02b_report_wp5.py::test_step02b_report_is_produced_by_the_real_writer` 在本次单独运行中**通过**；文档的“2 failed”已过时。

### 证据日志

- `.pytest_tmp/dsh_tasks/pytest_step01_20261113.log`（原样，47 failed）
- `.pytest_tmp/dsh_tasks/pytest_step01_nodel_20261113.log`（关闭 shim，0 failed）
- `.pytest_tmp/dsh_tasks/pytest_step02a_20261113.log`
- `.pytest_tmp/dsh_tasks/pytest_step02_20261113.log`
- `.pytest_tmp/dsh_tasks/pytest_shared_20261113.log`
- `.pytest_tmp/dsh_tasks/pytest_step02b_20261113.log`
- `.pytest_tmp/dsh_tasks/pytest_step03_20261113.log`

## 8. H：文档 M09-M10 未列出的真实隐患

### H1（高）Step02a 全量运行的覆盖洞

- 证据：`step02a_frame_gen/execution_plan.py:616-618` 在 `only_indices is None` 时 `wanted = sorted(entries)`，只检查计划里已有条目；`step02a_frame_gen/main.py:105-108` 随后把 `only_indices` 收窄为计划中的 `_planned_shots`。
- 实测探针：

```text
full_run_issue= []
explicit_issue= [{'code': 'plan_coverage_missing_shot', ... '镜头 2 不在画面计划内'}]
```

- 一句话风险：计划漏掉一个镜头时，全量运行不会报 `plan_coverage_missing_shot`，而是静默只生成计划内镜头；这与 `verify_plan_coverage` 注释“缺单元必须零调用拒收”相反。
- 文档：M09-M10 自报“复核第三轮补充 verify_plan_coverage”，但没有提全量路径这个洞。

### H2（高，条件触发）Step02b plan-only/候选写生产 `shots/`

- 证据：
  - `step02b_video_gen/main.py:533-545`：`--plan-only` 只写 candidate `plans/`，没有 production snapshot。
  - `step02b_video_gen/main.py:451-456`：`build_plan` 调 `build_frozen_for_shot`。
  - `step02_video_gen/submit_poll.py:456-459`：`build_frozen_for_shot` 调 `_make_request`。
  - `step02_video_gen/prompt_builder.py:1945-1948`：`fit_refs` 的 `sheet_dir` 是 `shot_work_dir(...)`（生产 shots）。
  - `shared/ref_compose.py:543-563`：参考数超过 `limit` 时 `mkdir` 并写 `ref_sheet_*.png`。
  - `step02_video_gen/submit_poll.py:319-331`：`_get_shot_hash` 在 r2v 下也调用 `_collect_r2v_references`，所以“算哈希”也可能写表。
  - `step02b_video_gen/main.py:302-315` 的 `production_snapshot` 包含 `shots/`；`:658-664` 只在 `_run_candidate` 前后比较，`_plan_only` 没有这层守卫。
- 一句话风险：r2v 且参考数超限时，plan-only 会静默改生产 `shots/`；候选模式会先写生产、再在跑完报 `isolation_violation`，此时污染已发生。
- 文档：M09-M10 只写“参考图清单”和“隔离”，没有列这个写路径。

### H3（中）`_shot_size_level` 的“近景”盲区与全段最长匹配

- 证据：`validation.py:5293-5295` 无 `近景`；`split_visuals.py:30` 有 `近景`；`validation.py:5312-5317` 全段最长匹配；`validation.py:585-589` 多样性检查仍首匹配。
- 当前样本：`baselines/2026-11-12-pre-layered/step01_outputs/run_review_20261013/script_result.json:766,1497` 是 `[近景]`；`_shot_size_level` 返回 `None`。
- 一句话风险：合法 `近景` 镜头不参与景别跳跃检查；含“远景转中景”等动作描述的 prompt 会被判成后一个景别。
- 文档：M09-M10 只讨论“大远景 vs 远景”，没有提 `近景` 和全段匹配。

### H4（中）Step02b 缓存可绕过执行要求门

- 证据：`step02b_video_gen/main.py:263-266` 构建 `_reqs` 后只 `logger.warn`；`:277-283` 先做 `cache_decision`，命中就直接返回 `None`；真正的 `validate_requirements` 在 `step02_video_gen/submit_poll.py:1510-1519`，只在提交路径执行。
- `execution_requirements.py:209-214` 会把 `visual_plan_blocked` / `visual_plan_missing` 放进 `_reqs.issues`。
- 一句话风险：计划已阻塞或首尾状态不成立时，只要旧视频缓存命中，就不会走到 `validate_requirements`，缓存复用了本应被拦的产物。
- 文档：M09-M10 没有列缓存与执行要求门的先后关系。

### H5（中）TTS 探测失败时回退声明值，`status` 仍可能 ready

- 证据：`shared/voice_preparation.py:160-165` 在 `_probe_duration` 失败时把 `actual` 回退到 `declared`；`:185-187` 只记 `verified=false`；`:261` 的 `status` 只看 `conflicts`；`:269` 的 `unverified` 不参与 `status`。
- 一句话风险：ffprobe 不可用/音频不可读时，Step03 的 `voice_preparation.json` 可能仍报 `ready`，把“未实测”留给 Step04 才发现。
- 文档：`review-round3` 只列“两态未拆”，没有列“探测失败回退声明值”的具体路径。

### H6（中）`postprocess_reverted` 仍只是台账，不是门禁

- 证据：`step01_script_gen/main.py:6166-6193` 明确写“只记账、不改变接受判据”；`:9607-9610` 把 `_authorized_changes_lost` 写进诊断；`_repair_acceptance_decision`（`:6639-6713`）没有使用它。
- `tools/build_m02_u6_candidate.py:167-182` 只是离线工具检查投影后残余。
- 一句话风险：未来若再有后处理 writer 把已授权修复改回，修复轮仍可能接受并提交；U6 工具能发现，但生产提交门不会。
- 文档：M02/M09 已把它列为 GATE/只记账，属于自报；但 M09-M10 没有把它放进“当前开放问题”清单。

### H7（低）`issue_ledger` 身份只到“镜头+字段”，不是“镜头+字段+事实”

- 证据：`step01_script_gen/issue_ledger.py:60-70` 的 `issue_identity` 只返回 `(shot_id, field_path)`；模块 doc `:22-23` 却说“镜头+字段+事实”。
- 一句话风险：同一字段上的两个不同事实会被合并成同一身份，观察侧任意一条命中都会把确认项记成 `still_reported`；因为关闭仍需证据，不会误关闭，但会制造假阻塞/错误统计。
- 文档：M03 文档 `:36` 实际写的是“（镜头, 字段）”，与模块 docstring 不一致；M09-M10 没列。

### H8（低）`verify_plan_execution` 对缺 `path`/`sha256` 的参考条目静默跳过

- 证据：`step02a_frame_gen/execution_plan.py:535-541`：`if not expected or not path: continue`。
- 一句话风险：被篡改的计划只要删掉参考条目的 `path`/`sha256`，就不会触发 `plan_reference_missing`/`plan_reference_drift`；计划自洽检查仍可能通过。
- 文档：M09-M10 说“参考摘要重算”，没有列“字段缺失即跳过”的边界。

## 9. 优先级排序的“当前开放问题”清单

| 优先级 | 问题 | 证据 | 文档已自报？ | 建议下一步 |
|---|---|---|---|---|
| **高** | `issue_ledger.closed` 未接进提交门，仍用 `before-after` 判解决 | `main.py:6670-6713`、`:4178-4244`；`issue_ledger.py:91`；仅 `tools/build_m03_issue_status.py` 消费 | 是（M09:61、M03:81） | 按 GATE 裁决后接提交门；先补真实入口正反例 |
| **高** | Step02a 全量运行漏镜不报，`only_indices` 被收窄为计划镜头 | `execution_plan.py:616-618`；`step02a_frame_gen/main.py:105-108`；探针 `full_run_issue=[]` | 否（M09 只说已补 coverage） | `verify_plan_coverage` 在 `only_indices=None` 时用 `script["shots"]` 全集比较；补“漏镜”用例 |
| **高（条件）** | Step02b `fit_refs` 在 plan-only/候选写生产 `shots/` | `prompt_builder.py:1945-1948`；`ref_compose.py:543-563`；`step02b_video_gen/main.py:533-545`、`:658-664`；`submit_poll.py:319-331` | 否 | 把拼表目录改为 attempt/临时目录；plan-only 也加 production snapshot；禁止 `_get_shot_hash` 产生写副作用 |
| **中** | `ADDED_DIALOGUE` 生产路径不产出 | `script_findings.py:1101-1102,1146-1160`；`audit_adjudication.py:208,575`；`main.py:2959-2981` | 是（M03:71，M09 未列） | 把 `source_text` 贯通到 `build_shadow_report`；补真实入口测试 |
| **中** | `_shot_size_level` 全段最长匹配 + `近景` 缺失；多样性仍首匹配 | `validation.py:5293-5317,585-589`；`split_visuals.py:30`；基线 `script_result.json:766,1497` | 部分（只自报旧子串缺陷） | 锚定显式景别；补 `近景` 映射；复用同一解析函数 |
| **中** | M06 真实缺口是 prepare 后素材改写未回写提示词，不是“prompt 构建早于定稿” | `prompt_builder.py:1509-1515,1637-1650`；`submit_poll.py:1450,1474`；`volcengine_provider.py:331-335,474-551`；`request_plan.py:267-299` | 是，但描述不准确（M09:16,28,63） | 用 `prepared.materials` 重建/校验图N；文档改为“prepare 后未闭合” |
| **中** | 声音没有准备态/视频后确认态；探测失败回退声明值 | `voice_preparation.py:129-270,160-165,261,269`；`step03_tts_audio/main.py:150-165,390-427` | 是（review-round3:116；M09:29 未强调） | 增加 `prepared` / `confirmed_after_video`；让 `unverified` 参与 gate |
| **中** | Step02b 缓存命中绕过 `validate_requirements` | `step02b_video_gen/main.py:263-266,277-283`；`submit_poll.py:1510-1519` | 否 | 在 cache 前校验 `_reqs.issues`；或把 requirements 纳入 hash |
| **中** | `postprocess_reverted` 只诊断，不拦提交 | `main.py:6166-6193,9607-9610`；`_repair_acceptance_decision` 未使用 | 是（M02/M09 GATE） | 裁决后决定是否升级为拒绝/约束跳过 |
| **低** | `issue_ledger` 身份不含“事实”，同字段多事实会合并 | `issue_ledger.py:60-70`；模块 doc `:22-23` 与 M03 doc `:36` 不一致 | 否 | 把 evidence/code 指纹纳入身份，或明确写“同字段合并”的限制 |
| **低** | `verify_plan_execution` 对缺 path/sha 的参考条目直接跳过 | `execution_plan.py:535-541` | 否 | 缺字段即 `plan_reference_missing`，不要 `continue` |
| **低** | step02b 的已知测试失败是夹具未跟上 `start_end_state_mismatch` 门 | `tests/step02b.../test_production_trace.py:164-169`；`execution_requirements.py:216-218`；`submit_poll.py:1510-1519` | 文档称“既有”，不准确 | 补 `ff_desc`/`lf_desc` 到夹具，或明确测试只测缓存；不要把新门禁回归记成既有 |

## 10. “文档自报 vs 代码实测”对照表

| 文档自报 | 代码实测 | 判定 |
|---|---|---|
| `ADDED_DIALOGUE` 两条生产调用点不传 `source_text`（M03:71） | 属实；`audit_adjudication.py:208,575` 都不传，`main.py` 也没把原文传进影子入口 | **证实**；M09-M10 漏列 |
| `framing_jump` 旧实现按词表顺序子串匹配，`大远景` 先命中 `远景`（M09:53-55） | 旧缺陷的具体反例已修：`validation.py:5303-5317` 最长匹配，测试 `test_m02...:29-34` 通过 | **已修但文档未更新**（review-round3 已部分更新） |
| `framing_jump` 已改“最长匹配” | 是，但未锚定声明景别；`近景` 仍无档位；多样性检查仍首匹配 | **部分修复/文档夸大** |
| 提示词构建早于参考清单定稿，读不到最终列表（M09:16,28,63） | `prompt_builder.py:1509-1515` 先选参考，`:1637-1650` 后建用途清单；真正未闭合的是 `prepare_request` 之后 | **文档描述错位/夸大** |
| 声音准备态/视频后确认态未拆（review-round3:116） | 属实；只有一个 `build_voice_preparation`，无两态状态机 | **证实**；M09:29 未强调 |
| `issue_ledger.closed` 未接提交门（M09:61、M03:81） | 属实；提交门仍 `before_keys - after_keys` | **证实** |
| M05 已补 `verify_plan_coverage`（M09:27） | 显式 `only_indices` 能查缺镜；`only_indices=None` 时不能，且主入口会收窄范围 | **部分成立/文档夸大** |
| M06 首帧用途口径已修（M09:28） | 属实；`test_m06_reference_purpose.py` 通过；但“最终参考清单到 prepare”仍未闭合 | **部分成立** |
| M09 的 4255 passed / 278 skipped / 4 xfailed | 本次 4266 passed / 278 skipped / 4 xfailed（关闭 safe-delete 后） | **数字已过时（新增测试）** |
| step02b 有 2 条既有失败（M09:121-126） | 本次 step02b 82 passed / 1 failed；第二条 `test_step02b_report...` 通过；失败根因是 `start_end_state_mismatch` 夹具问题 | **文档过时/归因不准确** |
| U6 工具“投影 7/7 成立”（M09:24） | 工具改动未提交，但代码 diff 与文档结果一致；建议提交后才能复现 | **工作树与文档不一致** |
| M01 说 `fit_refs` 是定稿点（M01:162-164） | 与代码一致；与 M09 “提示词早于定稿”自相矛盾 | **文档内部口径冲突** |

## 11. 未覆盖/未验证边界

本次是只读静态核查 + 离线测试，没有做以下事项：

- 没有发起真实 Step01/02a/02b/03 调用；真实模型遵从、真实 TTS 音色、真实成片、发布中断/并发事务均未验证。
- 没有运行全仓测试；没有验证 `tests/enhancements`、`tests/integration`、`tests/tools` 等目录。
- 没有复现 `issue_ledger` 接线后的真实提交语义；只核了当前代码没有接线。
- 没有验证 `fit_refs` 在生产 r2v 配置下的实际触发次数；当前 `.env.content` 是 `VIDEO_REFERENCE_MODE=image`，H2 是条件触发风险。
- 没有验证 provider 真实返回的 `prepared.materials` 与 prompt 的偏差；只核了代码没有一致性检查。

## 12. 建议的下一步顺序

1. **先修 H1（Step02a 全量覆盖洞）**：这是确定性的生成范围错误，修复成本低，且不改变产品语义。
2. **修 H2（Step02b 拼表写生产）**：把 `fit_refs` 的落盘目录改成候选 attempt/临时目录；`_get_shot_hash` 不应有写副作用。先补一条 r2v 超限的入口测试。
3. **按 GATE 处理 E/H6**：`issue_ledger.closed` 与 `postprocess_reverted` 都是准入语义变更，先做影子对照，再决定是否接提交门。
4. **修 A（ADDED_DIALOGUE 接线）**：低风险，补真实入口测试。
5. **修 B 残余**：先锚定显式景别、补 `近景`，再统一多样性检查。
6. **重写 C 的文档表述**：把“prompt 构建早于参考清单”改成“请求级列表已定稿，prepare 后素材改写未回写”。
7. **提交 F 的工具改动**，让 U6 投影证据可复现。
