"""规则版本索引 — 分镜规则的单一事实源 (Single Source of Truth).

每个版本号对应一组规则：
  - prompt_sections:   prompts/*.md 中的段落（行号锚点，方便跳转）
  - constraints:       constraints/ 目录下的 Constraint 子类名
  - validation_checks: validation.py 中的 _check_* 函数
  - timecode_changes:  timecode.py 中的拆分/拉伸逻辑
  - style_prefix:      style_prefix.py 中的配套改写函数
  - tests:             测试文件路径

原则：
  1. 改任何一版规则前，先查此文件 → 知道要改哪些文件
  2. 新增规则版本时，在此文件追加一条记录
  3. 版本号遵循语义化：v<大版本>.<小版本>（v6.51 = v6 系列第 51 次小迭代）

用法：
  from step01_script_gen.rules_index import RULES_INDEX
  print(RULES_INDEX["v6.51"]["constraints"])
"""
from __future__ import annotations

import re
from typing import TypedDict


class RuleEntry(TypedDict, total=False):
    """单版本规则条目."""
    name: str                    # 简短描述
    date: str                    # 引入日期 YYYY-MM-DD
    prompt_sections: list[str]   # prompts/*.md 段落位置（文件:行号 或 锚点文字）
    constraints: list[str]       # Constraint.name（与 ALL_CONSTRAINTS 中 name 一致）
    validation_checks: list[str] # validation.py 中 _check_* 函数名
    timecode_changes: list[str]  # timecode.py 相关改动描述
    style_prefix: list[str]      # style_prefix.py 相关函数名
    tests: list[str]             # 测试文件路径（相对项目根）


# ---------------------------------------------------------------------------
# 规则索引（按版本号降序，最新版在前）
# ---------------------------------------------------------------------------

RULES_INDEX: dict[str, RuleEntry] = {
    # ========================================================================
    # ========================================================================
    # v6.102 — 第十五次重跑暴露的三项（用户：全部修复）
    #
    # 1. **修复轮空转**：第十五次第 2/3 轮 `before/after/resolved` **完全相同**
    #    （`resolved=0`），却都通过验收——靠的是 `audit_resolved=2`，而这个数
    #    **幂等**：`_audit_issue_resolved()` 问的是「evidence 现在还在不在稿子里」，
    #    第 1 轮清掉后永远为真。于是同一批 4 镜被白改两次、烧掉 2 次 LLM 调用。
    #    修法：新增 `_audit_issue_newly_resolved(script_after, script_before, issue)`
    #    ——基线里已经解决的不算本轮功绩；`_repair_acceptance_decision` 增加
    #    `script_before` 参数（调用点传 `_round_before_script`）。
    #    另加防御：**通过验收但零改善**（resolved=0 且 audit_resolved=0）→ 回退本轮
    #    并停止后续轮次（`_no_gain`）。
    # 2. **候选浪费**：同两条长台词（原文 50 / 59 字）让候选 1/2/3 全部结构不合法，
    #    每轮白烧 3 个候选（各 ~26k token），而第三个已是 100 分。修法：新增
    #    `_candidate_only_splittable()`——候选的**唯一**结构问题是
    #    `shot_content_over_cap`（有确定性修复路径：定向拆分）时**提前采纳**；
    #    `shot_duration_below_min` 无确定性路径，仍拒收重取。摘要新增
    #    `structural_codes`（消息文本判不出类型）。
    # 3. **KF 数 < 时段数**（第十五次 5 条，最大一类）：规则 16.4 模型稳定违反，
    #    而 CLAUDE.md 禁止「代码派生 kf desc」。新增约束
    #    `motion_segment_kf_align`（①段末尾）：**按 KF 落点分组**把 `motion_desc`
    #    的时段合并到与 KF 数一致——只加宽时间窗、文本一字不丢、不派生任何 kf。
    #    保守边界：无 KF 的镜头不碰；目标段数不低于 2（压到 1 段会被检查器整段
    #    跳过 = 藏告警）；`kf 数 = 1` 的镜头修不掉，告警如实保留交修复轮。
    # ========================================================================
    "v6.102": {
        "name": "修复轮空转（audit 判据改「新解决」）/ 候选提前采纳 / 时段合并对齐 KF 数",
        "date": "2026-09-23",
        "prompt_sections": [],
        "constraints": ["motion_segment_kf_align"],
        "validation_checks": [],
        "timecode_changes": [],
        "style_prefix": [],
        "tests": [
            "tests/step01_script_gen/constraints/test_motion_segment_kf_align.py",
            "tests/step01_script_gen/test_repair_acceptance.py",
            "tests/step01_script_gen/test_repair_shot_replacements.py",
            "tests/step01_script_gen/test_candidate_parallel.py",
        ],
    },

    # ========================================================================
    # v6.101 — 第十四次重跑的另外三项（用户：全部修复）
    #
    # 1. **修复预算**：`_REPAIR_MAX_ROUNDS` 1 → 3、`_REPAIR_MAX_SHOTS_PER_ROUND`
    #    3 → 4、`REPAIR_MAX_OUTPUT_TOKENS` 8000 → 16000。第十四次实测 22 条阻塞
    #    分布在 14 镜上，而一轮只动 3 镜、只解掉 1 条——真正的瓶颈是预算。
    #    **必须一起改的两处**（否则轮数放开就出事）：
    #      · 原实现只有第 1 轮限量，第 2 轮起 `picked = dict(shot_items)` 全量
    #        → 一次性发 10+ 个镜头对象 → `finish_reason=length` 整份丢弃；
    #      · 接受判定原本在**循环外**（整段后处理 + 一次验收）→ 前两轮的提交
    #        完全不设防。改为**每轮一个独立事务**（后处理 + 快照还原 + 验收都在
    #        轮内；未通过则回退本轮并停止）。
    #    另加：每轮开始前复查 `_budget_exceeded()`（入口那次只保证开始时没超）。
    # 2. **结构收敛轮超时**：`_repair_timeout_sec()`（180s）→
    #    `_full_script_timeout_sec()`（300s）。它是**全脚本级**请求（输入整章 +
    #    输出 ~26k token），第十四次实测 180s 直接客户端超时 → 连候选都没拿到。
    # 3. **开篇空镜接入结构 regen**：v6.99 把两个空镜约束改成 report-only（代码
    #    不再删镜头）时，审核要求「删除诉求交结构 regen」**没有落地**——收敛轮
    #    只读 `_shot_duration_issues`，看不见空镜。现在：
    #      · `quality_issues.opening_empty_shot_issues()` / `is_opening_empty_shot()`
    #        是**唯一检测源**（`opening_empty_shot_cap` 改为复用，去掉自带的三件套）；
    #      · `_structural_shot_issues()` 纳入该 issue（**advisory**，不新增阻塞）；
    #      · `_structural_feedback()` 补第 5 条修法（合并/替换成带主体的建立镜，
    #        **不是**删镜头）。
    # ========================================================================
    "v6.101": {
        "name": "修复预算 3 轮×4 镜（每轮独立事务）/ 结构轮超时 300s / 开篇空镜接入结构 regen",
        "date": "2026-09-23",
        "prompt_sections": [
            "main.py:def _structural_feedback",
        ],
        "constraints": [
            "opening_empty_shot_cap",
            "opening_empty_shot_remove",
        ],
        "validation_checks": [],
        "timecode_changes": [],
        "style_prefix": [],
        "tests": [
            "tests/step01_script_gen/test_repair_shot_replacements.py",
            "tests/step01_script_gen/test_structural_convergence.py",
            "tests/step01_script_gen/constraints/test_opening_empty_shot_cap.py",
        ],
    },

    # ========================================================================
    # v6.100 — 第十四次重跑实测：mask 闸门不认「本轮授权过的改动」
    #
    # 实测（2026-09-23 21:03:46）：
    #   [REPAIR] 接受条件: before=25+audit1 after=15 resolved=10 audit_resolved=1 new=0
    #   [REPAIR] LLM 改动了锁定字段 2 处: ['shot 4.duration: 4.0 -> 6.0', 'shot 7.duration: 4.0 -> 5.0']
    #   [REPAIR] 修复未满足接受条件（reason=locked_field_changed, ...），回退本轮修复
    #
    # `_final_field_mask_violations()` 拿 `_FINAL_LOCKED_FIELDS`（含 `duration`）当
    # 唯一判据，而 `duration` **正是**时长类 issue 授权改的字段
    # （`_REPAIR_CODE_FIELDS["shot_duration"] == ("duration",)`；消息里的「时长」也会被
    # `_fields_from_text` 解析成 `duration`）。旁证：shot 7 的「未授权路径已回填」列表
    # **不含** duration——它确实在授权集合内、被 merge 有意放行。
    # 结果：10 条已解决的阻塞被这一个闸门否掉。**主线 B 的新形态**：不是「不认管线
    # 自己的归一化」，而是「**不认本事务自己授权的改动**」。
    #
    # 修法：mask 判据改为「锁定字段中**本轮未授权**的那些」，调用点传
    # `allowed_by_target`。授权层按 issue 推导（具体），`_FINAL_LOCKED_FIELDS` 是
    # 粗粒度默认值（兜底）——具体优先于默认，默认仍管住没被授权的锁定字段。
    # 同时修掉修复 prompt 里自相矛盾的一句：原文把 `duration` 列为「本轮禁止修改」，
    # 后半句却说「duration_sec 由 duration 派生，你**只**给 duration」。
    # ========================================================================
    "v6.100": {
        "name": "mask 闸门改为授权感知：不得把本轮授权过的 duration 改动判成越权",
        "date": "2026-09-23",
        "prompt_sections": [
            "main.py:Mask 字段清单（本轮禁止修改）",
        ],
        "constraints": [],
        "validation_checks": [],
        "timecode_changes": [],
        "style_prefix": [],
        "tests": [
            "tests/step01_script_gen/test_repair_mask_baseline.py",
        ],
    },

    # ========================================================================
    # v6.99 — 二次代码复核（2026-09-23）的 2 P0 + 2 P1
    #
    # 复核文档：`docs/step01-convergence-fixes-review-2026-09-22.md` 第八节。
    # v6.98 的 6 项里 4 项复核通过，4 项有缺口：
    #
    #   P0-A **统一验收没比较全部 blocking**：`_accept_regen_candidate()` 只检查
    #        手工列出的 9 个 `integrity_codes`。确定性反例：候选只多一个越界 KF
    #        （`kf_time_out_of_shot`，hard），`integrity_ids` 完全相同 → 旧判据
    #        返回 `(True, [])`。修法：新增 `_blocking_not_degraded()`，用
    #        **Counter 多重集**比较**全部** hard/visual_risk 身份（集合会漏「同一
    #        问题 1 次→3 次」，总数会漏「修掉 A、引入 B」）。身份与镜头序号无关。
    #   P0-B **空镜删除的 sanctioned 方案与时间屏障冲突**：只豁免结构检查、没豁免
    #        时间检查 → 删首镜让后续镜头按**位置**前移 → 实测 20 条
    #        `time_field_audit_write` HARD，与 advisory 同发。用户决定（选项 B）：
    #        `opening_empty_shot_cap` / `opening_empty_shot_remove` **全部改
    #        report-only**（不删/不合并/不重编号/不改时长），
    #        `STRUCTURAL_SANCTIONED_WRITERS` **恢复空集**。
    #   P1-A **结构屏障看不见等数量替换**：`del` 一镜 + `insert` 一镜（总数不变）在
    #        digest 层面与「逐镜文本改写」同形。修法：结构判定改用
    #        **对象身份 + 内容多重集**双信号（`diff_shot_structure`）——同一对象原地
    #        改写 = content；对象增删/替换且内容多重集也变 = structural；纯重排 =
    #        structural；深拷贝重建（内容逐字相同）= content。
    #   P1-B **cap 只在运行时追加块里统一**：`.md` 静态模板仍写死「单镜 ≤ 15s」
    #        「对话镜 6-15s」「安全值 ≤56 字」，cap=6/12 时模型同时看到两个值。
    #        修法：模板改占位符（`{{SHOT_CAP}}` / `{{SHOT_CAP_DEC}}` /
    #        `{{NARR_MAX_CHARS}}` / `{{DIALOGUE_TARGET}}` / `{{DUR_RANGES}}`），
    #        `_load_prompt()` 导入期统一渲染；残留占位符**抛错**（fail-closed）。
    # ========================================================================
    "v6.99": {
        "name": "二次复核修复：全部 blocking 验收 / 空镜 report-only / 结构身份判定 / 模板 cap 占位符",
        "date": "2026-09-23",
        "prompt_sections": [
            "prompts/system.md:B. 单镜时长",
            "prompts/main.md:2. 时长分配",
            "prompts/doubao_system.md:RULE-9",
        ],
        "constraints": [
            "opening_empty_shot_cap",
            "opening_empty_shot_remove",
        ],
        "validation_checks": [],
        "timecode_changes": [],
        "style_prefix": [],
        "tests": [
            "tests/step01_script_gen/test_regen_acceptance_gates.py",
            "tests/step01_script_gen/constraints/test_opening_empty_shot_cap.py",
            "tests/step01_script_gen/constraints/test_opening_empty_shot_remove.py",
            "tests/shared/test_semantic_barrier.py",
        ],
    },

    # ========================================================================
    # v6.98 — 收敛闭环审核（2026-09-22）的 6 项修复：4 项 P0/P1 缺口 + 2 项 P2
    #
    # 审核文档 `docs/step01-convergence-fixes-review-2026-09-22.md` 提出 4 P0 +
    # 2 P1 + 2 P2；逐条复核后**全部成立**（P0-3 的产品取舍部分单列待定），
    # 确定性反例见该文档第六节与 `_verify_review.py`。
    #
    #   P0-1 结构收敛 `N → 0` 走提前返回 → **绕过完整性与内容门**：
    #        修得最干净的那次重生反而验收最弱。修法：取消快速返回，
    #        所有 `N → M`（含 `M=0`）统一走 `_accept_regen_candidate()`。
    #   P0-2 B2 内容不退化的**分母由候选自己提供** + 异常 fail-open：
    #        实测 5 种逃逸（删光 core_keywords / 删光台词 / 按比例删内容 /
    #        同数量换身份 / 检查器抛异常）。修法：`_content_inventory()` 从
    #        **旧稿**导出固定清单（已覆盖关键词 + 台词锚），异常一律按退化。
    #   P0-3 屏障只保护**时间字段**，审计段里 `del shots[ri]` 完全不可见：
    #        新增 `semantic_payload_digest` / `diff_semantic_payload`，
    #        跟踪镜头增删、重排、逐镜语义、hook/characters；
    #        未受豁免 → `semantic_audit_write`(hard)；受豁免
    #        （`STRUCTURAL_SANCTIONED_WRITERS`）→ advisory 留证。
    #   P0-4 `run_hard_fix()` 在跟踪器最后一次回调之后调 LLM merge：
    #        时间屏障看不见、hook 整对象替换、无目标集合/字段授权/身份验收。
    #        修法（审核首选）：删除该调用，LLM 侧统一交事务式修复管线。
    #   P1-1 「单镜上限唯一源」未覆盖**实际生成提示**：`min(8.0, cap)` 与
    #        「超 8s 必拆镜」仍在 → 生成层主动拆开 8-12s 合法镜头、抬高总时长；
    #        另有「拆成多个 2-3 秒短镜」低于最短时长 3.5s。修法：提示词一律
    #        按 `timecode.active_shot_cap()` 渲染（含类型推荐区间夹取），
    #        并删除各模块 `except: return 8.0` 的静默回退。
    #   P1-2 完整性「数量不增加」不能证明没有新问题：新增与序号无关的
    #        `integrity_ids` 身份集合，要求 `new_ids - old_ids == ∅`。
    #   P2-1 mask 纵深检查**恒为空**（比较的是旧稿 vs 旧稿）：改比 `merged`。
    #   P2-2 汇总文档与实现不同步（在重跑后一并更新）。
    #
    # 待定（产品取舍，未在本次改动内）：P0-3 的空镜删除是否改为 report-only。
    # ========================================================================
    "v6.98": {
        "name": "收敛闭环审核修复：统一验收 / 内容清单 / 语义屏障 / 提示词 cap 唯一源",
        "date": "2026-09-22",
        "prompt_sections": [
            "prompts.py:def _build_total_duration_constraint",
            "prompts.py:def _duration_cap_guidance",
            "prompts.py:def _dur_range",
            "prompts.py:def _build_regen_iron_rules",
            "main.py:def _structural_feedback",
        ],
        "constraints": [],
        "validation_checks": [],
        "timecode_changes": [],
        "style_prefix": [],
        "tests": [
            "tests/step01_script_gen/test_regen_acceptance_gates.py",
            "tests/step01_script_gen/test_structural_convergence.py",
            "tests/shared/test_semantic_barrier.py",
            "tests/step01_script_gen/constraints/test_regen_trigger.py",
            "tests/step01_script_gen/test_repair_shot_replacements.py",
        ],
    },

    # v6.97 — 「生效单镜上限」收敛为唯一源（用户批准，2026-09-22）
    #
    # v6.93 把项目单镜上限 8s → 15s 时，**有 3 处写死的 8.0 漏改**，另有多处各算各的：
    #   · `duration_budget._SHOT_CAP = 8.0` → **真缺陷**：`min(8, 模型 12) = 8`，
    #     于是凡台词需求 8-12s 的镜头（当前上限下**合法**）都被报成 `Severity.HARD`
    #     「必须拆镜」→ 顶起 `_has_hard`、可能触发无事可修的修复轮；
    #   · `validation._REDGUO_SHOT_DURATION_MAX = 8.0` → 日志写「单镜时长超 8s 上限：
    #     shot1=12.0s」，与真实口径矛盾，误导人/模型；
    #   · `opening_empty_shot_cap` 的 `cap = 8.0` 兜底；
    #   · `main._shot_cap` / `quality_issues` / `runtime_extend` / `motion_time_bounds`
    #     各自内联 `min(项目, 模型)`——4 份重复实现，改一处漏三处。
    # 修法：`timecode.active_shot_cap()` 作为**全仓唯一源**（`main._shot_cap` 的实现
    # 搬过去），7 处全部调用它。这正是 CLAUDE.md 的要求：「模型参数限制跟着模型走，
    # 禁止写死」。
    # ========================================================================
    "v6.97": {
        "name": "生效单镜上限收敛为唯一源（清掉 3 处写死的 8.0）",
        "date": "2026-09-22",
        "prompt_sections": [],
        "constraints": [
            "duration_budget",
            "motion_time_bounds",
            "opening_empty_shot_cap",
            "runtime_extend",
        ],
        "validation_checks": [],
        "timecode_changes": [
            "新增 `active_shot_cap()`：生效单镜上限 = min(视频模型 cap, 项目 "
            "MAX_SHOT_DURATION)，全仓唯一源",
        ],
        "style_prefix": [],
        "tests": [
            "tests/step01_script_gen/constraints/test_duration_budget.py",
        ],
    },

    # ========================================================================
    # v6.96 — ① `locked_changed` 假拒绝 + ② 子镜时长与 KF 对账（用户指示，2026-09-22）
    #
    # ① `_locked_after_patch` 名字写着「patch 之后」，赋值却在**尾部、归一化与还原
    #    之后**（实测第十次：拆分子镜台词末尾的「，」被管线清掉 → 判成 LLM 越权 →
    #    整轮假拒绝——`new_blocking=0`、`非目标镜头被改动`=0、`resolved=5` 全被这
    #    一个闸门否掉）。挪到**合并后立刻取**，与 v6.87 修 mask 基线同一原则：
    #    **闸门只管 LLM 的写入**。
    # ② 第十次 `shot_content_over_cap` 归零，但新暴露 `kf_time_out_of_shot` × 3：
    #    子镜的 `keyframes[].t_sec` 是模型按自己以为的时长写的，而子镜时长由**内容
    #    需求**派生——两者没对账。修法同发现 18（钩子）：**时长必须覆盖自己的最大
    #    KF 时刻**（帧对齐取整到略小则向上加一帧）；KF 超出单镜上限则**带反馈重试**。
    #    抽出 `timecode.max_kf_t_sec()` 给钩子与拆分共用。
    # ========================================================================
    "v6.96": {
        "name": "锁定字段基线挪到合并后 + 子镜时长覆盖自己的 KF 时刻",
        "date": "2026-09-22",
        "prompt_sections": [],
        "constraints": [],
        "validation_checks": [],
        "timecode_changes": [
            "新增 `max_kf_t_sec()`：钩子与拆分子镜共用「帧时刻决定时长下限」这条判据",
        ],
        "style_prefix": [],
        "tests": [
            "tests/step01_script_gen/test_scoped_shot_split.py",
            "tests/step01_script_gen/test_repair_mask_baseline.py",
        ],
    },

    # ========================================================================
    # v6.95 — ③ 带反馈重试 + `locked_changed` 字段级诊断（用户指示，2026-09-22）
    #
    # ③ **视觉重复**：prompt 写明「相邻子镜必须换机位/景别、说话人保持在画面内」
    #    后模型仍照抄（第九次实测相似度 98%）——与发现 14 同型：**prompt 说了、
    #    模型不做**。改为视觉重复被拒时**带反馈重试一次**：把「你的子镜1/2 相似度
    #    98%」这个**具体失败事实**回喂（与「反馈格式教了模型一个错误写法」同一经验：
    #    泛泛要求不如具体到某两个子镜）。只对这一类重试；结构类失败（段数不符 /
    #    台词不逐字）是没照**给定输入**做，重试不划算。上限 `_SPLIT_MAX_ATTEMPTS=2`。
    # `locked_changed` **字段级诊断**：它是第九次重跑后修复轮**唯一**的绑定闸门，
    #    而判据是「7 个字段的整串 JSON 变了没有」——不看变了什么、也不看本轮授权了
    #    什么。新增 `_locked_signature_diff()` 打印 `镜头.字段: 旧 → 新`，先拿到字段名
    #    再决定修法（与 v6.87/`non_target_changed` 两处同族：**闸门不认本事务自己
    #    授权的改动**，疑似同类假拒绝）。
    # ========================================================================
    "v6.95": {
        "name": "拆分视觉重复带反馈重试 + 锁定字段闸门字段级诊断",
        "date": "2026-09-22",
        "prompt_sections": [],
        "constraints": [],
        "validation_checks": [],
        "timecode_changes": [],
        "style_prefix": [],
        "tests": [
            "tests/step01_script_gen/test_scoped_shot_split.py",
            "tests/step01_script_gen/test_repair_acceptance.py",
        ],
    },

    # ========================================================================
    # v6.94 — 第八次重跑暴露的三项（用户指示 ②③④ 一起修，2026-09-22）
    #
    # ② **审计段后写时间字段**：`prompt_repetition` 与 `boat_width` 的字段表都是
    #    `("prompt", "ff_desc", "lf_desc", "motion_desc")`、写入都是
    #    `for fld in self.fields: shot[fld] = new_v`，注册位置却在审计段。
    #    `prompt_repetition` 真实触发（`shot 14.motion_desc`），`boat_width` 是
    #    加宽静态检查后当场抓到的潜在写入者。两者前移到第 1 段。
    #    **静态防腐检查加宽**：原只匹配 `for <var> in (<字面量元组>)`，看不见
    #    「类属性/模块常量」形式（`for fld in self.fields`）——已支持解析类属性元组。
    # ③ **拆分子镜视觉重复 100%**：拆分 prompt 写明「相邻子镜必须换机位/景别、
    #    说话人保持在画面内（不能切听者反应镜，否则视频模型没有嘴型对象）」，
    #    并给出 ≥85% 相似度即拒的具体阈值。
    # ④ **`非目标镜头被改动` 命中拆分子镜（假拒绝）**：根因是轮尾 `run_hard_fix`
    #    会跑 `shot_index_renumber`（无 repair-loop 守卫），把半整数子镜归一成整数
    #    → 其后 index 平移 → 按 index 标识的**快照/还原**与**点名目标**双双失配。
    #    修法：`shot_index_renumber` 加 `ctx.is_in_repair_loop` 守卫（轮内不重编号）；
    #    快照/还原改为**按列表位置**为键（保留重编号后的 index），双保险。
    #    与 `shared/semantic_barrier` 在 v6.84 踩过的坑同源。
    # ========================================================================
    "v6.94": {
        "name": "审计段写入者清尾 + 拆分 prompt 机位差异 + 修复轮内不重编号",
        "date": "2026-09-22",
        "prompt_sections": [],
        "constraints": [
            "prompt_repetition",
            "boat_width",
            "shot_index_renumber",
        ],
        "validation_checks": [],
        "timecode_changes": [],
        "style_prefix": [],
        "tests": [
            "tests/shared/test_semantic_barrier.py",
            "tests/step01_script_gen/constraints/test_shot_index_renumber.py",
            "tests/step01_script_gen/test_repair_shot_replacements.py",
            "tests/step01_script_gen/test_scoped_shot_split.py",
        ],
    },

    # ========================================================================
    # v6.93 — 单镜上限 8s → 15s（用户决定，2026-09-22）
    #
    # 依据：**cap 是 `min(视频模型能力, 项目编辑规则)`**，而当前注册的视频模型
    # （`doubao-seedance-1-0-pro-250528`，免费档）cap = 12s，所以项目值放到 15
    # 后**生效值是 12s**；换到 Seedance 2.0 / wan2.7（均 15s）时自动拿到 15。
    # 8s 是 2026-08-23 的编辑规则，不是模型限制。
    #
    # 实测收益（本章台词需求 9.8 / 12.5 / 14.7 / 20.2s）：
    #   · cap 8  → 全部要拆镜，89 字那条要切 **4 镜**
    #   · cap 12 → 9.8s 一镜合法，89 字那条切 **2 镜**
    #   · cap 15 → 9.8/12.5/14.7 一镜合法，只剩 20.2 那条要拆
    #
    # 同步移动的地方（门禁放到 15 而提示词还教 8 = 没放）：
    #   `timecode.MAX_SHOT_DURATION`（唯一源）、`quality_issues` 兜底常量、
    #   `prompts/{system,main,doubao_system}.md` 里全部 8s 表述。
    #   各约束模块（motion_time_bounds / opening_empty_shot_cap / runtime_extend）
    #   与拆分路径都读同一常量，自动跟随。
    # ========================================================================
    "v6.93": {
        "name": "单镜上限 8s → 15s（生效值 = min(模型 cap, 项目值)）",
        "date": "2026-09-22",
        "prompt_sections": [
            "prompts/system.md:B. 单镜时长",
            "prompts/system.md:B2. 台词容量自检",
            "prompts/main.md:2. 时长分配",
            "prompts/doubao_system.md:RULE-9",
        ],
        "constraints": [],
        "validation_checks": [],
        "timecode_changes": [
            "`MAX_SHOT_DURATION` 8.0 → 15.0；各约束与拆分路径经同一常量自动跟随",
        ],
        "style_prefix": [],
        "tests": [
            "tests/step01_script_gen/constraints/test_runtime_extend.py",
            "tests/step01_script_gen/constraints/test_opening_empty_shot_cap.py",
            "tests/step01_script_gen/test_projected_timecode_score.py",
            "tests/step01_script_gen/test_scoped_shot_split.py",
        ],
    },

    # ========================================================================
    # v6.92 — 拆分的**切点改为确定性计算**（用户指示，2026-09-22）
    #
    # 第六次重跑（20:49）把「切点交给模型」走死了：4 次请求只有 1 次采纳，
    # 三种失败全是同一族「模型不肯切 / 切得不对」：
    #   · 镜头15「子镜仍要 11.9s」——**不均衡**（补了段数算术 prompt，无效）
    #   · 镜头16「子镜数 1」——20.2s 那条直接**拒绝拆**
    #   · 上一轮镜头11「原 76 字 → 拼回 120 字」——**补字/改写**
    #
    # 关键认识：**「台词逐字连续」是唯一语义约束 → 切在哪是 `(台词文本, 上限)` 的
    # 纯函数**。`timecode._find_best_split_point()` 早就在算这件事；当年被禁的是
    # **复制视觉字段**造子镜，不是算切点。
    #
    # 改为：`_deterministic_narration_cuts()` 按标点边界切出 N 段（N = ⌈需求÷上限⌉，
    # 各段 ≤ 上限、长度均衡），**模型只撰写视觉字段**，且 prompt 里直接列出每段的
    # narration 原文；采纳时逐段做逐字比对（`_narration_body` 相等）。
    # 「逐字连续」「各段均衡」「段数正确」全部由构造成立。
    #   · 无标点可切 → 退化为等字长切（刻意兜底：宁可略生硬，也不要留一处必然 hard）
    #   · 需求段数 > 4（schema 上限）→ 交整脚本 regen
    # ========================================================================
    "v6.92": {
        "name": "拆分切点确定性化：代码切台词、模型只写视觉字段",
        "date": "2026-09-22",
        "prompt_sections": [],
        "constraints": [],
        "validation_checks": [],
        "timecode_changes": [
            "新增 `_deterministic_narration_cuts()` / `_balanced_cut_points()`："
            "按标点边界确定性切分台词，各段估时 ≤ 单镜上限",
        ],
        "style_prefix": [],
        "tests": [
            "tests/step01_script_gen/test_scoped_shot_split.py",
        ],
    },

    # ========================================================================
    # v6.91 — 未授权路径**强制回填**（用户决定，2026-09-22）
    #
    # 来源：第五次重跑（07:39）修复轮 3/3 目标因「越权改动」被拒 → 产出 0。
    # replacement 契约要求返回**完整**镜头对象（24 字段全必填，schema 层强约束），
    # 而授权只覆盖少数几个路径——模型把未授权字段的措辞写得略有不同（哪怕同义）
    # 就判越权、整条作废。修复 prompt 已两处写「未授权字段逐字照抄原镜」，模型不遵守。
    #
    # 改法：提交前用 `merge_authorized()` 把未授权路径**从原镜回填**——
    # 「未授权路径 = 原样」由**构造保证**（提交后与原镜的差异**只**在授权路径内，
    # 正是 `check_replacement_authorized` 自己那条判据），原判据保留为**纵深防御**
    # （它能抓到回填器自身的 bug）。
    #   · 列表长度变化 = 结构改动，需**整表授权**才放行（叶子授权不放行增删元素）
    #   · `new` 多出的未授权键 → 丢弃；缺字段 → 回填原值
    #   · 只记**确实被改过**的回填路径（`index` 这类恒定字段不得塞满日志）
    # ========================================================================
    "v6.91": {
        "name": "未授权路径强制回填：越权改动丢弃而不是作废整条替换",
        "date": "2026-09-22",
        "prompt_sections": [],
        "constraints": [],
        "validation_checks": [],
        "timecode_changes": [],
        "style_prefix": [],
        "tests": [
            "tests/step01_script_gen/test_repair_fields.py",
            "tests/step01_script_gen/test_repair_shot_replacements.py",
        ],
    },

    # ========================================================================
    # v6.90 — 拆分路径的三条改进（2026-09-22 07:40 实测后）
    #
    # 第一次真实生效的拆分（4 请求 → 2 采纳 / 2 拒绝）暴露了三件事：
    #   1. **子镜相邻视觉重复**：最终门带出 `adjacent_duplicate_visuals`（镜头11/12
    #      相似度 89%）——两条子镜来自同一父镜，模型把同一动作写了两遍。拆分 prompt
    #      里写了「禁止同一个动作各演一遍」，模型不遵守 → 做成**确定性采纳判据**
    #      `_split_has_duplicate_pair()`，复用全仓唯一判定 `find_adjacent_duplicate_pairs`
    #      （阈值 0.85 与条件都不复制；该判定会跳过拆镜子镜，故先用临时整数 index 复制）
    #   2. **拆得不均衡被拒**（镜头 10：子镜仍要 11.9s）→ prompt 补**段数算术**：
    #      最少段数 = ⌈需求 ÷ 上限⌉、每段均衡、每段 ≤ 上限，并给正/反例
    #   3. **补字被拒**（镜头 11：原文 76 字 → 拼回 120 字）→ prompt 明确
    #      「段数不够就继续切，**不是**往段里补字」
    # 另：`SPLIT_SHOT_SCHEMA` 的 maxItems 3 → 4——提示词要求「最少段数 = ⌈需求÷上限⌉」，
    # 若某镜需求 > 3×上限，3 段就**不可能**满足，上限必须容得下那条指令。
    # ========================================================================
    "v6.90": {
        "name": "拆分路径收口：子镜近似重复判据 + 段数算术 + 不许补字",
        "date": "2026-09-22",
        "prompt_sections": [],
        "constraints": [],
        "validation_checks": [],
        "timecode_changes": [],
        "style_prefix": [],
        "tests": [
            "tests/step01_script_gen/test_scoped_shot_split.py",
        ],
    },

    # ========================================================================
    # v6.89 — 发现 14 选项 2：**单镜定向拆分**（2026-09-22）
    #
    # prompt 侧的显式许可（v6.88）**已被证伪**：07:11 三个候选的超载数值与加规则前
    # 逐字一致（9.8 / 12.5 / 14.7s）。根因是「**单条**原文台词本身超长」——原文里
    # 那是一整句、一整对引号（89 字 → 20.2s），模型稳定地不切。
    #
    # 故改为：对**单个**被点名的超载镜头发一次定向拆分请求（`SPLIT_SHOT_SCHEMA`），
    # 返回 2-3 个**完整**子镜。与当年被禁止的「复制字段拆镜」的区别：那时是**代码
    # 复制**父镜字段、产出 ff==lf 空壳；这里是**模型重新生成**每个子镜。
    #   · `SPLIT_SHOT_SCHEMA` 复用 `_SHOT`（24 字段全必填）→ 子镜在 schema 层不可能是空壳
    #   · 采纳判据：①台词**逐字连续**（拼回与父镜相等，`_narration_continuous`）
    #     ②每个子镜容量合格——**直接问质量门本体** `_shot_duration_issues`，不复制判据
    #   · 子镜时长由**内容需求**派生（不照抄模型给的 duration），下限 3.5s
    #   · 只动被点名那一镜，其它镜头逐字节不动；整脚本 regen 退居兜底
    #   · 每次运行最多 3 个（`_STRUCT_SPLIT_MAX_SHOTS`）
    # ========================================================================
    "v6.89": {
        "name": "单镜定向拆分：把一条装不下的台词拆成多个完整子镜",
        "date": "2026-09-22",
        "prompt_sections": [],
        "constraints": [],
        "validation_checks": [],
        "timecode_changes": [],
        "style_prefix": [],
        "tests": [
            "tests/step01_script_gen/test_scoped_shot_split.py",
        ],
    },

    # ========================================================================
    # v6.88 — 发现 14 的 prompt 侧修复：显式许可「单条长台词切开」（2026-09-22）
    #
    # 来源：docs/step01-temporal-writers-postrun-review-2026-09-21.md 发现 14。
    # 三轮运行的 `shot_content_over_cap` 数值完全一致（9.8/12.5/14.7/8.8/20.2s），
    # 查原文确认超长台词是**一整句、一整对引号**（89 字 → 20.2s）——只能把同一条
    # 台词切成两段。合法切分存在，但 `narration_segment 只放台词原句，禁止概括/改写`
    # 这条**竞争性指令**让模型把「一句引号」当成不可分原子。
    #
    # 本次只**解除那个误解**，不新增机制：
    #   · `system.md` 新增 **B2.1**：一条台词 >8s 时按原文连续切 2-3 段分给多镜，
    #     各段自带「说话人道：」前缀、段间首尾相接、逐字不增不减 + 工作示例；
    #   · `system.md` **R1** 在冲突指令旁加旁注：「原句」= 文字不得改写/增删，
    #     **不等于**「一条台词只能给一个镜头」；
    #   · `main.md` 新增 **2c**（用户 prompt 侧同一许可）；
    #   · `doubao_system.md` 同步 **RULE-9a/9b** 与 **RULE-28** 旁注——同一缺陷
    #     在 doubao profile 上同样存在，不能让两条 profile 在这条根因上分叉。
    # ========================================================================
    "v6.88": {
        "name": "发现 14 prompt 侧修复：单条长台词连续切开的显式许可",
        "date": "2026-09-22",
        "prompt_sections": [
            "prompts/system.md:B2.1 单条台词也允许切开",
            "prompts/system.md:不等于「一条台词只能给一个镜头」",
            "prompts/main.md:2c. ★",
            "prompts/doubao_system.md:RULE-9b",
        ],
        "constraints": [],
        "validation_checks": [],
        "timecode_changes": [
            "`derive_hook_duration()`：**覆盖内容优先于帧对齐**——"
            "`_align_frame_duration(4.8, 24)` 会向下取整成 4.75，把 KF（4.8）挤出镜头；"
            "对齐后装不下就按帧步长向上加帧（硬上限防死循环）",
        ],
        "style_prefix": [],
        "tests": [
            "tests/step01_script_gen/test_hook_time_invariant.py",
        ],
    },

    # ========================================================================
    # v6.87 — v6.86 重跑暴露的两个「判据永远不可能通过」缺陷（2026-09-21 23:19）
    #
    # 来源：docs/step01-temporal-writers-postrun-review-2026-09-21.md
    # 真实重跑 BLOCK / hard=7 / visual=21（结构性超载仍在，见该文档发现 14）。
    #   · **完整性判据由布尔改计数**：原判据 `new_q["integrity_ok"] is False` 在
    #     **原稿本身就不干净**时恒为真（三个候选摘要全标「完整性异常」）→ 结构轮
    #     5 → 4 的改善稿被拒，采纳分支永不通过 = 没有收敛轮。新增
    #     `integrity_count`（checker 失败为 None，未知不得当成干净）+
    #     `_integrity_not_degraded()`：**不退化**（用户文档用词）而非「必须干净」。
    #     结构轮与时长轮共用。
    #   · **锁定字段闸门的基线改到「合并后、后处理前」**：旧基线是本轮开始前，中间
    #     隔着 post_fix / hard_fix / write_timeline_from_durations 一串**管线自己的
    #     确定性归一化**，于是 B.1 `narration_subtitle_consistency` 去掉被点名镜头
    #     subtitle 末尾句号也被算成 LLM 越权 → `non_target_changed` → 整轮回退，
    #     `resolved=11` 一起丢。已确定性复现（tests/.../test_repair_mask_baseline.py）。
    #   · **闸门分开命名 + 打出到底什么变了**：`non_target_changed` 连续三次成为
    #     回退原因而日志从不说明是哪个镜头哪个字段；现拆为
    #     `non_target_changed` / `locked_field_changed`，并在回退前打印具体条目。
    #   · `_rejected` 日志拆「真被拒」与「部分提交说明」（A1 逐目标提交后语义不同）。
    # ========================================================================
    "v6.87": {
        "name": "采纳判据收口：完整性按「不退化」比较 + 锁定字段基线改合并后 + "
                "回退原因分开命名",
        "date": "2026-09-21",
        "prompt_sections": [],
        "constraints": [],
        "validation_checks": [],
        "timecode_changes": [
            "新增 `_integrity_not_degraded()` 与 `_candidate_quality_summary` 的 "
            "`integrity_count`：采纳判据统一按「不退化」比较",
            "`derive_hook_duration()` 候选增加 **KF 最大 `t_sec`**：关键帧写在哪个"
            "物理时刻，镜头就必须长到能拍到它（实测钩子 3.0s 而 KF 到 7.2s → 2 条 "
            "`kf_time_out_of_shot` HARD）",
        ],
        "style_prefix": [],
        "tests": [
            "tests/step01_script_gen/test_repair_mask_baseline.py",
            "tests/step01_script_gen/test_repair_acceptance.py",
            "tests/step01_script_gen/test_structural_convergence.py",
            "tests/step01_script_gen/test_duration_convergence.py",
        ],
    },

    # ========================================================================
    # v6.86 — 用户决定 A1 / B1+B2 / 朝向同义词豁免（2026-09-21）
    #
    # 来源：用户审阅 docs/step01-temporal-writers-postrun-review-2026-09-21.md
    # 的「待决问题」后给出的决定。真实重跑 hard=7 / visual=20。
    #   · 发现 10 → **A1 逐目标提交**：合法且授权的目标独立生效，缺项/越权只丢
    #     自己；缺项作为**如实回报**进 `rejected`。保留批次级安全网（全局完整性 /
    #     非目标 revision / 阻塞退化 / 锁定字段签名）。
    #   · 发现 11 → **B1 判定口径改实际 `Σduration_sec`**（原用拆镜后投影，而生产
    #     已停用拆镜 pre-pass）；**B2 采纳判据 = 严格改善 + 内容完整性 + 内容不退化**
    #     （原为解析成功即无条件替换）。「内容不退化」由 `_content_degradation()`
    #     给出：结构非法镜头数不增加、原文覆盖**率**不退化、grounding **率**不退化
    #     ——缩短时长不得用「删内容」换。两条度量刻意用**比率**：计数口径会把
    #     「合法合并镜头」误判成退化，让这条守卫变成新的「永不收敛」缺口。
    #     修复预算**暂时保持 1 次**。
    #   · `multi_person_staging`：`直立` / `直立扬手` 属检查器同义词漏项 → 豁免；
    #     `站位「水面」`没有左右方位事实、不能确定性推断 → **继续报告**交 LLM。
    # ========================================================================
    "v6.86": {
        "name": "用户决定落地：逐目标提交 + 时长收敛实际口径与采纳守卫 + "
                "朝向姿态词豁免",
        "date": "2026-09-21",
        "prompt_sections": [],
        "constraints": [],
        "validation_checks": [
            "_check_multi_person_staging",
        ],
        "timecode_changes": [
            "新增 `_actual_shots_total()`：时长收敛统一用实际 Σduration_sec，"
            "拆镜投影口径只留给候选打分",
            "新增 `_content_degradation()`：B2 的内容退化判据（结构非法镜头数 / "
            "原文覆盖率 / grounding 率，用比率保镜头数无关）",
        ],
        "style_prefix": [],
        "tests": [
            "tests/step01_script_gen/test_duration_convergence.py",
            "tests/step01_script_gen/test_repair_shot_replacements.py",
            "tests/step01_script_gen/test_multi_person_staging_actions.py",
        ],
    },

    # ========================================================================
    # v6.85 — 半整数重编号后重跑审查的收敛路径缺口（2026-09-21 22:22 实测）
    #
    # 来源：docs/step01-temporal-writers-postrun-review-2026-09-21.md
    # 真实重跑 hard=7 / visual=20（16 镜 / 正片 93.3s）。
    #   · 结构收敛轮的采纳判据**由内容分主导**，实测把「非法镜头 7 → 5」判成
    #     「未改善」丢弃 → 改为「结构非法数严格变少即采纳」，唯一附加守卫是
    #     不许引入完整性异常（`integrity_ok is False`）
    #   · `kf_event_window_mismatch`（最大阻塞桶 18 → 9 条）既不在修复轮白名单、
    #     也不是结构类 → **无任何收敛路径**；接进 `_REPAIR_PRIORITY_CATEGORIES`，
    #     并在 `repair_fields` 登记 canonical 形态 `keyframes.*.desc`
    #     （原 `field_path="keyframes.desc"` 过不了 `path_allowed`）
    #   · `weapon_holder` 新增两类误报：比喻（目光如刀）、动宾（挥鞭）；
    #     首位出现位置是比喻时整词漏判（`t.find` 只看首个）→ 逐出现位置判定
    #   · `prompts`：补「单镜台词容量自检（总字数 ÷ 4 ≤ 8s，安全值 ≤ 28 字）」；
    #     把「动作/空镜 3-5s」收紧为 **3.5-5s**——原写法推荐的 3.0-3.4s 正好落在
    #     质量门 `shot_duration_below_min` 的 blocking 区间，提示词与门禁直接矛盾
    # ========================================================================
    "v6.85": {
        "name": "收敛路径接线：结构采纳判据 + kf_event_window_mismatch 入修复轮 + "
                "武器 holder 比喻/动宾误报 + 提示词与门禁口径对齐",
        "date": "2026-09-21",
        "prompt_sections": [
            "prompts/system.md:B1. 单镜下限",
            "prompts/system.md:B2. 台词容量自检",
            "prompts/main.md:2b. 台词容量",
        ],
        "constraints": [],
        "validation_checks": [
            "_check_weapon_holder",
            "_first_literal_occurrence",
            "_clause_index_at",
        ],
        "timecode_changes": [],
        "style_prefix": [],
        "tests": [
            "tests/step01_script_gen/test_structural_convergence.py",
            "tests/step01_script_gen/test_weapon_holder.py",
        ],
    },

    # ========================================================================
    # v6.84 — 重跑审查 6 项收口（2026-09-21）
    #
    # 来源：docs/step01-temporal-writers-postrun-review-2026-09-21.md
    # 真实重跑结果 hard=12 / visual_risk=9，缺口在**生成与修复的收敛路径**。
    #   发现 1（P0）：超长/过短镜头只有报告，没有可执行的上游收敛路径。
    #       · `_regen_structural_over_limit()`：结构问题点名反馈给 LLM **重做
    #         shot-plan**（超限段落拆成多个完整子镜 / 过短镜头并入相邻剧情），
    #         新稿只有不劣化才采纳；预算用尽明确 BLOCK
    #       · 候选选择把 `shot_content_over_cap` / `shot_duration_below_min`
    #         当**拒收条件**（有预算重取，没预算明确 BLOCK）
    #       · `_candidate_quality_summary` 增 `structural_ok` 档，置于排序首位
    #   发现 2（P0）：屏障有效，但注册顺序仍让两个写入者落在审计段之后。
    #       · 约束链拆成显式三阶段（PRE_TEMPORAL_NORMALIZE / TEMPORAL_DERIVE /
    #         POST_TEMPORAL_AUDIT），分界 `TEMPORAL_DERIVE_START` 与
    #         `semantic_barrier.AUDIT_SEGMENT_START` **同一份定义**
    #       · `prompt_sanitize` 与 `v6_43_prompt_enforcement` 前移到第 1 段
    #         （两者都在写 motion_desc）
    #   发现 3（P1）：修复调用失败后仍跑全局后处理 → 模型失败立即 return；
    #       事务内后处理只作用于被点名镜头（未点名镜头快照-还原）；批次 6→3。
    #   发现 4（P1）：`weapon_holder` 把「匕首插在心口」误判成悬浮武器 →
    #       关系级落点判定（身体部位 + 插入动词 / 延续词），显式悬浮标记优先。
    #   发现 5（P1）：hook `t_sec=0` 到最终门才暴露 → 钩子质量门内先
    #       `sync_hook_timeline()` 再检查；`duration_sec` 缺失回退 `duration`；
    #       `t_sec<=0` 单独编码为 `kf_at_shot_start`。
    #   发现 6（P2）：`temporal_plan` 保持只读，候选报告统计
    #       present/valid/grounded 积累真实样本。
    # ========================================================================
    "v6.84": {
        "name": "重跑收口：结构收敛路径 + 约束链三阶段 + 事务范围收口 + "
                "武器落点关系 + 钩子时间不变量",
        "date": "2026-09-21",
        "constraints": [
            "prompt_sanitize",
            "v6_43_prompt_enforcement",
            "group_actor_continuity",
            "emotion_visualization_repair",
            "kf_start_time_normalize",
            "shot_index_renumber",
            "runtime_extend",
        ],
        "validation_checks": [
            "_check_weapon_holder",
        ],
        "timecode_changes": [
            "抽出 derive_hook_duration()/sync_hook_timeline()：钩子质量门与 "
            "_compute_timecodes 共用同一份时长推导（发现 5）",
        ],
        "style_prefix": [],
        "tests": [
            "tests/step01_script_gen/test_structural_convergence.py",
            "tests/step01_script_gen/test_hook_time_invariant.py",
            "tests/step01_script_gen/test_temporal_plan_migration_stats.py",
            "tests/step01_script_gen/test_weapon_holder.py",
            "tests/step01_script_gen/test_repair_shot_replacements.py",
            "tests/shared/test_semantic_barrier.py",
            "tests/step01_script_gen/test_temporal_plan_migration_stats.py",
            "tests/step01_script_gen/constraints/test_shot_index_renumber.py",
        ],
    },

    # ========================================================================
    # v6.83 — 二次审核 6 项收口（2026-09-20）
    #
    # 来源：docs/step01-temporal-writers-rereview-2026-09-20.md
    #   发现 1（P0）：阶段屏障 fail-open —— 白名单放行链尾 `runtime_extend`，
    #     12s 镜头夹成 8s 后 `t_sec=10` 的 KF 留在最终对象里。
    #       · `SANCTIONED_TIME_WRITERS` 清空（审计段不允许任何时间字段写入者）
    #       · `runtime_extend` 的单镜 cap 夹取降为 HARD 报告（cap 属时长派生，
    #         只能发生在 KF 生成之前；`_compute_timecodes` 已在权威派生点夹取）
    #       · 新增 `finalize_step01_script()`：正常生成 / 缓存命中 / BEST-GUARD
    #         三条路径统一走「hard-fix(含屏障) → timeline 序列化 → 最终不变量」
    #       · 屏障结果进质量门（`time_field_audit_write`，blocking）
    #       · 新增最终时间不变量检查（`kf_time_out_of_shot` /
    #         `duration_two_calibers` / `timecode_span_mismatch`）
    #   发现 2（P0）：整镜事务只锁固定结构字段 → 新增 `repair_fields.py`，
    #     按 issue 推导授权字段 + canonical 路径 diff；`duration` 补进最终 mask。
    #   发现 3（P1）：hook-only replacement 在主路径不可达 → envelope 两个键
    #     都允许为空；hook 必须由 `hook_replacement` 承载。
    #   发现 4（P1）：整批原子未校验目标集合 → 每个 picked 目标恰好出现一次。
    #   发现 5（P1）：事件表只比边界不比语义 → 描述与 motion 段正文「无任何共同
    #     内容」即拒；`source_quote` 范围收到**本镜**原文窗口（越界 = blocking）。
    #   发现 6（P2）：`_source_hash` 被运行时字段清理删除 → 改落 `source_hash`
    #     （读侧兼容旧键）；规则源指纹纳入 4 份共享实现。
    #   另修一处**未在审核中报告**的静默 bug：`allowed_targets` 是未定义名，
    #     整段接受判定抛 NameError 被 except 吞掉并回退整轮修复——v6.79～v6.82
    #     新增的 non-target gate / 最终 mask 在生产里从未真正跑过。
    # ========================================================================
    "v6.83": {
        "name": "二次审核收口：屏障 fail-closed + 按 issue 授权 + 事务完整性 + "
                "缓存身份",
        "date": "2026-09-20",
        "constraints": [
            "runtime_extend",
        ],
        "validation_checks": [
            "_check_temporal_plan",
        ],
        "timecode_changes": [
            "新增 finalize_step01_script()：三条落盘路径统一的「hard-fix(含阶段"
            "屏障) → write_timeline_from_durations → 最终时间不变量」收口",
        ],
        "style_prefix": [],
        "tests": [
            "tests/shared/test_semantic_barrier.py",
            "tests/shared/test_motion_segments.py",
            "tests/step01_script_gen/test_repair_fields.py",
            "tests/step01_script_gen/test_repair_shot_replacements.py",
            "tests/step01_script_gen/test_rule_version_cache.py",
            "tests/step01_script_gen/constraints/test_runtime_extend.py",
        ],
    },

    # ========================================================================
    # v6.82 — 复核剩余项收口（2026-09-20，发现 3/4/5/6/7/8/11）
    #
    # 上一批（v6.79～v6.81）把四个链尾写入者降为只报告、统一了解析器、接上了
    # 整镜事务与阶段屏障。本批处理复核里**剩下的**那一类问题：契约文字与实现
    # 互相矛盾、缺回归、以及守卫只存在于测试里。
    #   发现 11：temporal_plan 纳入 semantic revision（已随 v6.79 落地）+
    #            规则版本升级使旧 audit 失效 —— 本版补齐 revision 回归
    #   发现 3 ：修复轮改调纯序列化 write_timeline_from_durations（不重估时长）+
    #            锁定字段 mask 在**全部后处理结束后**比较 + 增删镜头由逐镜
    #            revision gate 拒绝 —— 本版补齐「时长/mask/revision 三重不变」回归
    #   发现 4 ：权威时间语法拒绝句内裸量词范围（人数/步数/尺寸）、丢弃完全越界段
    #            而非产出反向区间；`extract_total_duration` 不再另持一套正则；
    #            裸范围保留兼容但**可观测**（bare_range_atoms → 校验上报）
    #   发现 5 ：事件窗口统一半开区间 [start, end)（末段闭合）；重复采样契约
    #            三处对齐（docstring / 告警文案 / 实现），边界归属锁定到具体事件
    #   发现 6 ：`_Final` 删除理由改为如实的「顺序依赖」而非「全输入字节级等价」；
    #            新增 blood monotonic ↔ v6_43 的交换律反例回归
    #   发现 7 ：拆镜差异化不得删除人物位置/道具/姿态事实（补事实守恒回归）
    #   发现 8 ：`get_kf_t_sec` 双 duration 不一致时的兼容契约补测试
    # ========================================================================
    "v6.82": {
        "name": "复核剩余项：契约一致化 + 顺序依赖回归 + 阶段屏障运行时守卫",
        "date": "2026-09-20",
        "constraints": [
            "kf_blood_monotonic",
        ],
        "validation_checks": [
            "_check_temporal_plan",
        ],
        "timecode_changes": [
            "修复轮不再调用 _compute_timecodes(allow_split=False)，改调纯序列化 "
            "write_timeline_from_durations（发现 3）",
        ],
        "style_prefix": [],
        "tests": [
            "tests/shared/test_kf_time_contract.py",
            "tests/shared/test_semantic_barrier.py",
            "tests/shared/test_quality_gate.py",
            "tests/shared/test_motion_segments.py",
            "tests/step01_script_gen/constraints/test_constraint_order_dependency.py",
            "tests/step01_script_gen/constraints/test_structure_fixes_0912.py",
            "tests/step01_script_gen/test_repair_shot_replacements.py",
            "tests/step01_script_gen/test_split_shot_differentiation.py",
            "tests/step01_script_gen/test_golden_chapter_001.py",
        ],
    },

    # ========================================================================
    # v6.81 — 整镜事务边界收口（2026-09-20，发现 9/10）
    #
    # 发现 9：hook 与普通镜头 schema 分离；`duration_sec` 单一派生口径；
    #         整批全有或全无；事务末尾再比一次最终字段 mask
    # 发现 10：source_quote 必须在原文可定位（+ 对抗测试）
    # ========================================================================
    "v6.81": {
        "name": "整镜事务边界收口：hook 分离 / duration 单一口径 / 整批原子 / 最终 mask / 引文 grounding",
        "date": "2026-09-20",
        "constraints": [],
        "validation_checks": ["_check_temporal_plan"],
        "timecode_changes": [],
        "style_prefix": [],
        "tests": [
            "tests/step01_script_gen/test_repair_shot_replacements.py",
            "tests/shared/test_motion_segments.py",
        ],
    },

    # ========================================================================
    # v6.80 — 阶段屏障：时间字段写入跟踪（2026-09-20）
    #
    # 对应 implementation-review 发现 1 的实施方案 #1/#2：约束链是单遍流水线，
    # 链后写入会让链前检查失效。本版给引擎加可选钩子（默认 None，零行为变更）
    # 并新增 shared/semantic_barrier.py，把「审计段不得写时间字段」变成
    # **可执行守卫**而不是靠人工维持的注册顺序约定。
    # ========================================================================
    "v6.80": {
        "name": "阶段屏障：时间字段写入跟踪 + 引擎回调钩子",
        "date": "2026-09-20",
        "constraints": [],
        "validation_checks": [],
        "timecode_changes": [],
        "style_prefix": [],
        "tests": [
            "tests/step01_script_gen/test_golden_chapter_001.py",
        ],
    },

    # ========================================================================
    # v6.78 — 时间结构落地：事件表 + 事件边界拆镜 + 镜头事务修复（2026-09-19）
    #
    # 文档 docs/step01-temporal-writers-audit-2026-09-19.md 第二/三/四阶段：
    #   二阶段：temporal_plan 事件表（LLM 直出可选，不合格严格回退派生）
    #   三阶段：拆镜切点改为**事件边界**（不再用旁白字符比例）；无合法边界不拆；
    #           移除拆镜的语义注入（过肩/情绪/焦点）
    #   四阶段：修复轮改为**完整镜头事务**（shot_replacements），
    #           并新增「非目标镜头 revision 必须不变」判据
    # ========================================================================
    "v6.78": {
        "name": "时间结构落地：temporal_plan 事件表 + 事件边界拆镜 + 镜头事务修复",
        "date": "2026-09-19",
        "constraints": [
            "motion_time_integrity",
            "split_continuity",
        ],
        "validation_checks": [
            "_check_temporal_plan",
        ],
        "timecode_changes": [
            "拆镜切点改为 temporal_plan 事件边界（新增 _event_boundary_cut），不再用旁白字符比例",
            "无合法事件边界时不拆（[SPLIT-BLOCKED] 上报），不再退化硬切",
            "移除拆镜伪造的 motion 文本（「从首帧推进到切分状态，完成：…」）",
            "移除同一函数内重复的 differentiate_split_visuals 调用",
        ],
        "style_prefix": [],
        "tests": [
            "tests/shared/test_motion_segments.py",
            "tests/step01_script_gen/test_golden_chapter_001.py",
            "tests/step01_script_gen/test_repair_shot_replacements.py",
            "tests/step01_script_gen/test_split_shot_differentiation.py",
            "tests/step01_script_gen/test_split_frame_layouts_kfs.py",
            "tests/step01_script_gen/test_timecode_split_gate.py",
        ],
    },

    # ========================================================================
    # v6.77 — motion_desc 时间解析统一（2026-09-19）
    #
    # 同一件事（从 motion_desc 抽时间段）原先有 **11 套互不一致的解析器**：
    # 分隔符两组互不覆盖（`至/到` vs 全角 `－/～`），且剥标签正则只认整数
    # ——`0-0.378378秒：…` 被剥成 `.378378秒：…` 残片写进 keyframes[*].desc。
    # 现全部收敛到 `shared/motion_segments`（唯一事实源），并解除
    # step02→step01 的反向 import。
    # ========================================================================
    "v6.77": {
        "name": "motion_desc 时间解析统一：16 处解析点收敛到 shared/motion_segments",
        "date": "2026-09-19",
        "constraints": [
            "motion_time_bounds",
            "kf_event_order",
            "split_continuity",
            "final_quality_normalizer",
            "kf_time_window_alignment",
            "kf_temporal_consistency",
            "duration_budget",
            "weapon_state_consistency",
        ],
        "validation_checks": [
            "_parse_motion_time_segments",
            "_split_motion_to_seg_texts",
            "_check_kf_time_window_alignment",
            "_check_temporal_plan",
        ],
        "timecode_changes": [
            "_rewrite_motion_time / _split_motion_desc_at_cut / _extract_motion_duration 委托 shared.motion_segments",
            "时间标签统一截 2 位小数（原先 :g 输出 6 位有效数字 → 消除「0-0.378378秒」残缺文本）",
        ],
        "style_prefix": [],
        "tests": [
            "tests/shared/test_motion_segments.py",
            "tests/step01_script_gen/test_golden_chapter_001.py",
        ],
    },

    # ========================================================================
    # v6.76 — 时间轴证据门：KF 时间写入者全降为只报告（2026-09-19）
    #
    # 与 v6.75 同属 "evidence-gated writes" campaign 的另一半：
    #   v6.75 覆盖 **desc 层**写入者（不确定的文本修复保留原文并报告）；
    #   v6.76 覆盖 **时间层**写入者（t_sec / duration_sec），即
    #   docs/step01-temporal-writers-audit-2026-09-19.md 的第一阶段。
    #
    # 另删除 3 个纯重复注册的 _Final 变体（kf_time_window_alignment_final /
    # kf_event_order_final / kf_pose_anchor_final）——其基类已降为只报告，
    # 实例只是把同一条只读检测再跑一遍。另 4 个 _Final（血状态×2 / 群体在场 /
    # 帧文本清理）**保留**：基类仍写 kf.desc，用于链尾 frame_anchor_rebuild
    # 重建后回补锚点，删掉属行为变更，须与各自基类降级一并进行。
    # ========================================================================
    "v6.76": {
        "name": "时间轴证据门：KF 时间写入者降为只报告 + 修复轮禁拆镜 + 时长不再拉伸",
        "date": "2026-09-19",
        "constraints": [
            "kf_monotonic",
            "kf_t_sec_bounds",
            "kf_timeline_normalize",
            "kf_time_window_alignment",
            "kf_temporal_consistency",
            "hook_duration",
            "runtime_extend",
            "duplicate_shot_merge",
        ],
        "validation_checks": [],
        "timecode_changes": [
            "_compute_timecodes 新增 allow_split 闸门：修复轮传 False，只重建 timeline 不增删镜头",
        ],
        "style_prefix": [],
        "tests": [
            "tests/step01_script_gen/constraints/test_kf_monotonic.py",
            "tests/step01_script_gen/constraints/test_kf_t_sec_bounds.py",
            "tests/step01_script_gen/constraints/test_kf_timeline_normalize.py",
            "tests/step01_script_gen/constraints/test_kf_time_window_alignment.py",
            "tests/step01_script_gen/constraints/test_kf_pre_post_conflict.py",
            "tests/step01_script_gen/constraints/test_hook_duration.py",
            "tests/step01_script_gen/constraints/test_runtime_extend.py",
            "tests/step01_script_gen/constraints/test_full_hardfix_kf_idempotency.py",
            "tests/step01_script_gen/test_timecode_split_gate.py",
            "tests/step01_script_gen/constraints/test_structure_fixes_0912.py",
        ],
    },

    # ========================================================================
    # v6.75 — 语义写入安全门 + 全约束源码指纹（2026-09-19）
    # ========================================================================
    "v6.75": {
        "name": "evidence-gated writes：不确定的语义修复保留原文并报告，禁止自动猜状态/删内容",
        "date": "2026-09-19",
        "constraints": [
            "axis_jump",
            "char_side_continuity",
            "threat_gaze_coupling",
            "prop_spatial_anchor",
            "surface_backfill",
            "frame_spec_repair",
            "kf_pose_anchor",
            "climax_completeness",
            "restrained_emotion_chain",
            "redguo_scene_char_limit",
            "dialogue_density",
            "v6_43_prompt_enforcement",
            "final_quality_normalizer",
            "group_actor_continuity",
            "split_continuity",
        ],
        "validation_checks": [],
        "timecode_changes": [],
        "style_prefix": [],
        "tests": [
            "tests/step01_script_gen/constraints/test_axis_jump.py",
            "tests/step01_script_gen/constraints/test_structure_fixes_0912.py",
            "tests/step01_script_gen/constraints/test_threat_gaze_coupling.py",
            "tests/step01_script_gen/constraints/test_prop_position.py",
            "tests/step01_script_gen/constraints/test_surface_backfill.py",
            "tests/step01_script_gen/constraints/test_frame_spec_repair.py",
            "tests/step01_script_gen/constraints/test_kf_pose_anchor.py",
            "tests/step01_script_gen/constraints/test_climax_completeness.py",
            "tests/step01_script_gen/constraints/test_restrained_emotion_chain.py",
            "tests/step01_script_gen/constraints/test_redguo_scene_char_limit.py",
            "tests/step01_script_gen/constraints/test_v6_62_2_generic_fallbacks.py",
            "tests/step01_script_gen/constraints/test_prompt_enforcement.py",
            "tests/step01_script_gen/constraints/test_final_quality_normalizer.py",
            "tests/step01_script_gen/constraints/test_group_actor_continuity.py",
            "tests/step01_script_gen/constraints/test_split_continuity.py",
        ],
    },

    # ========================================================================
    # v6.74 — 60-90s 硬时长门 + 修复契约 + hook 结构前置（2026-09-19）
    # ========================================================================
    "v6.74": {
        "name": "正片 60-90s 硬时长门 + patch-only 修复接受条件 + hook 质检前结构补齐",
        "date": "2026-09-19",
        "constraints": [
            "duration_budget",
            "motion_time_bounds",
            "offscreen_action_consistency",
            "identity_unification",
            "frame_anchor_rebuild",
            "kf_event_order",
            # "kf_time_window_alignment_final" 已于 v6.76 删除（纯重复注册实例）
            "split_continuity",
            "core_keyword_pollution_cleanup",
            "emotion_visualization_repair",
        ],
        "validation_checks": [
            "_check_weapon_holder",
            "_check_multi_person_staging",
            "_check_person_boat_position",
            "_check_action_load",
        ],
        "timecode_changes": [
            "motion_desc 范围缺少“秒”时仍按区间终点计算",
            "正片 duration_sec 之和纳入 60-90s 验收区间",
        ],
        "tests": [
            "tests/step01_script_gen/constraints/test_duration_budget.py",
            "tests/step01_script_gen/constraints/test_new_quality_guards.py",
            "tests/step01_script_gen/test_repair_acceptance.py",
            "tests/step01_script_gen/test_repair_patches.py",
            "tests/step01_script_gen/test_metrics.py",
            "tests/step01_script_gen/test_hook_quality_gate.py",
            "tests/step01_script_gen/test_weapon_holder.py",
            "tests/step01_script_gen/test_multi_person_staging_actions.py",
            "tests/step01_script_gen/test_person_boat_position.py",
            "tests/step01_script_gen/test_action_load.py",
            "tests/step01_script_gen/constraints/test_identity_unification.py",
            "tests/step01_script_gen/constraints/test_frame_anchor_rebuild.py",
            "tests/step01_script_gen/constraints/test_kf_event_order.py",
            "tests/step01_script_gen/constraints/test_split_continuity.py",
            "tests/step01_script_gen/constraints/test_core_keyword_pollution_cleanup.py",
            "tests/step01_script_gen/constraints/test_emotion_visualization_repair.py",
        ],
    },

    # ========================================================================
    # v6.70 — 总时长规划目标 70-80s + 武器 holder 校验 + kf 帧级冗余（2026-09-13）
    # ========================================================================
    "v6.70": {
        "name": "总时长规划目标 70-80s（合法带 60-90s 不动）+ weapon_holder 校验 + kf 帧级冗余 + kf 参考图 per-frame 化",
        "date": "2026-09-13",
        "prompt_sections": [
            "prompts/system.md:K3. mid 阶段 kf 必须与 ff 拉开动作差异",
            "prompts/system.md:规划目标 70-80s（按 ~75s 分配）",
            "prompts/doubao_system.md:RULE-13b",
            "prompts/doubao_system.md:RULE-8",
            "prompts.py:_build_total_duration_constraint",
            "prompts.py:_build_regen_iron_rules",
        ],
        "constraints": [
            "kf_frame_redundancy",       # K9: mid-kf≈ff / kf≈kf / spec 复制检测（WARN）
        ],
        "validation_checks": [
            "_check_weapon_holder",      # 武器词必须有持握主体/落点锚（WARN，hook 悬空匕首根因）
        ],
        "tests": [
            "tests/step01_script_gen/constraints/test_kf_frame_redundancy.py",
            "tests/step01_script_gen/test_weapon_holder.py",
            "tests/shared/test_kf_refs_per_frame.py",  # vimax_bridge._compose_kf_refs（step02a 侧）
        ],
    },

    # ========================================================================
    # v6.69 — 旧 _hard_fix_* 家族删除 + 结构归一约束三件套（2026-09-12）
    # ========================================================================
    "v6.69": {
        "name": "旧 hard_fix 家族删除（-2973 行）+ kf 归一/重复合并/侧翻转约束 + 拆分验证回滚",
        "date": "2026-09-12",
        "prompt_sections": [],
        "constraints": [
            # 新增三件套（95 分稿复盘根治）
            "kf_timeline_normalize",     # kf 时间线归一：同刻重排/越界钳制/标签错位/hold帧回归
            "duplicate_shot_merge",      # 相邻镜重复视觉 HARD 合并（12/12.5 凑镜根治）
            "char_side_continuity",      # 同地点连续镜角色侧翻转交换（10→11 越轴根治）
            # 随家族删除一并变更：ship-integrity 条款加「本镜在船上」门控（vimax_bridge）
            # timecode 超长拆镜器补接 differentiate + 拆分后验证回滚（b.ff/b.lf、a.lf/b.lf ≥0.85 不拆）
            # post_fix：motion「N-M三」归一 / prompt 句读终端不变量 / 打斗反馈水体选词
        ],
        "validation_checks": [],
        "tests": [
            "tests/step01_script_gen/constraints/test_kf_timeline_normalize.py",
            "tests/step01_script_gen/constraints/test_structure_fixes_0912.py",
            "tests/step01_script_gen/test_postfix_roots_0912.py",
        ],
        # 删除记录：legacy-only 行为 kneel_beside_victim / frame_layout_consistency /
        # hook_env_anchor / hook_witness_not_killer 随家族移除（v6.42 起未在默认管线
        # 运行；git 历史可找回）。约束共享常量 16 个迁至 validation 顶部保留。
    },

    # ========================================================================
    # v6.68 — per-model prompt 剖面（2026-09-12）
    # ========================================================================
    "v6.68": {
        "name": "prompt/system 跟实际模型走（MODEL_PROMPT_PROFILES）：根治候选结构性零高潮",
        "date": "2026-09-12",
        "prompt_sections": [
            "prompts.py:MODEL_PROMPT_PROFILES",
        ],
        "constraints": [],
        "validation_checks": [],
        "tests": [
            "tests/step01_script_gen/test_model_prompt_profile.py",
        ],
    },

    # ========================================================================
    # v6.61 — provider-neutral step01 framework
    # ========================================================================
    "v6.61": {
        "name": "共享生成/修复/GAP/硬修复框架：provider 只影响 prompt/system",
        "date": "2026-09-06",
        "prompt_sections": [],
        "constraints": [
            "narration_subtitle_consistency",
            "core_keyword_coverage",
            "role_anchor",
            "opening_empty_shot_insert",
            "climax_distribution",
            "overshoulder_consistency",
            "frame_spec_repair",
            "mandatory_biz_fields",
        ],
        "validation_checks": [],
        "tests": [
            "tests/step01_script_gen/constraints",
            "tests/step01_script_gen",
            "tests/tools/test_bench_text_provider_ab.py",
        ],
    },

    # ========================================================================
    # v6.60 — historical provider-specific chain, superseded by v6.61
    # ========================================================================
    "v6.60": {
        "name": "doubao 专用链：DOUBAO_SYSTEM_PROMPT + 修复轮/GAP 重生 prompt + 7 个 hard_fix Constraint",
        "date": "2026-09-05",
        "prompt_sections": [
            "prompts/doubao_system.md",
        ],
        "constraints": [
            # 历史专用约束已由 v6.61 的全局共享约束承接
            "narration_subtitle_consistency",
            "core_keyword_coverage",
            "role_anchor",
            "opening_empty_shot_insert",
            "climax_distribution",
            "overshoulder_consistency",
            "frame_spec_repair",
        ],
        "validation_checks": [],
        "tests": [
            "tests/step01_script_gen/constraints",
        ],
        # 非 Constraint 配套模块（构造器/规则行常量）：
        # 该历史链已删除，功能由 v6.61 全局约束承接。
    },

    # ========================================================================
    # v6.59 — shell 环境变量覆盖 .env.content 的 PROVIDER 检测（仅 WARN）
    # ========================================================================
    "v6.59": {
        "name": "PROVIDER shell 覆盖检测（shared/env_guard，不阻断）",
        "date": "2026-09-04",
        "constraints": [],   # 非 Constraint：main.py 模块级调 env_guard.warn_shell_overrides
        "validation_checks": [],
        "tests": [],
    },

    # ========================================================================
    # v6.58 — camera_movement 字段接入 3 个视频 provider（Phase C-1）
    #       + motion_desc 时间分段解析与 prompt 注入（Phase C-2）
    #       + A7 拆分镜内容重复检测 + B9 拆分镜 frame_layouts.kfs 修复
    # ========================================================================
    "v6.58": {
        "name": "camera_movement provider 接入 + motion 分段 + 拆分镜重复/kfs 修复",
        "date": "2026-09-03",
        "prompt_sections": [
            # 不改 prompt，只改消费侧
        ],
        "constraints": [
            # 复用 v6.47 camera_movement_fill 写入的结构化字段
            # 复用 v6.42 kf_time_window_alignment 的 _parse_motion_time_segments
            "split_content_similarity",  # A7: 拆分镜内容重复检测（WARN）
        ],
        "validation_checks": [],
        "style_prefix": [],
        "tests": [
            "tests/_video_gen/test_camera_movement_provider.py",     # 18 测
            "tests/shared/test_motion_segments.py",                 # 19 测
            "tests/step01_script_gen/constraints/test_split_content_similarity.py",  # 17 测
            "tests/step01_script_gen/test_split_frame_layouts_kfs.py",  # 6 测
        ],
    },

    # ========================================================================
    # v6.57 — frame_spec / per-frame 出镜表事实源
    # ========================================================================
    "v6.57": {
        "name": "per-frame spec 事实源（frame_layouts.kfs 与 keyframes 一一对应）",
        "date": "2026-08-30",
        "prompt_sections": [
            "prompts/main.md:frame_layouts 字段",
        ],
        "constraints": [
            "frame_spec_consistency",  # S: per-frame spec 必填+完整性+desc 一致性
        ],
        "validation_checks": [
            # validation.py 侧的旧检查已逐步迁移到 constraint
        ],
        "tests": [
            "tests/shared/test_frame_spec.py",
        ],
    },

    # ========================================================================
    # v6.51 — 时序三铁律 + 跨镜衔接 + 三态冗余 + 结构化字段
    # ========================================================================
    "v6.51": {
        "name": "时序三铁律（FF前一刻 / LF留接口 / motion分段写）+ 跨镜衔接 + 拆分镜kf派生",
        "date": "2026-09-02",
        "prompt_sections": [
            "prompts/main.md:v6.51 时序三铁律",
            "prompts/main.md:v6.51.1 单镜内字段一致性",
            "prompts/hook.md",  # 整文件同步适用
        ],
        "constraints": [
            # FF 前一刻
            "ff_motion_overlap",        # F3 增强：water_break 变体 + WB姿态→水下姿态
            "ff_lf_temporal",           # F10 ff/lf 时序协议
            "lf_midstate",              # Q: lf 动作进行态→终态
            # 跨镜衔接
            "cross_shot_continuity",    # K2: 场景转场 + 状态跳变 + narr重复 + LF同态
            "transition_keywords",      # W4: 转场关键词（旧约束，与 K2 协同）
            # 三态冗余
            "ff_lf_redundancy",         # K3: FF/LF/Motion 内容重复检测
            # 结构化字段
            "camera_movement_fill",     # J3 增强：同步填 camera_movement 字段
            "prompt_sanitize",          # J4: （画中X）/（AI生成）清理
            "prop_prompt_consistency",  # L2: 道具别名→标准名
        ],
        "style_prefix": [
            "normalize_ff_desc",            # 主入口
            "detect_ff_multi_action",       # 检测
            "_WB_POSE_TO_UNDERWATER",       # v6.51 新增：出水后姿态→水下姿态
            "_strip_ff_time_advance",
            "_split_ff_clauses",
            "_preactionize_ff_motion",
            "strip_spatial_anchors",        # shared/utils/text.py
            "strip_debug_markers",
        ],
        "timecode_changes": [
            "拆分镜 keyframes 派生（_split_narration_shot：从原镜截取后半段kf）",
        ],
        "validation_checks": [
            "_check_ff_lf_desc_completeness",
            "_check_first_frame_timing",
            "_check_transition_keywords",
            "_check_keyframe_quality",
        ],
    },

    # ========================================================================
    # v6.50 — ff/lf 时序协议（本镜有攻击时 ff 禁结束态）
    # ========================================================================
    "v6.50": {
        "name": "ff/lf 时序协议：本镜有攻击时 ff 禁结束态",
        "date": "2026-08-28",
        "constraints": [
            "ff_lf_temporal",           # F10
        ],
    },

    # ========================================================================
    # v6.49 — 红果 60-90s 标准 + 8 项硬修复（F3-F10）
    # ========================================================================
    "v6.49": {
        "name": "红果标准（60-90s/3-8镜/≤3场景/≤4人物）+ F3-F10 硬修复",
        "date": "2026-08-28",
        "prompt_sections": [
            "prompts/main.md:v6.49 红果场景/人物硬约束",
        ],
        "constraints": [
            "redguo_scene_char_limit",   # F5: 场景/人物硬上限（HARD）
            "runtime_extend",            # W6: 时长拉伸（HARD）
            "short_shot_merge",          # F9: 短镜合并（HARD）
            "climax_priority",           # F8: 高潮优先（0镜日常）
            "ff_motion_overlap",         # F3: ff 动作重叠
            "kf_blood_monotonic",        # F2: kf 血状态单调
            "kf_pose_anchor",            # F4: kf 姿态+位置锚定
            "post_combat_blank",         # F6: 打斗战后空镜收尾
            "restrained_emotion_chain",  # F7: 克制情绪时间链
        ],
        "timecode_changes": [
            "W6 时长拉伸（runtime_extend：每镜均匀+blank_hold）",
            "短镜合并（short_shot_merge：<2s 合并到相邻镜）",
        ],
        "tests": [
            "tests/step01_script_gen/test_split_shot_differentiation.py",
        ],
    },

    # ========================================================================
    # v6.47 — 运镜缺失补填
    # ========================================================================
    "v6.47": {
        "name": "运镜缺失补填（camera_movement_fill）",
        "date": "2026-08-27",
        "constraints": [
            "camera_movement_fill",      # J3: 无运镜语言→按角色补/兜底固定机位
        ],
    },

    # ========================================================================
    # v6.45 — kf 弱模板占位
    # ========================================================================
    "v6.45": {
        "name": "kf 弱模板占位修复（W16）",
        "date": "2026-08-26",
        "constraints": [
            "kf_weak_template",          # W16: 弱模板承袭 ff/lf + step02a 双兜底
        ],
    },

    # ========================================================================
    # v6.43 — prompt 强化（4+1 规则）+ kf 单调性
    # ========================================================================
    "v6.43": {
        "name": "v6.43 prompt 强化（4+1 规则：小船/抬轿/重叠/抽象/威胁源朝向）",
        "date": "2026-08-23",
        "constraints": [
            "v6_43_prompt_enforcement",  # 5 条规则集中实现
            "kf_monotonic",              # F1: kf t_sec 单调性
        ],
        "validation_checks": [
            # v6.43 5 条规则的检测函数散在 validation.py 各节
        ],
        "tests": [],
    },

    # ========================================================================
    # v6.42 — 基础约束体系（从旧 _hard_fix_* 迁移到 Constraint 框架）
    # ========================================================================
    "v6.42": {
        "name": "基础约束体系（42+ 个约束从 _hard_fix_* 迁移到 Constraint 框架）",
        "date": "2026-08-22",
        "constraints": [
            # Phase 1 核心（A-D）——名字与 ALL_CONSTRAINTS 实际 name 对齐（2026-09-06 核对）
            "fatal_wound_continuity",   # A: 致命伤持续（旧称 injury_continuity）
            "gaze_direction",           # B: 视线方向
            "prop_spatial_anchor",      # C: 道具空间方位（旧称 prop_position）
            "kf_temporal_consistency",  # D: kf 时序矛盾（旧称 kf_pre_post_conflict）
            # Phase 2（E-K）
            "kf_time_window_alignment", # E: kf 时段缺段
            "kf_t_sec_bounds",          # F: kf t_sec 越界
            "kf_monotonic",             # F1: kf 单调性
            "hook_duration",            # R: hook 时长下限
            "opening_hook",             # K: 开篇冲突
            # Phase 3 更多（W开头+其他）
            "climax_distribution",      # W7: 高潮分布
            "framing_jump",             # W10: 景别跳跃
            "axis_jump",                # W11: 轴线越轴
            "punch_density",            # L: 爆点密度
            "bridge_words",             # W13: 衔接词
            "camera_variety",           # J2: 运镜多样性
            "framing_diversity_v2",     # I2: 景别多样性
            "lf_desc_length",           # M: lf 字数下限
            "lf_midstate",              # Q: lf 进行态
            "multi_char_layout_depth",  # W5: 多人站位纵深
            "narration_subtitle_consistency",  # H: narration/subtitle 一致
            "threat_gaze_coupling",     # G: 威胁源-视线耦合
            "combat_impact",            # W3: 打击反馈
            "char_layout_sync",         # N: char_layout 同步
            "facing_to_gaze",           # T2: 面向→凝望
            "boat_width",               # T1: 船体宽度
            "boat_crop_strip",          # V: 船体裁切（旧称 boat_crop）
            "prompt_repetition",        # P: 复读机
            "platform_compliance",      # O: 首镜 AI 生成标注
            # conflict_detector：独立检测器（engine.detect_and_log 直调，非 Constraint 子类）
        ],
        "validation_checks": [
            # 旧 validation.py 中 50+ 个 _check_* 函数（逐步迁移到 Constraint）
            "_check_injury_continuity",
            "_check_gaze_direction",
            "_check_prop_position",
            "_check_kf_pre_post_conflict",
            "_check_kf_time_window_alignment",
            "_check_strong_emotion_chain",
            "_check_threat_gaze_coupling",
            "_check_combat_actions",
            "_check_multi_char_layout",
            # ... 更多见 validation.py
        ],
    },
}


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _version_key(v: str) -> tuple[int, int]:
    """版本号数值排序键（"v6.100" > "v6.99"）——字符串字典序在 v6.100 边界
    翻车（"v6.100" < "v6.70"），latest/get_rule_version 必须走数值序。"""
    m = re.fullmatch(r"v(\d+)\.(\d+)", v)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def get_rule_version(version: str) -> RuleEntry | None:
    """获取指定版本的规则条目（支持不精确匹配：v6.5 匹配 v6.5x 最新版）."""
    if version in RULES_INDEX:
        return RULES_INDEX[version]
    # 模糊匹配：v6.5 匹配 v6.5x 中最新的
    candidates = [k for k in RULES_INDEX if k.startswith(version)]
    if not candidates:
        return None
    return RULES_INDEX[max(candidates, key=_version_key)]


def latest_version() -> str:
    """返回最新版本号（数值序——09-13 修：字符串序在 v6.100 处翻车）."""
    return max(RULES_INDEX.keys(), key=_version_key)


def all_constraint_names() -> set[str]:
    """返回所有版本涉及的 constraint name 集合（去重）."""
    names: set[str] = set()
    for entry in RULES_INDEX.values():
        names.update(entry.get("constraints", []))
    return names


__all__ = [
    "RULES_INDEX",
    "RuleEntry",
    "get_rule_version",
    "latest_version",
    "all_constraint_names",
]
