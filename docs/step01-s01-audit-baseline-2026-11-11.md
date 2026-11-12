# Step01 S01 内容与裁决基线（2026-11-11，修订版）

本文件是 S01 的交付物：**冻结样本 + 逐项裁决表 + 保护清单**。S02/S03/S06 消费同一份冻结预期。
本版按复核意见改用 **U6 主线对象**，并保留 `92c8000` 作为历史补充样本。

## 1. 冻结身份（U6 主线）

| 项 | 值 |
|---|---|
| 仓库 HEAD | `7286516`（工作树另有本包改动，见交付说明） |
| 主线候选 | `.pytest_tmp/u1_u5_closeout_20261110/candidate_d1a/candidate.json` |
| revision | `84ff4112e6f6649489d7571bc0a1df73b2800247c902b8bd8bf07780ee514dc7` |
| SHA256 | `3f88a060082173ca36cdcb6f14f94002843d502fe09930812ea13dc3bf42321f` |
| 送审快照 | `…/run_s02_20261111/output/锦衣夜行/part_01_杀青州/chapter_001/snapshots/script_result_20261111_212850.json`（revision 复核一致） |
| 审计 | `step01_audit.blocked.json`：`status=ok`、**10 条**、`audit_prompt_version=a5929a9f6abc`、`audit_focus_version=066a87e6da63` |
| run_id | `20261111T212239-13228`（收据 `authorization.claim_run_id`） |
| 请求体 | `step01_call_bodies/ce04d8719abac4549.txt` |
| 原文 | `_resume/novel_aba1adc22826.txt`（14295 字节） |
| 收据 | `…/run_s02_20261111/budget_receipt.json`：`sends=1`、`remaining_after=0`、**`audit_status=null`** |
| BLOCK 清单 | `step01_commit.json`：`status=BLOCK`、`audit_status=ok`、`blocking_count=1` |

### 1.1 上一版结论的更正

上一版写「`84ff4112` / `20261111T212239-13228` 全库无命中，必须替换基线」——**该结论错误**。
原因是检索范围错误：上一版只检索了 `output/` 与 `docs/`（`grep` 限定了这两个根，`glob` 超时后
未重试全仓），而 U6 产物位于 `.pytest_tmp/u1_u5_closeout_20261110/`（被 `.gitignore` 忽略，
也不出现在 `git status` 里）。现已核实：候选 revision、SHA256、审计 run、请求体与原文**都在盘上**。
本版以 U6 为主线；`92c8000` 只作历史补充样本（§6）。

## 2. 冻结样本族（内容无关、离线可跑）

位置：`tests/step01_script_gen/fixtures/audit_narrative_families/`。

| 族 | 合法样本 | 反例 | 判别依据 |
|---|---|---|---|
| 时间省略 | `time_ellipsis_legal.json`：隔夜后同院练剑，`scene` 变化 + 淡出黑场 + `context_bridge`「次日清晨」 | `time_ellipsis_broken.json`：送信→三日后敌营，省略掉「送出/被擒/投敌」这一观众必须知道的关系 | `scene` / `clip_transition` / `context_bridge` |
| 状态矛盾 | `state_legal.json`：同场景连续时间内，空酒壶留在案上、甲空手 | `state_broken.json`：壶已被放下，下一镜又回到手里，无任何依据 | `ff_desc` / `frame_layouts.ff` |
| 画外声音 | `voice_legal.json`：丙始终在画外，旁白前缀一直是丙，甲只作听者 | `voice_broken.json`：把丙的台词改成甲的，画面里甲闭口 | `narration_segment` / `frame_layouts.ff` |

三族都成对（`legal` + `defect`），反例都是 `contradiction|unclear`，判别字段在样本里真实存在，
样本不含任何作品专名（`test_audit_narrative_context.py` 校验）。

## 3. U6 十条问题裁决表

确定性列由 `audit_adjudication.deterministic_checks` 生成（只报结构事实）；语义列按**冻结原文 +
冻结快照**判断。处置四类：`确认缺陷` / `证据不足（待裁决）` / `已有合法表达` / `改进建议`。

⚠️ 本表的「处置」是**冻结语义裁决**（S01 产物，供 S06 当金标准）；S03 的影子报告是
**确定性-only** 的另一份记录，两者不一致是预期行为——S03 只输出代码能证明的部分（§5）。

| # | 原始发现 | 确定性核验 | 语义裁决 | 处置 | 依据（原文/字段） | 修复后必须保留 |
|---|---|---|---|---|---|---|
| 1 | `ACTION_OMISSION` shot13 `ff_desc`：船→车的搬运与上车过程被跳过，因果链断裂 | 证据逐字存在于 `ff_desc` | **不成立** | 已有合法表达 | 原文本身就是「**一柱香的时间之后**，杨府别庄大门洞开，张十三驾着马车疾驰而出」——省略是原著的合法时间跳跃，观众不需要看搬运过程 | 杨旭已在车厢、张十三驾车离开 |
| 2 | `PROP_CONTINUITY` shot7 `lf_desc`：尾帧写「右手酒壶倾倒」 | 证据逐字存在 | **成立** | 确认缺陷 | 原文「忽然**丢了酒囊**，酒水汩汩地洒向甲板」；shot6 `lf_desc` 也写「酒壶脱手」——壶已离手，不能再在右手 | 酒水洒在甲板、壶不在手中 |
| 3 | `PROP_CONTINUITY` shot5 `frame_layouts.ff[2].props`：杨旭条目为空、未列钓杆 | 字段存在；证据为散文（非逐字） | **成立（轻微）** | 改进建议 | 原文钓杆直到中刃后才「五指一松」脱手；shot4/shot6 布局均列钓杆，shot5 为空 | 钓杆仍在杨旭手中 |
| 4 | `FF_RESULT_LEAK` shot11 `ff_desc`：「逼近耳畔、右手食指竖于唇前」 | 证据逐字存在 | **成立** | 确认缺陷 | `motion_desc` 前 2 秒才是「逼近…右手竖指封口」；首帧提前完成过程动作，镜头无可演过程 | 首帧=俯身朝向、右手尚未抬起 |
| 5 | `FIELD_CONFLICT` shot1 `prompt`：写「大特写」 | 证据逐字存在 | **成立** | 确认缺陷 | 同镜 `ff_desc`/`lf_desc` 均写「大远景」，原文为环境建立空镜 | 大远景、空镜、无人 |
| 6 | `TEXT_REPETITION` shot6 `prompt`：同一括号说明连写两遍 | **逐字证据本身即 2 次重复** | **成立** | 确认缺陷 | 该说明在 `prompt` 中确实出现两次 | 说明保留一次 |
| 7 | `SCENE_CONTINUITY` shot13 `prompt`：写「湾堤岸边」，`ff_desc` 写「马车停在门前」 | 证据逐字存在 | **待裁决** | 证据不足 | `prompt` 与同镜 `scene`/`location`（均为「湾堤岸边」）一致；与 `ff_desc`/`kf0` 的措辞不同，孰为准需内容裁决 | 地点单一、与 `scene` 字段不冲突 |
| 8 | `STATE_CHAIN_CONFLICT` shot10 `ff_desc`：听香「披头散发撑坐船板」 | 证据逐字存在 | **成立** | 确认缺陷 | 原文步摇是在**掌掴同时**被碰落（「抓住头发…步摇碰落…一头秀发顿时披落」）；`ff` 在掌掴前，应是挽髻 | 首帧挽髻、尾帧散发；散落发生在动作内 |
| 9 | `PROP_CONTINUITY` shot4 `ff_desc`：「右手提青瓷酒壶」 | 证据逐字存在 | **成立** | 确认缺陷 | shot3 `lf_desc` 写「青瓷酒壶搁在船板」；原文杨旭全程持壶——根因在 shot3 的「搁在船板」偏离原文 | 壶在手中；shot3 尾帧不该把壶放下 |
| 10 | `PROP_CONTINUITY` shot7 `ff_desc`：神秘人「右手反握带血短刃」 | 证据逐字存在 | **成立（修法需改向）** | 确认缺陷 | 原文「一道寒光…刺进心口」「推臂一送，双腿一弹，倒纵入水」——**没有拔刃动作**。正解是短刃留在心口、刺客空手，而不是补「拔刃」 | 短刃留在心口；刺客空手入水 |

**汇总**：确认缺陷 7（#2/#4/#5/#6/#8/#9/#10）、已有合法表达 1（#1）、待裁决 1（#7）、
改进建议 1（#3）。

**与审计建议的偏差**：#10 的审计建议（补拔刃动作）与原文相反，按原文应去掉他手里的短刃；
#1 被审计判为断裂，但原文自身就省略了搬运。这两条是"模型判断需按原文复核"的实例，
不因审计措辞而改判。

## 4. 保护清单（任何修订都不得动）

1. **旁白**：shot12 `narration_segment`「张十三道：公子爷吃醉了酒，险些跌落水中」逐字与说话人前缀。
2. **状态链**：杨旭心口暗红洇痕自 shot6 起持续；听香发髻在 shot10 掌掴**过程中**散落，此前挽髻。
3. **道具**：酒壶 shot6 脱手（原文「丢了酒囊」），shot7 不得回到手中；钓杆 shot4/5 仍在左手，
   中刃后才脱手；短刃刺入后**未被拔回**。
4. **在场关系**：杨旭在 shot5/10/11 是「被遮挡/仰倒但仍出镜」，不是离场。
5. **时间关系**：shot13 的搬运省略是原著「一柱香的时间之后」的合法跳跃，不得补成连续动作。

## 5. 与 S02/S03/S06 的接口

- S02 已把 `scene`/`location`/`clip_transition`/`context_bridge` 纳入送审字段，并写入
  「缺失即未知 / 合法省略 / 信息缺失举证责任 / 状态冲突先核时间关系 / 硬切合法」五条判据。
- S03（收窄后）在 U6 上只由确定性规则证实 1 条（#6 重复文本）；其余 9 条保留给语义裁决——
  其中 8 条 `advisory` 进内容评阅、1 条 `visual_risk` 进待裁决。**不再**把枚举合法或字段非空
  当作"已有合法表达"。
- S06 判读标准（冻结）：模型应把 #2/#4/#5/#6/#8/#9/#10 判为缺陷、把 #1 判为合法省略、
  把 #7 留作待裁决；#3 作为轻微连续性记录。

## 6. 历史补充样本 92c8000（15 条）

对象：`snapshots/script_result_20261108_222857.json`（revision `92c8000…`），审计
`step01_audit.blocked.json`（run `20261108T222543-23920`）。收窄后影子裁决：
确认缺陷 1（重复文本）、已有合法表达 4（3 条 stage 枚举 + 1 条 props 已列）、待裁决 7、
改进建议 3；其中 3 条「合法表达」仍被现行门禁阻断，1 条「已降级但仍待语义裁决」。
该样本只用于对照，不作为本轮主线。

## 7. 未决

- #7（shot13 地点措辞）需内容裁决：`prompt` 与 `scene` 字段一致，与 `ff_desc` 措辞不同。
- S05 的收据缺口在 U6 上可见：`step01_commit.json.audit_status=ok` 而
  `budget_receipt.json.audit_status=null`——已在本包修正（见 §8）。
- **S06 已执行**（8 次发送，未超授权），结论见
  [S06 有限对照报告](step01-s06-contract-ab-2026-11-12.md)：新契约未新增对合法表达的阻断、
  状态矛盾检出更细，但同一稿的 blocking 由 1 变 0、且漏掉 3 条本表确认缺陷
  （#8/#9/#10）——**严重度不稳定**，不据此放宽门禁；修订失败的瓶颈是授权面（越权
  `keyframes` 被拒），不是模型输出面。
- 本文件的语义裁决是**离线判断**；S06 的终局审计不改变本表，只作为对照证据。

## 8. 收口状态（S04/S05）

| 项 | 落点 | 证据 |
|---|---|---|
| S04 四栏台账 | `step01_repair_rounds.json` 的 `related_readback`：模型请求变化 / 实际应用变化 / 越权回填 / 投影后残余 | 真实修复入口用例（桩响应只改 `ff_desc`、不动布局） |
| S04 投影后回读 | 修复轮内对已接受稿跑 `project_for_commit` 并回读关联字段 | 同上用例断言 `residual_after_projection` |
| S05 终局状态 | `summary.audit_status` → 提交清单（COMMITTED 也写）→ 收据 `audit_status` | `test_step01_commit.py`、`test_audit_entry_subprocess.py` 断言三处一致 |
| S05 预算来源 | 收据 `budget_sources`：初始额度/已用/剩余/发送数/派生剩余各自标来源 | 同上 |

**「范围不足」的判据边界**：台账只记「模型请求改动关联字段但不在授权面 → 已回填」这一
**事实**（`reverted` 栏）；「该字段是否确实需要改」由本轮重验与提交点审计判定，不由台账
或日志下结论。合并处的 WARN 已按此措辞改写。

**验证读数**（本机，2026-11-12 00:0x）：受影响子集 `950 passed / 27 skipped / 0 failed`；
`tests/step01_script_gen` 全量 `4189 passed / 278 skipped / 4 xfailed / 0 failed`；
四个新增离线用例文件共 46 条。

## 9. 跳过清单核对（本包受影响子集 27 条）

| 条数 | 用例 | 跳过原因 | 与本包的关系 |
|---|---|---|---|
| 22 | `test_run_lifecycle_r12`…`r25` | `.tmp/run_lifecycle.py` 缺失（2026-10 决定不恢复） | **无关**：这些用例验证运行生命周期，不读审计契约身份，也不读裁决/关联字段台账 |
| 4 | `test_archived_e_chain.py` | `fixtures/archived_runs/slot_close_20261008` 不在盘上 | **无关但不可证**：归档回放数据缺失；无法证明该归档在新审计契约下仍通过（数据本来就不在盘上，属既有缺口） |
| 1 | `test_candidate_commit_real.py` | 需显式 `STEP01_FROZEN_CANDIDATE` | **部分可证**：用 U6 候选实跑该用例，提交链本身走通（`COMMITTED`、投影 revision 一致、恰好一次桩审计），但卡在其自带断言「冻结候选的显式声源必须保留」——U6 候选的 `chapter_outline.narration_lines` 共 4 条、`voice_role` **0 条**，该断言面向的是另一份带声源的交付件，与本包改动无关 |

结论：27 条跳过中没有一条因本包改动而新增；其中 1 条可用 U6 候选部分复核（结论如上），
1 条因数据缺失不可证，22 条与审计契约无关。**不**把「既有 harness 缺失」当作通用免责理由。
