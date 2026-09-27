"""Conservative subject attribution for per-character frame state checks."""

from __future__ import annotations


def leading_named_span(clause: str, names: list[str]) -> tuple[str, str]:
    """Return the leading named actor and text before another actor appears.

    A name later in a clause can be a location or object (``张十三蹲在杨旭身旁``).
    Such a mention cannot establish whose pose the clause describes. When the
    leading subject is unknown, callers must not assign its state to a person.
    """
    text = str(clause or "").strip()
    known = sorted({str(name).strip() for name in names if str(name).strip()},
                   key=len, reverse=True)
    subject = next((name for name in known if text.startswith(name)), "")
    if not subject:
        return "", text
    end = min((pos for name in known if name != subject
               for pos in [text.find(name, len(subject))] if pos >= 0),
              default=len(text))
    return subject, text[:end]
