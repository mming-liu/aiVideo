# U6 R2 候选修订与重算（2026-11-14，零调用）

## 1. 目标与输入

- 修复 S03 唯一 blocking：shot 13 `model_audit_ACTION_OMISSION`（船→车搬运/上车过程被跳过）。
- 收敛 S03 的 9 条 advisory（仅局部、零风险、不碰锁定字段与 shot 2 的条目）。
- 在 S06 接线后的最终规则指纹下重投影、重建质量报告、重算 U6 包身份（`budget.remaining=0`，待新授权）。

输入：`.dsh/u6_closeout_20261110/candidate_d1a/candidate.json`（revision `84ff4112…`，
sha256 `3f88a060…`）、`u6_inputs/novel_chapter_001.txt`（sha256 `19bc212c…`）。
工作副本与脚本：`.dsh/u6_closeout_20261110/r2_rebuild.py`、`r2_package.py`。

## 2. 修订内容（34 处显式数据编辑，8 镜）

| 镜头 | 问题 | 修法 |
|---|---|---|
| 13 | **blocking** ACTION_OMISSION：前镜 12 尾帧在船梢，本镜首帧已在车厢/车辕，搬运上车被跳过；prompt「湾堤岸边」与 ff/kfs「别庄门前」不一致 | `ff_desc` 改为门前搀扶/走向车厢踏板起始态；`motion_desc` 补「扶起→半扶半抬→托上车厢→跳上车辕扬鞭→启动」；`lf_desc` 改为启动驶离；prompt 场景改为别庄门前并写入搬运过程；ff 布局三人位置/姿态/承重面同步；kfs 三段同步 |
| 7 | 酒壶位置矛盾（尾帧酒壶在右手倾倒 vs 前镜已脱手）；拔刃过程缺失 | `lf_desc`/prompt 改「酒壶倒在船板，酒水汩汩洒出」；`ff_desc` 改「刃上带血、刚离杨旭心口」（避开 style_prefix 的「拔出→未出鞘」确定性改写）；`motion_desc` 首段补拔刃反握（不新增时段，保持 3 帧对应） |
| 5 | 钓杆在刺杀前消失 | prompt 补「左手持钓杆垂向湖面」；ff/lf 杨旭布局 props 补「钓杆」 |
| 11 | FF 提前完成竖指 | `ff_desc` 改「右手尚未抬起、食指未竖」；ff 布局 pose 改「俯身未竖指」；kfs[0] 同步 |
| 1 | prompt「大特写」与 ff/lf「大远景」冲突 | prompt 改「大远景」 |
| 6 | 同一括号说明重复两次 | 删除重复一处 |
| 10 | 听香披发来源缺失 | `ff_desc` 补「发髻已散、步摇已失」；`motion_desc` 首段补「抬手摸到散落长发」（不新增时段） |
| 4 | 缺「从船板拿起酒壶」动作 | `ff_desc` 改「右手正伸向船板青瓷酒壶（拿起起始态）」；`motion_desc` 首段补提起；ff 布局 pose/props 同步 |

约束：不改 `narration_segment`/`subtitle`/`index`/`duration_sec`/`variation_type`/`scene`/`location`；
不增删镜头；**shot 2 零改动**；shot 8 全槽位无听香残留。9 条 advisory 全部处置为「已修」，
无保留项（逐条核对见 §3）。

## 3. 重投影与身份（真实生产入口，零模型调用）

| 项 | 读数 |
|---|---|
| 修订后 revision | `278a5193065f77621cc36431aea23f643b31c401b234ef86d6bbfeef31da10a8`（`ensure_script_revision` 与 `compute_script_revision` 一致） |
| candidate_r2 文件 sha256 | `30fcfd5a7b776ce0084294eabb6991c525bcacef654134715808889683445414` |
| step01_quality_r2 sha256 | `8b305edbd7082094f6e1bfab35dbcd3293baef43e031dcd1575582819bac35ce` |
| 质量 | **PASS**；hard=0；visual_risk=0；advisory_count=82（S03 的 9 条模型 advisory 已逐条修复；确定性质量 advisory 数量不代表模型 advisory） |
| 规则指纹 | `db08353ea107b486`（S06 接线后；审计提示版本 `857e4768d3dc`） |
| 镜头/时长 | 13 镜 / 75.25s（不变） |
| 改动面 | 仅 shot 1/4/5/6/7/10/11/13；shot 2 diff=0；shot 8 readback_clean=true |
| shot 2 FF | `vimax_working/shots/2/frame_hash.txt = 06ab04ab252884f8`（不变） |
| advisory 逐条核对 | 18 项文本/布局断言全部通过（含 shot 13 搀扶/托上车厢、shot 7 酒壶倒地/刚离心口、shot 5 钓杆、shot 11 未竖指、shot 10 散发、shot 4 提起酒壶等） |

## 4. 新审计包与零发送门禁

包：`.dsh/u6_closeout_20261110/u6_package/audit_package_r2.json`
（`budget.remaining=0`、`new_authorization.authorization_id=""`）。

- **binding_digest = `fe1029d7ece7fd59ca80d9b28b652793`**（`--print-binding-digest` 计算后逐字写回）。
- dry-run 实测：`{"status": "VALIDATED", "revision": "278a5193…", "would_send": 0}`，exit 0。
- 未授权 `-Execute` 实测：`{"status": "REFUSED", "code": "authorization_required", "send_state": "not_sent", "would_send": 0}`，exit 2；未创建 `run_r2/`。
- TLS 预检（2026-11-14 早前复跑）：`opencode.ai:443` 证书 `notBefore=2026-11-13 00:00:15 UTC`、`notAfter=2027-02-11 00:53:06 UTC`，当前有效（TLSv1.3）。

## 5. 当前状态与下一步

**停在「等待新的 1 次 S02 审计授权」**：未发送任何真实调用、未执行修复后审计、未进 S04–S08。
下一步（需用户确认费用并下发授权）：

1. 人工核对 `binding_digest = fe1029d7ece7fd59ca80d9b28b652793`；
2. 下发**新的** `authorization_id`（max_sends=1、remaining=1、granted_by/granted_at），发送前复跑 TLS 预检；
3. 用 `--out-root .dsh/u6_closeout_20261110/u6_package/run_r2` 执行 1 次隔离审计（S03）；
4. 审计 PASS（blocking=0）后连续执行 S04 隔离提交、S05 Step02b/03 零调用预检、S06 正式采纳、S07 图片样本（31/172 → ≤35/172）、S08 收口。

证据：`.dsh/u6_closeout_20261110/r2/rebuild_report.json`（本目录另有快照
`artifacts/u6/u6_r2_rebuild_report.json`）、`u6_package/audit_package_r2.json`、`u6_package/dryrun_r2/`。
