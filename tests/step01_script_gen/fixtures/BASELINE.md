# step01 基线冻结（阶段 0）

> 冻结日期：2026-09-23
> 基线 commit：`61d7433a25f3c524e9853a06b45f45ec0ba5a3ec`（"更新"）
> 上游计划：[step01 收敛与 awesome-seedance 吸收总计划](../../../docs/step01-and-awesome-seedance-master-plan-2026-09-23.md) §阶段 0

本目录是**只读 fixture**：后续每一项修改都与这里的产物比较，任何测试都不得
写回这些文件（`test_fixture_integrity.py` 锁住 SHA1）。

## 1. 第十六次落盘产物（`chapter001_16th/`）

| 文件 | 来源 | 说明 |
|---|---|---|
| `script_result.json` | `output/video/锦衣夜行/part_01_杀青州/chapter_001/script_result.json`（2026-09-23 21:55） | 正式分镜产物 |
| `step01_quality.json` | 同目录（2026-09-23 21:55） | 确定性质量门 |
| `step01_audit.json` | 同目录（2026-09-23 22:12，由 `tools/run_step01_final_audit.py` 补跑） | 模型语义审计 |
| `blockers.json` | 从上表 audit 里筛出 severity ∈ {hard, visual_risk, blocking} | 目录 12 四条 blocking 的身份清单 |

### 1.1 基线数字（实测，非计划稿转抄）

```text
主分镜        11 镜，时长合计 73.02 秒（hook 4.0 秒，total_duration 77.02）
结构化质量    status=PASS，hard=0，visual_risk=0，advisory=103
rule_version  2026-09-23.4
审计          status=ok，issues=21（advisory 17 / visual_risk 4）
三份 revision 完全一致（audit.input_revision == quality.script_revision == script.script_revision）
规则指纹      audit 与 quality 相同（7f75221ed6263cc3）
```

> 计划稿 §阶段 0 写的是「11 镜、73.02 秒」——73.02 是**主分镜合计**，
> `script_result.total_duration`（含 hook）是 77.02。两个口径都在此登记，避免
> 后续对不上时误判成回归。

### 1.2 这组产物的**核心矛盾**（本计划要解决的 P0）

`step01_quality.json` 是 `PASS`，而同一 revision 的语义审计有 4 条真实
blocking —— `require_semantic_gate()` 判定为拒绝。也就是说：

- 确定性层与模型层**对同一份对象给出相反结论**；
- 质量门的 `PASS` 不能代表可进入 step02a；
- 四条 blocking 没有任何确定性检测能看见（这正是阶段 2 要补的）。

### 1.3 四条 blocking（`blockers.json`）

| shot | frame | 类型 | 矛盾 |
|---:|---|---|---|
| 1 | lf | 在场状态 | `lf_desc` 写「老农已走远」，`frame_layouts.lf` 的「群演」仍 `on_screen=true` |
| 2 | ff | 道具持有 | `ff_desc` 写「钓杆已脱手垂湖面，酒壶已脱手」，`frame_layouts.ff` 的 props 仍是 `[钓杆, 酒壶]` |
| 5 | lf | 帧阶段 | `lf` 是结果帧，`frame_layouts.lf` 的 pose 仍是「纵身跃起」过程态 |
| 7 | lf | 动作链缺口 | `lf_desc` 有「反手一掌 / 耳光扇在她脸上」，`motion_desc` 无对应过程段 |

## 2. 最小反例

| 文件 | 内容 | 用途 |
|---|---|---|
| `cross_field_minimal.json` | 四条真实 blocking 各自的最小镜头切片（只留判据需要的字段） | `test_cross_field_checks.py` 的正例 |
| `motion_kf_tail_gap.json` | 3 个非空 motion 时段、只有 2 个 KF（尾段无帧） | 阶段 1.1 的退出条件：确定性层**不得**靠合并时段把它归零 |

## 3. 可重复的本地检查

```bash
# 基线数字复算（三份 revision 一致 / 结构化 PASS / 审计 4 blocking）
python -m pytest tests/step01_script_gen/test_fixture_integrity.py -q

# 四类跨字段检测
python -m pytest tests/step01_script_gen/test_cross_field_checks.py -q

# 时段/KF 方向反转
python -m pytest tests/step01_script_gen/constraints/test_motion_segment_kf_align.py -q
```

## 4. 既有测试基线

历史复核记录：`1 failed / 1837 passed / 1 skipped`，失败项是候选并行早停的
**线程调度竞态**（阶段 1.2 已改为 `Barrier` + `Event` 的确定性写法）。
