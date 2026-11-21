# -*- coding: utf-8 -*-
"""S06（G3）隔离请求差异样本：先定稿参考清单，再构建正文（三分支 + 真实 prepare）.

**隔离样本，不接线生产**。在独立进程里，对同一镜头分别走三条正文构建分支：

1. `seedance_concise`：Seedance 精简正文（agent_plan，r2v）；
2. `full_prompt`：完整提示（bailian，image）；
3. `first_last_image`：`first_last + image` 运动专用正文（volcengine）。

每条分支：
- 先 `build_frozen_for_shot` 构建**当前**请求（正文 + 参考清单）；
- 调真实 provider 的 `prepare_request`（**零提交**）冻结实际参考清单；
- 按最终参考清单逐项生成 `【参考绑定】参考图k=…`，追加到正文得到候选；
- 候选再走一次 `prepare_request`，核对：
  ① 最终参考清单与候选正文编号/用途**逐项对应**；
  ② 候选正文经 provider prepare 后仍成立（压缩/改写记录如实报告）；
  ③ 参考清单本身不变。

默认门禁不变；候选正文是工具内追加，未接线生产。
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

#: 分支定义：provider 决定正文构建路径与 prepare 能力。
BRANCHES = {
    "seedance_concise": {
        "video_provider": "agent_plan",
        "service_provider": "agent_plan",
        "reference_mode": "r2v",
        "media_policy": "auto",
        "prompt_path": "seedance_concise（从 shot 重建，调用方 prompt 不生效）",
    },
    "full_prompt": {
        "video_provider": "bailian",
        "service_provider": "bailian",
        "reference_mode": "image",
        "media_policy": "auto",
        "prompt_path": "full_prompt（sanitize + ensure_prefix）",
    },
    "first_last_image": {
        "video_provider": "volcengine",
        "service_provider": "volcengine",
        "reference_mode": "image",
        "media_policy": "first_last",
        "prompt_path": "first_last_image（build_motion_prompt，从 shot 重建）",
    },
}

#: 参考文件 → 正文绑定身份（隔离样本的固定映射；真实链路应从 registry/镜头布局取）。
_REF_IDENTITY = {
    "portrait_jia.png": "甲（角色外观）",
    "first_frame.png": "本镜首帧（起始构图）",
    "last_frame.png": "本镜尾帧（终态）",
}
_ROLE_IDENTITY = {
    "first_frame": "本镜首帧（起始构图）",
    "image": "本镜首帧（起始构图）",
    "last_frame": "本镜尾帧（终态）",
    "reference_image": "参考素材（角色/道具外观）",
    "reference": "参考素材（角色/道具外观）",
}


def _make_assets(tmp: Path) -> dict:
    import os as _os

    from PIL import Image

    # r2v 的首帧构图锚点要求 >10KB：用随机像素保证 PNG 足够大。
    portrait = tmp / "portrait_jia.png"
    Image.frombytes("RGB", (512, 512), _os.urandom(512 * 512 * 3)).save(portrait)
    ff = tmp / "first_frame.png"
    Image.frombytes("RGB", (512, 512), _os.urandom(512 * 512 * 3)).save(ff)
    lf = tmp / "last_frame.png"
    Image.frombytes("RGB", (512, 512), _os.urandom(512 * 512 * 3)).save(lf)
    return {"portrait": str(portrait), "ff": str(ff), "lf": str(lf)}


def _service(provider: str):
    if provider == "agent_plan":
        import _video_gen.agent_plan_provider as ap
        if not getattr(ap, "AGENT_PLAN_API_KEY", ""):
            ap.AGENT_PLAN_API_KEY = "test-key"
        svc = ap.AgentPlanVideoService()
        # r2v 需要 2.0/2.5 系列（1.0-pro 不支持参考生视频）。
        svc._model = "doubao-seedance-2.0"
        return svc
    if provider == "volcengine":
        import _video_gen.volcengine_provider as vp
        if not getattr(vp, "VOLCENGINE_API_KEY", ""):
            vp.VOLCENGINE_API_KEY = "test-key"
        svc = vp.VolcengineVideoService()
        svc._t2v_model_queue = ["doubao-seedance-1.0-pro"]
        svc._i2v_model_queue = ["doubao-seedance-2.0"]
        return svc
    from _video_gen import create_service
    return create_service(provider)


def _safe_prepare(svc, req) -> dict:
    """真实 provider 的 `prepare_request`（零提交）；失败/不支持如实记录."""
    try:
        prepared = svc.prepare_request(req)
    except Exception as exc:                       # noqa: BLE001 - 能力不足/未配置
        return {"status": "prepare_failed", "reason": str(exc)[:300],
                "prompt": None, "materials": [], "modifications": []}
    if not prepared.is_ready():
        return {"status": "not_ready",
                "reason": prepared.unsupported_reason or "prepared_not_ready",
                "prompt": None, "materials": [], "modifications": []}
    materials = [{
        "ref_id": getattr(m, "ref_id", ""),
        "role": getattr(m, "role", ""),
        "kind": getattr(m, "kind", ""),
        "name": Path(str(getattr(m, "source_path", "") or "")).name,
    } for m in (prepared.materials or [])]
    return {"status": "prepared", "reason": "",
            "prompt": str(prepared.prompt or ""),
            "materials": materials,
            "modifications": list(prepared.modifications or [])}


def _identity_of(item: dict) -> str:
    name = item.get("name") or ""
    if name in _REF_IDENTITY:
        return _REF_IDENTITY[name]
    return _ROLE_IDENTITY.get(item.get("role") or "", "参考素材")


def _reference_list(prepared: dict, frozen) -> list[dict]:
    """最终参考清单：优先取 provider prepared.materials，否则取冻结 references."""
    if prepared.get("status") == "prepared":
        return prepared["materials"]
    refs = []
    for ref in (getattr(frozen, "references", None) or []):
        refs.append({
            "ref_id": getattr(ref, "ref_id", ""),
            "role": getattr(ref, "role", ""),
            "kind": "",
            "name": Path(str(getattr(ref, "path", "") or "")).name,
        })
    return refs


def _binding_block(refs: list[dict]) -> tuple[str, list[str]]:
    identities = [_identity_of(item) for item in refs]
    lines = ["参考图%d=%s" % (i, identity)
             for i, identity in enumerate(identities, 1)]
    return "【参考绑定】" + "；".join(lines), identities


def _branch_run(branch: dict, tmp: Path, assets: dict) -> dict:
    for key in ("VIDEO_PROVIDER", "VIDEO_REFERENCE_MODE", "VIDEO_MEDIA_POLICY"):
        os.environ.pop(key, None)
    os.environ["VIDEO_PROVIDER"] = branch["video_provider"]
    os.environ["VIDEO_REFERENCE_MODE"] = branch["reference_mode"]
    os.environ["VIDEO_MEDIA_POLICY"] = branch["media_policy"]

    import shared.paths as paths
    from step02_video_gen import request_plan as rp
    from step02_video_gen.submit_poll import build_frozen_for_shot

    chapter = tmp / "chapter_001"
    chapter.mkdir(exist_ok=True)
    work = tmp / "vimax_working"
    work.mkdir(exist_ok=True)
    shot_work = work / "shots" / "1"
    shot_work.mkdir(parents=True, exist_ok=True)
    (shot_work / "first_frame.png").write_bytes(Path(assets["ff"]).read_bytes())
    (shot_work / "last_frame.png").write_bytes(Path(assets["lf"]).read_bytes())
    from shared.json_io import atomic_dump_json
    atomic_dump_json(work / "character_portraits_registry.json",
                     {"甲": {"front": {"path": assets["portrait"],
                                       "description": "青衫少年"}}},
                     ensure_ascii=False)

    shot = {
        "index": 1, "duration_sec": 5.0,
        "location": "渡口", "scene": "渡口",
        "prompt": "甲站在渡口，望向江面。",
        "ff_desc": "甲站在渡口，手里提着灯笼，望向江面。",
        "lf_desc": "甲抬头，灯笼举高。",
        "motion_desc": "甲抬手把灯笼举高。",
        "camera_movement": "固定机位",
        "frame_layouts": {
            "ff": [{"name": "甲", "on_screen": True,
                    "position": "渡口", "pose": "站立"}],
            "lf": [{"name": "甲", "on_screen": True,
                    "position": "渡口", "pose": "举灯"}],
            "kfs": []},
    }
    script = {"characters": [{"identifier": "甲", "name": "甲"}],
              "shots": [shot], "total_duration": 5.0,
              "script_revision": "g3-isolated"}

    svc = _service(branch["service_provider"])
    frame_paths = [assets["ff"], assets["lf"]]
    original_chapter_dir = paths.chapter_dir
    original_vimax = paths.vimax_working_dir
    paths.chapter_dir = lambda *a, **k: chapter
    paths.vimax_working_dir = lambda *a, **k: work
    try:
        current_frozen, current_req = build_frozen_for_shot(
            shot=shot, script=script, video_adapter=None,
            frame_paths=frame_paths, mode="A", scope={"chapter": 1},
            provider=branch["video_provider"],
            model_id="doubao-seedance-2.0", script_revision="g3-isolated")
        current_prepared = _safe_prepare(svc, current_req)
        refs = _reference_list(current_prepared, current_frozen)
        block, identities = _binding_block(refs)

        # 参考清单先行：候选正文按最终参考清单逐项绑定后，再走真实 prepare。
        candidate_prompt = str(current_req.prompt or "") + "\n" + block
        candidate_req = dataclasses.replace(current_req, prompt=candidate_prompt)
        candidate_prepared = _safe_prepare(svc, candidate_req)
        candidate_frozen = rp.build_frozen_request(
            candidate_req, shot=shot, scope={"chapter": 1},
            script_revision="g3-isolated", provider=branch["video_provider"],
            model_id="doubao-seedance-2.0",
            requirements=current_frozen.requirements,
            prepared=None)
    finally:
        paths.chapter_dir = original_chapter_dir
        paths.vimax_working_dir = original_vimax

    candidate_refs = _reference_list(candidate_prepared, candidate_frozen)
    candidate_final_prompt = (
        candidate_prepared["prompt"] if candidate_prepared["status"] == "prepared"
        else str(candidate_frozen.prompt or ""))
    current_final_prompt = (
        current_prepared["prompt"] if current_prepared["status"] == "prepared"
        else str(current_frozen.prompt or ""))
    # 逐项对应：候选正文的 `参考图k=身份` 必须与最终参考清单第 k 项一致。
    per_item = []
    for index, (ref, identity) in enumerate(
            zip(candidate_refs, identities), 1):
        marker = "参考图%d=%s" % (index, identity)
        per_item.append({
            "index": index,
            "ref": ref,
            "expected": marker,
            "in_final_prompt": marker in candidate_final_prompt,
        })
    return {
        "branch": branch["prompt_path"],
        "env": {"VIDEO_PROVIDER": branch["video_provider"],
                "VIDEO_REFERENCE_MODE": branch["reference_mode"],
                "VIDEO_MEDIA_POLICY": branch["media_policy"]},
        "current": {
            "prompt_tail": current_final_prompt[-80:],
            "references": refs,
            "prepared_status": current_prepared["status"],
            "prepared_reason": current_prepared["reason"],
        },
        "candidate": {
            "block": block,
            "prompt_tail": candidate_final_prompt[-120:],
            "references": candidate_refs,
            "prepared_status": candidate_prepared["status"],
            "prepared_reason": candidate_prepared["reason"],
            "modifications": candidate_prepared["modifications"],
        },
        "checks": {
            "reference_list_unchanged": (
                [(r.get("role"), r.get("name")) for r in refs]
                == [(r.get("role"), r.get("name")) for r in candidate_refs]),
            # 三态：True=经 prepare 后仍在最终正文；False=prepare 后丢失；
            # None=provider 无 prepare_request，**未验证**（不得写成 true）。
            "block_survives_prepare": (
                block in candidate_prepared["prompt"]
                if candidate_prepared["status"] == "prepared" else None),
            "block_survives_prepare_status": (
                "verified" if candidate_prepared["status"] == "prepared"
                else "unverified_provider_no_prepare"),
            "block_in_frozen_prompt": block in str(candidate_frozen.prompt or ""),
            "bindings_one_to_one": (
                len(per_item) == len(candidate_refs)
                and all(item["in_final_prompt"] for item in per_item)),
            "binding_count": len(identities),
            "reference_count": len(candidate_refs),
        },
        "per_item": per_item,
    }


def build_report(tmp: Path) -> dict:
    assets = _make_assets(tmp)
    branches = {name: _branch_run(branch, tmp, assets)
                for name, branch in BRANCHES.items()}
    return {
        "schema": "s06.g3_isolated_request_diff.v2",
        "evidence_level": (
            "isolated_sample:真实 build_frozen_for_shot + 真实 provider."
            "prepare_request（零提交）；候选绑定段为工具内追加，未接线生产"),
        "branches": branches,
        "summary": {
            "branches": list(branches),
            "all_reference_lists_unchanged": all(
                b["checks"]["reference_list_unchanged"]
                for b in branches.values()),
            "all_bindings_one_to_one": all(
                b["checks"]["bindings_one_to_one"]
                for b in branches.values()),
            "prepared_branches": [
                name for name, b in branches.items()
                if b["current"]["prepared_status"] == "prepared"],
            "unprepared_branches": [
                name for name, b in branches.items()
                if b["current"]["prepared_status"] != "prepared"],
            "prepare_verified_branches": [
                name for name, b in branches.items()
                if b["checks"]["block_survives_prepare"] is True],
            "prepare_unverified_branches": [
                name for name, b in branches.items()
                if b["checks"]["block_survives_prepare"] is None],
        },
        "limits": [
            "候选正文只在工具内追加，未接线生产；真实收益需实跑对照。",
            "bailian 未实现 prepare_request，full_prompt 分支的最终参考清单"
            "取自冻结请求（生产同路径记 pending_verification），不是 provider 实测。",
            "样本使用隔离角色肖像/首帧，不读真实资产库；不代表真实资产分布。",
        ],
    }


def _tri(value) -> str:
    """三态显示：True/False/None（None=未验证）."""
    if value is True:
        return "是"
    if value is False:
        return "否"
    return "未验证"


def _markdown(report: dict) -> str:
    lines = [
        "# S06（G3）隔离请求差异样本：三分支 + 真实 prepare_request",
        "",
        "> 生成：`python tools/build_s06_g3_isolated_diff.py`（离线、零模型调用）",
        "> **隔离样本，不接线生产**；默认门禁不变，真实收益未验证。",
        "",
        "| 分支 | 正文路径 | 参考清单 | prepare | 绑定逐项对应 |",
        "|---|---|---|---|---|",
    ]
    for name, b in report["branches"].items():
        lines.append("| %s | %s | %s | %s | %s |" % (
            name, b["branch"],
            "%d 项（%s）" % (
                b["checks"]["reference_count"],
                "、".join(r.get("name") or r.get("role")
                         for r in b["current"]["references"])),
            b["current"]["prepared_status"],
            "是" if b["checks"]["bindings_one_to_one"] else "否"))
    lines += ["", "## 逐分支候选", ""]
    for name, b in report["branches"].items():
        lines += [
            "### %s" % name,
            "",
            "- 正文路径：%s" % b["branch"],
            "- 候选新增段：`%s`" % b["candidate"]["block"],
            "- 候选正文尾部：`%s`" % b["candidate"]["prompt_tail"],
            "- 参考清单不变：%s；绑定逐项对应：%s；绑定数=%d / 参考数=%d" % (
                b["checks"]["reference_list_unchanged"],
                b["checks"]["bindings_one_to_one"],
                b["checks"]["binding_count"],
                b["checks"]["reference_count"]),
            "- 绑定经 prepare 后仍成立：%s（%s）；冻结正文含绑定：%s" % (
                _tri(b["checks"]["block_survives_prepare"]),
                b["checks"]["block_survives_prepare_status"],
                b["checks"]["block_in_frozen_prompt"]),
            "- prepare：%s%s" % (
                b["candidate"]["prepared_status"],
                ("（%s）" % b["candidate"]["prepared_reason"]
                 if b["candidate"]["prepared_reason"] else "")),
            "",
        ]
        for item in b["per_item"]:
            lines.append("- 参考图%d：%s（%s）→ `%s` 在最终正文=%s" % (
                item["index"],
                item["ref"].get("name") or item["ref"].get("role"),
                item["ref"].get("role"),
                item["expected"], item["in_final_prompt"]))
        lines.append("")
    summary = report["summary"]
    lines += [
        "## 结论（供裁决）",
        "",
        "- 三分支参考清单均不变：%s；绑定逐项对应：%s。"
        % (summary["all_reference_lists_unchanged"],
           summary["all_bindings_one_to_one"]),
        "- 真实 `prepare_request` 可用分支：%s；不可用：%s。"
        % ("、".join(summary["prepared_branches"]) or "无",
           "、".join(summary["unprepared_branches"]) or "无"),
        "- 绑定经 prepare 验证：%s；未验证（provider 无 prepare）：%s。"
        % ("、".join(summary["prepare_verified_branches"]) or "无",
           "、".join(summary["prepare_unverified_branches"]) or "无"),
        "- 不可用分支按生产同一路径记 `pending_verification`，不冒充已准备。",
        "- 本样本只回答「结构上参考绑定能否进正文并通过 prepare」；"
        "不回答模型是否因此改善。",
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
    from shared.json_io import atomic_dump_json
    atomic_dump_json(out_dir / "g3_isolated_request_diff.json", report,
                     ensure_ascii=False, indent=2)
    (out_dir / "g3_isolated_request_diff.md").write_text(
        _markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print("out:", str(out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
