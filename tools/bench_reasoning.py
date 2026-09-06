"""bench_reasoning.py — 自动对比 DeepSeek 各思考模式档位对分镜质量的影响.

背景（2026-08-13）:
  DeepSeek 新增思考模式强度档位（Responses API `reasoning.effort`）:
    none          = 关闭思考（本项目 v4.1 起一直如此）
    minimal/low   = 开启，低强度
    medium/high/xhigh = 开启，高强度（默认 high）
    max           = 开启，最强
  本脚本对同一章节、同一 prompt，逐档位 × 多次生成分镜，用项目自身校验体系打分，
  自动确定「哪种推理模式让分镜更好」，并可选把最优档位写回 .env.content。

用法:
  python tools/bench_reasoning.py                          # 默认 high,max（none/low 为已确认基线）
  python tools/bench_reasoning.py --trials 2 --workers 4
  python tools/bench_reasoning.py --models deepseek-v4-flash,deepseek-v4-pro
  python tools/bench_reasoning.py --efforts high,max --max-tokens 80000
  python tools/bench_reasoning.py --chapter 3 --max-tokens 60000
  python tools/bench_reasoning.py --apply                  # 跑完把最优档位写入 .env.content
  python tools/bench_reasoning.py --dry-run                # 不调 LLM，用桩数据验证打分管线

设计要点:
  - 公平 A/B：所有档位共用同一 prompt（一次 build_prompt），只变 reasoning.effort。
  - LLM 调用并发（--workers），解析/打分串行（parse_response 用模块级全局变量，
    非线程安全）。
  - 打分复用项目校验体系：parse_response + validate_script_gap + _compute_timecodes，
    而非另起炉灶——这样「更好」的定义与生产流水线一致。
  - 关键风险指标：max_output_tokens 包含思维链 token，思考档位越高越容易截断
    （incomplete/max_output_tokens），脚本会记录 reasoning_tokens 与截断失败。
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 价格（元/百万 tokens，2026-08-13 现行价，仅用于相对成本比较）
# 8/17 起 DeepSeek 将切峰谷定价，此处只做档位间横向对比，绝对值不敏感。
_PRICE = {
    "deepseek-v4-flash": {"input": 1.0, "output": 2.0},
    "deepseek-v4-pro": {"input": 3.0, "output": 6.0},
}
_DEFAULT_MODEL = "deepseek-v4-flash"

# 打分权重（可 --weights 覆盖）。均 ≥0，越高代表该维度越重要。
_DEFAULT_WEIGHTS = {
    "parse": 3.0,             # JSON 解析成功（production 标准，最硬的门）
    "schema": 2.0,            # 每镜必填字段齐全
    "gap": 2.0,               # 章节覆盖（validate_script_gap 自身门槛）
    "subtitle_binding": 2.0,  # subtitle == narration_segment（音画同步基础）
    "duration": 1.5,          # 总时长贴近 60s（抖音成片目标）
    "shot_count": 1.0,        # 镜头数在合理区间
    "dialogue": 1.0,          # 对白比例合理 + 台词 ≤15 字（爆点密度）
    "camera": 1.0,            # camera_movement / cam_idx 运镜多样性
    "shot_size": 1.0,         # 景别多样性
    "ff_lf": 1.0,             # 首尾帧描述完整（图生视频前导）
    "keyframes": 0.5,         # 关键帧存在
    "warns": 1.0,             # 校验 WARN 数量少
    "cost": 0.5,              # token 开销小（输出 token 主导）
}


def _build_prompt(chapter: int) -> tuple[str, str]:
    """构建本次基准统一使用的 prompt（所有档位共用同一份）.

    与生产 main.py 的差异：不做角色预提取/大纲/场景物体预提取（那些是额外 LLM
    调用，会引入混淆变量）。只加载磁盘上已有的 bank（ch1 为空 → prompt 写
    「无已知角色」）。所有档位共享同一 prompt，相对比较仍然公平。
    """
    from shared.chapter_reader import extract_section
    from shared.character_bank import CharacterBank
    from shared.scene_objects_bank import SceneObjectsBank

    novel_file = ROOT / "锦衣夜行_77119小说.txt"
    novel_text = extract_section(str(novel_file), chapter)

    bank = CharacterBank()
    bank.load()
    objects_bank = SceneObjectsBank()
    objects_bank.load()

    from step01_script_gen.prompts import build_prompt, SYSTEM_PROMPT
    prompt = build_prompt(novel_text, bank, objects_bank)
    return prompt, novel_text


def _strip_fences(raw: str) -> str:
    raw = re.sub(r"^```json\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"^```\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)
    return raw.strip()


# ============================================================================
# 打分
# ============================================================================

def _shot_required_keys() -> list[str]:
    from _text_gen.deepseek_schema import _SHOT
    return list(_SHOT["required"])


def _dialogue_body(narration: str) -> str:
    """剥说话人前缀取台词体（与 step01._speech_body 一致，去标点）."""
    body = re.split(r"[:：]", narration or "", maxsplit=1)[-1]
    return re.sub(r"[\s　，。！？、；：""''（）【】《》…—-]", "", body)


def score_one(script: dict, novel_text: str, parse_ok: bool, raw_json_ok: bool,
              warn_count: int, usage: dict | None, model: str) -> dict:
    """对一份生成结果打分（0..1，越高越好）.

    script 为 parse_response 处理后的最终脚本（与生产一致）；parse_ok 表示
    LLM 返回被成功解析；raw_json_ok 表示不经修复首次解析即成功（诊断用）。
    """
    m: dict[str, float] = {}
    shots = script.get("shots", []) if script else []
    n = len(shots)

    # --- parse ---
    m["parse"] = 1.0 if parse_ok else 0.0

    # --- schema：每镜必填字段齐全 ---
    if not parse_ok or n == 0:
        m["schema"] = 0.0
    else:
        req_keys = set(_shot_required_keys())
        conform = sum(1 for s in shots if req_keys.issubset(s.keys()))
        m["schema"] = conform / n

    # --- shot_count ---
    if not parse_ok:
        m["shot_count"] = 0.0
    elif 8 <= n <= 25:
        m["shot_count"] = 1.0
    elif 4 <= n <= 40:
        m["shot_count"] = 0.5
    else:
        m["shot_count"] = 0.0

    # --- duration：贴近 60s，超 110s 判 0 ---
    total = float(script.get("total_duration", 0) or 0) if parse_ok else 0
    if not parse_ok or total <= 0:
        m["duration"] = 0.0
    elif total > 110:
        m["duration"] = 0.0
    else:
        m["duration"] = max(0.0, 1.0 - abs(total - 60.0) / 60.0)

    # --- subtitle_binding ---
    if not parse_ok or n == 0:
        m["subtitle_binding"] = 0.0
    else:
        bound = sum(1 for s in shots if (s.get("subtitle") or "") == (s.get("narration_segment") or ""))
        m["subtitle_binding"] = bound / n

    # --- dialogue：对白比例合理 + 台词 ≤15 字 ---
    if not parse_ok or n == 0:
        m["dialogue"] = 0.0
    else:
        dial_shots = [s for s in shots if (s.get("narration_segment") or "").strip()]
        ratio = len(dial_shots) / n
        if 0.3 <= ratio <= 0.9:
            ratio_score = 1.0
        else:
            ratio_score = max(0.0, 1.0 - abs(ratio - 0.6) / 0.6)
        lens = [len(_dialogue_body(s.get("narration_segment", ""))) for s in dial_shots]
        mean_len = sum(lens) / len(lens) if lens else 0
        len_score = 1.0 if mean_len <= 15 else (0.8 if mean_len <= 20 else 0.4)
        m["dialogue"] = 0.6 * ratio_score + 0.4 * len_score

    # --- camera：运镜类型多样性（兼容 camera_movement 与旧 cam_idx） ---
    if not parse_ok or n == 0:
        m["camera"] = 0.0
    else:
        movements = {
            str(s.get("camera_movement", "")).strip()
            for s in shots
            if str(s.get("camera_movement", "")).strip()
        }
        cam_groups = {
            s.get("cam_idx")
            for s in shots
            if s.get("cam_idx") is not None
        }
        d = max(len(movements), len(cam_groups))
        m["camera"] = 1.0 if 3 <= d <= 10 else (0.5 if d >= 2 else 0.0)

    # --- shot_size：景别多样性 ---
    from step01_script_gen.validation import _SHOT_SIZE_ORDER
    if not parse_ok or n == 0:
        m["shot_size"] = 0.0
    else:
        sizes = set()
        for s in shots:
            p = s.get("prompt", "") or ""
            for sz in _SHOT_SIZE_ORDER:
                if sz in p:
                    sizes.add(sz)
                    break
        d = len(sizes)
        m["shot_size"] = 1.0 if d >= 3 else (0.6 if d == 2 else (0.3 if d == 1 else 0.0))

    # --- ff_lf：首尾帧描述完整 ---
    if not parse_ok or n == 0:
        m["ff_lf"] = 0.0
    else:
        full = sum(1 for s in shots
                   if (s.get("ff_desc") or "").strip() and (s.get("lf_desc") or "").strip())
        m["ff_lf"] = full / n

    # --- keyframes：关键帧存在 ---
    if not parse_ok or n == 0:
        m["keyframes"] = 0.0
    else:
        kf = sum(1 for s in shots if s.get("keyframes"))
        m["keyframes"] = kf / n

    # --- gap：章节覆盖（项目自身门槛） ---
    if not parse_ok:
        m["gap"] = 0.0
    else:
        try:
            from step01_script_gen.gap_analysis import validate_script_gap
            with contextlib.redirect_stdout(io.StringIO()):
                v = validate_script_gap(script, novel_text)
            m["gap"] = 1.0 if v.get("overall_pass") else 0.0
        except Exception:
            m["gap"] = 0.0

    # --- warns：解析/校验 WARN 少 ---
    m["warns"] = max(0.0, 1.0 - warn_count / max(1, 2 * n)) if parse_ok else 0.0

    # --- cost：输出 token 开销（当前价，横向比较用）---
    out_tok = (usage or {}).get("output_tokens") or 0
    price = _PRICE.get(model, _PRICE[_DEFAULT_MODEL])["output"]
    m["cost"] = max(0.0, 1.0 - out_tok / 150_000)
    # 附注：思维链占比（诊断用，不进分数）
    r_tok = (usage or {}).get("reasoning_tokens") or 0
    m["_reasoning_ratio"] = (r_tok / out_tok) if out_tok else 0.0
    m["_output_tokens"] = float(out_tok)
    return m


# ============================================================================
# 聚合 + 输出
# ============================================================================

_METRIC_LABELS = {
    "parse": "解析", "schema": "字段", "gap": "覆盖", "subtitle_binding": "音画",
    "duration": "时长", "shot_count": "镜数", "dialogue": "对白", "camera": "机位",
    "shot_size": "景别", "ff_lf": "首尾帧", "keyframes": "关键帧", "warns": "低WARN",
    "cost": "低成本",
}


def composite(metrics_list: list[dict], weights: dict) -> float:
    if not metrics_list:
        return 0.0
    total_w = sum(weights.values())
    total = 0.0
    for m in metrics_list:
        s = sum(weights[k] * m.get(k, 0.0) for k in weights)
        total += s / total_w
    return total / len(metrics_list)


def aggregate(trials: list[dict], weights: dict) -> dict:
    """trials: [{config, metrics, ...}]，返回按 config 聚合的均值."""
    groups: dict[str, list[dict]] = {}
    for t in trials:
        groups.setdefault(t["config"], []).append(t)
    rows = []
    for cfg, items in groups.items():
        ms = [i["metrics"] for i in items]
        row = {"config": cfg, "n": len(items), "composite": composite(ms, weights)}
        for k in weights:
            row[k] = sum(m.get(k, 0.0) for m in ms) / len(ms)
        # 附注：输出/思维链 token 均值、失败数
        row["out_tok"] = sum(m.get("_output_tokens", 0) for m in ms) / len(ms)
        row["reason_ratio"] = sum(m.get("_reasoning_ratio", 0) for m in ms) / len(ms)
        row["fails"] = sum(1 for i in items if not i["metrics"]["parse"])
        rows.append(row)
    rows.sort(key=lambda r: r["composite"], reverse=True)
    return rows


def print_table(rows: list[dict], weights: dict) -> None:
    keys = list(weights.keys())
    hdr = f"{'配置':<22}{'n':>3} {'综合':>6} " + " ".join(f"{_METRIC_LABELS[k]:>5}" for k in keys)
    print("=" * len(hdr))
    print(hdr)
    print("=" * len(hdr))
    for r in rows:
        cells = " ".join(f"{r.get(k, 0.0) * 100:>5.0f}" for k in keys)
        fail = f" (失败{r['fails']})" if r["fails"] else ""
        print(f"{r['config']:<22}{r['n']:>3} {r['composite'] * 100:>5.1f} " + cells + fail)
    print("=" * len(hdr))
    for r in rows:
        print(
            f"  {r['config']}: 输出 {r['out_tok']:.0f} tok / 思维链占比 {r['reason_ratio'] * 100:.0f}%"
        )


# ============================================================================
# --apply：把最优档位写回 .env.content
# ============================================================================

def apply_winner(rows: list[dict], env_path: Path) -> None:
    if not rows:
        print("[--apply] 无有效结果，跳过")
        return
    winner = rows[0]
    model, effort = winner["config"].split("/", 1)
    text = env_path.read_text(encoding="utf-8")
    if "DEEPSEEK_REASONING_EFFORT=" in text:
        text = re.sub(
            r"(?m)^DEEPSEEK_REASONING_EFFORT=.*$",
            f"DEEPSEEK_REASONING_EFFORT={effort}",
            text,
        )
    else:
        text = text.rstrip("\n") + f"\nDEEPSEEK_REASONING_EFFORT={effort}\n"
    env_path.write_text(text, encoding="utf-8")
    print(f"[--apply] 已写入 .env.content: DEEPSEEK_REASONING_EFFORT={effort}"
          f"（最优档位，综合 {winner['composite'] * 100:.1f}）")


# ============================================================================
# 主流程
# ============================================================================

def main() -> None:
    # Windows 控制台默认 GBK，显式切 UTF-8 防中文乱码（见 memory gbk-safe-print-characters）
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="对比 DeepSeek 思考模式档位对分镜质量的影响")
    ap.add_argument("--chapter", type=int, default=None, help="章节号（默认 NOVEL_CHAPTER 或 1）")
    ap.add_argument("--models", default="deepseek-v4-flash",
                    help="逗号分隔模型列表（默认 deepseek-v4-flash）")
    ap.add_argument("--efforts", default="high,max",
                    help="逗号分隔思考档位（默认 high,max——none/low 为已确认基线，"
                         "需重测时用 --efforts none,low,high,max）")
    ap.add_argument("--trials", type=int, default=2, help="每档位试验次数（默认 2）")
    ap.add_argument("--max-tokens", type=int, default=40000,
                    help="max_output_tokens（含思维链，思考越高越易截断；默认 40000 与生产一致）")
    ap.add_argument("--workers", type=int, default=3, help="LLM 调用并发数（默认 3）")
    ap.add_argument("--dry-run", action="store_true", help="不调 LLM，用桩数据验证打分管线")
    ap.add_argument("--apply", action="store_true", help="跑完把最优档位写入 .env.content")
    ap.add_argument("--results", default=str(ROOT / "tools" / "bench_results.json"),
                    help="结果 JSON 输出路径")
    args = ap.parse_args()

    from shared.config import NOVEL_CHAPTER as _NC
    chapter = args.chapter or _NC
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    efforts = [e.strip() for e in args.efforts.split(",") if e.strip()]

    print("=" * 70)
    print(f"  DeepSeek 思考模式档位基准  章节 {chapter}  "
          f"模型 {models}  档位 {efforts}  trials={args.trials}")
    print("  打分权重: " + ", ".join(f"{k}×{v}" for k, v in _DEFAULT_WEIGHTS.items()))
    print("=" * 70)

    prompt, novel_text = _build_prompt(chapter)
    print(f"  prompt 长度: {len(prompt)} 字符（所有档位共用同一份，公平 A/B）")

    from _text_gen import create_service, TextGenerationRequest
    from _text_gen.deepseek_schema import SCRIPT_SCHEMA

    def fetch(model: str, effort: str) -> dict:
        if args.dry_run:
            return _stub_result()
        from step01_script_gen.prompts import SYSTEM_PROMPT
        svc = create_service("deepseek")
        req = TextGenerationRequest(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            max_tokens=args.max_tokens,
            temperature=0.2,
            json_schema=SCRIPT_SCHEMA,
            reasoning_effort=effort,
            model=model,
        )
        result = svc.generate(req)
        return {"success": result.success, "text": result.text,
                "error": result.error, "usage": result.usage}

    # ---- LLM 调用并发收集 + 逐 trial 处理 ----
    # as_completed：谁先完成先处理先打印，不被最慢调用卡住（此前按插入序迭代，
    # 快调用结果被慢调用挡住，日志长时间无动静，误判卡死）。
    # parse_response 用模块级全局变量，非线程安全 → 只在主线程调用。
    tasks = [(m, e, t) for m in models for e in efforts for t in range(args.trials)]
    print(f"  共 {len(tasks)} 次调用（workers={args.workers}），约 {len(tasks) * 4} 分钟...")

    def process_trial(model: str, effort: str, t: int, raw_res: dict) -> dict:
        cfg = f"{model}/{effort}"
        label = f"[{cfg} #{t + 1}]"
        if not raw_res["success"]:
            print(f"{label} 生成失败: {raw_res['error']}")
            return {"config": cfg, "metrics": score_one(None, novel_text, False, False, 0,
                                                        raw_res["usage"], model)}

        raw = _strip_fences(raw_res["text"])
        # 首次不经修复能否解析（诊断：修复层是否被反复调用）
        raw_json_ok = False
        try:
            from shared.utils.json_repair import safe_parse_json_object
            safe_parse_json_object(raw)
            raw_json_ok = True
        except Exception:
            raw_json_ok = False

        from step01_script_gen.validation import parse_response
        buf = io.StringIO()
        parse_ok = False
        script = None
        try:
            with contextlib.redirect_stdout(buf):
                script = parse_response(raw, novel_text=novel_text)
            parse_ok = True
        except Exception as e:
            print(f"{label} 解析失败: {e}")
            import traceback
            traceback.print_exc()  # 完整 traceback 定位崩溃行（stderr 进日志）
            # 失败时 dump 原始响应，便于离线复现定位（如 < str/int 类型错误）
            dump_dir = ROOT / "tools" / "dump"
            dump_dir.mkdir(parents=True, exist_ok=True)
            dump_path = dump_dir / f"{model}__{effort}__t{t + 1}.json"
            try:
                dump_path.write_text(raw, encoding="utf-8")
                print(f"{label} 原始响应已 dump: {dump_path.name}")
            except Exception:
                pass
        warn_count = buf.getvalue().count("[WARN]")

        if parse_ok:
            try:
                from step01_script_gen.timecode import _compute_timecodes
                _compute_timecodes(script)
            except Exception:
                pass
            _out = (raw_res["usage"] or {}).get("output_tokens")
            print(f"{label} OK: {len(script.get('shots', []))} 镜  "
                  f"warn={warn_count}  out_tok={_out}")

        return {"config": cfg,
                "metrics": score_one(script, novel_text, parse_ok, raw_json_ok, warn_count,
                                     raw_res["usage"], model)}

    trials: list[dict] = []
    if args.dry_run:
        for tk in tasks:
            trials.append(process_trial(tk[0], tk[1], tk[2], fetch(tk[0], tk[1])))
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            fut_map = {ex.submit(fetch, tk[0], tk[1]): tk for tk in tasks}
            for fut in as_completed(fut_map):
                tk = fut_map[fut]
                trials.append(process_trial(tk[0], tk[1], tk[2], fut.result()))

    rows = aggregate(trials, _DEFAULT_WEIGHTS)
    print_table(rows, _DEFAULT_WEIGHTS)

    # ---- 落盘 + 可选应用 ----
    with open(args.results, "w", encoding="utf-8") as f:
        json.dump({
            "chapter": chapter, "models": models, "efforts": efforts,
            "trials": args.trials, "max_tokens": args.max_tokens,
            "weights": _DEFAULT_WEIGHTS, "rows": rows,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {args.results}")

    if args.apply:
        apply_winner(rows, ROOT / ".env.content")


def _stub_result() -> dict:
    """--dry-run 用桩结果：返回一份合法分镜 JSON（12 镜），验证打分管线."""
    shots = []
    for i in range(1, 13):
        shots.append({
            "index": i,
            "narration_segment": f"张十三道：快撤{i}",
            "subtitle": f"张十三道：快撤{i}",
            "scene": "杨府别庄",
            "location": "照月湾",
            "main_subject": "张十三",
            "core_keywords": ["撤退", "夜色"],
            "prompt": "中景，张十三压低身形穿过庭院，画左跑向大门。",
            "motion_desc": "压低身形穿行",
            "ff_desc": "张十三蹲伏在假山后，双眼看向画左。",
            "lf_desc": "张十三已到院墙下，手搭墙沿。",
            "context_bridge": "上承刺客落水，下启张十三翻墙",
            "audio_cue": "夜晚虫鸣，衣料摩擦声",
            "fx_effect": "无",
            "color_tone": "冷蓝夜色",
            "cam_idx": i % 3 + 1,
            "clip_transition": "硬切",
            "duration": 5.0,
            "variation_type": "small",
            "keyframes": [],
        })
    script = {
        "title": "夜探别庄", "shot_count": 12, "characters": [],
        "resolution": "704x1280", "ratio": "9:16", "fps": 24, "render_style": "古风写实",
        "shots": shots,
    }
    return {"success": True, "text": json.dumps(script, ensure_ascii=False),
            "error": None, "usage": {"input_tokens": 12000, "output_tokens": 8000,
                                      "reasoning_tokens": 0, "total_tokens": 20000}}


if __name__ == "__main__":
    main()
