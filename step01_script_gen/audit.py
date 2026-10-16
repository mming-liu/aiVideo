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

#: 一次审计调用**最小可用时长**（v6.152 / A1 边界，2026-09-29 复核）：实测成功的单次
#: 审计耗时 74–144s（约 20 次调用样本），明显低于此值的请求基本注定超时，发出去只会
#: 挤占提交收尾余量。可用 `AUDIT_MIN_USEFUL_SEC` 覆盖。
AUDIT_MIN_USEFUL_SEC_DEFAULT = 60
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
                                            "要修改的字段叶子。布局路径按下列形状写、"
                                            "下标一层都不能省：ff/lf 是人物列表一层"
                                            "（frame_layouts.ff[0].pose）；kfs 是"
                                            "关键帧→人物两层，两个下标都要写"
                                            "（frame_layouts.kfs[2][0].props）。"
                                            "**要增删人物条目时改为写「帧的人物列表」"
                                            "本身**：frame_layouts.ff / frame_layouts.lf / "
                                            "frame_layouts.kfs[关键帧下标]（写条目叶子如 "
                                            "…[0].name 只授权改名字，删除会被回填）。"
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
            },
            # §14 P2（2026-10-03）**窄协议：身份关系声明**。
            #
            # 为什么必须加：`LAYOUT_SPURIOUS_ENTITY` 这类问题说「某称谓与某已在场人物
            # 是重复实体，删掉」，但契约里**没有任何字段**能表达那条关系——代码只剩
            # 「照做」或「猜」两条路。实测（批次 20261003T182636-d26992-001-564d9b
            # ch2）：模型照做删对了，`group_actor_continuity` 又按文本加了回来，
            # 整轮被记成「模型没修好」（`no_target_resolved`）→ BLOCK。
            #
            # 语义（**只管一件事**）：声明「这个称谓指的是那个已在场人物」。
            #   · 不是角色名册，不是新人物，不参与人物数/肖像/参考图任何统计；
            #   · `identifier` 必须**已经**在本稿 `characters` 里；
            #   · 不声明 ⇒ 身份关系**未知**（既不能据此删条目，也不能据此判「是另一
            #     个人」）；**默认空表**，旧响应缺这个键一律按「未声明」读。
            # 不放进顶层 `required`：旧响应缺它必须仍然合法（兼容读法在
            # `_normalize_character_aliases`）。
            "characters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "identifier": {
                            "type": "string",
                            "description": ("本稿 `characters` 里**已存在**的角色 "
                                            "identifier（逐字照抄，不要新造）"),
                        },
                        "aliases": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": ("指向该同一实体的**表层称谓**（如布局条目里"
                                            "写的「小厮」）。只写你**有证据**的那些；"
                                            "给不出证据就不要写这条。"),
                        },
                        "evidence": {
                            "type": "string",
                            "description": "原文或帧内证据（为什么这两个写法是同一实体）",
                        },
                    },
                    "required": ["identifier", "aliases"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["issues"],
        "additionalProperties": False,
    },
}


_AUDIT_SYSTEM = """你是独立分镜审查员。不要证明脚本优秀，只找可验证的问题。
**只输出 JSON**（严格遵守给定 schema：不要 markdown 报告、不要标题、不要项目符号列表、不要在 JSON 之外写任何说明）。
先逐镜扫描全章，再统一输出问题，避免修复一处后才发现下一处。优先报告所有确定影响剧情或可执行性的 blocking，再报告 warning/info；总数最多 20 条，数量不足时舍弃 warning/info。每条 evidence/impact/minimal_fix 尽量控制在 80 字以内。每个问题必须给出镜头、字段、证据和最小修复建议。程序检查是最终权威。
逐镜检查明显错词、连续重复词句、截断文本和跨字段状态矛盾；相同缺陷出现在多个镜头时逐镜给出字段与证据。
证据字段契约：文本问题的 evidence 必须是 field_path 所指字段中逐字连续出现的缺陷片段（保持原始文字和标点，不加引号外壳、字段名前缀或解释；不要用省略号拼接）。解释、正确对照与原因放进 impact，修法放进 minimal_fix。程序用该片段在修前/修后字段中的存在性区分是否真的修掉问题；把“prompt 写着……因此有问题”整句当 evidence 会使这条证据无法用于验收。跨字段矛盾引用被点名待修字段里的问题片段，在 impact 说明另一侧依据。缺失项或非文本关系没有可引用片段时，如实说明缺失，不伪造逐字证据。

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
- **要增删条目（不是改条目字段）时写第三形状——「帧的人物列表」本身**：`frame_layouts.ff` / `frame_layouts.lf`（该帧一层），或 `frame_layouts.kfs[关键帧下标]`（那一个关键帧一层）。**这一形状只用于增删条目**；改条目的字段仍按上面两种形状写叶子。
- 反例（**无效**，会被判「不是该镜头的真实字段路径」→ 该条目标被剔除）：`frame_layouts.kfs[2].props`（漏人物下标——一个关键帧里可能有多个出镜人物，系统**不会**替你猜是谁）、`frame_layouts.ff.props`（漏人物下标）、`frame_layouts.kfs`（只写列表名）。

## repair_targets（**唯一被用于授权的字段**）
一对一的 `(shot_id, field_path)` 列表，写清「要修好这条问题，必须**一起**修改的全部镜头与字段」：
- **必须联动修改的字段一个都不能漏**：修复端只被授权改这里列出的字段，没列出的改动会被**回填**；漏掉的那部分会让修复自相矛盾（半条修复）→ 引入新的阻塞 → **整组被回退**。典型漏法：kf 时序矛盾通常要同时改 `keyframes[N].desc` + `keyframes[N].stage` + `frame_layouts.kfs[N][n].props`，只写 `desc` 必然被回填。
- `shot_id` 一律写该镜在上文分镜里的 **`index` 裸数字**（如 `5`；钩子写 `0`）。不要写 `shot5` / `shot_5` / `第5镜` 之类的写法——**本文档全篇的镜头号都按裸数字读**，下面举例中的「5 号镜」等价于 `"shot_id": "5"`。
- 只写该镜头里**真实存在**的字段叶子：`ff_desc` / `lf_desc` / `motion_desc` / `prompt` / `keyframes[N].desc` / `keyframes[N].stage` / `keyframes[N].t_sec` / `frame_layouts.ff[N].<叶子>` / `frame_layouts.lf[N].<叶子>` / `frame_layouts.kfs[N][n].<叶子>` / `temporal_plan`。不要写 `ff_desc.props` 这类组合路径，也不要编造字段。**数组下标必须落在本稿该镜的实际长度内**（该镜 `keyframes` 只有 2 项就不要写 `keyframes[2]`；`frame_layouts.kfs[0]` 的出镜表只有 1 人就别写 `[0][1]`）——越界、编造的路径会被逐条剔除，该问题本轮拿不到修复授权，白等一轮审计。
- 一条问题需要跨镜联动才能修好时（例如同一事实在 5 号镜的 ff_desc 与 7、8、9 号镜的 ff_desc 里互相矛盾），**每一镜各写一条**。系统把它们当成**一个事务**：整组修、整组验收、整组回退——写漏一镜会让整组被回退。
- **单条问题的 repair_targets 最多覆盖 6 个镜头**（联动组容量上限）。systemic 缺陷（例如某字段取值全篇非法）**按镜拆成多条问题**，每条只覆盖它自己的那几镜。**跨 7 镜以上的单条声明修复侧整组做不完，会整轮停在门口**（不调修复、不再发审计）——拆成多条才能逐镜修掉。
- 只改一个字段就写一条（**不要多写**没必要的字段：多列的条目同样要一起改完才算通过）。确实无法确定要改哪个字段时写空数组 `[]`，该问题本轮不进入定向修复。
- **要增删某个出镜人物条目，目标必须是那一「帧的人物列表」本身，不是条目里的叶子**：
  `frame_layouts.ff` / `frame_layouts.lf`（该帧这一层），或 `frame_layouts.kfs[N]`（第 N 个关键帧这一层）。
  授权它 = 允许**这一帧**的列表增删条目；别的帧、别的字段一律回填。
  ⚠️ **写错形状的代价（实测踩过）**：建议「删除 `kfs[0][1]` 的『小厮』条目」却把目标写成
  `frame_layouts.kfs[0][1].name`（叶子）时，系统只被授权改**这个名字**——删除造成的列表长度变化
  会被**原样回填**，删除白做，还会被记成"没修好"。**凡建议删除/新增条目，目标就写列表本身。**
  仍然**不要**写整张 `frame_layouts.kfs`（那是**所有**关键帧，会一并放开）。
- **某一帧的人物列表整块缺失时**（本镜有 `ff_desc` / `lf_desc`，`frame_layouts` 里却没有 `ff` / `lf` 这一项），
  要**新建**它就把目标写成 `frame_layouts.ff` / `frame_layouts.lf` 本身：授权它会新写这一帧的人物列表
  （一帧一个非空列表，每项含 `name` / `on_screen` / `position` / `pose` / `gaze_target` / `props`）。
  这是**唯一**允许指向"此刻还不存在的字段"的情形——其余路径一律必须是本稿里**真实存在**的叶子。
- **列了列表本身之后，那一帧里只允许纯增删**：保留下来的条目会被**逐字核对**（字段、相对顺序都不许变）。同轮想顺带改写某个保留条目的字段，会让整轮被拒——一条问题只做一件事。
- `shot_id` / `field_path` 仍要填，用来**定位问题出现在哪里**（可与 repair_targets 不同：问题出现在这镜，要改的是别的镜）。
严重级别：blocking=确定影响剧情/可执行性；warning=可能影响；info=优化建议。

## characters[].aliases（**身份关系声明**，与上面的问题清单并列的根级字段）

你建议「某个称谓与某个已在场人物是**同一实体**，删掉多余条目」时，**必须**在根级
`characters` 里把这条关系显式写出来，否则修复端拿不到裁决依据：

```json
"characters": [
  {{"identifier": "张十三", "aliases": ["小厮"], "evidence": "原文此段只有张十三在场，小厮是他随从的泛称，不是第二个角色"}}
]
```
⚠️ 上面这段是 JSON 示例（`{{`/`}}` 是模板转义，实际输出写单个花括号）。

- `identifier` 必须**逐字照抄**本稿 `characters` 里已有的 identifier，**不要新造角色**。
- `aliases` 写指向**同一实体**的表层称谓（就是布局条目 `name` 里那个写法）。
- **这不是角色名册**：只写你声明了身份关系的那些；没有关系要声明就写 `[]`（或省略）。
- **给不出同一实体的证据就不要写**——写了会被当作「同一实体」用于裁决（决定那条
  删除是否保留）；反过来，**不写**等于「身份未知」，系统**不会**替你猜，那条删除
  会停在 `repair_capability_missing` 上、整轮拿不到结论。
- 只说明**类别**的称谓（「小厮」「家丁」「船夫」这类泛称）**本身不构成**同一实体的
  证据，也不构成「是另一个人」的证据——两个方向都必须由你按原文给出关系。
"""


#: 审计上下文里原文的字符上限（主审计与补全**共用**同一条，不各写一份）
AUDIT_NOVEL_MAX_CHARS = 12000


def _audit_context_block(novel_text: str, plan: dict | None, script: dict) -> str:
    """审计上下文：**原文 / 故事规划 / 当前分镜**——主审计与补全请求共用同一份渲染.

    复核 2026-10-04 第三轮 P1：补全请求此前**不带任何上下文**，生产者只看到几条问题
    的摘要，连"改哪一镜的哪个字段"都要靠猜；两条只有后半段修复关系不同的问题会发出
    **完全相同**的请求。上下文块抽出来共用，两处看到的是同一份冻结稿与同一份原文。
    """
    return (f"""# 原文
{(novel_text or '')[:AUDIT_NOVEL_MAX_CHARS]}

# 故事规划
{json.dumps(plan or {}, ensure_ascii=False)}

# 当前分镜（本节编号即问题清单里 `issue_index` 所依据的版本）
{json.dumps(_compact_script(script), ensure_ascii=False)}""")


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

{_audit_context_block(novel_text, plan, script)}

{_AUDIT_RULES_TEMPLATE.format(hook_rule=hook_rule)}
"""


def _audit_root(text: str) -> "dict | None":
    """审计响应 → **根对象**（dict）；不可解析 / 根不是对象一律返回 `None`.

    抽出来是因为「issues」与「characters[].aliases」（§14 P2 窄协议）两个消费方
    必须看**同一份**根对象——各自再解一遍 JSON 就会出现「一个说 parse_failed、
    另一个说 ok」的分叉。
    """
    raw = (text or "").strip()
    raw = re.sub(r"^```json\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"^```\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)
    try:
        data = json.loads(raw)
    except Exception:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end < 0:
            return None
        try:
            data = json.loads(raw[start:end + 1])
        except Exception:
            return None
    return data if isinstance(data, dict) else None


def _normalize_character_aliases(root: "dict | None") -> list[dict]:
    """`characters[].aliases`（§14 P2 窄协议）的**宽容读**.

    协议（生产者侧声明「某称谓与某已在场人物是**同一实体**」）：

        "characters": [{"identifier": "张十三", "aliases": ["小厮"],
                        "evidence": "<原文/帧内证据>"}]

    **宽容读**（本函数只做形状归一，不猜语义）：
      · 缺 `characters`（旧响应的兼容读法）⇒ **空表** = 「未声明任何身份关系」，
        不是「声明了没有关系」。消费者据此判 `ambiguous`，**不得**当成「另一实体」；
      · `identifier` 或 `aliases` 缺失/类型不对 ⇒ **整条丢弃**（不猜它的意图）；
      · `aliases` 里非字符串 / 空串 ⇒ 逐项丢弃；
      · 同一条里重复的写法去重，保持出现顺序。
    """
    if not isinstance(root, dict):
        return []
    raw = root.get("characters")
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        ident = str(item.get("identifier") or "").strip()
        aliases = item.get("aliases")
        if not ident or not isinstance(aliases, list):
            continue
        seen: list[str] = []
        for a in aliases:
            if not isinstance(a, str):
                continue
            name = a.strip()
            if name and name != ident and name not in seen:
                seen.append(name)
        if not seen:
            continue
        out.append({"identifier": ident, "aliases": seen,
                    "evidence": str(item.get("evidence") or "")})
    return out


def _parse_audit_payload(text: str) -> "tuple[list[dict], str, list[dict]]":
    """审计响应 → `(issues, status, character_aliases)`——**一次**解析，三方同源."""
    root = _audit_root(text)
    if root is None:
        return [], "parse_failed", []
    issues = root.get("issues")
    if not isinstance(issues, list):
        return [], "parse_failed", []
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
            return [], "parse_failed", []
        if str(item.get("severity") or "").strip().lower() not in ("blocking", "warning", "info"):
            return [], "parse_failed", []
        if not any(str(item.get(k) or "").strip()
                   for k in ("evidence", "impact", "minimal_fix", "message")):
            return [], "parse_failed", []
    return list(issues), "ok", _normalize_character_aliases(root)


def _parse_issues_with_status(text: str) -> tuple[list[dict], str]:
    """Parse audit output and distinguish empty from malformed JSON.

    ⚠️ 返回值**形状不变**（历史调用方与单测按二元组断言）。身份关系声明走
    `_parse_audit_payload` / `parse_audit_payload`。
    """
    issues, status, _characters = _parse_audit_payload(text)
    return issues, status


def parse_audit_payload(text: str) -> "tuple[list[dict], str, list[dict]]":
    """公开入口：`(issues, status, character_aliases)`（§14 P2 窄协议）."""
    return _parse_audit_payload(text)


def _parse_issues(text: str) -> list[dict]:
    """Backward-compatible list-only parser."""
    return _parse_issues_with_status(text)[0]


def _normalize_repair_targets(item: dict) -> "list | None":
    """把一条 issue 的 `repair_targets` 规整成 `[{"shot_id": str, "field_path": str}]`.

    **三态必须保住**（v6.119 审核）：字段**缺** → `None`（旧条目，走兼容路径）；
    字段在但为空/null/非数组 → `[]`（**显式**「无修复目标」→ 不授权）。
    把两者都归一成 `[]`，会让「模型明确说不修」被当成「旧条目没提供」而回退到定位
    字段推导——正是这次要修掉的那类静默降级。

    只做**形状**归一；**路径是否真实存在**由授权层按当前镜头逐项校验
    （`repair_fields.validate_repair_targets`）——解析层拿不到镜头对象。

    ⚠️ **坏成员不得被过滤成半组**（复核 2026-10-04 第三轮）。既有边界是「一条问题的
    成对目标**任一成员无效 ⇒ 整条拒绝**」（v6.119：半组修出来的东西自相矛盾）。
    旧实现在这里把非对象/缺字段的成员**丢掉**，剩下的组成"半组"继续往下走——
    授权层拿到的已经是过滤后的列表，看不到被丢的那一半，
    「任一成员无效则整条零授权」这条边界因此**可绕**。
    现在：**先整组看一遍，有一个坏成员就整条返回 `[]`**（显式零授权；三态不变）。
    """
    if "repair_targets" not in (item or {}):
        return None
    raw = (item or {}).get("repair_targets")
    if not isinstance(raw, list):
        return []
    out: list = []
    for _entry in raw:
        if not isinstance(_entry, dict):
            return []
        _sid = str(_entry.get("shot_id") or "").strip()
        _fp = str(_entry.get("field_path") or "").strip()
        if not _sid or not _fp:
            return []
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
    _bad_group = 0
    for _i in issues:
        _raw = _i.get("repair_targets") if isinstance(_i, dict) else None
        if isinstance(_raw, list) and any(
                (not isinstance(_e, dict)
                 or not str(_e.get("shot_id") or "").strip()
                 or not str(_e.get("field_path") or "").strip()) for _e in _raw):
            _bad_group += 1
    if _bad_group:
        out.append(f"{_bad_group}/{total} 条 `repair_targets` 含无效成员"
                   f"——按「任一成员无效则整条零授权」处置")
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
    # A3（复核 2026-10-04 第三轮）：**授权元数据补全**那一次调用的完整记录
    # （`call_id` / 成败 / 响应头与长度 / 用量 / 请求与补上的条数），或 `{"skipped": 原因}`。
    # 补全也是**真实发出的一次调用**——计入 `attempts`，用量在这里单列
    # （审计总量 = 顶层 `usage` + 本字段的 `usage`），不再出现"实际发了 2 次、只报 1 次"。
    completion: dict | None = None
    # §14 P2（2026-10-03）：**身份关系声明**（`characters[].aliases` 窄协议）.
    # 形状 `[{"identifier": str, "aliases": [str], "evidence": str}]`，已归一。
    # **空表 = 未声明任何身份关系**（旧响应缺该键的兼容读法），不是「声明了没关系」。
    character_aliases: list = field(default_factory=list)


#: A3 补全的单次上限与输出上限（有界：不为补一个字段把请求撑大）
AUDIT_TARGET_COMPLETION_MAX = 8
AUDIT_TARGET_COMPLETION_MAX_TOKENS = 2000


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


def _audit_request_params(*, model: "str | None") -> dict:
    """**进请求**、且参与「是不是同一份请求」判定的那部分参数（**唯一源**）.

    2026-10-03 复核之十九 P1：提交循环里同一份稿会被反复审计，所以必须能**离线**
    说出"这一次要发的请求与上一次是不是同一条"。请求参数集中在这里，由调用点与
    `final_audit_request_identity` 共用——两边各写一份迟早分叉。

    ⚠️ **不含** `timeout_sec`：它是**本次调用**的本地余量（总预算越跑越少），
    既不是被审对象也不是判据。算进去的话，"同一份稿 + 同一份提示 + 同一档位"会因为
    剩余预算不同而被判成两条不同请求——那正是要修掉的"同稿重复抽样"。反过来，
    `AUDIT_MAX_OUTPUT_TOKENS` / `AUDIT_REASONING_EFFORT` 这类**改变请求语义**的
    参数必须在里面。
    """
    _channel_provider, _channel_model = resolve_audit_channel()
    return {
        "system_prompt": _AUDIT_SYSTEM,
        "schema": AUDIT_SCHEMA,
        "temperature": AUDIT_TEMPERATURE,
        "max_tokens": _env_int("AUDIT_MAX_OUTPUT_TOKENS", 16000, minimum=1),
        "reasoning_effort": (os.getenv("AUDIT_REASONING_EFFORT") or "").strip() or None,
        "channel_provider": _channel_provider or "",
        "channel_model": _channel_model or "",
        # 专用通道下**必须**用该通道的模型 ID 覆盖调用方传来的值（那是 plan 通道的名字）
        "model": _channel_model or model or "",
    }


def final_audit_request_identity(novel_text: str, plan: dict | None, script: dict, *,
                                 audit_focus: list[str] | None = None,
                                 metadata: dict | None = None,
                                 model: str | None = None) -> str:
    """这次审计**将会发出的那一条请求**的身份（纯函数：不发请求、不读盘、不写盘）.

    既定规则（总计划 §1.3）禁止「同一 revision 反复调用 full audit 博一次偶然 PASS」。
    同一次提交循环里修复被**整轮回退**时，稿件与请求都没变——再 POST 一次就是那条
    被禁止的抽样（2026-10-03 实测：同一次运行内同指纹两次调用，换来 2 条 / 5 条
    两份不同的问题清单，其中一次白花 133.5 秒）。身份必须覆盖进请求的**每一个
    字节**，否则"复用"会变成"拿旧结论给新请求盖章"。
    """
    import hashlib as _hashlib
    import json as _json
    _blob = {
        "params": _audit_request_params(model=model),
        "user_prompt": build_audit_prompt(novel_text, plan, script,
                                          audit_focus=audit_focus, metadata=metadata),
    }
    return _hashlib.sha256(
        _json.dumps(_blob, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# A3：**有界补全授权元数据**（复核 2026-10-04 决定采用）
# ---------------------------------------------------------------------------
#: `repair_targets` 规则段在审计规则模板里的标题（切段用，**不手抄规则**）
AUDIT_REPAIR_TARGETS_HEADING = "## repair_targets"


def _repair_targets_subschema() -> dict:
    """`repair_targets` 的子 schema——**从 `AUDIT_SCHEMA` 取**，不另抄一份."""
    import copy as _copy
    return _copy.deepcopy(
        AUDIT_SCHEMA["schema"]["properties"]["issues"]["items"]["properties"]["repair_targets"])


def _repair_targets_rule_block() -> str:
    """`repair_targets` 的规则段——**从 `_AUDIT_RULES_TEMPLATE` 里切出来**（单一来源）.

    另抄一份规则的代价这个仓库已经付过（复核之十二：「提示手抄一份、应用器另一份，
    改一处漏一处」）。这里按标题切段，模板改了这里跟着变；`audit_prompt_version()`
    本来就把整份模板算进哈希，旧审计结论照旧自动失效。
    """
    _t = _AUDIT_RULES_TEMPLATE
    _i = _t.find(AUDIT_REPAIR_TARGETS_HEADING)
    if _i < 0:
        return ""
    _j = _t.find(chr(10) + "## ", _i + len(AUDIT_REPAIR_TARGETS_HEADING))
    return _t[_i:_j if _j > 0 else len(_t)].rstrip()


def _target_completion_schema() -> dict:
    return {
        "name": "audit_repair_targets",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "targets": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "issue_index": {
                                "type": "integer",
                                "description": "要补的那条问题的**序号**（见上文列表里的 issue_index）"},
                            "repair_targets": _repair_targets_subschema(),
                        },
                        "required": ["issue_index", "repair_targets"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["targets"],
            "additionalProperties": False,
        },
    }


def _target_completion_prompt(items: list, *, novel_text: str, plan: dict | None,
                              script: dict) -> str:
    """补全请求：**带上冻结稿与原文**，只问 `repair_targets`.

    复核 2026-10-04 第三轮 P1：此前不带上下文、且把 `evidence` / `minimal_fix` 截到
    200 字 —— 两条**只有后半段修复关系不同**的问题会发出完全相同的请求，生产者无从
    区分该改哪组目标（`minimal_fix` 里"要一起改的另外几处"恰恰长在句尾）。
    现在：完整上下文（与主审计同一份渲染）＋**逐字**问题记录，不截断。
    """
    _lines = [
        "下面是从你上一轮的审计结论里摘出的条目：**问题本身已经确认，不许改写、不许删除、"
        "不许改判严重级别**——它们缺的只是 `repair_targets`（修复侧唯一的授权依据）。",
        "请**只**为每一条补上 `repair_targets`：「要修好这条问题，必须**一起**修改的全部镜头与字段」。",
        "",
        _audit_context_block(novel_text, plan, script),
        "",
        _repair_targets_rule_block(),
        "",
        "## 要补的条目（`issue_index` 是**上面那份「当前分镜」**对应的清单编号）",
    ]
    for _n, _it in enumerate(items, 1):
        _lines.append(
            "%d) issue_index=%d｜code=%s｜shot_id=%s｜frame_id=%s｜field_path=%s"
            % (_n, _it["idx"], _it["code"], _it["shot_id"], _it["frame_id"], _it["field_path"]))
        if _it["evidence"]:
            _lines.append("   evidence：%s" % _it["evidence"])
        if _it["minimal_fix"]:
            _lines.append("   minimal_fix：%s" % _it["minimal_fix"])
        if _it["impact"]:
            _lines.append("   impact：%s" % _it["impact"])
    _lines += [
        "",
        "## 输出格式",
        '{"type": "json_object", "targets": [{"issue_index": %d, "repair_targets": '
        '[{"shot_id": "5", "field_path": "ff_desc"}]}]}' % items[0]["idx"],
        "上文每一条都要出现一次。确实无法确定要改哪个字段时给空数组 `[]`"
        "（那表示这一条本轮不进入定向修复）——**不要**用编造的路径充数，"
        "路径必须落在本稿该镜的真实字段上。",
    ]
    return chr(10).join(_lines)


def _complete_missing_repair_targets(parsed: list, out: list, *, service, model,
                                     timeout_sec: int, deadline_ts, min_useful_sec,
                                     remaining_calls: int, novel_text: str,
                                     plan: dict | None, script: dict) -> tuple:
    """给**缺 `repair_targets` 的 blocking 条目**补一次授权元数据；返回说明行（进偏离记录）.

    边界（复核 2026-10-04 决定）：

    - **只补该字段**：命中的 `Issue` 只改 `repair_targets`，`message` / `severity` /
      `code` / `evidence` 一律不动；响应里多出来的东西（新条目、改判）**全部忽略**。
    - **合法 `[]` 保持零授权**：只处理**键缺失或为 `null`** 的条目——模型明确写 `[]`
      是"这条没有明确修复目标"，那是它的结论，不去覆盖。
    - **有界**：一次最多 `AUDIT_TARGET_COMPLETION_MAX` 条；超出部分留给下一轮，
      不为补字段把请求撑大。
    - **共用既有预算与截止**：与审计重采样同一套（`timeout_sec` / `deadline_ts` /
      `min_useful_sec`），**并消耗剩余重试预算**——`remaining_calls <= 0` 就**不发**。
      复核 2026-10-04 第三轮实测：旧写法不看预算，`AUDIT_PARSE_RETRIES=0` 时实际发了
      2 次调用而只报 1 次；补全把"重试预算"这张牌**暗中改大了**。
    - **调用要入账**：返回 `(说明行, 记录 | None)`；记录含序号 / `call_id` / 成败 /
      响应头部与长度 / 用量 / 请求条数与实际补上条数，调用方据此计入 `attempts` 与产物。
      复核 2026-10-04 第四轮：`called` **只在真的发出调用时**为真——请求准备失败记
      `request_failed` 且 `called=False`（**不占预算、不算 attempt**），调用异常记
      `exception` 且 `called=True`；两类都保留原 blocking，绝不虚记一次调用。
    - **失败保留原问题**：调用失败、解析失败、序号越界、形状非法，一律按"没补上"处理，
      原 blocking 原样保留（零授权 ⇒ 与补全之前完全一致），其余合法目标照常修。
    """
    _idx: list = []
    for _i, _rec in enumerate(parsed or []):
        if not isinstance(_rec, dict):
            continue
        if _i >= len(out or []):
            break
        if not str(getattr(out[_i], "severity", "")) == "visual_risk":
            continue                       # 只补 blocking（advisory 不参与提交门）
        if "repair_targets" in _rec and _rec.get("repair_targets") is not None:
            continue                       # 有值（含合法 `[]`）⇒ 是结论，不覆盖
        _idx.append(_i)
    if not _idx:
        return [], None
    _cap = _env_int("AUDIT_TARGET_COMPLETION_MAX", AUDIT_TARGET_COMPLETION_MAX, minimum=0)
    if _cap <= 0:
        return [], None
    if int(remaining_calls or 0) <= 0:
        return (["%d 条 blocking 缺 `repair_targets`，补全**未发起**（重试预算已用尽）"
                 "——这些条目保持零授权" % len(_idx)],
                {"called": False, "skipped": "budget_exhausted",
                 "requested": len(_idx), "filled": 0})
    _skipped = max(0, len(_idx) - _cap)
    _idx = _idx[:_cap]
    _items = [{
        "idx": _n,
        "code": str(parsed[_i].get("code") or ""),
        "shot_id": str(parsed[_i].get("shot_id") or ""),
        "frame_id": str(parsed[_i].get("frame_id") or ""),
        "field_path": str(parsed[_i].get("field_path") or ""),
        "evidence": str(parsed[_i].get("evidence") or ""),
        "minimal_fix": str(parsed[_i].get("minimal_fix") or ""),
        "impact": str(parsed[_i].get("impact") or ""),
    } for _n, _i in enumerate(_idx, 1)]

    import time as _time
    _left = None
    if deadline_ts is not None:
        _left = float(deadline_ts) - _time.time()
        _min_useful = float(min_useful_sec if min_useful_sec is not None
                            else _env_int("AUDIT_MIN_USEFUL_SEC",
                                          AUDIT_MIN_USEFUL_SEC_DEFAULT, minimum=1))
        if _left < _min_useful:
            return (["%d 条 blocking 缺 `repair_targets`，补全未发起（剩余 %.1fs < %.1fs）"
                     "——这些条目保持零授权" % (len(_idx), _left, _min_useful)],
                    {"called": False, "skipped": "deadline",
                     "requested": len(_idx), "filled": 0})
    # 复核 2026-10-04 第四轮 P2：请求**准备**（构造 `TextGenerationRequest`）与**实际调用**
    # 原先挤在同一个 `except` 里，异常一律记 `called=True`——内存故障注入实测：真的只发出
    # 1 次调用，台账报 2 次。`called` 是"这张牌打出去了没有"的唯一凭据（调用方据此
    # `_attempts += 1`、并写进审计台账），必须**紧邻实际调用**置位，两类失败分开记名。
    _base = {"called": False, "requested": len(_idx), "filled": 0}
    try:
        _req = TextGenerationRequest(
            system_prompt=_AUDIT_SYSTEM,
            user_prompt=_target_completion_prompt(_items, novel_text=novel_text,
                                                  plan=plan, script=script),
            max_tokens=AUDIT_TARGET_COMPLETION_MAX_TOKENS,
            temperature=0.0,
            json_schema=_target_completion_schema(),
            model=model,
            timeout_sec=int(min(timeout_sec, _left)) if _left is not None else timeout_sec,
        )
    except Exception as exc:                                     # noqa: BLE001
        return (["%d 条 blocking 缺 `repair_targets`，补全请求准备失败（%s: %s）"
                 "——这些条目保持零授权" % (len(_idx), type(exc).__name__, exc)],
                dict(_base, skipped="request_failed",
                     error="%s: %s" % (type(exc).__name__, exc)))
    _base["called"] = True                    # 紧邻 `service.generate`：只有真的发出才算打出去
    try:
        _res = service.generate(_req)
    except Exception as exc:                                     # noqa: BLE001
        return (["%d 条 blocking 缺 `repair_targets`，补全调用异常（%s: %s）"
                 "——这些条目保持零授权" % (len(_idx), type(exc).__name__, exc)],
                dict(_base, skipped="exception",
                     error="%s: %s" % (type(exc).__name__, exc)))
    _rec = {"called": True, "requested": len(_idx), "filled": 0,
            "call_id": str(getattr(_res, "call_id", "") or ""),
            "ok": bool(getattr(_res, "success", False)),
            "error": str(getattr(_res, "error", "") or "")[:300],
            "truncated": bool(getattr(_res, "truncated", False)),
            "usage": getattr(_res, "usage", None),
            "_text": str(getattr(_res, "text", "") or "")}
    if not _rec["ok"]:
        return (["%d 条 blocking 缺 `repair_targets`，补全调用失败（%s）"
                 "——这些条目保持零授权" % (len(_idx), _rec["error"][:120])], _rec)
    _root = _audit_root(_rec["_text"])
    _rows = (_root or {}).get("targets") if isinstance(_root, dict) else None
    if not isinstance(_rows, list):
        return (["%d 条 blocking 缺 `repair_targets`，补全响应不可解析"
                 "——这些条目保持零授权" % len(_idx)], _rec)
    _filled = 0
    for _row in _rows:
        if not isinstance(_row, dict):
            continue
        _n = _row.get("issue_index")
        if not isinstance(_n, int) or isinstance(_n, bool) or not 1 <= _n <= len(_idx):
            continue                       # 序号越界/非法 ⇒ 忽略这一行
        _rt = _normalize_repair_targets({"repair_targets": _row.get("repair_targets")})
        if not _rt:
            continue                       # 形状非法或空 ⇒ 不写（保持零授权）
        _target = out[_idx[_n - 1]]
        if getattr(_target, "repair_targets", None):
            continue                       # 已有授权的不覆盖（并发/重复行）
        _target.repair_targets = _rt
        _filled += 1
    _note = ("%d 条 blocking 缺 `repair_targets`——已按 A3 补全 %d 条"
             "（只补该字段，原问题未改写）" % (len(_idx), _filled))
    if _filled < len(_idx):
        _note += "；其余 %d 条保持零授权" % (len(_idx) - _filled)
    if _skipped:
        _note += "；另有 %d 条超出单次上限（%d）未补，原样保留" % (_skipped, _cap)
    _rec["filled"] = _filled
    return [_note], _rec


def run_same_model_audit_with_status(
    novel_text: str,
    plan: dict | None,
    script: dict,
    *,
    model: str | None = None,
    timeout_sec: int = 180,
    audit_focus: list[str] | None = None,
    metadata: dict | None = None,
    deadline_ts: float | None = None,
    min_useful_sec: float | None = None,
) -> AuditResult:
    """Run audit while preserving failure state for logs and repair acceptance.

    v6.152（A1 边界，2026-09-29 复核）：`deadline_ts` 是**整次审计**（含截断/解析
    重采样）的截止时刻。此前超时只在进入审计前算一次、重采样沿用同一个值——首调
    耗掉时间后，重采样仍能拿到完整上限，「给提交收尾留 60s」形同虚设。现在：
    ①每次**实际发起前**重算可用时间，单次超时取 `min(timeout_sec, 剩余)`；
    ②剩余低于 `min_useful_sec` 时**不发请求**，直接返回 `budget_exhausted`
    （如实失败，而不是发一个注定挤占收尾的短请求）。`deadline_ts=None` = 不设
    截止（无总预算约束的调用方/单测，行为与旧版一致）。"""
    # 审计专用通道（方案 A）：未配置 → provider=None，`create_service()` 仍按
    # 全局 TEXT_PROVIDER 建服务、`req.model` 仍用调用方传来的模型名（零行为变化）。
    # 请求参数**唯一源**在 `_audit_request_params`（与请求身份共用同一份）。
    _params = _audit_request_params(model=model)
    channel_provider = _params["channel_provider"] or None
    channel_model = _params["channel_model"] or None
    _chan = {"channel_provider": _params["channel_provider"],
             "channel_model": _params["channel_model"]}
    # v6.134.1（审核）：工厂初始化（含 config.validate / provider 注册）此前在
    # 结果处理之外——异常会直接冒出，既不是 request_failed、也不进审计台账，
    # 整轮崩在提交链上。收进来按调用失败处置（提交点 BLOCK 语义不变，台账有据）。
    try:
        service = create_service() if channel_provider is None else create_service(channel_provider)
    except Exception as exc:
        return AuditResult("request_failed", [], f"audit service init failed: {exc}",
                           attempts=1, **_chan)
    import time as _time
    _audit_max_tokens = _params["max_tokens"]
    req = TextGenerationRequest(
        system_prompt=_params["system_prompt"],
        user_prompt=build_audit_prompt(novel_text, plan, script, audit_focus=audit_focus, metadata=metadata),
        max_tokens=_audit_max_tokens,
        temperature=_params["temperature"],
        json_schema=_params["schema"],
        model=_params["model"],
        timeout_sec=timeout_sec,
        reasoning_effort=_params["reasoning_effort"],
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
    import dataclasses as _dc
    _min_useful = float(min_useful_sec if min_useful_sec is not None
                        else _env_int("AUDIT_MIN_USEFUL_SEC",
                                      AUDIT_MIN_USEFUL_SEC_DEFAULT, minimum=1))
    while True:
        # v6.152（A1 边界）：**每次实际发起前**重算可用时间——截止时刻覆盖整次审计，
        # 而不是只算一次「进审计前的超时」。剩余不足以发起一次有意义的调用时，不发
        # 请求、如实记 `budget_exhausted`（提交点照旧 BLOCK，且不挤占收尾余量）。
        _left = None
        _req = req
        if deadline_ts is not None:
            _left = float(deadline_ts) - _time.time()
            if _left < _min_useful:
                _failed_attempts.append({
                    "attempt": _attempts + 1,
                    "failure": "budget_exhausted",
                    "available_sec": round(_left, 1),
                    "min_useful_sec": _min_useful,
                })
                return AuditResult(
                    "budget_exhausted", [],
                    "总预算不足：本次审计剩余可用 %.1fs < 最小可用 %.0fs —— "
                    "未发出请求（发出也只会挤占提交收尾余量）"
                    % (_left, _min_useful),
                    attempts=_attempts, failed_attempts=_failed_attempts, **_chan)
            _req = _dc.replace(req, timeout_sec=int(min(timeout_sec, _left)))
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
            result = service.generate(_req)
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
        # v6.152（D1）：计量行**成功也要打**——此前它写在 `if not result.success:` 里，
        # 成功的审计一声不响，只能靠 submit/response 两个时间戳反推耗时（run 01 的 144s
        # 就是这么反推的）。失败时它同时是重采样判据的输入，位置不变、只上移。
        _dur = _time.time() - _t_call
        _u = _usage or {}
        _out = _u.get("output_tokens") or _u.get("completion_tokens")
        _rsn = _u.get("reasoning_tokens")
        print(f"[AUDIT] 第 {_attempts} 次返回：耗时 {_dur:.1f}s，"
              f"max_completion_tokens={_req.max_tokens or '全局'}，"
              f"timeout={_req.timeout_sec}s（本次可用 "
              + ("不限" if _left is None else "%.1fs" % _left) + "），"
              f"output={_out}（思维链 {_rsn}），"
              f"truncated={bool(getattr(result, 'truncated', False))}，"
              f"success={bool(getattr(result, 'success', False))}")
        if not result.success:
            # v6.144（2026-09-28 跑批实测）：**截断**与「传输/超时失败」是两类，处置不同。
            #   - 截断（`truncated=True`，含「思维链吃光配额、正文为空」）：同请求**换一次
            #     采样就可能不再截断**——实测同一稿的思维链长度在 7.5k ↔ 32k+ 之间波动，
            #     撞顶与否取决于采样。故并入**同一个有界重采样预算**（`AUDIT_PARSE_RETRIES`），
            #     不新开一层、不叠加传输类重试。
            #   - 传输/超时（`truncated=False`）：provider 层已尽力（见 retry_policy），
            #     这里**一次都不重试**，直接按 request_failed 终局。
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
                # v6.153（2026-10-03 复核）：**上述「档位改不动 payload」已只对方舟成立**
                # ——provider 现在按**端点**取请求方言，OpenCode Go 端点会真的发出
                # `reasoning_effort`（目录登记 low/high/max），三档不再塌成同一份请求。
                # 但本调用的档位来自 `AUDIT_REASONING_EFFORT=low`，已是该端点**最低登记档**，
                # 「换更省的档位」仍无处可降 ⇒ 截断重采样继续走 `max_tokens`。
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
        # §14 P2：issues 与身份关系声明**同一次解析**（`_parse_audit_payload`）——
        # 各自再解一遍会出现「一个说 parse_failed、另一个说 ok」的分叉。
        parsed, status, _character_aliases = _parse_audit_payload(result.text)
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
    # A3（复核 2026-10-04）：blocking 条目缺 `repair_targets` 时，向**同一个审计者**
    # 追一次**只补这一个字段**的请求——否则整份可用审计会因缺一个字段被判成
    # 「零授权 ⇒ 必然 BLOCK」（章 2 实测：13/13 条缺，其中 5 条 blocking 全零授权，
    # 模型按审计建议做的修改被逐条回填）。
    # 补全与重采样**共用同一份剩余预算**（`_parse_retries + 1` 是本次审计的调用总量）
    _comp_notes, _comp_rec = _complete_missing_repair_targets(
        parsed, out, service=service, model=_params["model"], timeout_sec=timeout_sec,
        deadline_ts=deadline_ts, min_useful_sec=min_useful_sec,
        remaining_calls=max(0, _parse_retries + 1 - _attempts),
        novel_text=novel_text, plan=plan, script=script)
    _deviations.extend(_comp_notes)
    if _comp_rec is not None and _comp_rec.get("called"):
        # **只有真发出去的调用**才计入 attempts（跳过/未发起不算；否则又变成账实不符）
        _attempts += 1
        _comp_rec["attempt"] = _attempts
        if "_text" in _comp_rec:
            _txt = _comp_rec.pop("_text")
            _comp_rec["response_head"] = _txt[:2000]
            _comp_rec["response_len"] = len(_txt)
    return AuditResult("ok", out, schema_deviations=_deviations, usage=_usage,
                       attempts=_attempts, failed_attempts=_failed_attempts,
                       completion=_comp_rec,
                       character_aliases=_character_aliases, **_chan)


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
    "final_audit_request_identity", "parse_audit_payload",
]
