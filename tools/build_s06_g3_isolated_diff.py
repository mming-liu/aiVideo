# -*- coding: utf-8 -*-
"""S06（G3）隔离请求差异样本：先定稿参考清单，再构建正文.

**隔离样本，不接线生产**：本工具在独立进程里先构建当前请求，再用同一份参考
清单构建候选请求（正文末尾追加 `【参考绑定】` 段），逐字段 diff。默认门禁不变。

当前链路（已核对 `step02b_video_gen/main.py:464`）：
`build_frozen_for_shot` 先构建正文与参考清单 → `prepare_request` 冻结实际参考 →
再用 `prepared` 重建冻结请求。正文构建时读不到 `prepare_request` 才定稿的参考
清单，因此正文里无法声明「参考图 k 是谁/什么」。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _make_assets(tmp: Path) -> dict:
    from PIL import Image

    portrait = tmp / "portrait_jia.png"
    Image.new("RGB", (64, 96), (40, 60, 90)).save(portrait)
    ff = tmp / "first_frame.png"
    Image.new("RGB", (64, 64), (18, 24, 30)).save(ff)
    lf = tmp / "last_frame.png"
    Image.new("RGB", (64, 64), (30, 24, 18)).save(lf)
    return {"portrait": str(portrait), "ff": str(ff), "lf": str(lf)}


def _build_requests(tmp: Path) -> tuple[dict, dict, dict]:
    """返回 (current_frozen, current_req, candidate_req)."""
    os.environ["VIDEO_REFERENCE_MODE"] = "r2v"
    os.environ["VIDEO_MEDIA_POLICY"] = "auto"
    # 非 Seedance 路径才会消费调用方传入的 prompt（Seedance 精简路径从 shot 重建）。
    os.environ["VIDEO_PROVIDER"] = "stub"
    import shared.paths as paths
    from step02_video_gen import prompt_builder as pb
    from step02_video_gen.submit_poll import build_frozen_for_shot

    assets = _make_assets(tmp)
    chapter = tmp / "chapter_001"
    chapter.mkdir(exist_ok=True)
    (chapter / "first_frame.png").write_bytes(
        Path(assets["ff"]).read_bytes())
    work = tmp / "vimax_working"
    work.mkdir(exist_ok=True)
    (work / "character_portraits_registry.json").write_text(
        json.dumps({"甲": {"front": {"path": assets["portrait"],
                                    "description": "青衫少年"}}},
                   ensure_ascii=False),
        encoding="utf-8")
    # 隔离：只让样本自己的角色肖像/首帧参与参考收集，不读真实资产库。
    pb._load_character_registry = lambda: {
        "甲": {"front": {"path": assets["portrait"], "description": "青衫少年"}}}
    pb._load_scene_assets_registry = lambda: {}
    pb._collect_prop_asset_refs = lambda *a, **k: []

    original_chapter_dir = paths.chapter_dir
    original_vimax = paths.vimax_working_dir
    paths.chapter_dir = lambda *a, **k: chapter
    paths.vimax_working_dir = lambda *a, **k: work
    try:
        shot = {
            "index": 1,
            "duration_sec": 5.0,
            "location": "渡口",
            "scene": "渡口",
            "prompt": "甲站在渡口，望向江面。",
            "ff_desc": "甲站在渡口，手里提着灯笼，望向江面。",
            "lf_desc": "甲抬头，灯笼举高。",
            "motion_desc": "甲抬手把灯笼举高。",
            "camera_movement": "固定机位",
            "frame_layouts": {"ff": [{"name": "甲", "on_screen": True,
                                      "position": "渡口", "pose": "站立"}],
                              "lf": [{"name": "甲", "on_screen": True,
                                      "position": "渡口", "pose": "举灯"}],
                              "kfs": []},
        }
        script = {"characters": [{"identifier": "甲", "name": "甲"}],
                  "shots": [shot], "total_duration": 5.0}
        frozen, req = build_frozen_for_shot(
            shot=shot, script=script, video_adapter=None,
            frame_paths=[assets["ff"], assets["lf"]], mode="A",
            scope={"chapter": 1}, provider="stub", model_id="stub-model")
        current_prompt = str(req.prompt or "")

        # 参考清单先行：按当前请求实际收集到的参考，给出身份/角色绑定。
        refs = list(req.reference_paths or [])
        bindings = []
        for i, path in enumerate(refs, 1):
            p = str(path)
            if p == assets["portrait"]:
                who = "甲（角色外观）"
            elif p == assets["ff"]:
                who = "本镜首帧（起始构图）"
            else:
                who = "参考素材%d" % i
            bindings.append("参考图%d=%s" % (i, who))
        candidate_prompt = current_prompt + "\n【参考绑定】" + "；".join(bindings)
        frozen2, req2 = build_frozen_for_shot(
            shot=shot, script=script, video_adapter=None,
            frame_paths=[assets["ff"], assets["lf"]], mode="A",
            scope={"chapter": 1}, provider="stub", model_id="stub-model",
            prompt=candidate_prompt)
        return {
            "assets": assets,
            "current": {"prompt": current_prompt,
                        "reference_paths": refs,
                        "media_selection": req.media_selection,
                        "frozen": frozen},
            "candidate": {"prompt": candidate_prompt,
                          "reference_paths": list(req2.reference_paths or []),
                          "media_selection": req2.media_selection,
                          "frozen": frozen2},
        }
    finally:
        paths.chapter_dir = original_chapter_dir
        paths.vimax_working_dir = original_vimax


def _frozen_facts(frozen) -> dict:
    """冻结请求的只读事实（兼容 dataclass / dict）."""
    data = frozen.to_dict() if hasattr(frozen, "to_dict") else (
        frozen if isinstance(frozen, dict) else {})
    refs = []
    for ref in data.get("references") or []:
        item = ref if isinstance(ref, dict) else {}
        raw = str(item.get("source_path") or item.get("path") or "")
        refs.append({
            "role": item.get("role"),
            "kind": item.get("kind"),
            "name": Path(raw).name if raw else "",
        })
    return {
        "prompt": str(data.get("prompt") or ""),
        "media_mode": data.get("media_mode"),
        "references": refs,
    }


def build_report(tmp: Path) -> dict:
    data = _build_requests(tmp)
    cur, cand = data["current"], data["candidate"]
    cur_frozen = _frozen_facts(cur["frozen"])
    cand_frozen = _frozen_facts(cand["frozen"])
    added_block = ""
    if cand["prompt"].startswith(cur["prompt"]):
        added_block = cand["prompt"][len(cur["prompt"]):].strip()

    return {
        "schema": "s06.g3_isolated_request_diff.v1",
        "evidence_level": (
            "isolated_sample:真实 build_frozen_for_shot 构建两版请求；"
            "候选正文为工具内追加，未接线生产"),
        "current_chain": [
            "build_frozen_for_shot 先构建正文与参考清单",
            "prepare_request 冻结实际生效参考",
            "再用 prepared 重建冻结请求",
        ],
        "samples": {
            "current": {
                "prompt": cur["prompt"],
                "reference_paths": [Path(p).name for p in cur["reference_paths"]],
                "reference_roles": [
                    item.get("role") for item in
                    ((cur["media_selection"] or {}).get("items") or [])],
                "frozen_prompt": cur_frozen["prompt"],
                "frozen_references": cur_frozen["references"],
                "frozen_media_mode": cur_frozen["media_mode"],
            },
            "candidate": {
                "prompt": cand["prompt"],
                "reference_paths": [Path(p).name for p in cand["reference_paths"]],
                "reference_roles": [
                    item.get("role") for item in
                    ((cand["media_selection"] or {}).get("items") or [])],
                "frozen_prompt": cand_frozen["prompt"],
                "frozen_references": cand_frozen["references"],
                "frozen_media_mode": cand_frozen["media_mode"],
            },
        },
        "diff": {
            "prompt_added_block": (
                "【参考绑定】" in cand["prompt"]
                and "【参考绑定】" not in cur["prompt"]),
            "added_block": added_block,
            "reference_list_unchanged": (
                list(cur["reference_paths"]) == list(cand["reference_paths"])),
            "frozen_prompt_changed": (
                cur_frozen["prompt"] != cand_frozen["prompt"]),
            "reference_count": len(cur["reference_paths"]),
            "binding_count": added_block.count("参考图"),
        },
        "limits": [
            "候选正文只在工具内追加，未接线生产；真实收益需实跑对照。",
            "样本使用隔离角色肖像/首帧，不读真实资产库；不代表真实资产分布。",
        ],
    }


def _markdown(report: dict) -> str:
    cur = report["samples"]["current"]
    cand = report["samples"]["candidate"]
    lines = [
        "# S06（G3）隔离请求差异样本：先定稿参考、再构建正文",
        "",
        "> 生成：`python tools/build_s06_g3_isolated_diff.py`（离线、零模型调用）",
        "> **隔离样本，不接线生产**；默认门禁不变，真实收益未验证。",
        "",
        "## 当前链路",
        "",
    ]
    lines += ["%d. %s" % (i, step) for i, step in
              enumerate(report["current_chain"], 1)]
    lines += [
        "",
        "## 逐字段差异",
        "",
        "| 字段 | 当前 | 候选 |",
        "|---|---|---|",
        "| `request.prompt`（尾部） | …%s | …%s |" % (
            json.dumps(cur["prompt"][-60:], ensure_ascii=False),
            json.dumps(cand["prompt"][-100:], ensure_ascii=False)),
        "| 正文新增段 | （无） | %s |" % (
            json.dumps(report["diff"]["added_block"], ensure_ascii=False)
            or "（空）"),
        "| `reference_paths` | %s | %s |" % (
            "、".join(cur["reference_paths"]) or "（空）",
            "、".join(cand["reference_paths"]) or "（空）"),
        "| `references[*].role` | %s | %s |" % (
            "、".join(str(r) for r in cur["reference_roles"]) or "（空）",
            "、".join(str(r) for r in cand["reference_roles"]) or "（空）"),
        "| `frozen.references[*].role` | %s | %s |" % (
            "、".join("%s:%s" % (r["role"], r["name"])
                      for r in cur["frozen_references"]) or "（空）",
            "、".join("%s:%s" % (r["role"], r["name"])
                      for r in cand["frozen_references"]) or "（空）"),
        "| `frozen.media_mode` | %s | %s |" % (
            cur["frozen_media_mode"] or "（空）",
            cand["frozen_media_mode"] or "（空）"),
        "| 正文参考绑定 | 无 | %d 项 |" % report["diff"]["binding_count"],
        "",
        "## 结论（供裁决）",
        "",
        "- 参考清单本身不变（%s）；差异只在正文是否声明参考身份。"
        % ("一致" if report["diff"]["reference_list_unchanged"] else "不一致"),
        "- 候选块为工具内追加，**未接线生产**；要证明收益，需在隔离分支实现"
        "「参考清单先行」后做真实模型对照。",
        "- 本样本只回答「结构上能不能把参考绑定写进正文」，不回答模型是否因此改善。",
        "",
        "## 限制",
        "",
    ]
    lines += ["- %s" % item for item in report["limits"]]
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out", default="docs/step01-layered-execution/artifacts/s06",
        help="输出目录（默认 docs/.../artifacts/s06）")
    args = parser.parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    import tempfile
    with tempfile.TemporaryDirectory(prefix="g3_iso_",
                                     ignore_cleanup_errors=True) as tmp:
        report = build_report(Path(tmp))
    (out_dir / "g3_isolated_request_diff.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    (out_dir / "g3_isolated_request_diff.md").write_text(
        _markdown(report), encoding="utf-8")
    print(json.dumps(report["diff"], ensure_ascii=False, indent=2))
    print("out:", str(out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
