"""bench_text_provider_ab.py — 对比 deepseek vs agent_plan/doubao-seed-2-1-turbo 分镜质量.

v6.59 (2026-09-04) 新增：
  2026-09-04 实跑：doubao-seed-2-1-turbo + 65536 max_completion_tokens 单章 9-12 分钟 +
  kf t_sec 越界 11/12 镜 + narration 吐出"[本镜信息已并入旁白，镜头降级为背景/过渡]"占位
  文本。本脚本做严格 A/B：相同 novel_text、相同 seed、相同 temperature，共用同套打分
  （bench_reasoning.score_one）——只看"模型能力"差异，不掺杂 prompt 工程差异。

设计要点：
  - **每个 provider 用各自的 prompt builder**（生产条件一致）：
      - deepseek: build_prompt + SYSTEM_PROMPT + json_schema strict
      - doubao:   build_doubao_prompt + DOUBAO_SYSTEM_PROMPT + json_object (no strict schema)
  - **pre-extract 共用**：角色/物体/大纲预提取只跑一次（用 deepseek 基线），存盘后 A/B 共用。
  - **打分复用 bench_reasoning.score_one**（与生产流水线一致），横向可比。
  - **输入对齐**：deepseek 与 doubao 均使用同一份全章 novel_text；章节总上限
    由上游 PROMPT_MAX_CHARS 统一控制。
  - **成本控制**：默认 trials=1（每 provider 1 次 LLM 调用），约 5-7 分钟。
    提高 trials 会指数级拉长（doubao 单次 3-5 分钟 + deepseek 1-2 分钟）。

用法：
  python tools/bench_text_provider_ab.py --providers deepseek,agent_plan --trials 1
  python tools/bench_text_provider_ab.py --providers deepseek,agent_plan --trials 1 --chapter 3
  python tools/bench_text_provider_ab.py --dry-run                   # 用桩数据验证管线
  python tools/bench_text_provider_ab.py --pre-extract-only          # 只跑 pre-extract 存盘
  python tools/bench_text_provider_ab.py --reuse-pre-extract         # 复用已有 pre-extract（默认）

  ⚠️ 该脚本调真 API，运行时计费（deepseek 走 deepseek-v4-flash，doubao 走 seed-2-1-turbo）。
     调试建议：先 --dry-run 验证管线，再 --pre-extract-only 落盘，再正式 A/B。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

# 复用 bench_reasoning 的打分（保证横向可比）
from bench_reasoning import score_one, _DEFAULT_WEIGHTS, aggregate, print_table, _strip_fences

_PRE_EXTRACT_DIR = ROOT / "tools" / "bench_pre_extract"


def _dump_raw(provider: str, raw: str, trial: int = 1) -> None:
    """保存成功原始输出，便于复核解析与评分口径。"""
    dump_dir = ROOT / "tools" / "dump"
    dump_dir.mkdir(parents=True, exist_ok=True)
    name = f"{provider}__ab__t{trial}.json"
    (dump_dir / name).write_text(raw, encoding="utf-8")
    # 兼容旧路径：保留一个最新成功样本，方便直接打开复核。
    (dump_dir / f"{provider}__ab.json").write_text(raw, encoding="utf-8")


def _apply_final_hardfixes(script: dict) -> tuple[dict, int]:
    """Run the production final hard-fix chain shared by all providers."""
    from step01_script_gen.constraints import run_hard_fix

    script, report = run_hard_fix(
        script,
        is_in_repair_loop=False,
        is_final_pass=True,
        return_report=True,
    )
    return script, len(getattr(report, "hard_unresolved", []))


# ============================================================================
# pre-extract：角色/物体/大纲一次跑完，A/B 共用
# ============================================================================
# v6.59：pre-extract 简化策略
#   bench_reasoning.py 已验证：不跑 pre-extract（用空 bank）+ 共用 prompt 仍可横向比
#   较。pre-extract 本身是 3 路并发 LLM 调用（~30-60s），会引入"哪个 provider 做 pre-extract"
#   的额外混淆变量。本 bench 沿用 bench_reasoning 的"无 pre-extract"做法：
#     - ch1 bank 为空（首章），空 bank 已能反映生产现实
#     - ch2+ 时用户可手动 bank.load() 后传入（已支持 --reuse-bank 参数预留）
#   如果未来需要 pre-extract，注意 generate_outline 内部 create_service() 不接
#   service 参数——需要 monkey-patch _text_gen.service.create_service 才可控。
# ============================================================================

def _load_existing_banks() -> tuple:
    """从磁盘加载 ch1 已有 bank（global/characters_bank + scene_objects_bank）.

    ch1 通常已积累若干角色/物体（用户跑过 step01），直接传 bank 对象更准——
    不再 dict→bank 二次构造（CharacterBank.register_character 需要 4 个参数，
    API 设计是给 LLM 提取流用的，不是给 disk load 用的；disk load 走 bank.load()）。

    Returns:
        (char_bank, obj_bank) — CharacterBank / SceneObjectsBank 实例，已 load()
    """
    from shared.character_bank import CharacterBank
    from shared.scene_objects_bank import SceneObjectsBank
    char_bank = CharacterBank()
    char_bank.load()
    obj_bank = SceneObjectsBank()
    obj_bank.load()
    return char_bank, obj_bank


# ============================================================================
# prompt builder
# ============================================================================

def _build_prompts(novel_text: str, bank, objs_bank, outline) -> dict[str, str]:
    """每个 provider 用各自的 prompt builder 与 system prompt（生产配置）.

    bank / objs_bank 直接是 CharacterBank / SceneObjectsBank 实例（已 load()）。
    """
    from step01_script_gen.prompts import (
        auto_select_prompt_builder,
        auto_select_system_prompt,
    )
    from step01_script_gen.outline import outline_to_anchor

    outline_anchor = outline_to_anchor(outline) if outline else ""

    output = {}
    for provider in ("deepseek", "agent_plan"):
        build_main, _build_hook = auto_select_prompt_builder(provider)
        output[provider] = (
            build_main(novel_text, bank, objs_bank, outline_anchor),
            auto_select_system_prompt(provider),
        )
    return output


# ============================================================================
# fetch: 各 provider 一次生成
# ============================================================================

def fetch_deepseek(prompt: str, sys_prompt: str, max_tokens: int) -> dict:
    from _text_gen import create_service, TextGenerationRequest
    from _text_gen.deepseek_schema import SCRIPT_SCHEMA
    svc = create_service("deepseek")
    req = TextGenerationRequest(
        system_prompt=sys_prompt,
        user_prompt=prompt,
        max_tokens=max_tokens,
        temperature=0.2,
        top_p=0.2,
        seed=42,
        json_schema=SCRIPT_SCHEMA,
    )
    t0 = time.time()
    res = svc.generate(req)
    return {
        "success": res.success, "text": res.text, "error": res.error,
        "usage": res.usage, "elapsed": round(time.time() - t0, 1),
    }


def fetch_doubao(prompt: str, sys_prompt: str, max_tokens: int) -> dict:
    from _text_gen import create_service, TextGenerationRequest
    svc = create_service("agent_plan")
    req = TextGenerationRequest(
        system_prompt=sys_prompt,
        user_prompt=prompt,
        max_tokens=max_tokens,
        temperature=0.2,
        top_p=0.2,
        seed=42,
        # doubao 走 json_object，provider 内默认开；不传 json_schema
    )
    t0 = time.time()
    res = svc.generate(req)
    return {
        "success": res.success, "text": res.text, "error": res.error,
        "usage": res.usage, "elapsed": round(time.time() - t0, 1),
    }


def fetch_stub() -> dict:
    """--dry-run 用桩数据（与 bench_reasoning._stub_result 同款）."""
    from bench_reasoning import _stub_result
    r = _stub_result()
    return {"success": r["success"], "text": r["text"], "error": r["error"],
            "usage": r["usage"], "elapsed": 0.0}


# ============================================================================
# 主流程
# ============================================================================

def process_trial(
    provider: str,
    raw_res: dict,
    novel_text: str,
    trial: int = 1,
    dry_run: bool = False,
) -> dict:
    """解析 + 打分（与 bench_reasoning.process_trial 同结构）."""
    cfg = provider
    label = f"[{cfg}]"
    if not raw_res["success"]:
        print(f"{label} 生成失败: {raw_res['error']}")
        return {"config": cfg, "provider": provider, "trial": trial,
                "parse_ok": False, "raw_json_ok": False,
                "metrics": score_one(None, novel_text, False, False, 0,
                                      raw_res["usage"], provider),
                "elapsed": raw_res.get("elapsed", 0.0), "text_len": 0,
                "script": None}

    raw = _strip_fences(raw_res["text"])
    if not dry_run:
        _dump_raw(provider, raw, trial)
    raw_json_ok = False
    try:
        from shared.utils.json_repair import safe_parse_json_object
        safe_parse_json_object(raw)
        raw_json_ok = True
    except Exception:
        pass

    from step01_script_gen.validation import parse_response
    buf = io.StringIO()
    parse_ok = False
    script = None
    err = None
    try:
        with contextlib.redirect_stdout(buf):
            script = parse_response(raw, novel_text=novel_text)
        parse_ok = True
    except Exception as e:
        err = str(e)
        print(f"{label} 解析失败: {e}")
        import traceback
        traceback.print_exc()
        if not dry_run:
            _dump_raw(provider, raw, trial)

    warn_count = buf.getvalue().count("[WARN]")
    hard_unresolved_count = 0
    final_validation: dict = {}
    if parse_ok:
        # 与 main.py 落盘前路径一致：双方共用 final hard_fix。
        try:
            script, hard_unresolved_count = _apply_final_hardfixes(script)
        except Exception as _bhfe:
            print(f"{label} final hard_fix 失败（保留原版）: {_bhfe}")
        try:
            from step01_script_gen.timecode import _compute_timecodes
            _compute_timecodes(script)
        except Exception as _tce:
            warn_count += 1
            print(f"{label} timecode 计算失败: {_tce}")
        try:
            from step01_script_gen.gap_analysis import validate_script_gap
            with contextlib.redirect_stdout(io.StringIO()):
                final_validation = validate_script_gap(script, novel_text)
            if not final_validation.get("overall_pass"):
                warn_count += 1
        except Exception:
            warn_count += 1
        warn_count += hard_unresolved_count
        n = len(script.get("shots", []))
        out_tok = (raw_res["usage"] or {}).get("output_tokens")
        print(f"{label} OK: {n} 镜  warn={warn_count}  out_tok={out_tok}  "
              f"elapsed={raw_res['elapsed']}s")

    return {
        "config": cfg, "provider": provider, "trial": trial,
        "parse_ok": parse_ok, "raw_json_ok": raw_json_ok,
        "metrics": score_one(script, novel_text, parse_ok, raw_json_ok, warn_count,
                              raw_res["usage"], provider),
        "elapsed": raw_res["elapsed"], "text_len": len(raw_res["text"]),
        "parse_err": err,
        "hard_unresolved_count": hard_unresolved_count,
        "final_gap_pass": bool(final_validation.get("overall_pass")),
        "script": script if parse_ok else None,
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(
        description="A/B 对比 deepseek vs agent_plan/doubao-seed-2-1-turbo 分镜质量")
    ap.add_argument("--chapter", type=int, default=None)
    ap.add_argument("--providers", default="deepseek,agent_plan",
                    help="逗号分隔 provider 列表（默认 deepseek,agent_plan）")
    ap.add_argument("--trials", type=int, default=1, help="每 provider 试验次数（默认 1）")
    ap.add_argument("--max-tokens", type=int, default=None,
                    help="覆盖 provider 默认 max_tokens（None=用 provider 自身配置）")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true",
                    help="用桩数据验证管线（不覆盖真实 raw dump）")
    ap.add_argument("--results", default=str(ROOT / "tools" / "bench_ab_results.json"))
    args = ap.parse_args()

    from shared.config import NOVEL_CHAPTER as _NC
    chapter = args.chapter or _NC
    providers = [p.strip() for p in args.providers.split(",") if p.strip()]

    print("=" * 70)
    print(f"  文本模型 A/B  章节 {chapter}  providers={providers}  trials={args.trials}")
    print(f"  打分权重: " + ", ".join(f"{k}×{v}" for k, v in _DEFAULT_WEIGHTS.items()))
    print("=" * 70)

    # 1. 提取章节
    from shared.chapter_reader import extract_section
    novel_text = extract_section(str(ROOT / "锦衣夜行_77119小说.txt"), chapter)
    print(f"  章节 {chapter}: {len(novel_text)} 字符")

    # 2. 加载已有 bank + 生成大纲（用当前 provider，单次 LLM 调用）
    if args.dry_run:
        bank, objs_bank, outline = None, None, None
    else:
        bank, objs_bank = _load_existing_banks()
        print(f"  加载 bank: 角色 {len(bank.get_all_identifiers())} 个，"
              f"场景物体 {len(objs_bank.get_all_identifiers())} 个")
        # 大纲：单次 LLM 调用（不并发——bench 只做"模型能力 A/B"，
        # 不把 pre-extract 算入对比成本；用 env 当前 provider 即可）
        print("  生成大纲锚点（单次 LLM 调用，复用 bench_reasoning 模式）...")
        from step01_script_gen.outline import generate_outline
        outline = generate_outline(novel_text)
        if outline:
            print(f"  大纲 OK: {len(outline.get('main_events', []))} 个主事件, "
                  f"climax={'有' if outline.get('climax') else '无'}")
        else:
            print("  [WARN] 大纲生成失败（不阻断 A/B）")

    # 3. 构建每个 provider 的 prompt
    prompts = _build_prompts(novel_text, bank, objs_bank, outline)
    for p, (u, s) in prompts.items():
        print(f"  {p}: user_prompt={len(u)} 字符  system_prompt={len(s)} 字符")

    # 4. 每个 provider × N trials，调用 + 解析 + 打分
    def fetch(provider: str, trial: int) -> dict:
        if args.dry_run:
            return fetch_stub()
        user_p, sys_p = prompts[provider]
        max_tokens = args.max_tokens
        if max_tokens is None:
            # provider 默认值（深拷贝，避免修改 _text_gen/config.py 类属性）
            from _text_gen.config import config as _cfg
            if provider == "deepseek":
                max_tokens = _cfg.DEEPSEEK_MAX_TOKENS or 50000
            else:
                max_tokens = _cfg.AGENT_PLAN_TEXT_MAX_TOKENS or 16384
        if provider == "deepseek":
            return fetch_deepseek(user_p, sys_p, max_tokens)
        if provider == "agent_plan":
            return fetch_doubao(user_p, sys_p, max_tokens)
        raise ValueError(f"未知 provider: {provider}")

    tasks = [(p, t) for p in providers for t in range(args.trials)]
    print(f"  共 {len(tasks)} 次 LLM 调用（{args.workers} 并发），预计 5-7 分钟...")

    trials: list[dict] = []
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        fut_map = {ex.submit(fetch, p, t): (p, t) for (p, t) in tasks}
        for fut in as_completed(fut_map):
            p, t = fut_map[fut]
            raw = fut.result()
            trials.append(process_trial(
                p, raw, novel_text, trial=t + 1, dry_run=args.dry_run
            ))

    rows = aggregate(trials, _DEFAULT_WEIGHTS)
    print_table(rows, _DEFAULT_WEIGHTS)

    # 5. 落盘
    with open(args.results, "w", encoding="utf-8") as f:
        json.dump({
            "chapter": chapter, "providers": providers, "trials": args.trials,
            "novel_len": len(novel_text),
            "prompts": {p: {"user_len": len(u), "sys_len": len(s)}
                          for p, (u, s) in prompts.items()},
            "weights": _DEFAULT_WEIGHTS, "rows": rows, "trials_raw": trials,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n结果: {args.results}")


if __name__ == "__main__":
    main()
