"""Exact revision request evidence and a separately scoped replay identity."""
import copy
import hashlib
import json


def revision_request_evidence(system_prompt, user_prompt, payload, response_contract):
    """Keep actual bytes; mask only named budget fields in the rendered payload.

    The stable digest is diagnostic, never an admission or cache identity. Missing
    or ambiguous payloads fail rather than silently masking unrelated prompt text.
    """
    if user_prompt.count(payload) != 1:
        raise ValueError("revision payload must occur exactly once")
    value = json.loads(payload)
    stable = copy.deepcopy(value)
    volatile = {}
    for container, key in (("limits", "deadline_at"), ("budget", "remaining_sec")):
        if key in stable.get(container, {}):
            volatile[container + "." + key] = stable[container][key]
            stable[container][key] = "<volatile-budget>"
    stable_payload = json.dumps(stable, ensure_ascii=False, indent=1)
    stable_prompt = user_prompt.replace(payload, stable_payload, 1)
    raw = ("system:" + system_prompt + "\nuser:" + user_prompt).encode("utf8")
    identity = json.dumps({"system_prompt": system_prompt, "user_prompt": stable_prompt,
                           "response_contract": response_contract},
                          ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"schema": "revision_request.evidence.v1",
            "system_prompt": system_prompt, "user_prompt": user_prompt,
            "response_contract": copy.deepcopy(response_contract),
            "prompt_sha256": hashlib.sha256(raw).hexdigest(),
            "replay_sha256": hashlib.sha256(identity.encode("utf8")).hexdigest(),
            "volatile_budget": volatile}
