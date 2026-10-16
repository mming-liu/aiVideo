"""step02_video_gen.reroll_decision - 视频候选的**纯函数**裁决（计划 §V3）.

只做裁决，没有模型调用、没有文件副作用（§V3：「``decide(report, request, history)``
只做裁决」）。六种出口：accept / recover_same_task / revise_inputs / postprocess /
unverified / stop。

判据分层（§V3 第 2 段）：
- **合同类**（声音身份、正文、动作、实体/帧绑定）不满足 ⇒ ``revise_inputs``，
  内容取舍交回原生产者；
- **后处理类**（颜色、字幕、编码、封装、响度）⇒ ``postprocess``，视频生成 key 不变；
- **未知**（工具未返回可靠证据 / coverage 不覆盖）⇒ ``unverified``——
  不允许用关键字或阈值把未知变通过；
- 同一 request_key + 同一证据集合反复出现且没有新信息 ⇒ ``stop``（stalled），
  但**不得**因此归因"故事不可满足"。
"""
from __future__ import annotations

from dataclasses import dataclass, field

DECISION_ACCEPT = "accept"
DECISION_RECOVER_SAME_TASK = "recover_same_task"
DECISION_REVISE_INPUTS = "revise_inputs"
DECISION_POSTPROCESS = "postprocess"
DECISION_UNVERIFIED = "unverified"
DECISION_STOP = "stop"

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_UNKNOWN = "unknown"

#: 合同类问题（必须回到生产者/输入层修，不能在后处理里掩盖）
CONTRACT_KINDS = frozenset({
    "action", "dialogue_text", "narration_text", "voice_identity", "entity_identity",
    "frame_binding", "reference_binding", "story_fact", "timing_window",
})

#: 后处理类问题（不改变模型输入）
POSTPROCESS_KINDS = frozenset({
    "color", "subtitle", "encoding", "container", "loudness", "watermark",
})

#: 媒体技术类（是否源自生成错误需要分类证据；没有可靠证据时按 unknown 处理）
MEDIA_KINDS = frozenset({"decode", "black", "freeze", "silence", "cut"})


@dataclass
class VideoIssue:
    issue_id: str
    kind: str
    time_range: tuple | None = None
    entity_refs: list = field(default_factory=list)
    relation: str | None = None
    expected: str | None = None
    observed: str | None = None            # 工具没给可靠证据时**不得**填写
    evidence_paths: list = field(default_factory=list)
    evidence_hashes: list = field(default_factory=list)
    confidence: float | None = None
    required_change: str | None = None
    field_paths: list = field(default_factory=list)

    def classify(self) -> str:
        if self.kind in CONTRACT_KINDS:
            return "contract"
        if self.kind in POSTPROCESS_KINDS:
            return "postprocess"
        if self.kind in MEDIA_KINDS:
            return "media"
        return "unknown"

    def fingerprint(self) -> str:
        """证据指纹：同一问题在两次尝试里"没有新信息"的判据。"""
        return "|".join([str(self.issue_id), str(self.kind), str(self.time_range),
                         str(sorted(map(str, self.entity_refs))), str(self.expected),
                         str(self.observed), str(sorted(map(str, self.evidence_hashes)))])


@dataclass
class VideoCheckReport:
    """``VideoCheckReport v1``（§V3 第 1 段）。"""

    media_status: str = STATUS_UNKNOWN       # pass/fail/unknown
    semantic_status: str = STATUS_UNKNOWN
    coverage: dict = field(default_factory=dict)
    issues: list[VideoIssue] = field(default_factory=list)
    tool_versions: dict = field(default_factory=dict)
    source: dict = field(default_factory=dict)
    request: dict = field(default_factory=dict)
    attempt: dict = field(default_factory=dict)

    def failing(self) -> list[VideoIssue]:
        return [i for i in self.issues]


@dataclass
class Decision:
    kind: str
    reasons: list[str] = field(default_factory=list)
    issue_ids: list[str] = field(default_factory=list)
    required_changes: list[dict] = field(default_factory=list)   # [{issue_id, kind, field_paths, required_change}]
    residual: list[str] = field(default_factory=list)            # 未处理完的 issue_id
    stalled: bool = False

    def to_dict(self) -> dict:
        return {"kind": self.kind, "reasons": self.reasons, "issue_ids": self.issue_ids,
                "required_changes": self.required_changes, "residual": self.residual,
                "stalled": self.stalled}


def decide(report: VideoCheckReport, request=None, history=None) -> Decision:
    """裁决入口（纯函数）。``history`` 是此前尝试的 ``[{"request_key":…, "fingerprints":[...]}]``。"""
    req_key = None
    if request is not None:
        req_key = getattr(request, "request_key", None) or (
            request.get("request_key") if isinstance(request, dict) else None)

    issues = list(report.issues)
    fingerprints = sorted(i.fingerprint() for i in issues)

    # 0) 无新信息的循环 ⇒ stop（不归因故事不可满足）
    for h in (history or []):
        same_key = (h.get("request_key") or None) == req_key
        same_evidence = sorted(h.get("fingerprints") or []) == fingerprints
        if same_key and same_evidence and fingerprints:
            return Decision(DECISION_STOP,
                            reasons=["同 request_key 且同证据集合已出现过，无新信息，停止盲重试"],
                            issue_ids=[i.issue_id for i in issues],
                            residual=[i.issue_id for i in issues], stalled=True)

    # 1) 未知优先：任何一轴 unknown 且没有明确 fail ⇒ unverified（零重生成）
    has_fail = report.media_status == STATUS_FAIL or report.semantic_status == STATUS_FAIL
    if not has_fail and (STATUS_UNKNOWN in (report.media_status, report.semantic_status)
                         or any(i.classify() == "unknown" for i in issues)):
        return Decision(DECISION_UNVERIFIED,
                        reasons=["存在未检维度或不可靠证据（保持 unverified，不伪报 PASS）"],
                        issue_ids=[i.issue_id for i in issues])

    if not has_fail:
        return Decision(DECISION_ACCEPT, reasons=["媒体与语义两轴通过"],
                        issue_ids=[i.issue_id for i in issues])

    # 2) 有明确失败：按类别决定修法
    contract = [i for i in issues if i.classify() == "contract"]
    post = [i for i in issues if i.classify() == "postprocess"]
    media = [i for i in issues if i.classify() == "media"]

    if contract:
        return Decision(DECISION_REVISE_INPUTS,
                        reasons=["合同类问题（声音身份/正文/动作/绑定）必须回到输入或原生产者"],
                        issue_ids=[i.issue_id for i in issues],
                        required_changes=[{"issue_id": i.issue_id, "kind": i.kind,
                                           "field_paths": list(i.field_paths),
                                           "required_change": i.required_change}
                                          for i in contract],
                        residual=[i.issue_id for i in (post + media)])

    if post and not media:
        return Decision(DECISION_POSTPROCESS,
                        reasons=["仅后处理类问题：不改变视频生成输入"],
                        issue_ids=[i.issue_id for i in issues],
                        required_changes=[{"issue_id": i.issue_id, "kind": i.kind,
                                           "field_paths": list(i.field_paths),
                                           "required_change": i.required_change}
                                          for i in post])

    if media:
        # 媒体类：只有带可靠证据（observed/evidence）的才允许进入修订路径，
        # 否则回退 unverified（§B：检出黑/静止/静音不自动等于生成错误）
        evidenced = [i for i in media if i.observed and i.evidence_paths]
        if not evidenced:
            return Decision(DECISION_UNVERIFIED,
                            reasons=["媒体类检出缺少可靠证据，保持 unverified"],
                            issue_ids=[i.issue_id for i in media])
        return Decision(DECISION_REVISE_INPUTS,
                        reasons=["媒体类问题带证据，按输入层修订（重新生成属新费用，需人工确认）"],
                        issue_ids=[i.issue_id for i in issues],
                        required_changes=[{"issue_id": i.issue_id, "kind": i.kind,
                                           "field_paths": list(i.field_paths),
                                           "required_change": i.required_change}
                                          for i in evidenced])

    return Decision(DECISION_REVISE_INPUTS, reasons=["存在未归类失败，按输入层修订"],
                    issue_ids=[i.issue_id for i in issues])
