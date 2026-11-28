# -*- coding: utf-8 -*-
"""候选夹具：走**生产者真实出口**（seal_manifest + commit_accepted）造候选。

2026-10-03 复核 P1-2 之后，消费侧要求 manifest 被 accepted 指针**指名**。夹具若只
写一份 manifest（旧写法），测的就是"从未被采纳的半成品"，与生产路径无关；因此这里
统一走与生产者相同的两步，并显式给出 scope/revision/source。
"""
from __future__ import annotations

from pathlib import Path

from shared import enhancement_io as eio

#: 夹具默认检查：**按包**给各自生产者的真实检查名（消费侧必需检查按包固定，
#: 用视频的 `media` 去套 TTS 只会把一切候选判成未通过）。
DEFAULT_CHECKS = {
    "video": [{"name": "media", "status": "pass"},
              {"name": "semantic", "status": "pass"}],
    "tts": [{"name": "segments_lossless", "status": "pass"},
            {"name": "identity_complete", "status": "pass"},
            {"name": "units", "status": "pass"}],
}


def seal_accepted_candidate(tmp_path, name: str, *, package: str, scope: dict,
                            revision: str, source: str = "accepted_script",
                            units: list | None = None, checks: list | None = None,
                            status: str = "accepted", package_dir=None,
                            accepted_root=None, input_key: str = "",
                            request_key: str | None = None):
    """造一个**已采纳**的候选：返回 ``(manifest_path, manifest_sha256)``.

    ``units`` 里的 ``_filename`` / ``_bytes`` 是夹具专用键（落盘用），其余键原样进
    manifest。``accepted_root=None`` ⇒ 指针落在 attempt 目录（视频包的真实约定）。
    """
    root = Path(package_dir) if package_dir else Path(tmp_path) / "exp" / package
    att = root / eio.ATTEMPTS_DIRNAME / name
    att.mkdir(parents=True, exist_ok=True)
    prepared_units = []
    for i, raw in enumerate(units or []):
        u = dict(raw)
        existing = u.pop("_existing_path", None) or u.get("path")
        if existing and Path(existing).is_file():
            # 调用方已造好素材（如带侧车的真实媒体）：**原样沿用**，不复制不改写
            u["path"] = str(Path(existing).resolve())
        else:
            fname = u.pop("_filename", None) or (
                "%s.bin" % (u.get("unit_id") or "unit_%s" % u.get("shot_index", i)))
            p = att / "outputs" / fname
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(u.pop("_bytes", b"media-%d" % i))
            u["path"] = str(p)
        u["sha256"] = u.get("sha256") or eio.sha256_file(Path(u["path"]))
        prepared_units.append(u)
    payload = {
        "package": package, "scope": dict(scope), "script_revision": str(revision),
        "source": str(source), "status": str(status), "units": prepared_units,
        "artifacts": [eio.artifact_record(Path(u["path"])) for u in prepared_units],
        "checks": list(checks if checks is not None
                       else DEFAULT_CHECKS.get(package,
                                               [{"name": "decode", "status": "pass"}])),
    }
    # 请求身份**必填**（消费侧核验要求 manifest 与指针记同一个 key）：
    # 缺省给一个与本次 attempt 绑定的确定值，调用方也可以显式给。
    key = str(request_key or input_key or ("rk-%s" % att.name))
    payload["request_key"] = key
    sealed = eio.seal_manifest(att, payload)
    pointer_root = Path(accepted_root) if accepted_root is not None else att
    eio.commit_accepted(pointer_root, attempt_id=att.name,
                        manifest_path=att / eio.MANIFEST_FILENAME,
                        manifest_sha256=sealed["manifest_sha256"],
                        input_key=key)
    return att / eio.MANIFEST_FILENAME, sealed["manifest_sha256"]
