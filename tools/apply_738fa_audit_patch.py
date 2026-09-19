"""Apply evidence-backed fixes from the current final audit."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.config import NOVEL_CHAPTER, NOVEL_NAME, BOOK_INDEX, BOOK_NAME


def _shot(script: dict, index: str) -> dict:
    for shot in script.get("shots") or []:
        if str(shot.get("index")) == index:
            return shot
    raise KeyError(index)


def _spec(shot: dict, bucket: str, frame_i: int | None = None) -> list:
    layouts = shot.setdefault("frame_layouts", {})
    if bucket == "kfs":
        kfs = layouts.setdefault("kfs", [])
        while len(kfs) <= frame_i:
            kfs.append([])
        return kfs[frame_i]
    return layouts.setdefault(bucket, [])


def _item(spec: list, name: str) -> dict:
    found = next((x for x in spec if isinstance(x, dict) and x.get("name") == name), None)
    if found is None:
        found = {"name": name, "on_screen": True, "position": "", "pose": "", "gaze_target": "无", "props": []}
        spec.append(found)
    return found


def main() -> None:
    base = ROOT / "output" / "video" / NOVEL_NAME / f"part_{BOOK_INDEX:02d}_{BOOK_NAME}" / f"chapter_{NOVEL_CHAPTER:03d}"
    path = base / "script_result.json"
    script = json.loads(path.read_text(encoding="utf-8"))

    s1 = _shot(script, "1")
    s1["ff_desc"] = str(s1.get("ff_desc") or "").replace("，空无一人", "").replace("空无一人", "")

    s2 = _shot(script, "2")
    s2["lf_desc"] = str(s2.get("lf_desc") or "").replace("听香跪坐船尾", "听香起身轻靠杨旭肩头")
    _item(_spec(s2, "lf"), "听香")["pose"] = "起身轻靠"
    _item(_spec(s2, "kfs", 2), "听香")["pose"] = "起身轻靠"

    s3 = _shot(script, "3")
    for item in _spec(s3, "lf"):
        if isinstance(item, dict) and item.get("name") in ("刺客", "神秘人"):
            item["on_screen"] = False

    s5 = _shot(script, "5")
    item5 = _item(_spec(s5, "kfs", 1), "张十三")
    item5["position"] = "画左"

    s75 = _shot(script, "7.5")
    item75 = _item(_spec(s75, "kfs", 1), "张十三")
    item75.update({"position": "画左", "pose": "反手挥掌", "on_screen": True})

    s95 = _shot(script, "9.5")
    _item(_spec(s95, "kfs", 0), "群演")["position"] = "画左"
    _item(_spec(s95, "kfs", 1), "群演")["pose"] = "转身回望"

    path.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
