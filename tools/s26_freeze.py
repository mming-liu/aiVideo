# -*- coding: utf-8 -*-
"""S26/S33：冻结一次运行的输入与正式产物保护清单（只读 + 写隔离产物）.

用法（先设置 `OUTPUT_BASE` 等隔离环境，再运行；本脚本本身不改业务文件）::

    python tools/s26_freeze.py --out .tmp/s26/freeze.json \
        --input novel=锦衣夜行_77119小说.txt \
        --input ch1_storyboard=.tmp/s14b/runs/s18_final/storyboard.json \
        --protect output/.../chapter_001/script_result.json

`--check` 按上次清单核 SHA-256；缺失/变化逐项报告。无任务前独立基线的保护文件
单列为 `no_pre_task_baseline`，不记成"未变化"。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(*args) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                             text=True, encoding="utf-8", errors="replace", check=False)
        return out.stdout.strip()
    except OSError as exc:
        return "git_error:%s" % exc


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _config_identity() -> dict:
    """只读有效配置；导入失败如实记为错误，不猜值."""
    try:
        from _text_gen.config import TextGenCfg
        from _text_gen.agent_plan_provider import is_subscription_endpoint
        from shared.config import duration_band, voice_allocation_limits
        from step01_script_gen.timecode import active_shot_cap
        base = str(getattr(TextGenCfg, "AGENT_PLAN_TEXT_BASE_URL", "") or "")
        return {
            "provider": TextGenCfg.PROVIDER,
            "model": TextGenCfg.default_text_model(),
            "agent_plan_base_url": base,
            "is_subscription_endpoint": bool(base) and is_subscription_endpoint(base),
            "duration_band": duration_band(),
            "voice_limits": voice_allocation_limits(),
            "shot_cap_sec": float(active_shot_cap()),
        }
    except Exception as exc:                                     # noqa: BLE001
        return {"error": "%s: %s" % (type(exc).__name__, exc)}


def _parse_items(values) -> dict:
    out = {}
    for raw in values or []:
        if "=" not in raw:
            raise SystemExit("--input 需要 label=path：%r" % raw)
        label, path = raw.split("=", 1)
        p = Path(path)
        if not p.is_absolute():
            p = (ROOT / p).resolve()
        out[label.strip()] = p
    return out


def _dir_files(root: Path, excludes: list) -> list:
    out = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
        if any(pattern and pattern in rel for pattern in excludes):
            continue
        out.append(path)
    return out


def build_freeze(*, inputs: dict, protect: list, out: Path,
                 protect_dirs: list | None = None,
                 excludes: list | None = None) -> dict:
    payload = {
        "kind": "s26_frozen_inputs",
        "root": str(ROOT),
        "git": {"head": _git("rev-parse", "HEAD"),
                "branch": _git("branch", "--show-current"),
                "status": _git("status", "--porcelain=v1").splitlines()},
        "config": _config_identity(),
        "inputs": {},
        "protection": {},
    }
    for label, path in inputs.items():
        if not path.is_file():
            raise SystemExit("输入不存在：%s（%s）" % (path, label))
        payload["inputs"][label] = {
            "path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
            "sha256": sha256_file(path), "bytes": path.stat().st_size}
    protected = [Path(x) if Path(x).is_absolute() else (ROOT / x).resolve()
                 for x in protect]
    for raw in protect_dirs or []:
        root = Path(raw) if Path(raw).is_absolute() else (ROOT / raw).resolve()
        if not root.is_dir():
            raise SystemExit("保护目录不存在：%s" % root)
        protected.extend(_dir_files(root, list(excludes or [])))
    for path in protected:
        rel = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
        payload["protection"][rel] = ({
            "exists": True, "sha256": sha256_file(path),
            "bytes": path.stat().st_size} if path.is_file()
            else {"exists": False, "sha256": None, "bytes": None})
    out.parent.mkdir(parents=True, exist_ok=True)
    from shared.json_io import atomic_dump_json
    atomic_dump_json(out, payload, ensure_ascii=False, indent=1)
    return payload


def check_freeze(path: Path) -> dict:
    frozen = _read_json(path)
    result = {"inputs": [], "protection": [], "missing": [], "changed": []}
    for section in ("inputs", "protection"):
        for rel, expected in (frozen.get(section) or {}).items():
            # inputs 用 label 作键、真实路径在 `path`；protection 直接用相对路径作键。
            target = Path((expected or {}).get("path") or rel)
            if not target.is_absolute():
                target = (ROOT / target).resolve()
            if not target.is_file():
                result["missing"].append({"section": section, "path": rel})
                continue
            actual = sha256_file(target)
            ok = actual == (expected or {}).get("sha256")
            row = {"section": section, "path": rel, "expected": (expected or {}).get("sha256"),
                   "actual": actual, "equal": ok}
            result[section].append(row)
            if not ok:
                result["changed"].append(row)
    result["ok"] = not result["missing"] and not result["changed"]
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--input", action="append", default=[])
    ap.add_argument("--protect", action="append", default=[])
    ap.add_argument("--protect-dir", action="append", default=[])
    ap.add_argument("--exclude", action="append", default=[])
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    out = args.out if args.out.is_absolute() else (ROOT / args.out)
    if args.check:
        report = check_freeze(out)
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0 if report["ok"] else 1
    payload = build_freeze(inputs=_parse_items(args.input), protect=args.protect,
                           protect_dirs=args.protect_dir, excludes=args.exclude,
                           out=out)
    print(json.dumps({"out": str(out), "inputs": sorted(payload["inputs"]),
                      "protected": len(payload["protection"]),
                      "config": payload["config"]}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
