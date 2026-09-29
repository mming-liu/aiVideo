"""Same-model independent audit for a generated Step01 script.

The audit gets a fresh context and must return structured, evidence-based
issues.  Its findings are advisory inputs to targeted repair; deterministic
checks remain the final authority.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from _text_gen import TextGenerationRequest, create_service
from step01_script_gen.quality_issues import Issue


AUDIT_SCHEMA = {
    "name": "script_audit",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "issues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                        "severity": {"type": "string", "enum": ["blocking", "warning", "info"]},
                        # v6.120：记法**显式钉死在 schema 里**（配上
                        # `_AUDIT_RULES_TEMPLATE` 的 repair_targets 段）。此前两处
                        # `shot_id` 都没有格式说明，而规则里的举例写的是 `shot5`——
                        # 同一份提示词下模型会随机选一种读法（2026-09-25 实测：
                        # 第 1 轮 `"5"`、第 2 轮 `"shot5"`，temperature=0.0）。
                        "shot_id": {
                            "type": "string",
                            "description": ("该镜在上文分镜里的 index 裸数字，如 \"5\"；"
                                            "钩子写 \"0\""),
                        },
                        "frame_id": {"type": "string"},
                        "entity_id": {"type": "string"},
                        "field_path": {"type": "string"},
                        "evidence": {"type": "string"},
                        "impact": {"type": "string"},
                        "minimal_fix": {"type": "string"},
                        "stage": {"type": "string", "enum": ["pre", "process", "result"]},
                        # v6.119：**成对修复目标**——「哪个镜头改哪个字段」。
                        # 授权只看这里；上面的 shot_id/field_path 只用于问题定位。
                        "repair_targets": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "shot_id": {
                                        "type": "string",
                                        "description": ("该镜在上文分镜里的 index 裸数字，"
                                                        "如 \"5\"；钩子写 \"0\""),
                                    },
                                    # v6.121：**成对目标**这才是授权来源，形状必须钉死。
                                    # 2026-09-25 14:52 实测：模型写
                                    # `frame_layouts.kfs[1].props` / `[2].props`（漏人物
                                    # 下标）→ 整条 blocking 作废 → 提交点 BLOCK。
                                    # 真实结构是「关键帧列表 → 人物列表」两层，且 kf2
                                    # 确实有 2 个出镜人物 → 系统不替它猜是谁。
                                    "field_path": {
                                        "type": "string",
                                        "description": (
                                            "要修改的字段叶子。布局路径按两种形状写、"
                                            "下标一层都不能省：ff/lf 是人物列表一层"
                                            "（frame_layouts.ff[0].pose）；kfs 是"
                                            "关键帧→人物两层，两个下标都要写"
                                            "（frame_layouts.kfs[2][0].props）。"
                                            "写 frame_layouts.kfs[2].props 这类漏人物"
                                            "下标的形状会让该条目标被剔除（问题本身仍保留为 blocking，但这一轮拿不到修复授权）。"),
                                    },
                                },
                                "required": ["shot_id", "field_path"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["code", "severity", "shot_id", "frame_id", "entity_id",
                                 "field_path", "evidence", "impact", "minimal_fix",
                                 "repair_targets"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["issues"],
        "additionalProperties": False,
    },
}


_AUDIT_SYSTEM = """你是独立分镜审查员。不要证明脚本优秀，只找可验证的问题。
**只输出 JSON**（严格遵守给定 schema：不要 markdown 报告、不要标题、不要项目符号列表、不要在 JSON 之外写任何说明）。
先逐镜扫描全章，再统一输出问题，避免修复一处后才发现下一处。优先报告所有确定影响剧情或可执行性的 blocking，再报告 warning/info；总数最多 20 条，数量不足时舍弃 warning/info。每条 evidence/impact/minimal_fix 尽量控制在 80 字以内。每个问题必须给出镜头、字段、证据和最小修复建议。程序检查是最终权威。
逐镜检查明显错词、连续重复词句、截断文本和跨字段状态矛盾；相同缺陷出现在多个镜头时逐镜给出字段与证据。

帧职责边界（必须遵守）：
- frame_id=ff：只审「动作开始前的准备态」。不得要求 ff 已经完成 motion_desc 中的过程动作或 lf_desc 中的结果动作。
- frame_id=kfN：只审该关键帧应呈现的中间过程态。
- frame_id=lf：只审动作结束后的结果态。
- 如果后续动作缺失，field_path 应指向 motion_desc 或对应 kf/lf，不得要求把它补进 ff。
- severity 必须填写 blocking/warning/info；impact 只写影响，不要把 blocking/warning 写进 impact。
- 可选 stage 必须填写 pre/process/result，与 frame_id 职责一致。
"""


# 送给审计的镜头字段（v6.114）：**审查规则要求逐镜检查错词/重复/截断**，而这里
# 此前没有 `prompt` —— 模型看不到该字段却被告知要审它（提示与输入范围不一致）。
# 本表同时进 `audit_prompt_version()`：改输入范围 = 换审计对象，旧结论必须失效。
_COMPACT_SHOT_FIELDS: tuple = (
    "index", "duration_sec", "main_subject", "ff_desc", "lf_desc",
    "motion_desc", "prompt", "narration_segment", "frame_layouts", "keyframes",
)


def _compact_script(script: dict) -> dict:
    shots = []
    for s in script.get("shots") or []:
        if not isinstance(s, dict):
            continue
        shots.append({_f: s.get(_f) for _f in _COMPACT_SHOT_FIELDS})
    return {"hook": script.get("hook"), "shots": shots,
            "total_duration": script.get("total_duration")}


# 审计调用的固定参数（总计划 §1.3：模型、温度与 prompt 版本都要写进 audit 元数据）
AUDIT_TEMPERATURE = 0.0

# ---- 审计专用文本通道（方案 A，2026-09-25）----------------------------------
# 背景：`TEXT_PROVIDER=agent_plan` 时 `AgentPlanTextService._build_payload()` 把
# `json_schema` 降级成 `response_format={"type":"json_object"}`（schema 内容整体丢弃）。
# 实测 2026-09-24 21:54:41 那次审计：HTTP 200、`finish_reason=stop`、
# `completion_tokens=3432`（**未截断**），正文却是 Markdown 报告（`# 分镜审查报告`…）
# → 解析器找不到 JSON → `parse_failed` → 提交点「调用失败 != 0 issue」直接 BLOCK。
# 因此把**最终审计**单独路由到支持 strict `json_schema` 的原生 DeepSeek；
# 不动全局 `TEXT_PROVIDER`（生成/修复/提取仍在原通道）。
#
# 未设置 `AUDIT_TEXT_PROVIDER` → `(None, None)` = 完全沿用现状（零行为变化）。
AUDIT_PROVIDER_ENV = "AUDIT_TEXT_PROVIDER"
AUDIT_MODEL_ENV = "AUDIT_TEXT_MODEL"


def resolve_audit_channel() -> tuple:
    """审计专用通道 `(provider, model)`；未配置时返回 `(None, None)`.

    **通道与模型 ID 必须一起换**：调用方传来的模型名来自 `SCRIPT_CANDIDATE_MODELS`，
    那是 **plan 通道**的名字（如 `deepseek-v4.1-flash`），而原生 DeepSeek 的 Responses
    API 只接受 `deepseek-flash` / `deepseek-v4-pro`（官方 create-response 文档，
    2026-09-25 核对）。只换 provider 而沿用旧模型名，请求会被直接拒。

    设了通道却缺模型名 → **显式抛错**：静默沿用调用方的 plan 通道模型名，等于把 400
    留到跑批时才暴露（宁可显式失败，不可静默降级）。

    解析实现已收敛到单一来源 `shared.text_channel.resolve_channel`（定向修复的通道
    覆盖用同一份语义，2026-09-28）。
    """
    from shared.text_channel import resolve_channel
    return resolve_channel(
        AUDIT_PROVIDER_ENV, AUDIT_MODEL_ENV,
        hint="原生 DeepSeek 只认 deepseek-flash / deepseek-v4-pro。")


def audit_prompt_version() -> str:
    """审计提示词版本 = hash(system prompt + 输出 schema)（v6.111，审核 P2-5）.

    为什么必须显式记录：`_reusable_final_audit` 的复用条件只有
    revision + 规则指纹 + scope，而规则指纹**不含本文件**——改了审计提示词或
    输出契约，旧审计结论仍会被复用，等于拿旧规则的结论给新稿盖章。把版本写进
    元数据并纳入复用判定，prompt 一变就自动失效。

    只覆盖**提示词与输出契约**；调用参数（温度/超时/token 上限）用元数据里的
    显式字段记录，不混进版本哈希。
    """
    import hashlib
    import json as _json
    blob = _json.dumps(
        {"system": _AUDIT_SYSTEM, "schema": AUDIT_SCHEMA,
         # v6.114（审核）：四条来源都要覆盖，否则「只改其中一条」时旧审计仍被复用：
         #   system prompt / 输出 schema / build_audit_prompt 的审查规则 / 送审字段范围
         "rules": _AUDIT_RULES_TEMPLATE, "compact_fields": list(_COMPACT_SHOT_FIELDS)},
        ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


# 审查规则的**静态模板**（v6.114）：抽成常量是为了让 `audit_prompt_version()` 能
# 覆盖它——此前版本哈希只含 system prompt 与 schema，**只改这里的规则时旧审计
# 仍会被复用**。
_AUDIT_RULES_TEMPLATE = """# 审查规则
重点检查：
1. 观众是否知道人物为什么这样做；
2. 是否省略理解剧情必需的信息；
3. 首帧→动作→关键帧→尾帧是否成立；
4. 哪些镜头重复表达同一信息；
{hook_rule}

合规替换（不算缺陷）：
- 台词字段 `narration_segment` / `subtitle` 与原文的差异若属于既定合规映射（`杀人→出人命`、`击杀→制服`、`刺客→神秘人` 等，见 `shared/content_safety.NARRATION_SUBSTITUTIONS` / `SENSITIVE_SUBSTITUTIONS`）则不判为错词/改词/漏词；
- 以 `出人命` 等合规词判错词前，先确认原文该句是否含对应被替换词（如原文含 `杀人` 则 `出人命` 为合规产物）。

帧职责：
- ff 只检查动作开始前准备态；
- kf 只检查对应中间过程；
- lf 只检查结果态；
- 后续动作缺失必须指向 motion_desc/对应 kf/lf，禁止要求 ff 提前完成过程或结果动作。

每个问题必须给 code、shot_id、frame_id、entity_id、field_path、evidence、impact、minimal_fix。
- `code`：该缺陷的**族名**，大写蛇形（如 `STATE_CHAIN_CONFLICT`、`PROP_CONTINUITY`、`FF_RESULT_LEAK`、`KF_STAGE_MISMATCH`）。**同类缺陷在不同镜头必须用同一个 `code`**——下游按它归族统计与判优先级；不得留空，也不得写 `ISSUE`/`UNKNOWN`/`PROBLEM` 这类无信息名。
- `field_path` 必须是**非空**的具体字段路径（就是你建议「修改」的那个字段）；留空的问题本轮**拿不到修复授权**，等于白报。
  ⚠️ 这两条是硬要求：通道不支持 json_schema 时（schema 里的 required 不再被强制）只能靠本段约束，实测漏掉会让全部问题退化成同一个无名族。
**field_path 必须是你建议「修改」的那个字段，不是矛盾被观察到的字段**：要改 ff_desc 就写 `ff_desc`；要改动作时序/过程就写 `motion_desc`；要改某一关键帧就写 `keyframes[N].desc`；要改布局条目按下面**两种形状**写（写错形状该条目标会被剔除，该问题本轮拿不到修复授权）。定位到观察点而非修改点，会把修复引导到错误字段。

**布局路径的两种形状（下标一层都不能省）**：
- `ff` / `lf` 是**人物列表**（一层）：`frame_layouts.ff[人物下标].<叶子>`，如 `frame_layouts.ff[0].pose`。
- `kfs` 是**关键帧列表 → 人物列表（两层）**：`frame_layouts.kfs[关键帧下标][人物下标].<叶子>`，如 `frame_layouts.kfs[2][0].props`。**两个下标都要写**。
- 反例（**无效**，会被判「不是该镜头的真实字段路径」→ 该条目标被剔除）：`frame_layouts.kfs[2].props`（漏人物下标——一个关键帧里可能有多个出镜人物，系统**不会**替你猜是谁）、`frame_layouts.ff.props`（漏人物下标）、`frame_layouts.kfs`（只写列表名）。

## repair_targets（**唯一被用于授权的字段**）
一对一的 `(shot_id, field_path)` 列表，写清「要修好这条问题，必须**一起**修改的全部镜头与字段」：
- **必须联动修改的字段一个都不能漏**：修复端只被授权改这里列出的字段，没列出的改动会被**回填**；漏掉的那部分会让修复自相矛盾（半条修复）→ 引入新的阻塞 → **整组被回退**。典型漏法：kf 时序矛盾通常要同时改 `keyframes[N].desc` + `keyframes[N].stage` + `frame_layouts.kfs[N][n].props`，只写 `desc` 必然被回填。
- `shot_id` 一律写该镜在上文分镜里的 **`index` 裸数字**（如 `5`；钩子写 `0`）。不要写 `shot5` / `shot_5` / `第5镜` 之类的写法——**本文档全篇的镜头号都按裸数字读**，下面举例中的「5 号镜」等价于 `"shot_id": "5"`。
- 只写该镜头里**真实存在**的字段叶子：`ff_desc` / `lf_desc` / `motion_desc` / `prompt` / `keyframes[N].desc` / `keyframes[N].stage` / `keyframes[N].t_sec` / `frame_layouts.ff[N].<叶子>` / `frame_layouts.lf[N].<叶子>` / `frame_layouts.kfs[N][n].<叶子>` / `temporal_plan`。不要写 `ff_desc.props` 这类组合路径，也不要编造字段。**数组下标必须落在本稿该镜的实际长度内**（该镜 `keyframes` 只有 2 项就不要写 `keyframes[2]`；`frame_layouts.kfs[0]` 的出镜表只有 1 人就别写 `[0][1]`）——越界、编造的路径会被逐条剔除，该问题本轮拿不到修复授权，白等一轮审计。
- 一条问题需要跨镜联动才能修好时（例如同一事实在 5 号镜的 ff_desc 与 7、8、9 号镜的 ff_desc 里互相矛盾），**每一镜各写一条**。系统把它们当成**一个事务**：整组修、整组验收、整组回退——写漏一镜会让整组被回退。
- **单条问题的 repair_targets 最多覆盖 6 个镜头**（联动组容量上限）。systemic 缺陷（例如某字段取值全篇非法）**按镜拆成多条问题**，每条只覆盖它自己的那几镜。**跨 7 镜以上的单条声明修复侧整组做不完，会整轮停在门口**（不调修复、不再发审计）——拆成多条才能逐镜修掉。
- 只改一个字段就写一条（**不要多写**没必要的字段：多列的条目同样要一起改完才算通过）。确实无法确定要改哪个字段时写空数组 `[]`，该问题本轮不进入定向修复。
- `shot_id` / `field_path` 仍要填，用来**定位问题出现在哪里**（可与 repair_targets 不同：问题出现在这镜，要改的是别的镜）。
严重级别：blocking=确定影响剧情/可执行性；warning=可能影响；info=优化建议。
"""


def build_audit_prompt(novel_text: str, plan: dict | None, script: dict,
                       audit_focus: list[str] | None = None,
                       metadata: dict | None = None) -> str:
    focus_lines = "\n".join(f"- {item}" for item in (audit_focus or [])) or "- 无额外定向项"
    hook_rule = "5. 钩子和结尾是否真的来自当前故事。" if script.get("hook") else "5. 结尾是否真的来自当前故事。"
    metadata_text = json.dumps(metadata or {}, ensure_ascii=False, indent=2)
    return f"""# 审查元数据
{metadata_text}

# 定向审查项
{focus_lines}

# 原文
{(novel_text or '')[:12000]}

# 故事规划
{json.dumps(plan or {}, ensure_ascii=False)}

# 当前分镜
{json.dumps(_compact_script(script), ensure_ascii=False)}

{_AUDIT_RULES_TEMPLATE.format(hook_rule=hook_rule)}
"""


def _parse_issues_with_status(text: str) -> tuple[list[dict], str]:
    """Parse audit output and distinguish empty from malformed JSON."""
    raw = (text or "").strip()
    raw = re.sub(r"^```json\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"^```\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)
    try:
        data = json.loads(raw)
    except Exception:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end < 0:
            return [], "parse_failed"
        try:
            data = json.loads(raw[start:end + 1])
        except Exception:
            return [], "parse_failed"
    if isinstance(data, dict):
        issues = data.get("issues")
    else:
        # v6.134.1（审核，2026-09-26）：**只接受根对象里的 `issues` 数组**。
        # AUDIT_SCHEMA 要求根对象必含 `issues`；顶层 JSON 数组不符合契约，此前
        # 却被整份当作 issues——顶层 `[]` 尤其危险：合法 JSON、解析 ok、零条问题，
        # 等于把「响应无效」当成「审计通过」（比 BLOCK 更危险的静默假绿）。
        # 服务端 schema 执行不能当作保障（台账实测 19 轮 1 轮输出不合预期），
        # 根形状必须在本地 fail-closed。
        issues = None
    if not isinstance(issues, list):
        return [], "parse_failed"
    # v6.114（审核）：**只检查 `issues` 是列表**会放行形状错误的 JSON——
    # 实测 `{"issues":[1,2]}` 与 `{"issues":[{"foo":1}]}` 都被判 `ok` + 零问题，
    # 于是「审计没跑对」被当成「审计通过」（比 BLOCK 更危险：静默假绿）。
    # Agent Plan 通道把 `json_schema` 降级为 `response_format=json_object`
    # （`_text_gen/agent_plan_provider.py`），schema 的**唯一执行点就是这里**。
    #
    # 判据（按 schema 里真正决定行为的字段）：
    #   - 每条必须是对象；
    #   - `severity` 必须是 blocking/warning/info 之一（它决定这条算不算 blocking）；
    #   - 至少要有一个可执行/可核对的描述字段，否则这条问题无法被处理。
    #   v6.134：severity 判据 `.lower()`——与下方归一化（`sev_raw…lower()`）同口径。
    #   此前门比归一化严一档：`"BLOCKING"` 会让**整份审计** parse_failed，而归一化
    #   层本来就能吃下它；大小写不改变语义，不属于放水。
    for item in issues:
        if not isinstance(item, dict):
            return [], "parse_failed"
        if str(item.get("severity") or "").strip().lower() not in ("blocking", "warning", "info"):
            return [], "parse_failed"
        if not any(str(item.get(k) or "").strip()
                   for k in ("evidence", "impact", "minimal_fix", "message")):
            return [], "parse_failed"
    return list(issues), "ok"


def _parse_issues(text: str) -> list[dict]:
    """Backward-compatible list-only parser."""
    return _parse_issues_with_status(text)[0]


def _normalize_repair_targets(item: dict) -> "list | None":
    """把一条 issue 的 `repair_targets` 规整成 `[{"shot_id": str, "field_path": str}]`.

    **三态必须保住**（v6.119 审核）：字段**缺** → `None`（旧条目，走兼容路径）；
    字段在但为空/null/非数组 → `[]`（**显式**「无修复目标」→ 不授权）。
    把两者都归一成 `[]`，会让「模型明确说不修」被当成「旧条目没提供」而回退到定位
    字段推导——正是这次要修掉的那类静默降级。

    只做**形状**归一（丢空值/非对象条目）；**路径是否真实存在**由授权层按当前镜头
    逐项校验（`repair_fields.validate_repair_targets`）——解析层拿不到镜头对象。
    """
    if "repair_targets" not in (item or {}):
        return None
    raw = (item or {}).get("repair_targets")
    out: list = []
    for _entry in (raw if isinstance(raw, list) else []):
        if not isinstance(_entry, dict):
            continue
        _sid = str(_entry.get("shot_id") or "").strip()
        _fp = str(_entry.get("field_path") or "").strip()
        if not _sid or not _fp:
            continue
        out.append({"shot_id": _sid, "field_path": _fp})
    return out


def _required_issue_fields() -> tuple:
    """`issues[]` 的 required 字段——从 AUDIT_SCHEMA 推导，不另抄一份."""
    return tuple(AUDIT_SCHEMA["schema"]["properties"]["issues"]["items"]["required"])


def _schema_deviations(issues: list) -> list:
    """逐条列出响应体与 `AUDIT_SCHEMA` 的偏离（**归一化前**观测到的）.

    为什么是「记录」而不是「硬失败」（v6.117，方案 A）：通道降级后 schema 不再由
    服务端执行，本地解析器是唯一执行点。但把 required 缺失直接判 `parse_failed`
    会造成**另一种静默失败**——实测 2026-09-24 落盘的 20 条 issue **全部**缺 `code`
    （归一化后统一成 `model_audit_issue`），硬失败等于「每次审计都 BLOCK、可用结论
    整份丢弃」。所以这里的口径是：**归一化继续做，偏离显式记录**，由审计边界
    （台账 + WARN 日志）暴露通道是否真的在守 schema。
    """
    if not issues:
        return []
    total = len(issues)
    out: list = []
    _missing_code = sum(1 for i in issues if not str(i.get("code") or "").strip())
    if _missing_code:
        out.append(f"{_missing_code}/{total} 条缺 `code`（schema required）"
                   f"——已归一为 `model_audit_issue`")
    _num_types = sorted({
        type(i.get("shot_id")).__name__ for i in issues
        if i.get("shot_id") is not None and not isinstance(i.get("shot_id"), str)
    })
    if _num_types:
        out.append(f"`shot_id` 非 string（实际 {'/'.join(_num_types)}）——已 `str()` 归一")
    # 类型感知：**数组字段的空数组是合法值**（`repair_targets: []` 的语义是
    # 「这条问题没有明确修复目标」），用 `str(v or "")` 判非空会把它误报成缺字段。
    # 字段类型同样从 AUDIT_SCHEMA 推导，不另抄一份。
    _props = (AUDIT_SCHEMA.get("schema", {}).get("properties", {})
              .get("issues", {}).get("items", {}).get("properties", {}))
    for _f in _required_issue_fields():
        if _f in ("code", "shot_id"):
            continue
        _ftype = (_props.get(_f) or {}).get("type")
        _bad = 0
        for i in issues:
            if _f not in i or i.get(_f) is None:
                _bad += 1
            elif _ftype == "array":
                if not isinstance(i.get(_f), list):
                    _bad += 1
            elif not str(i.get(_f)).strip():
                _bad += 1
        if _bad:
            out.append(f"{_bad}/{total} 条缺 `{_f}`（schema required）")
    return out


@dataclass
class AuditResult:
    status: str  # ok | request_failed | parse_failed
    issues: list[Issue] = field(default_factory=list)
    error: str = ""
    raw_text: str = ""
    # 本次审计**实际**走的通道与提交的模型（v6.117，方案 A）：台账与产物据此记录归属。
    # 空串 = 未配置专用通道，沿用全局 TEXT_PROVIDER（`main._active_text_provider()`）。
    channel_provider: str = ""
    channel_model: str = ""
    # 响应体与 AUDIT_SCHEMA 的偏离清单（**观测字段，不参与解析结论**，见上）。
    schema_deviations: list = field(default_factory=list)
    # provider 自报的 token 用量（v6.118，方案 A 观测项）。**取不到就是 None**——
    # 观测字段不参与任何判定，也**禁止用估算值填充**：填了估算就再也分不清
    # 「实际花了这么多」和「我们猜的」。各 provider 的 usage 键不完全一致
    # （agent_plan / deepseek 均为 input_tokens / output_tokens / reasoning_tokens /
    # total_tokens），台账原样记为 dict，不做归一。
    usage: dict | None = None
    # v6.134（2026-09-26 BLOCK 根因）：本次审计**实际**发出的调用次数。1 = 单次调用；
    # >1 = 输出不可解析后按 `AUDIT_PARSE_RETRIES` 重试过。只记事实，参与方（提交点）
    # 的 fail-closed 语义不变。
    attempts: int = 1
    # v6.134.1（审核）：被重试掉的失败尝试的**完整留档**——attempt 编号、响应头部、
    # 总长与该次自报用量。此前「重试后成功」的路径只留 attempts=2：坏响应与第一次
    # 的用量都不进产物，事后无法还原（总用量 = 本字段各次 + 顶层 usage）。
    # 只记**被重试覆盖**的尝试；最后一次失败已由顶层 raw_text/usage 覆盖，不重复。
    failed_attempts: list = field(default_factory=list)


def _env_int(name: str, default: int, *, minimum: int | None = None) -> int:
    """读一个整数型 env：**非法值一律 WARN 后回退默认**，绝不抛异常.

    v6.134（2026-09-26 复核）：审计参数此前直接 `int(os.getenv(...))`。实测
    `AUDIT_PARSE_RETRIES=abc` 会在**审计调用处**抛 `ValueError`，而提交链没有
    try/except —— 整轮会崩在提交链上：既不是 BLOCK、也不写欠账台账、三份产物
    停在半路。手误一个 env 不该有这种后果（与 `REPAIR_MAX_OUTPUT_TOKENS`
    同一条口径：非法值 WARN 后回退，不因手误改变保护强度）。

    `minimum=None` = 不设下限；`minimum=0` = 允许 0（显式关闭），负数按默认。
    """
    _raw = (os.getenv(name) or "").strip()
    if not _raw:
        return default
    try:
        _n = int(_raw)
    except ValueError:
        print(f"[WARN] {name}={_raw!r} 不是整数，按默认 {default} 处理")
        return default
    if minimum is not None and _n < minimum:
        print(f"[WARN] {name}={_raw!r} 小于下限 {minimum}，按默认 {default} 处理")
        return default
    return _n


def run_same_model_audit_with_status(
    novel_text: str,
    plan: dict | None,
    script: dict,
    *,
    model: str | None = None,
    timeout_sec: int = 180,
    audit_focus: list[str] | None = None,
    metadata: dict | None = None,
) -> AuditResult:
    """Run audit while preserving failure state for logs and repair acceptance."""
    # 审计专用通道（方案 A）：未配置 → provider=None，`create_service()` 仍按
    # 全局 TEXT_PROVIDER 建服务、`req.model` 仍用调用方传来的模型名（零行为变化）。
    channel_provider, channel_model = resolve_audit_channel()
    _chan = {"channel_provider": channel_provider or "",
             "channel_model": channel_model or ""}
    # v6.134.1（审核）：工厂初始化（含 config.validate / provider 注册）此前在
    # 结果处理之外——异常会直接冒出，既不是 request_failed、也不进审计台账，
    # 整轮崩在提交链上。收进来按调用失败处置（提交点 BLOCK 语义不变，台账有据）。
    try:
        service = create_service() if channel_provider is None else create_service(channel_provider)
    except Exception as exc:
        return AuditResult("request_failed", [], f"audit service init failed: {exc}",
                           attempts=1, **_chan)
    import os as _os
    import time as _time
    _audit_max_tokens = _env_int("AUDIT_MAX_OUTPUT_TOKENS", 16000, minimum=1)
    # v6.124（验证计划 (c)）：**审计专用**思考档位，便于「固定输入 × 不同 effort ×
    # 重复运行」的稳定性/召回实验。未设置 → 不传 → 沿用 provider 全局
    # `DEEPSEEK_REASONING_EFFORT`（零行为变化）。
    _audit_effort = (_os.getenv("AUDIT_REASONING_EFFORT") or "").strip() or None
    req = TextGenerationRequest(
        system_prompt=_AUDIT_SYSTEM,
        user_prompt=build_audit_prompt(novel_text, plan, script, audit_focus=audit_focus, metadata=metadata),
        max_tokens=_audit_max_tokens,
        temperature=AUDIT_TEMPERATURE,
        json_schema=AUDIT_SCHEMA,
        # 专用通道下**必须**用该通道的模型 ID 覆盖调用方传来的值（那是 plan 通道的名字）
        model=channel_model or model,
        timeout_sec=timeout_sec,
        reasoning_effort=_audit_effort,
    )
    # v6.134（2026-09-26 实跑 BLOCK 根因）：`parse_failed` 是**采样噪声**，不是内容结论。
    #   - DeepSeek Responses 的 `text.format` 官方只有 type/name/schema 三字段、没有
    #     strict 开关（2026-09-26 核对 api-docs.deepseek.com/api/create-response），
    #     json_schema 的执行是服务端尽力而为；
    #   - 思考模式下 temperature=0 被 API 静默忽略（deepseek_provider 头注释），
    #     审计每次都是一次重采样；
    #   - 台账实测 19 轮审计 1 轮输出不可解析（2026-09-26 01:02），而同一输入的
    #     最小复现 ok——单次失败不可避免，但提交点此前把它当确定性致命错误直接
    #     BLOCK，整章进欠账台账。
    # 因此对 parse_failed 做**有界重试**（`AUDIT_PARSE_RETRIES`，默认 1 次）：请求
    # 不变、换一次采样。v6.144（2026-09-28）把**截断**并入同一预算——截断同样
    # 取决于采样（思维链长度 7.5k ↔ 32k+），换一次就可能不截断；
    # 而**传输/超时**失败**不**重试（provider 层已尽力，见 `_text_gen.retry_policy`，
    # 上层不得再叠一层）。全部尝试失败仍按原 status 返回——fail-closed 语义不变。
    # `0` 合法（= 关闭重试，与旧版一致）；非法/负数回退默认 1——手误不该把保护关掉
    _parse_retries = _env_int("AUDIT_PARSE_RETRIES", 1, minimum=0)
    _failed_attempts: list = []
    _attempts = 0
    while True:
        _attempts += 1
        # v6.134：审计调用自报家门——provider 里是所有调用共用的一句硬编码
        # "生成分镜脚本" 日志，排查时无法从日志区分调用点（2026-09-26 实录）。
        print(f"[AUDIT] 最终语义审计第 {_attempts} 次调用"
              f"（provider={channel_provider or '全局TEXT_PROVIDER'}，"
              f"model={channel_model or model}）")
        # v6.152（复核要求 2）：每次审计都记**耗时 / 输出与思维链 token / 停止依据**。
        # 抬高输出上限后要回答的第一个问题是「64k 能不能在时间预算内跑完」——
        # 没有这一行就只能靠猜（今天三次截断都是事后从 DEBUG 行反推的）。
        _t_call = _time.time()
        try:
            result = service.generate(req)
        except Exception as exc:
            # v6.134.1（审核）：provider 大多自行捕获异常，但漏网异常同样不得
            # 崩出提交链——按调用失败处置（不重试：异常多为结构性，重试不收敛）。
            return AuditResult("request_failed", [], f"audit call raised: {exc}",
                               attempts=_attempts, failed_attempts=_failed_attempts,
                               **_chan)
        # v6.118：usage 原样带走（取不到 None），供台账记录实际消耗。
        # 重试后记的是**最后一次**调用的用量；前面各次的用量进 failed_attempts，
        # 合计 = failed_attempts 各次 + 顶层 usage。
        _usage = getattr(result, "usage", None)
        if not result.success:
            # v6.144（2026-09-28 跑批实测）：**截断**与「传输/超时失败」是两类，处置不同。
            #   - 截断（`truncated=True`，含「思维链吃光配额、正文为空」）：同请求**换一次
            #     采样就可能不再截断**——实测同一稿的思维链长度在 7.5k ↔ 32k+ 之间波动，
            #     撞顶与否取决于采样。故并入**同一个有界重采样预算**（`AUDIT_PARSE_RETRIES`），
            #     不新开一层、不叠加传输类重试。
            #   - 传输/超时（`truncated=False`）：provider 层已尽力（见 retry_policy），
            #     这里**一次都不重试**，直接按 request_failed 终局。
            _dur = _time.time() - _t_call
            _u = getattr(result, "usage", None) or {}
            _out = _u.get("output_tokens") or _u.get("completion_tokens")
            _rsn = _u.get("reasoning_tokens")
            print(f"[AUDIT] 第 {_attempts} 次返回：耗时 {_dur:.1f}s，"
                  f"max_completion_tokens={req.max_tokens or '全局'}，"
                  f"output={_out}（思维链 {_rsn}），"
                  f"truncated={bool(getattr(result, 'truncated', False))}，"
                  f"success={bool(getattr(result, 'success', False))}")
            _truncated = bool(getattr(result, "truncated", False))
            if _truncated and _attempts <= _parse_retries:
                _failed_attempts.append({
                    "attempt": _attempts,
                    "raw_text_head": str(result.text or "")[:2000],
                    "raw_text_len": len(str(result.text or "")),
                    "usage": _usage,
                    "failure": "truncated",
                })
                # v6.151（实跑根因）：截断的根因是**思维链把 `max_tokens` 吃光**
                # ——实测同一稿两次调用都恰好撞 32000 且正文 0 token、`finish_reason`
                # 都是 length。同请求重采样等于同一枚硬币再抛一次，救不了；改成
                # **换更省的思考档位重采样**（默认 `minimal`：探针实测思维链 ~16.4k，
                # 32000 上限下留得下正文）。不动上限是有意的——抬高上限会把生成时间
                # 拉长，撞上审计自身的超时（超时**不重试**，见 `retry_policy`）。
                # v6.152（复核更正 v6.151）：**换 `reasoning_effort` 在这个通道上改不动
                # 发出的请求**
                # ——provider 把 `low`/`minimal` 都序列化成 `thinking.type=enabled`，
                # 两份 HTTP payload 完全相同（实测首跑两次调用：32,000 全为思维链、
                # 正文为空）。该通道能改变 payload 的思考降档只有「开关思考」，而
                # **关思考的审计质量未验证**（探针：3 次里 2 次首个采样解析失败）。
                # 故改成**给重采样更多输出余量**：`req.max_tokens` 确实进 payload
                # （provider: `max_completion_tokens = req.max_tokens or 全局值`）。
                # 只加在**重采样**上——首跑上限不动，避免把每次审计都拉长撞超时。
                import dataclasses as _dc
                # 默认 **0 = 不抬**：首跑上限已按实测放到 64000（.env
                # `AUDIT_MAX_OUTPUT_TOKENS`），重采样就是「同请求再采一次」。
                # 保留这个 env 是为将来验证过更高上限时留口子。
                _retry_cap = _env_int("AUDIT_TRUNCATION_RETRY_MAX_TOKENS", 0,
                                      minimum=0)
                _bumped = False
                if _retry_cap and _retry_cap > int(_audit_max_tokens or 0):
                    req = _dc.replace(req, max_tokens=_retry_cap)
                    _bumped = True
                print(f"[AUDIT] 第 {_attempts} 次审计输出被截断"
                      f"（{str(getattr(result, 'error', '') or '')[:80]}），"
                      + (f"重采样并把输出上限抬到 {_retry_cap}"
                         if _bumped else "同请求重采样")
                      + f"（还剩 {_parse_retries + 1 - _attempts} 次机会）")
                continue
            return AuditResult("truncated" if _truncated else "request_failed", [],
                               str(getattr(result, "error", "") or ""),
                               str(getattr(result, "text", "") or ""), usage=_usage,
                               attempts=_attempts, failed_attempts=_failed_attempts,
                               **_chan)
        parsed, status = _parse_issues_with_status(result.text)
        if status == "ok":
            break
        if _attempts > _parse_retries:
            return AuditResult(status, [], "audit output is not valid JSON or lacks issues[]",
                               result.text, usage=_usage, attempts=_attempts,
                               failed_attempts=_failed_attempts, **_chan)
        # v6.134.1（审核）：这一轮是被重试**覆盖**的失败尝试——必须留档，否则
        # 「重试后成功」路径上坏响应与首次用量双双消失（attempts=2 只能说明发生过）。
        # 头部封顶与 main._DIAG_RAW_HEAD_MAX 同值（2000 字符）。
        _text = str(result.text or "")
        _failed_attempts.append({
            "attempt": _attempts,
            "raw_text_head": _text[:2000],
            "raw_text_len": len(_text),
            "usage": _usage,
        })
        print(f"[AUDIT] 第 {_attempts} 次审计输出不可解析（{status}），"
              f"同请求重采样重试（还剩 {_parse_retries + 1 - _attempts} 次机会）")
    _deviations = _schema_deviations(parsed)
    out: list[Issue] = []
    for d in parsed:
        _rt = _normalize_repair_targets(d)
        sev_raw = str(d.get("severity") or "warning").strip().lower()
        impact_raw = str(d.get("impact") or "").strip().lower()
        # Model occasionally writes "blocking" into impact while leaving
        # severity=warning.  Treat that as blocking; it is not safe to
        # silently downgrade an explicit blocking claim.
        if impact_raw.startswith("blocking") or sev_raw == "blocking":
            severity = "visual_risk"
        else:
            severity = "advisory"
        _sid = d.get("shot_id")
        shot_id = str(_sid).strip() if _sid is not None and str(_sid).strip() else None
        out.append(Issue(
            code=f"model_audit_{d.get('code') or 'issue'}",
            severity=severity,
            message=f"{d.get('impact') or ''}；建议：{d.get('minimal_fix') or ''}",
            shot_id=shot_id,
            frame_id=str(d.get("frame_id") or ""),
            entity_id=str(d.get("entity_id") or ""),
            field_path=str(d.get("field_path") or ""),
            evidence=str(d.get("evidence") or ""),
            repair_targets=_rt,
            repairable=True,
        ))
    return AuditResult("ok", out, schema_deviations=_deviations, usage=_usage,
                       attempts=_attempts, failed_attempts=_failed_attempts, **_chan)


def run_same_model_audit(novel_text: str, plan: dict | None, script: dict,
                         *, model: str | None = None,
                         timeout_sec: int = 180) -> list[Issue]:
    """Backward-compatible list-returning wrapper."""
    return run_same_model_audit_with_status(
        novel_text, plan, script, model=model, timeout_sec=timeout_sec
    ).issues


__all__ = [
    "AUDIT_SCHEMA", "AuditResult", "build_audit_prompt", "resolve_audit_channel",
    "run_same_model_audit", "run_same_model_audit_with_status", "audit_prompt_version",
]
