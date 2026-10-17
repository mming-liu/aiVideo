# aiVideo 全流程诊断与工程治理计划（v3 交叉复核与裁决版）

**当前评估基线**：Commit `3ea8a7199a2d6ac05d72acf992741c70b0da4064` + 工作区本轮修复增量

**评估范围**：针对本地执行端反馈的 P0/P1/P2 交叉核验结果、伤情模板写入链路清查、以及新发现的词表分叉与一次性补丁脚本问题，给出最终技术裁决与落地指令。

## 一、 本轮交叉核验状态总表

| 

| **事项** | **当前状态** | **复核结论与技术裁决** | 
| **伤情兜底写入路径（`main.py:7621-7658`）** | **已断源** | `main.py` 硬修复兜底已由「拼接 `。伤处白袍暗红洇痕持续可见` + `"杨旭"` 特判」改为「只报 `WARN`」；配合工作区已收口的 `injury_continuity.py` 与 `kf_blood_monotonic.py`，生产链路已无任何自动 append「白袍心口」模板的代码。 | 
| **`ff_lf_temporal.py:39` 极性误判纠正** | **⚠️ 严禁删减（需补词）** | 本地报告误将 `ff_lf_temporal.py:36-40` 的 `_POST_STATE_WORDS` 当成「合法白名单」。**实测源码该元组是违例黑名单（检出 `ff_desc` 提前写出动作结束态并报 `WARN`）**。删减会导致漏检，不仅不能收窄，还应把 `main.py` 旧兜底变体 `"暗红洇痕持续可见"` 补入黑名单。 | 
| **P0-1 路由致死代码与测试盲区（`outline.py`）** | **待按 `(a)+(c)` 收口** | 本地跑通 70 项是因为未入库的 `tests/step01_script_gen/conftest.py` 挂了 `autouse=True` 的 `_legacy_protocol_replay`（强制设为 `ROUTE_AUTO`），在测试中屏蔽了生产默认分支。裁决走 **`(a) + (c)` 组合方案**，并将 `tests/step01_script_gen/conftest.py` 纳入版本库。 | 
| **P0-2 环境异常被吞（`main.py` / `validation.py`）** | **已修复** | `main.py:4999`、`main.py:7607`、`validation.py:5232` 已对 `ENV_FAULT_TYPES` 原样 `raise`；建议补 1 条针对 `finalize_step01_script` 注入 `ImportError` 的端到端回归测试。 | 
| **P0-3 冻结复制 24MB 字体（`tools/motion_lab.py`）** | **已修复** | `_freeze_assets` 已改为 `os.link` 同卷硬链接 + 跨盘 `copyfile` 回退，`same inode = True`，`70 passed`。 | 
| **P1-1 缓存计划未先 `normalize_plan_ranges`（`outline.py:3714`）** | **待修** | `outline.py:3653` 读取 `_cached_plan` 后需先执行 `normalize_plan_ranges(_cached_plan)` 再做 `plan_acceptance_problems`。 | 
| **P1-2 `timecode.py:273` 二次拆镜** | **结案（确认不可达）** | `_compute_timecodes(..., allow_split=False)` 在 `timecode.py:828, 897` 默认关闭且生产 5 处调用点均不传 `True`，确认为历史死分支，无需为它大改契约（加一行注释或废弃断言即可）。 | 
| **P2 `.gitignore` 与误入库目录清理** | **已修复（需补漏 1 项）** | 4 个 `review-*` 目录与 `unused.wav` 已 `git rm --cached`；`!tests/conftest.py` 已加，但还需补充 `!tests/step01_script_gen/conftest.py` 及测试依赖模块白名单。 | 

## 二、 关键技术裁决与具体改法（可直接交由执行端落地）

### 2.1 【紧急纠偏】`step01_script_gen/constraints/ff_lf_temporal.py:39` 是违例黑名单，严禁收窄！

* **代码事实核对（`ff_lf_temporal.py:36-68`）**：

  ```
  _POST_STATE_WORDS = (
      "洇痕扩大", "洇开一片", "洇开大片", "已刺入", "刺中后", "匕首依旧",
      "已中刀", "已倒下", "已刺中", "洇痕清晰", "胸口插着", "心口插着",
      "一片暗红血迹洇开", "血痕清晰可见", "伤处白袍", "暗红痕迹洇开",
  )
  
  ```

  在第 `58-68` 行中，逻辑是：当本镜存在攻击动作（`_ATTACK_VERBS`）且首帧 `ff_desc` 命中 `_POST_STATE_WORDS` 时，**记录 `Violation(constraint_name="ff_lf_temporal", severity=Severity.WARN, message="... ff_desc 已写成动作结束态")`！**

* **为什么之前加那几句注释**： 第 `39` 行注释 `# 伤锚完整句（如"伤处白袍心口一片暗红血迹洇开，血痕清晰可见"）属于结束态，不应出现在攻击镜头的 ff_desc` 的意思是**把伤锚句列入违例黑名单，防止它混进攻击镜头的首帧 `ff_desc`**，绝不是白名单放行！

* **裁决指令**：

  1. **严禁从 `_POST_STATE_WORDS` 中删减** `"一片暗红血迹洇开"`、`"血痕清晰可见"`、`"伤处白袍"`；

  2. **反向补齐黑名单**：把 `main.py` 旧兜底曾写过的变体 `"暗红洇痕持续可见"` 也加入 `_POST_STATE_WORDS`，确保无论旧快照还是模型幻觉把该词写进攻击镜头的 `ff_desc`，都会被抓出 `WARN`。

### 2.2 【P0-1 裁决】`outline.py` 路由死分支与 `conftest.py` 盲区：执行 `(a) + (c)` 组合方案

* **为什么你本地跑 `70 passed` 而干净检出跑 `3 failed`**：

  1. `tests/step01_script_gen/conftest.py` 目前被 `.gitignore:117`（`tests/step01_script_gen/*`）拦在版本库外（未入库）；

  2. 该 `conftest.py` 挂了 `autouse=True` 的 `_legacy_protocol_replay`（把协议设为 `ROUTE_AUTO`），使本地跑 `test_plan_feasibility.py` 时 `_replan_route` 全部走回 `_legacy_route`，**在测试环境里掩盖了生产默认分支（`legacy_request_protocol = None`）下的死代码与协议错位**。

* **为什么选 `(a) + (c)` 而不是 `(b)`**：

  * `outline.py:3184-3185` 注释本身写得很清楚：`# 只有 legacy 的纯预算轮才保留旧的重选路径（它是预算反馈的既有语义，不是另一套响应协议）`。

  * `replan_dialogue_within_budget` 与 `_budget_feedback_block` 的职责本来就是「带数值验算表与替代菜单的旧版预算重规划（仅支持 `ROUTE_OPS` 与 `ROUTE_FULL_PLAN`）」。当它被调用且未显式传入合法 `route` 时，理应按 `_legacy_route(problems)` 路由，绝不能拿 `ROUTE_VIEW_DELTA` 的提示词（`"你只回改动（增量），不用回抄"`）去拼 `OUTLINE_SCHEMA` 全量计划请求。

* **裁决指令（三步到位）**：

  1. **执行 `(a)` 修好 `outline.py` 三处路由边界**：

     ```
     # 1) step01_script_gen/outline.py:593 (_budget_feedback_block 内)
     _route = route if route in (ROUTE_OPS, ROUTE_FULL_PLAN) else _legacy_route(rep.get("problems"))
     
     # 2) step01_script_gen/outline.py:787 (replan_dialogue_within_budget 内)
     _route = route if route in (ROUTE_OPS, ROUTE_FULL_PLAN) else _legacy_route(rep.get("problems"))
     _ops_mode = (_route == ROUTE_OPS)
     
     # 3) step01_script_gen/outline.py:3186 (revise_plan_once 内)
     if _budget_ctx and _route != ROUTE_VIEW and plan_mode(plan) != MODE_HYBRID:
         _budget_route = _legacy_route(problems)
         _new, _info = enforce_budget_by_replanning(
             plan, novel_text, model=model, base_prompt=base_prompt,
             system_prompt=system_prompt, timeout_sec=_timeout,
             deadline_monotonic=_deadline_at, ignore_prev=True,
             rejections=rejections, evidence=_ev, route=_budget_route)
     
     ```

  2. **执行 `(c)` 补齐 `v2_request` 生产路由回归测试**： 在 `test_plan_feasibility.py` 中新增带 `v2_request` 标记（绕开 `_legacy_protocol_replay`）的用例，直接验证生产默认（`legacy_request_protocol is None`）下：① `legacy_audio` 非纯预算问题走 `ROUTE_VIEW_DELTA`；② `legacy_audio` 纯预算问题（`_budget_ctx`）在 `revise_plan_once` 中正确走通 `enforce_budget_by_replanning` 且不发生协议错位。

  3. **把 `!tests/step01_script_gen/conftest.py` 加入 `.gitignore` 白名单并入库**，消除本地与干净检出环境的测试行为差异。

### 2.3 【新发现 1 裁决】反派/威胁源词表合并：按 3 层语义角色收口至 `prompt_semantic/registry.py`

本地执行端发现 5 处词表不一致非常准确，但**不能无脑合并成同一个元组**，因为这 5 处实际上承担了 **3 种不同的语义职责**。裁决在 `step01_script_gen/prompt_semantic/registry.py` 中按语义分层单源定义，各处按职责引用：

| **语义层级** | **单源常量名（定义于 registry.py）** | **规范成员元组** | **消费方与改法** | 
| **1. 核心泛化身份词（仅作角色 ID 前缀匹配）** | `THREAT_CORE_IDENTIFIERS` | `("刺客", "杀手", "黑衣人")` | `shared/vimax_bridge.py:1918`（`_THREAT_IDS`）：用于匹配角色库中泛化威胁角色并拼装外貌后缀，保持窄口径以免误把普通神秘角色当杀手。 | 
| **2. 画面可见威胁/蒙面别名词表（身份归一与出画守卫）** | `THREAT_VISIBILITY_KEYWORDS`（`registry.py:164` 已有） | `("神秘人", "刺客", "杀手", "黑衣人", "蒙面人", "蒙面黑影", "黑影")` | **统一收口以下 3 处**：  ① `shared/style_prefix.py:641`：改为引用 `THREAT_VISIBILITY_KEYWORDS`；  ② `step01_script_gen/validation.py:5347`（`_THREAT_NAMES`）：改为引用 `THREAT_VISIBILITY_KEYWORDS`；  ③ `step01_script_gen/constraints/identity_unification.py:17,30,36`：`_THREAT_ALIASES` 改为 `frozenset(THREAT_VISIBILITY_KEYWORDS)`，规范名优选顺序扩充为 `("刺客", "神秘人", "杀手", "黑衣人", "蒙面人")`，彻底解决「蒙面人」在 `style_prefix` 与 `identity_unification` 裁决不一致的问题。 | 
| **3. 持械动作 + 威胁主体联合判据（`quality_issues.py:405`）** | `WEAPON_HOLDER_VERBS` + `THREAT_VISIBILITY_KEYWORDS` | 动词：`("握着", "手持", "持刀", "挥刀", "举刀", "横刀")`；主体：`THREAT_VISIBILITY_KEYWORDS` | `step01_script_gen/quality_issues.py:405`：拆开「动词」与「身份名词」混写，改为 `any(v in _fn_text for v in WEAPON_HOLDER_VERBS) or any(t in _fn_text for t in THREAT_VISIBILITY_KEYWORDS)`，语义清晰且不再漏掉「黑衣人/蒙面人/杀手」持刀的场景。 | 

> **验收要求**：合并后跑一遍 `vimax_bridge`、`style_prefix`、`identity_unification`、`quality_issues` 四处单测，确认准入对照全绿。

### 2.4 【新发现 2 裁决】一次性审计补丁脚本（`tools/apply_*_audit_patch.py`）归档与门禁防护

* **额外风险补充**：除了硬编码 `刺客/神秘人/杨旭/群演` 外，我核查了 `tools/apply_final_audit_patch.py:93`，发现它会**直接原地覆写 `script_result.json`（`path.write_text(...)`）而不更新 `step01_commit.json`**！一旦有人在已提交章节上误跑该脚本，会直接打碎 `step01_commit.json` 的 SHA1 签名链，导致下游 `step02a/02b/03/04` 全部门禁报错拒绝启动。

* **裁决指令**：

  1. 将 `tools/apply_738fa_audit_patch.py` 与 `tools/apply_final_audit_patch.py` 移入 `tools/archive/` 归档目录；

  2. 或者在脚本入口增加强制参数 `--expect-script-sha1 <sha1>`（校验目标 `script_result.json` 的 SHA1 必须匹配指定历史快照才允许执行），并在结尾打印醒目提示：`[WARN] 直接修改了 script_result.json，必须重跑 step01 commit 重签 step01_commit.json`。

### 2.5 其余收口项确认

1. **`tests/step01_script_gen/constraints/test_facing_to_gaze.py` 的 6 个既有失败**：

   * 根因明确：`facing_to_gaze.py` 加固了 `target in known_names`（要求被注视目标必须在 `char_layout` 或 `script["characters"]` 中声明），而旧测试夹具只填了说话人、未把目标角色放入 `characters` / `char_layout`。

   * **指令**：请同步更新 `test_facing_to_gaze.py` 的测试夹具（在 `script["characters"]` 中补齐对应角色声明，并单独保留 1 条「未声明词不误触发改写」的负例测试），让 `constraints` 全目录 100% 变绿。

2. **P0-2 端到端故障注入单测 + `main.py:7649`「报而不写」单测**：

   * **指令**：同意补齐这两条判别性测试：① 对 `finalize_step01_script` 模拟 `run_hard_fix` 抛出 `ImportError`，断言异常原样上抛、绝不写出 `step01_commit.json`；② 对 `main.py:7621` 伤情兜底构造「终局伤锚镜之后缺少伤情词」的镜头，断言只产生 `WARN` 记录、绝不修改 `shot["prompt"]`。

3. **`.gitignore` 剩余未入库测试文件白名单检查**：

   * 除了已加的 `!tests/conftest.py`，请一并确认以下测试文件在 `.gitignore` 中已放行（避免干净 clone 缺文件）：

     ```
     !tests/step01_script_gen/conftest.py
     !tests/step01_script_gen/test_repair_targets.py
     !tests/step01_script_gen/test_linked_group_capacity.py
     !tests/step01_script_gen/test_repair_target_rollback.py
     !tests/step01_script_gen/test_step01_commit.py
     
     ```