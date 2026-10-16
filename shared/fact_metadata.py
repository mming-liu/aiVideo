"""Lossless parsing of the labelled metadata on a required fact."""

FACT_METADATA_VERSION = "fact_metadata.v2"
LEGACY_ACCEPTANCE_VERSION = "legacy_semantic.v3"
LEGACY_ACCEPTANCE_FIELD = "planning_acceptance_version"


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
