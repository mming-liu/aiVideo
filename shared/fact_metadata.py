"""Lossless parsing of the labelled metadata on a required fact."""

FACT_METADATA_VERSION = "fact_metadata.v2"
LEGACY_ACCEPTANCE_VERSION = "legacy_semantic.v3"
LEGACY_ACCEPTANCE_FIELD = "planning_acceptance_version"
#: 轻量规划（`STEP01_PLAN_MODE=lightweight`）产出的计划：同一套旧形状与确定性验收，
#: 但**没有**计划层 LLM 内容核验记录（按设计不跑，不是缺失）。只由
#: `step01_script_gen.lightweight_plan` 写入，并带 `lightweight_plan` 身份块。
LIGHTWEIGHT_ACCEPTANCE_VERSION = "lightweight_plan.v1"
#: 走「新旧形状投影」语义（逐分句文字重合门让位给内容核验或轻量合同）的验收版本
SEMANTIC_ACCEPTANCE_VERSIONS = (LEGACY_ACCEPTANCE_VERSION, LIGHTWEIGHT_ACCEPTANCE_VERSION)


def split_claim_evidence(fact) -> tuple:
    text = str(fact or "")
    parts = text.split("｜")
    evidence = ""
    for part in parts[1:]:
        label, separator, value = part.strip().partition("：")
        if separator and label == "原文证据":
            evidence = value.strip()
            # Remove only the metadata wrapper, never an inner quotation.
            for opening, closing in (("“", "”"), ('"', '"'), ("「", "」"), ("『", "』")):
                if evidence.startswith(opening) and evidence.endswith(closing):
                    evidence = evidence[1:-1]
                    break
            break
    return parts[0].strip(), evidence
