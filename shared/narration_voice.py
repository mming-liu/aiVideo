"""Explicit offscreen voice ownership; text never determines a speaker."""
from __future__ import annotations


def narration_role(entry: dict, characters: list | None = None) -> str:
    """Missing means narrator; explicit identities must be nonempty and exact.

    With a character inventory, require a unique matching input character name.
    Without one, only validate the field shape (plan projection stage).
    """
    if "voice_role" not in entry:
        return "narrator"
    role = entry["voice_role"]
    if not isinstance(role, str) or not role.strip() or role != role.strip():
        raise ValueError("narration.voice_role: expected a nonempty exact role name")
    if role != "narrator" and characters is not None:
        matches = [c for c in characters if isinstance(c, dict)
                   and c.get("identifier", c.get("name")) == role]
        if len(matches) != 1:
            raise ValueError("narration.voice_role: unknown or ambiguous role %r" % role)
    return role


def role_fields(entry: dict) -> dict:
    """Preserve absence for legacy plan identity and round-trip compatibility."""
    role = narration_role(entry)
    return {"voice_role": role} if "voice_role" in entry else {}
