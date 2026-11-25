"""在**冻结稿**上回放修复事务（诊断/验证工具，不是生产闭环的一部分）.

两种用法：

1. **单轮诊断回放（历史行为，保留）**：`--script/--issues/--out/--note`，只调一次
   `_repair_shot_warnings(max_rounds=1)`，不审计、不提交、不写正式产物。
2. **固定输入验证（P03/P04）**：`--input-manifest` 显式给出原文、小说身份、部/章范围、
   计划、稿件、问题集、预算与通道；隔离根在**导入项目配置之前**确定，随后走
   「修复 → 完整验收/审计 → 隔离提交」的真实事务（复用 `_repair_shot_warnings` 与
   `_commit_step01_outputs`）。所有实际发送共用 `--max-sends` 上限，在发送边界检查。

## 红线

1. **不写生产正式产物**：`--output-base` 不得落在 `./output` 生产根内；隔离根里的
   `script_result.json` / `step01_commit.json` 是**隔离产物**，不等于正式采纳。
2. **诊断输入必须自带标记**：人工修补过的 `--issues` 必须用 `--note` 写明。
3. **不加修复轮**：单轮模式固定 `max_rounds=1`；清单模式的轮数由 manifest 预算显式给出。
4. **发送计数在边界执行**：已发出但超时的请求照样计数；额度不足时保存候选并报告
   `send_budget_exhausted`，不静默继续。
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _novel_file() -> Path:
    """原文路径来自配置（NOVEL_FILE），相对路径按仓库根解析."""
    from shared.config import resolve_novel_file
    p = Path(resolve_novel_file())
    return p if p.is_absolute() else ROOT / p
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_OFFICIAL_NAMES = ("script_result.json", "step01_audit.json", "step01_quality.json",
                   "step01_commit.json", "step01_repair_rounds.json",
                   "step01_audit_rounds.json")

#: 生产输出根（相对仓库）：隔离根不得落在这里
_PRODUCTION_OUTPUT = ROOT / "output"


def compute_script_revision(script: dict) -> str:
    """模块级导出（测试可替换）；延迟 import，避免在隔离根确定前冻结项目配置."""
    from shared.quality_gate import compute_script_revision as _impl
    return _impl(script)


class SendBudgetExceeded(RuntimeError):
    """发送上限已用尽（在**发送边界**抛出；已发出的请求照样计数）."""


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _normalized_novel_digest(text: str) -> str:
    return _sha256_text(str(text).replace("\r\n", "\n").replace("\r", "\n"))


def _read_text(path: Path) -> str:
    return Path(path).read_bytes().decode("utf-8-sig")


def _is_within(path: Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def _chapter_dir() -> Path:
    """生产章节目录（旧单轮模式用；清单模式一律用隔离根）."""
    from shared.config import NOVEL_CHAPTER, NOVEL_NAME, BOOK_INDEX, BOOK_NAME
    return (ROOT / "output" / "video" / NOVEL_NAME
            / f"part_{BOOK_INDEX:02d}_{BOOK_NAME}"
            / f"chapter_{NOVEL_CHAPTER:03d}")


def _read_raw_chapter() -> str:
    """旧单轮模式：从项目配置的原文路径读当前章（缺少时由调用方报错）."""
    from shared.chapter_reader import find_sections
    from shared.config import NOVEL_CHAPTER
    path = _novel_file()
    if not path.exists():
        raise SystemExit(f"[ABORT] 缺少原文：{path}（单轮模式需要可读原文；"
                         f"清单模式请用 --input-manifest 显式给出）")
    sections = find_sections(str(path))
    target = next((s for s in sections if s["index"] == NOVEL_CHAPTER), None)
    if target is None:
        return ""
    for encoding in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
        try:
            text = path.read_text(encoding=encoding)
            return text[target["start"]:target["end"]]
        except UnicodeDecodeError:
            continue
    return ""


def _guard_out_path(out: Path) -> None:
    """拒绝把单轮诊断输出写进正式产物（红线 1）."""
    if out.name in _OFFICIAL_NAMES:
        raise SystemExit(
            f"[ABORT] --out 指向正式产物名 `{out.name}`——诊断回放不得覆盖正式产物；"
            f"请换一个诊断路径（如 <某稿>.repaired.json）")
    if out.parent.resolve() == _chapter_dir().resolve():
        raise SystemExit(
            f"[ABORT] --out 落在章节目录根部（{out.parent}）——那里的文件名就是正式"
            f"产物口径；请写到 snapshots/ 或临时目录")


# ---------------------------------------------------------------------------
# 清单模式：输入身份、隔离根、发送上限
# ---------------------------------------------------------------------------

_MANIFEST_REQUIRED = ("novel", "scope", "script", "plan", "issues")


def _load_manifest(path: Path) -> dict:
    try:
        manifest = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"[ABORT] 读不出 --input-manifest：{exc}")
    if not isinstance(manifest, dict):
        raise SystemExit("[ABORT] --input-manifest 必须是 JSON 对象")
    missing = [k for k in _MANIFEST_REQUIRED if not isinstance(manifest.get(k), dict)]
    if missing:
        raise SystemExit("[ABORT] manifest 缺字段：%s" % "、".join(missing))
    for key in ("novel", "script", "plan", "issues"):
        item = manifest[key]
        if not str(item.get("path") or "").strip():
            raise SystemExit("[ABORT] manifest.%s.path 为空" % key)
    scope = manifest["scope"]
    for key in ("novel_name", "book_index", "book_name", "chapter"):
        if scope.get(key) in (None, ""):
            raise SystemExit("[ABORT] manifest.scope.%s 为空" % key)
    return manifest


def _validate_manifest(manifest: dict) -> dict:
    """身份核对（只读，不导入项目配置）：返回核对后的读数."""
    out = {}
    novel_path = Path(manifest["novel"]["path"]).resolve()
    if not novel_path.is_file():
        raise SystemExit("[ABORT] 原文不存在：%s" % novel_path)
    raw = novel_path.read_bytes()
    out["novel"] = {"path": str(novel_path), "sha256": hashlib.sha256(raw).hexdigest()}
    want = str(manifest["novel"].get("sha256") or "")
    if want and out["novel"]["sha256"] != want:
        raise SystemExit("[ABORT] 原文摘要不符：%s != %s" % (out["novel"]["sha256"], want))
    text = raw.decode("utf-8-sig")
    out["novel"]["digest"] = _normalized_novel_digest(text)
    # 可选：显式章文本（已从原文抽出的**当前章正文**）。给了它就不再从整本小说
    # 重新抽取——冻结计划绑的是这份正文，重新抽取的空白/章节头规范化会改摘要。
    text_path = str(manifest["novel"].get("text_path") or "").strip()
    if text_path:
        tp = Path(text_path).resolve()
        if not tp.is_file():
            raise SystemExit("[ABORT] 章文本不存在：%s" % tp)
        raw_text = tp.read_bytes()
        out["novel"]["text_path"] = str(tp)
        out["novel"]["text_sha256"] = hashlib.sha256(raw_text).hexdigest()
        want_text = str(manifest["novel"].get("text_sha256") or "")
        if want_text and out["novel"]["text_sha256"] != want_text:
            raise SystemExit("[ABORT] 章文本摘要不符")
        out["novel"]["text_digest"] = _normalized_novel_digest(
            raw_text.decode("utf-8-sig"))
        want_text_digest = str(manifest["novel"].get("text_digest") or "")
        if want_text_digest and out["novel"]["text_digest"] != want_text_digest:
            raise SystemExit("[ABORT] 章文本归一摘要不符")
    want_digest = str(manifest["novel"].get("digest") or "")
    if want_digest and out["novel"]["digest"] != want_digest:
        raise SystemExit("[ABORT] 原文归一摘要不符")
    for key in ("script", "plan", "issues"):
        path = Path(manifest[key]["path"]).resolve()
        if not path.is_file():
            raise SystemExit("[ABORT] %s 不存在：%s" % (key, path))
        digest = _sha256_file(path)
        want = str(manifest[key].get("sha256") or "")
        if want and digest != want:
            raise SystemExit("[ABORT] %s 摘要不符：%s != %s" % (key, digest, want))
        out[key] = {"path": str(path), "sha256": digest}
    budget = manifest.get("budget") or {}
    out["budget"] = {
        "max_sends": int(budget.get("max_sends") or 4),
        "repair_rounds": int(budget.get("repair_rounds") or 2),
        "audit_rounds": int(budget.get("audit_rounds") or 2),
    }
    if out["budget"]["max_sends"] < 1:
        raise SystemExit("[ABORT] budget.max_sends 必须 ≥ 1")
    out["scope"] = dict(manifest["scope"])
    out["channel"] = dict(manifest.get("channel") or {})
    out["output_base"] = str(Path(manifest.get("output_base") or "").resolve()) \
        if manifest.get("output_base") else ""
    return out


def _apply_manifest_env(manifest: dict, checked: dict, output_base: Path) -> None:
    """**导入项目配置之前**确定隔离根、台账、日志与作用域（P03 接线要求）."""
    (output_base / "calls").mkdir(parents=True, exist_ok=True)
    (output_base / "logs").mkdir(parents=True, exist_ok=True)
    scope = checked["scope"]
    env = {
        "OUTPUT_BASE": str(output_base),
        "STEP01_LOG_PATH": str(output_base / "logs" / "step01.log"),
        "TEXT_CALL_LEDGER": str(output_base / "calls" / "step01_calls.jsonl"),
        "NOVEL_NAME": str(scope["novel_name"]),
        "BOOK_INDEX": str(scope["book_index"]),
        "BOOK_NAME": str(scope["book_name"]),
        "NOVEL_CHAPTER": str(scope["chapter"]),
    }
    channel = checked.get("channel") or {}
    if channel.get("provider"):
        env["TEXT_PROVIDER"] = str(channel["provider"])
    if channel.get("model"):
        env["AGENT_PLAN_TEXT_MODEL"] = str(channel["model"])
        env["DEEPSEEK_MODEL"] = str(channel["model"])
        env["SCRIPT_CANDIDATE_MODELS"] = str(channel["model"])
    if channel.get("base_url"):
        env["AGENT_PLAN_TEXT_BASE_URL"] = str(channel["base_url"])
    os.environ.update(env)


def _guard_output_base(output_base: Path) -> None:
    if _is_within(output_base, _PRODUCTION_OUTPUT):
        raise SystemExit(
            "[ABORT] --output-base 落在生产输出根 ./output 内（%s）——固定输入验证只写隔离根"
            % output_base)


def _install_send_counter(max_sends: int) -> dict:
    """在**真实发送边界**（requests.post）计数并执行上限（P03 接线要求）.

    已发出的请求照样计数；到上限后**不再发出**，抛 `SendBudgetExceeded`。
    """
    import requests

    state = {"used": 0, "max": int(max_sends), "refused": 0, "sends": []}
    real_post = requests.post

    def _post(*args, **kwargs):
        if state["used"] >= state["max"]:
            state["refused"] += 1
            raise SendBudgetExceeded(
                "发送上限已用尽（%d/%d）" % (state["used"], state["max"]))
        state["used"] += 1
        state["sends"].append({"n": state["used"], "url": str(args[0] if args else "")[:120]})
        return real_post(*args, **kwargs)

    requests.post = _post
    state["restore"] = lambda: setattr(requests, "post", real_post)
    return state


def _install_network_blocker() -> dict:
    """`--dry-run`：禁止网络（任何发送都显式失败，而不是静默跳过）."""
    import requests

    state = {"blocked": 0}
    real_post = requests.post

    def _post(*args, **kwargs):
        state["blocked"] += 1
        raise SendBudgetExceeded("dry-run：禁止网络发送")

    requests.post = _post
    state["restore"] = lambda: setattr(requests, "post", real_post)
    return state


def _run_manifest(manifest: dict, *, dry_run: bool, max_sends_override: int | None) -> dict:
    checked = _validate_manifest(manifest)
    raw_base = str(checked.get("output_base") or manifest.get("output_base") or "").strip()
    if not raw_base:
        raise SystemExit("[ABORT] manifest.output_base 为空")
    output_base = Path(raw_base).resolve()
    _guard_output_base(output_base)
    if output_base.exists() and any(output_base.iterdir()):
        raise SystemExit("[ABORT] output_base 必须是全新空目录：%s" % output_base)
    _apply_manifest_env(manifest, checked, output_base)

    # **导入项目配置之后**：所有路径由 shared.paths 计算
    import step01_script_gen.main as m
    from step01_script_gen import outline as o
    from shared.quality_gate import compute_script_revision

    chapter_dir = Path(m.CHAPTER_DIR).resolve()
    if not _is_within(chapter_dir, output_base):
        raise SystemExit("[ABORT] 隔离章节目录不在 output_base 内：%s" % chapter_dir)
    chapter_dir.mkdir(parents=True, exist_ok=True)

    script = json.loads(Path(checked["script"]["path"]).read_text(encoding="utf-8-sig"))
    plan = json.loads(Path(checked["plan"]["path"]).read_text(encoding="utf-8-sig"))
    # C03：隔离根写入**既有格式**的计划缓存——提交点的恢复清单据此冻结计划身份
    (chapter_dir / "chapter_outline.cache.json").write_text(
        json.dumps({"outline": plan}, ensure_ascii=False), encoding="utf-8")
    payload = json.loads(Path(checked["issues"]["path"]).read_text(encoding="utf-8-sig"))
    if isinstance(payload, list):
        payload = {"issues": payload}
    novel = Path(checked["novel"]["path"]).read_bytes().decode("utf-8-sig")
    from shared.chapter_reader import extract_section
    if checked["novel"].get("text_path"):
        novel_text = (Path(checked["novel"]["text_path"]).read_bytes()
                      .decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n"))
    else:
        novel_text = extract_section(str(Path(checked["novel"]["path"]).resolve()),
                                     int(checked["scope"]["chapter"]))
    if not novel_text:
        raise SystemExit("[ABORT] 原文里找不到第 %s 章" % checked["scope"]["chapter"])

    budget = checked["budget"]
    max_sends = int(max_sends_override if max_sends_override is not None
                    else budget["max_sends"])
    issues = m._audit_payload_to_issues(payload)
    accept_codes = [str(x.get("code")) for x in
                    (o.plan_acceptance_problems(dict(plan), novel_text) or [])]
    report = {
        "stage": "dry_run" if dry_run else "run",
        "inputs": checked,
        "chapter_dir": str(chapter_dir),
        "plan_acceptance_codes": accept_codes,
        "issue_targets": [
            {"code": i.code, "shot_id": i.shot_id,
             "repair_targets": list(i.repair_targets or [])} for i in issues],
        "budget": dict(budget, max_sends=max_sends),
        "sends": [],
        "phases": [],
        "note": "",
    }
    if dry_run:
        # 只做输入/接线校验：任何发送都会被拦下（拦到就记数，供报告核对）
        blocker = _install_network_blocker()
        try:
            pass
        finally:
            blocker["restore"]()
        report["dry_run"] = {"network_blocked_calls": int(blocker["blocked"])}
        (output_base / "dry_run.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        return report

    counter = _install_send_counter(max_sends)
    diag: list = []
    block: list = []
    retry_state: dict = {}
    try:
        # ---- 阶段 A：冻结问题修复轮（**共享 retry_state**，首轮拒收后下一请求带真实反馈）----
        script_now = copy.deepcopy(script)
        repair_rounds = max(0, int(budget["repair_rounds"]))
        _repairs_used = 0
        for _idx in range(repair_rounds):
            before = compute_script_revision(script_now)
            script_now = m._repair_shot_warnings(
                copy.deepcopy(script_now), novel_text,
                model=(checked.get("channel") or {}).get("model") or None,
                # 冻结问题只喂第一轮；后续轮靠真实警告与上一轮拒收反馈
                audit_issues=issues if _idx == 0 else [],
                max_rounds=1,
                repair_diag=diag,
                block_reasons=block,
                retry_state=retry_state,
            )
            after = compute_script_revision(script_now)
            _repairs_used += 1
            report["phases"].append({"phase": "repair", "round": _idx + 1,
                                     "before": before, "after": after,
                                     "changed": before != after,
                                     "feedback_carried": bool(
                                         retry_state.get("feedback_records")
                                         or retry_state.get("feedback"))})
            # 没有变化且没有可续跑的纠正授权 ⇒ 不再空转（预算留给提交点审计）
            if before == after and not retry_state.get("correction_granted"):
                break

        # ---- 阶段 B：完整审计 + 隔离提交（复用唯一提交点）----
        # 提交点循环最多 `audit_rounds` 次审计；其内部修复轮上限 = 剩余修复预算，
        # 且不超过 `audit_rounds - 1`（最后一轮审计后不再修）。
        _inner_repairs = max(0, min(repair_rounds - _repairs_used,
                                    int(budget["audit_rounds"]) - 1))
        m.STEP01_AUDIT_REPAIR_MAX_ROUNDS = _inner_repairs
        final, summary = m._commit_step01_outputs(
            script_now, novel_text, plan=plan, label="固定输入回放提交")
        report["phases"].append({"phase": "commit", "status": summary.get("status"),
                                 "summary": summary})
        report["final_script"] = str(chapter_dir / "script_result.json")
        report["commit_manifest"] = str(chapter_dir / "step01_commit.json")
    except SendBudgetExceeded as exc:
        report["phases"].append({"phase": "stopped", "reason": "send_budget_exhausted",
                                 "detail": str(exc)})
    finally:
        counter["restore"]()
        report["sends"] = list(counter["sends"])
        report["sends_used"] = counter["used"]
        report["sends_refused"] = counter["refused"]
        report["repair_diag"] = diag
        report["block_reasons"] = block
        # C03：恢复清单实际生成且可静态装载
        _snap_dir = chapter_dir / "snapshots"
        _resume_snaps = (sorted(_snap_dir.glob("step01_resume_*.json"))
                         if _snap_dir.is_dir() else [])
        _recovery = {"path": str(_resume_snaps[-1]) if _resume_snaps else "",
                     "loadable": False}
        if _resume_snaps:
            try:
                from step01_script_gen import resume as _resume_mod
                _man = json.loads(_resume_snaps[-1].read_text(encoding="utf-8"))
                _resume_mod.validate_static(_man)
                _recovery["loadable"] = True
            except Exception as _exc:  # noqa: BLE001
                _recovery["error"] = "%s: %s" % (type(_exc).__name__, _exc)
        report["recovery"] = _recovery
        (output_base / "replay_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


# ---------------------------------------------------------------------------
# 单轮诊断模式（历史行为）
# ---------------------------------------------------------------------------

def replay(script_path: Path, issues_path: Path, out_path: Path,
           note: str, model: str | None, log_path: Path | None,
           novel_path: Path | None = None) -> dict:
    import step01_script_gen.main as m

    script = json.loads(script_path.read_text(encoding="utf-8"))
    payload = json.loads(issues_path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        payload = {"issues": payload}
    issues = m._audit_payload_to_issues(payload)
    if not issues:
        raise SystemExit("[ABORT] --issues 里没有任何 blocking 条目（可授权目标为空）")

    if novel_path is not None:
        from shared.chapter_reader import extract_section
        from shared.config import NOVEL_CHAPTER
        novel_text = extract_section(str(novel_path), int(NOVEL_CHAPTER))
    else:
        novel_text = _read_raw_chapter()
    if not novel_text:
        raise SystemExit("[ABORT] 缺少原文：单轮模式需要 --novel 或项目配置里的原文")

    if log_path is None:
        log_path = out_path.with_suffix(".log")
    _orig_logger = getattr(m, "_logger", None)
    try:
        from shared.tee_logger import get_logger
        m._logger = get_logger(log_path, console_level="INFO")
    except Exception:
        pass

    diag: list = []
    block: list = []
    try:
        repaired = m._repair_shot_warnings(
            json.loads(json.dumps(script)), novel_text,
            model=model,
            audit_issues=issues,
            max_rounds=1,                    # 红线 3：不加修复轮
            repair_diag=diag,
            block_reasons=block,
        )
    finally:
        if _orig_logger is not None:
            m._logger = _orig_logger

    before_rev = compute_script_revision(script)
    after_rev = compute_script_revision(repaired)
    report = {
        "diagnostic": True,
        "note": note or "诊断回放（输入非生产审计输出）",
        "script_path": str(script_path),
        "issues_path": str(issues_path),
        "novel_path": str(novel_path) if novel_path else "",
        "out_path": str(out_path),
        "log_path": str(log_path),
        "input_revision": before_rev,
        "output_revision": after_rev,
        "changed": before_rev != after_rev,
        "max_rounds": 1,
        "block_reasons": block,
        "repair_diag": diag,
        "issue_targets": [
            {"code": i.code, "shot_id": i.shot_id,
             "repair_targets": list(i.repair_targets or [])}
            for i in issues],
    }
    from shared.json_io import atomic_dump_json
    atomic_dump_json(out_path, repaired, ensure_ascii=False, indent=2)
    atomic_dump_json(out_path.with_suffix(".replay.json"), report,
                     ensure_ascii=False, indent=2)
    return report


def main(argv=None) -> int:
    _p = argparse.ArgumentParser(description="冻结稿上的修复回放（诊断/固定输入验证）")
    _p.add_argument("--input-manifest", default="",
                    help="固定输入清单（原文/小说身份/部章范围/计划/稿件/问题集/预算/通道）")
    _p.add_argument("--script", default="", help="冻结稿路径（单轮模式）")
    _p.add_argument("--issues", default="", help="审计 payload（单轮模式；人工修补须 --note）")
    _p.add_argument("--out", default="", help="修后稿输出路径（单轮模式，不得是正式产物名）")
    _p.add_argument("--note", default="", help="诊断输入说明")
    _p.add_argument("--model", default=None, help="覆盖修复模型（默认用生产配置）")
    _p.add_argument("--log", default=None, help="本次回放的日志文件")
    _p.add_argument("--novel", default="", help="原文路径（单轮模式缺省用项目配置）")
    _p.add_argument("--output-base", default="", help="隔离根（清单模式可覆盖 manifest）")
    _p.add_argument("--max-sends", type=int, default=None,
                    help="全部实际发送共用上限（清单模式覆盖 manifest.budget）")
    _p.add_argument("--dry-run", action="store_true", help="禁止网络，只校验输入与接线")
    args = _p.parse_args(argv)

    if args.input_manifest:
        manifest = _load_manifest(Path(args.input_manifest).resolve())
        if args.output_base:
            manifest["output_base"] = args.output_base
        report = _run_manifest(manifest, dry_run=bool(args.dry_run),
                               max_sends_override=args.max_sends)
        print(json.dumps({
            "stage": report.get("stage"),
            "output_base": report.get("inputs", {}).get("output_base", ""),
            "plan_acceptance_codes": report.get("plan_acceptance_codes"),
            "sends_used": report.get("sends_used"),
            "sends_refused": report.get("sends_refused"),
            "phases": [p.get("phase") for p in report.get("phases") or []],
        }, ensure_ascii=False))
        return 0

    # ---- 单轮诊断模式（历史参数）----
    if not (args.script and args.issues and args.out):
        _p.error("单轮模式需要 --script/--issues/--out；固定输入验证请用 --input-manifest")
    out_path = Path(args.out).resolve()
    _guard_out_path(out_path)
    script_path = Path(args.script).resolve()
    issues_path = Path(args.issues).resolve()
    for _pth in (script_path, issues_path):
        if not _pth.exists():
            raise SystemExit(f"[ABORT] 输入不存在：{_pth}")
    novel_path = Path(args.novel).resolve() if args.novel else None
    if novel_path is not None and not novel_path.exists():
        raise SystemExit(f"[ABORT] 原文不存在：{novel_path}")

    report = replay(script_path, issues_path, out_path, args.note, args.model,
                    Path(args.log).resolve() if args.log else None,
                    novel_path=novel_path)
    _diag0 = (report["repair_diag"] or [{}])[0] if report["repair_diag"] else {}
    print(json.dumps({
        "out": report["out_path"],
        "report": str(out_path.with_suffix(".replay.json")),
        "log": report["log_path"],
        "changed": report["changed"],
        "input_revision": report["input_revision"][:12],
        "output_revision": report["output_revision"][:12],
        "block_reasons": report["block_reasons"],
        "targets": _diag0.get("targets"),
        "groups": _diag0.get("groups"),
        "applied": _diag0.get("accepted"),
        "resolved": _diag0.get("resolved"),
        "new_blocking_keys": _diag0.get("new_blocking_keys"),
        "rolled_back_groups": _diag0.get("rolled_back_groups"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
