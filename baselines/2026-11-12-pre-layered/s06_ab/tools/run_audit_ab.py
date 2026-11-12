"""S06 阶段一：审计契约 A/B 对照（真实调用，**硬上限 6 次发送**）.

同一批冻结样本，分别用
  · old = HEAD(7286516) 的 audit.py（无衔接字段、无时间省略判据）
  · new = 当前工作树（S02 之后）
各发一次，条件一致（模型、温度、超时、时长带、metadata/focus 均为空）。

只写 `.pytest_tmp/s06/runs/<ts>/`，不触碰生产 output/ 与章节目录。
每次发送前把计数落盘；超过 --max-sends 立即停止。
"""
from __future__ import annotations

import os
import sys

# 时长带保持生效配置（`shared.config` 只允许 60 下限；强行改会被它拒绝）。
# 样本正片 12 秒、低于 60，审计规则只对**超过上限**判 blocking，两个契约看到同一组
# 数字，因此差异仍只来自 S02 的字段与判据；低于下限若被提及，按 advisory 记。

import hashlib
import importlib.util
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/step01_script_gen/fixtures/audit_narrative_families"
SAMPLES = ("time_ellipsis_legal", "state_broken", "voice_broken")
RUNS = ROOT / ".pytest_tmp/s06/runs"

sys.path.insert(0, str(ROOT))


def _load_old():
    path = ROOT / ".pytest_tmp/s06/audit_old.py"
    spec = importlib.util.spec_from_file_location("s06_audit_old", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["s06_audit_old"] = mod
    spec.loader.exec_module(mod)
    return mod


def _sample(name: str) -> dict:
    return json.loads((FIXTURES / (name + ".json")).read_text(encoding="utf-8"))


def _prompt(mod, sample: dict) -> str:
    return mod.build_audit_prompt(sample["novel"], sample["plan"], sample["script"])


def _run_one(mod, sample: dict, *, model: str, timeout: int) -> dict:
    started = time.time()
    res = mod.run_same_model_audit_with_status(
        sample["novel"], sample["plan"], sample["script"],
        model=model, timeout_sec=timeout)
    elapsed = round(time.time() - started, 1)
    return {
        "status": res.status,
        "error": res.error,
        "attempts": int(getattr(res, "attempts", 1) or 1),
        "usage": getattr(res, "usage", None),
        "channel_provider": getattr(res, "channel_provider", ""),
        "channel_model": getattr(res, "channel_model", ""),
        "raw_head": str(getattr(res, "raw_text", "") or "")[:400],
        "issues": [i.to_dict() for i in res.issues],
        "elapsed_sec": elapsed,
    }


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--max-sends", type=int, default=6)
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("--only", default="")
    ap.add_argument("--dry", action="store_true",
                    help="只构建提示并落盘，不发送（核对契约差异用）")
    args = ap.parse_args(argv)

    from step01_script_gen import audit as new_mod
    old_mod = _load_old()
    provider, model = new_mod.resolve_audit_channel()
    if not model:
        raise SystemExit("审计通道未解析出模型，拒绝发送")
    if args.only:
        names = [n for n in args.only.split(",") if n]
    else:
        names = list(SAMPLES)

    ts = time.strftime("%Y%m%dT%H%M%S")
    out = RUNS / ts
    out.mkdir(parents=True, exist_ok=True)
    ledger = out / "sends.jsonl"
    from shared.config import duration_band
    record = {
        "run_dir": str(out), "provider": provider, "model": model,
        "old_prompt_version": old_mod.audit_prompt_version(),
        "new_prompt_version": new_mod.audit_prompt_version(),
        "samples": names, "max_sends": args.max_sends,
        "duration_band": dict(duration_band()),
        "repo_head": os.popen("git rev-parse --short HEAD").read().strip(),
    }
    (out / "meta.json").write_text(json.dumps(record, ensure_ascii=False, indent=2),
                                    encoding="utf-8")

    sends = 0
    results = []
    for name in names:
        sample = _sample(name)
        for contract, mod in (("old", old_mod), ("new", new_mod)):
            if sends >= args.max_sends:
                print("[S06] 已达发送上限 %d，停止" % args.max_sends)
                break
            prompt = _prompt(mod, sample)
            entry = {
                "sample": name, "contract": contract,
                "prompt_sha1": hashlib.sha1(prompt.encode("utf-8")).hexdigest(),
                "prompt_chars": len(prompt),
                "expected": sample["expected"],
            }
            print("[S06] send %d/%d  %s / %s  (%d chars)"
                  % (sends + 1, args.max_sends, name, contract, len(prompt)),
                  flush=True)
            if args.dry:
                (out / ("prompt_%s_%s.txt" % (name, contract))).write_text(
                    prompt, encoding="utf-8")
                entry.update({"status": "dry", "issues": [], "issues_count": 0,
                              "blocking_count": 0, "elapsed_sec": 0.0})
                results.append(entry)
                continue
            res = _run_one(mod, sample, model=model, timeout=args.timeout)
            sends += 1
            entry.update(res)
            entry["issues_count"] = len(res["issues"])
            entry["blocking_count"] = sum(
                1 for i in res["issues"]
                if str(i.get("severity")) in ("visual_risk", "blocking", "hard"))
            with ledger.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"seq": sends, "sample": name,
                                     "contract": contract,
                                     "status": res["status"],
                                     "elapsed_sec": res["elapsed_sec"]},
                                    ensure_ascii=False) + "\n")
            results.append(entry)
            print("      -> status=%s issues=%d blocking=%d %.1fs"
                  % (res["status"], entry["issues_count"],
                     entry["blocking_count"], res["elapsed_sec"]), flush=True)
        if sends >= args.max_sends:
            break

    (out / "results.json").write_text(
        json.dumps({"meta": record, "sends": sends, "results": results},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print("[S06] done: sends=%d -> %s" % (sends, out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
