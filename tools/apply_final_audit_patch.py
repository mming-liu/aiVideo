"""Apply the final-audit content patch to the frozen Step01 artifact."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.config import NOVEL_CHAPTER, NOVEL_NAME, BOOK_INDEX, BOOK_NAME


def _by_index(script: dict, index: str) -> dict:
    for shot in script.get("shots") or []:
        if str(shot.get("index")) == index:
            return shot
    raise KeyError(index)


def _update_group_lf(shot: dict) -> None:
    layouts = shot.get("frame_layouts") or {}
    ff = layouts.get("ff") or []
    lf = layouts.get("lf")
    if not isinstance(lf, list):
        return
    source = next((item for item in ff if isinstance(item, dict) and item.get("name") == "群演"), None)
    target = next((item for item in lf if isinstance(item, dict) and item.get("name") == "群演"), None)
    payload = {
        "name": "群演",
        "on_screen": True,
        "position": (source or {}).get("position", "画左"),
        "pose": "转身欲走",
        "gaze_target": (source or {}).get("gaze_target", "无"),
        "props": list((source or {}).get("props") or []),
    }
    if target is None:
        lf.append(payload)
    else:
        target.update(payload)


def main() -> None:
    root = ROOT
    chapter_dir = root / "output" / "video" / NOVEL_NAME / f"part_{BOOK_INDEX:02d}_{BOOK_NAME}" / f"chapter_{NOVEL_CHAPTER:03d}"
    path = chapter_dir / "script_result.json"
    backup = chapter_dir / "script_result.pre_final_audit_patch.json"
    if not backup.exists():
        shutil.copy2(path, backup)
    script = json.loads(path.read_text(encoding="utf-8"))

    shot5 = _by_index(script, "5")
    for field in ("ff_desc",):
        shot5[field] = str(shot5.get(field) or "").replace("，刀已脱手，神秘人握着刀，刀尖朝下", "")
    for kf in shot5.get("keyframes") or []:
        if isinstance(kf, dict):
            kf["desc"] = str(kf.get("desc") or "").replace("，刀已脱手，神秘人握着刀，刀尖朝下", "")
    layouts5 = shot5.get("frame_layouts") or {}
    for item in layouts5.get("ff") or []:
        if isinstance(item, dict) and item.get("name") in ("刺客", "神秘人"):
            item["props"] = []
    kfs5 = layouts5.get("kfs") or []
    if kfs5 and isinstance(kfs5[0], list):
        for item in kfs5[0]:
            if isinstance(item, dict) and item.get("name") in ("刺客", "神秘人"):
                item["props"] = []

    shot8 = _by_index(script, "8")
    layouts8 = shot8.get("frame_layouts") or {}
    for key in ("ff", "lf"):
        for item in layouts8.get(key) or []:
            if isinstance(item, dict) and item.get("name") == "杨旭":
                item["position"] = "画中"
    for spec in layouts8.get("kfs") or []:
        for item in spec or []:
            if isinstance(item, dict) and item.get("name") == "杨旭":
                item["position"] = "画中"

    shot13 = _by_index(script, "13")
    for field in ("motion_desc", "lf_desc"):
        shot13[field] = str(shot13.get(field) or "").replace("佃仆们退下", "佃仆们转身欲走")
    for kf in shot13.get("keyframes") or []:
        if isinstance(kf, dict):
            kf["desc"] = str(kf.get("desc") or "").replace("佃仆们退下", "佃仆们转身欲走")
    for spec in (shot13.get("frame_layouts") or {}).get("kfs") or []:
        for item in spec or []:
            if isinstance(item, dict) and item.get("name") == "群演" and item.get("pose") == "退下":
                item["pose"] = "转身欲走"
    _update_group_lf(shot13)

    path.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
