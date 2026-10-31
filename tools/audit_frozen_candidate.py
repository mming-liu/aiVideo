"""审计包 → 既有提交入口的安全接线（冻结候选专用，2026-10-28）.

它**不**重新规划、不重新生成、不选稿：
  1. 读 `audit_package.json`：三份 SHA-256 必须合法非空并逐字节相符；单次发送开关必须是
     固定值（四个 `"0"`、`STEP01_AUDIT_ONLY="1"`）；范围（小说/部/章）必须显式声明；
  2. **在创建目录与导入产品模块之前**完成上述校验，并拒绝复用已有运行根、拒绝正式目录；
  3. 设好隔离输出根 / 日志 / 台账 / 审计通道 / 运行范围，再导入产品模块；
  4. 用真实投影复算 revision，与包内声明逐字比对——不符即**零发送**拒收；
  5. 比对包内声明的规则身份（审计提示版本 + 规则源码指纹）——不符即**零发送**拒收；
  6. 额度：剩余为 0 时即使 `-Execute` 也拒收；剩余 >0 必须绑定明确的新授权登记；
  7. 真实发送前创建章节目录 / 响应归档目录并验证可写，显式 `set_ledger_path()`；
     准备失败 ⇒ 零发送退出；
  8. 直接调用真实提交入口 `_commit_step01_outputs(..., plan=chapter_outline)` 消费冻结稿；
     发送后回读调用台账、响应正文摘要与审计轮次记录；留档异常只报“证据不完整”，不重发。

用法（PowerShell 见审计包 `launch.powershell`）:
  python tools/audit_frozen_candidate.py --package <package.json> [--dry-run] [--out-root DIR]
退出码：0 已提交 / 2 拒收（零发送）/ 3 阻塞（未提交）.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

EXIT_COMMITTED = 0
EXIT_REFUSED = 2
EXIT_BLOCKED = 3

#: Windows 控制台默认 GBK：拒收/失败文案里有 `⇒`、`｜` 等字符，直接 print 会抛
#: UnicodeEncodeError，**把退出码变成 1**（2026-10-29 SSE 实跑实测：本该 3）。
#: 固定 UTF-8 + replace：宁可显示替换符，也不能让"报告没打出来"改变退出码。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                          # noqa: BLE001
        pass

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")

#: 授权状态（占用 / attempt / outcome / result）由 shared.audit_authorization 单点负责；
#: 本入口只做：绑定摘要、顺序、零发送拒收与证据汇总。这里不再写第二套占用逻辑。

#: 允许包内改写的配置键（与交付工具同一份口径；未知键一律拒收）.
CONFIG_KEYS = {"VOICE_ADAPTATION_ENABLED", "DURATION_MAX_SEC", "DURATION_MIN_SEC",
               "DURATION_TARGET_MIN_SEC", "DURATION_TARGET_MAX_SEC",
               "STEP01_PRESENTATION_MODE", "HOOK_ENABLED"}
#: 本包只支持一次性授权（与 shared.audit_authorization.MAX_SENDS 同一口径）.
MAX_SENDS = 1

#: 已接发送守卫、可用于受控审计的文本通道（2026-10-29 起加入 agent_plan）.
#: 只放行**接线过守卫**的 provider：未接线的通道会静默绕开授权，必须零发送拒收。
AUDIT_CHANNEL_PROVIDERS = ("deepseek", "agent_plan")

#: 订阅制（OpenCode Go）通道：`budget.unlimited` **只**在这个通道上生效。
#: 仅凭 provider 名字不足以认定套餐——base_url/模型都能被环境改写，所以还要核
#: 实际解析到的模型与端点，并把端点写进绑定摘要（P1，2026-10-29 复核）。
SUBSCRIPTION_PROVIDER = "agent_plan"
SUBSCRIPTION_HOST = "opencode.ai"
SUBSCRIPTION_PATH_HINT = "/zen/go/"


def _audit_endpoint(pkg: dict) -> str:
    """本次审计**实际会打到的**端点（来自生效配置，不是包内声明）."""
    channel = pkg.get("audit_channel") if isinstance(pkg.get("audit_channel"), dict) else {}
    provider = str(channel.get("provider") or "")
    try:
        from _text_gen import config as cfg
    except Exception:                                              # noqa: BLE001
        return ""
    try:
        from _text_gen.agent_plan_provider import chat_completions_url, normalize_endpoint
        if provider == "agent_plan":
            return chat_completions_url(str(getattr(cfg, "AGENT_PLAN_TEXT_BASE_URL", "") or ""))
        if provider == "deepseek":
            return normalize_endpoint(str(getattr(cfg, "DEEPSEEK_BASE_URL", "") or ""))
    except Exception:                                          # noqa: BLE001
        return "<invalid-endpoint>"
    return ""


def _subscription_problem(pkg: dict) -> str:
    """``=订阅制通道可用；否则返回原因（调用方退回原一次性授权规则）."""
    channel = pkg.get("audit_channel") if isinstance(pkg.get("audit_channel"), dict) else {}
    provider = str(channel.get("provider") or "")
    if provider != SUBSCRIPTION_PROVIDER:
        return "provider=%r 不是订阅制通道" % provider
    try:
        from _text_gen import config as cfg
        configured_model = str(getattr(cfg, "AGENT_PLAN_TEXT_MODEL", "") or "")
        base_url = str(getattr(cfg, "AGENT_PLAN_TEXT_BASE_URL", "") or "")
    except Exception as exc:                                       # noqa: BLE001
        return "读取套餐配置失败: %s" % exc
    model = str(channel.get("model") or "")
    if not configured_model or model != configured_model:
        return "model=%r 与套餐配置 %r 不一致" % (model, configured_model)
    # F3（二轮）：只看**最终请求 URL 的精确 host+path**，不做前缀匹配、不逐个补编码反例。
    try:
        from _text_gen.agent_plan_provider import (
            SUBSCRIPTION_CHAT_PATH, SUBSCRIPTION_HOST, chat_completions_url,
            is_subscription_endpoint)
        final = chat_completions_url(base_url)
    except Exception as exc:                                   # noqa: BLE001
        return "endpoint 无法规范化: %s" % exc
    if not is_subscription_endpoint(base_url):
        return "endpoint=%r（最终 %r）不是 %s%s 套餐端点" % (
            base_url, final, SUBSCRIPTION_HOST, SUBSCRIPTION_CHAT_PATH)
    return ""


def _unlimited_active(pkg: dict) -> bool:
    """`budget.unlimited` 是否真的生效（必须同时满足：声明开启 + 通道确为订阅套餐）."""
    budget = pkg.get("budget") if isinstance(pkg.get("budget"), dict) else {}
    return budget.get("unlimited") is True and not _subscription_problem(pkg)

#: 单次发送开关的**固定值**：不给默认值，也不接受 `1`/空白/其它写法.
SEND_CONTROLS = {"TEXT_TRANSPORT_RETRIES": "0",
                 "TEXT_TRANSPORT_CONNECT_RETRIES": "0",
                 "AUDIT_PARSE_RETRIES": "0",
                 "REPAIR_ROUNDS_ENABLED": "0",
                 "STEP01_AUDIT_ONLY": "1"}


def _refuse(code: str, detail: str, **extra) -> int:
    """零发送拒收：所有 _refuse 出口都发生在占用之前，因此固定记 not_sent。"""
    row = {"status": "REFUSED", "code": code, "detail": detail,
           "send_state": "not_sent", "would_send": 0}
    row.update(extra)
    print(json.dumps(row, ensure_ascii=False))
    return EXIT_REFUSED


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _check_file(root: Path, rel, expected, what: str):
    """摘要必须**合法非空**且与文件逐字节相等；缺一个就拒收."""
    if not isinstance(rel, str) or not rel.strip():
        return None, f"{what} 路径缺失"
    if not isinstance(expected, str) or not _SHA_RE.match(expected):
        return None, f"{what} 摘要缺失或不是合法 SHA-256: {expected!r}"
    path = Path(rel) if Path(rel).is_absolute() else (root / rel)
    if not path.is_file():
        return None, f"{what} 不存在: {path}"
    actual = _sha256(path)
    if actual != expected:
        return None, f"{what} 摘要不符: 期望 {expected[:16]}…，实际 {actual[:16]}…"
    return path, ""


def _validate_controls(controls) -> str:
    if not isinstance(controls, dict):
        return "single_send_controls 必须是对象"
    for key, want in SEND_CONTROLS.items():
        if key not in controls:
            return f"缺少单次发送开关 {key}"
        if not isinstance(controls[key], str) or controls[key].strip() != want:
            return f"{key} 必须为字符串 {want!r}，实际 {controls[key]!r}"
    extra = sorted(set(controls) - set(SEND_CONTROLS))
    if extra:
        return f"未知发送开关: {extra}（不静默写入环境）"
    return ""


def _validate_scope(scope) -> str:
    if not isinstance(scope, dict):
        return "包内缺少 scope（小说/部/章范围）"
    for key in ("novel", "book_name"):
        if not isinstance(scope.get(key), str) or not scope[key].strip():
            return f"scope.{key} 必须是非空字符串"
    for key in ("book_index", "chapter"):
        value = scope.get(key)
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            return f"scope.{key} 必须是整数，实际 {value!r}"
        if number < 1:
            return f"scope.{key} 必须 ≥1，实际 {number}"
        if isinstance(value, bool):
            return f"scope.{key} 不接受布尔值"
    return ""


def _validate_out_root(out_root: Path, repo_root: Path) -> str:
    """创建**之前**校验：不得落在正式产物目录、不得复用已有目录."""
    try:
        resolved = out_root.resolve()
    except OSError as exc:
        return f"输出根不可解析: {exc}"
    production = (repo_root / "output").resolve()
    if resolved == production or production in resolved.parents:
        return f"输出根落在正式产物目录: {resolved}"
    if resolved == repo_root.resolve():
        return "输出根不能是仓库根"
    if out_root.exists():
        return f"运行根已存在，拒绝复用: {out_root}"
    return ""


def _validate_budget(pkg: dict, *, require_authorization: bool) -> str:
    """历史额度与本次新授权分开记账（M01/M09）.

    `remaining=0` 时允许 dry-run，但**带 -Execute 也不得发送**；`remaining>0`
    必须绑定一条明确的新授权登记（谁批的、最多几次）。本工具不自行增删额度。
    """
    budget = pkg.get("budget")
    if not isinstance(budget, dict):
        return "包内缺少 budget（额度记录）"
    remaining = budget.get("remaining")
    if isinstance(remaining, bool) or not isinstance(remaining, int) or remaining < 0:
        return f"budget.remaining 必须是 >=0 的整数，实际 {remaining!r}"
    if not require_authorization:
        return ""
    if _unlimited_active(pkg):
        # 2026-10-29 用户决定：step01 文本改走订阅制 OpenCode Go，**不再按次数限额**。
        # 只在**实际通道确为订阅套餐**时生效（P1：provider 名字、模型、端点三者都要核）；
        # 其他通道继续按下面的一次性授权规则走。
        return ""
    if remaining < 1:
        why = _subscription_problem(pkg) if budget.get("unlimited") is True else ""
        return (f"历史额度已用尽（authorized_total={budget.get('authorized_total')!r}, "
                f"used={budget.get('used')!r}, remaining=0）；未登记新的明确授权额度，拒绝发送"
                + (f"（budget.unlimited 未生效：{why}）" if why else ""))
    auth = budget.get("new_authorization")
    if not isinstance(auth, dict) or not str(auth.get("granted_by") or "").strip():
        return "remaining>0 但未登记明确的新授权（budget.new_authorization.granted_by 为空）"
    if not str(auth.get("authorization_id") or "").strip():
        return ("新授权缺少稳定身份 budget.new_authorization.authorization_id"
                "（同一授权跨运行必须可识别，不能每次启动重新计一次额度）")
    if not str(auth.get("granted_at") or "").strip():
        return "新授权缺少授权时间（budget.new_authorization.granted_at）"
    max_sends = auth.get("max_sends")
    if isinstance(max_sends, bool) or not isinstance(max_sends, int) or max_sends != 1:
        # 本包只支持一次性授权：>1 的多额度分配不在范围内，0 更不是「可以发」。
        return ("本包只支持一次性授权：budget.new_authorization.max_sends 必须为 1，"
                "实际 %r" % (max_sends,))
    if not str(auth.get("binding_digest") or "").strip():
        return ("新授权缺少 binding_digest（不得只凭 authorization_id 放行；绑定摘要覆盖"
                "候选/revision/原文/规则/通道/时长口径/发送开关/上限）")
    return ""


def _validate_rule_identity(pkg: dict) -> str:
    """包内声明的审计规则身份必须与**当前源码 + 生效配置**一致.

    候选摘要相同但规则已改变时，旧审计结论不能继续使用；入口必须在发送前拒收，
    不能悄悄按新规则审旧包。
    """
    contract = pkg.get("audit_contract")
    if not isinstance(contract, dict):
        return "包内缺少 audit_contract（audit_prompt_version + rule_source_fingerprint）"
    from step01_script_gen.audit import audit_prompt_version
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    checks = (
        ("audit_prompt_version", contract.get("audit_prompt_version"),
         audit_prompt_version(), "审计提示版本"),
        ("rule_source_fingerprint", contract.get("rule_source_fingerprint"),
         RULE_SOURCE_FINGERPRINT, "规则源码指纹"),
    )
    for key, declared, actual, label in checks:
        if not isinstance(declared, str) or not declared.strip():
            return f"audit_contract.{key} 缺失或不是字符串"
        if declared != actual:
            return (f"{label}不符：包内 {declared[:16]}… != 当前 {actual[:16]}…"
                    "——规则已变，旧审计包不得继续运行")
    return ""


def _authorization_binding(pkg: dict, *, revision: str) -> dict:
    """授权绑定摘要覆盖的内容：对象 + 规则 + 通道 + 时长口径 + 发送开关 + 上限.

    为什么不能只绑 authorization_id：同一个 id 在对象或规则变了之后如果仍然可以发，
    就等于「复制审计包 / 改一版候选即可沿用旧额度」。绑定不一致 ⇒ 拒收，必须重新申请授权。
    """
    budget = pkg.get("budget") if isinstance(pkg.get("budget"), dict) else {}
    auth = budget.get("new_authorization") if isinstance(
        budget.get("new_authorization"), dict) else {}
    contract = pkg.get("audit_contract") if isinstance(pkg.get("audit_contract"), dict) else {}
    obj = pkg.get("object") if isinstance(pkg.get("object"), dict) else {}
    channel = pkg.get("audit_channel") if isinstance(pkg.get("audit_channel"), dict) else {}
    return {
        "schema": "step01.audit_authorization.binding/1",
        "candidate_sha256": obj.get("candidate_sha256"),
        "revision": revision,
        "novel_sha256": obj.get("novel_sha256"),
        "quality_sha256": obj.get("quality_sha256"),
        "audit_prompt_version": contract.get("audit_prompt_version"),
        "rule_source_fingerprint": contract.get("rule_source_fingerprint"),
        "provider": channel.get("provider"),
        "model": channel.get("model"),
        "duration_band": contract.get("duration_band"),
        # 端点进摘要（P1）：否则同一个 provider 名字换个 base_url 就能沿用旧绑定。
        "endpoint": _audit_endpoint(pkg),
        "single_send_controls": dict(pkg.get("single_send_controls") or {}),
        # 摘要固定按 1 计算：本包只接受 max_sends=1（_validate_budget 强制）。若按包内
        # 当前值算，「未授权(0)」与「授权后(1)」会得到两个摘要，人工比对必然出错。
        "max_sends": MAX_SENDS,
    }


def _make_authorization(pkg: dict, *, revision: str, out_root: Path):
    """构造一次性授权对象（**不占用**；占用发生在真正的发送边界，见 S05/S08）."""
    from shared.audit_authorization import AuditAuthorization
    from shared.run_identity import current_run_id

    budget = pkg.get("budget") if isinstance(pkg.get("budget"), dict) else {}
    auth = budget.get("new_authorization") if isinstance(
        budget.get("new_authorization"), dict) else {}
    binding = _authorization_binding(pkg, revision=revision)
    auth_id = str(auth.get("authorization_id") or "").strip()
    if not auth_id and _unlimited_active(pkg):
        # 无限额度模式：授权单元 = 本次运行（每次 run 独占一个状态目录，
        # 既不会与历史 claim 冲突，也不会把两次运行混成一次发送）。
        auth_id = "auto-" + str(current_run_id())
    return AuditAuthorization.for_state_dir(
        auth_id,
        binding,
        run_id=current_run_id(),
        out_root=str(out_root),
    )


def _preview_authorization(pkg: dict) -> dict:
    """只读预览授权登记状态（dry-run / VALIDATED 行用；**绝不占用**）."""
    budget = pkg.get("budget") if isinstance(pkg.get("budget"), dict) else {}
    auth = budget.get("new_authorization") if isinstance(
        budget.get("new_authorization"), dict) else {}
    auth_id = str(auth.get("authorization_id") or "").strip()
    if not auth_id and _unlimited_active(pkg):
        return {"registered": True, "authorization_id": "auto-<run_id>",
                "send_state": "not_sent", "unlimited": True,
                "detail": "无限额度模式：每次运行按 run_id 生成独立授权单元"}
    if not auth_id:
        return {"registered": False, "authorization_id": "",
                "send_state": "unregistered",
                "detail": "包内未登记一次性授权（dry-run 允许；-Execute 必须登记）"}
    try:
        from shared.audit_authorization import describe_state, read_state
        from shared.paths import step01_audit_authorization_dir
        row = describe_state(read_state(step01_audit_authorization_dir(auth_id)))
        row.update({"registered": True, "authorization_id": auth_id})
        return row
    except Exception as exc:                                       # noqa: BLE001
        return {"registered": True, "authorization_id": auth_id,
                "send_state": "unknown", "detail": "授权状态不可读: %s" % exc}


def _prepare_run_layout(chapter_dir: Path) -> "tuple[dict, str]":
    """真实发送前创建章节目录 / 响应归档目录，并验证可写、接通模块内台账路径.

    已核实的机制：普通主入口会 `set_ledger_path()` 并创建章节目录；冻结候选入口
    直接进入提交函数会绕过这些初始化，只设 `TEXT_CALL_LEDGER` 环境变量并不等于
    配置了模块内台账路径。这里显式补上，且在**任何发送之前**失败即停。
    """
    dirs = {
        "chapter": chapter_dir,
        "call_bodies": chapter_dir / "step01_call_bodies",
        "snapshots": chapter_dir / "snapshots",
    }
    try:
        for directory in dirs.values():
            directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {}, f"目录创建失败（发送前停止）: {exc}"
    for label, directory in dirs.items():
        probe = directory / f".audit_write_probe_{os.getpid()}"
        try:
            probe.write_text("probe", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            return {}, f"{label} 目录不可写（发送前停止）: {directory}: {exc}"
    from shared.call_ledger import set_ledger_path
    ledger = chapter_dir / "step01_calls.jsonl"
    try:
        set_ledger_path(ledger)
        # 环境变量与模块内路径必须指向同一文件，避免下游读到另一份空台账。
        os.environ["TEXT_CALL_LEDGER"] = str(ledger)
    except Exception as exc:                                       # noqa: BLE001
        return {}, f"模块内调用台账路径设置失败: {exc}"
    return {"ledger": str(ledger), "call_bodies": str(dirs["call_bodies"]),
            "snapshots": str(dirs["snapshots"])}, ""


def _collect_evidence(chapter_dir: Path, ledger: Path, revision: str, *,
                      send_state: str = "not_sent", attempts: "list | None" = None,
                      run_id: str = "") -> dict:
    """回读本次发送留下的证据：授权尝试 + 台账行 + 响应正文摘要 + 审计轮次记录.

    **「证据完整」需要正面证据**（S12）：只要发生过发送，call_id / run_id / attempt 与台账
    行的对应关系就必须逐项成立；缺失即 complete=false，而不是「两边都不为空且不相等」
    才报错。反过来，**明确未发送时没有响应正文是正常状态**，不得伪造「响应归档失败」。

    注意：台账行数是**逻辑调用**数，不是发送次数（守卫拒收也会留下失败行）——
    两者各自报告，不互相顶替。
    """
    from shared.call_ledger import read_calls, verify_body

    problems: list[str] = []
    attempt_rows = list(attempts or [])
    sent = send_state in ("returned", "failed", "unknown") or bool(attempt_rows)
    rows = read_calls(ledger) if ledger.is_file() else []

    if attempts is None:
        problems.append("发送尝试记录不可读（发送次数与结果均不明）")

    attempt_call_ids = [str(a.get("call_id") or "") for a in attempt_rows]
    ledger_call_ids = {str(r.get("call_id") or "") for r in rows}
    ledger_run_ids = {str(r.get("run_id") or "") for r in rows if r.get("run_id")}
    if sent:
        if not ledger.is_file():
            problems.append("已进入发送边界，但 step01_calls.jsonl 不存在")
        elif not rows:
            problems.append("已进入发送边界，但调用台账为空（没有可关联的逻辑调用）")
        for row in rows:
            if not str(row.get("call_id") or "").strip():
                problems.append("调用台账存在缺少 call_id 的行")
            if not str(row.get("run_id") or "").strip():
                problems.append("调用台账存在缺少 run_id 的行")
        for cid in attempt_call_ids:
            if cid and cid not in ledger_call_ids:
                problems.append("发送尝试的 call_id 未出现在调用台账: %s" % cid)
        if len(rows) < len(attempt_rows):
            problems.append("调用台账行数少于发送尝试数（有发送没有逻辑调用记录）")
        if run_id and ledger_run_ids and run_id not in ledger_run_ids:
            problems.append("调用台账 run_id 不含本次运行 %s" % run_id)
        unverified = [str(r.get("call_id") or "") for r in rows if not verify_body(r)]
        if unverified:
            problems.append("响应正文缺失或摘要不符: " + ", ".join(unverified))
    elif rows:
        problems.append("声明未发送但调用台账已有行（证据自相矛盾）")

    rounds_path = chapter_dir / "step01_audit_rounds.json"
    rounds: list = []
    if rounds_path.is_file():
        try:
            data = json.loads(rounds_path.read_text(encoding="utf-8"))
            rounds = data if isinstance(data, list) else []
        except Exception as exc:                                   # noqa: BLE001
            problems.append("step01_audit_rounds.json 不可读: %s" % exc)
    if sent:
        if not rounds_path.is_file():
            problems.append("step01_audit_rounds.json 不存在")
        elif not rounds:
            problems.append("step01_audit_rounds.json 为空（没有可关联的审计轮次）")
    revisions = sorted({str(r.get("input_revision") or "") for r in rounds
                        if isinstance(r, dict) and r.get("input_revision")})
    if sent and revisions and revision not in revisions:
        problems.append("审计轮次记录未覆盖本次 revision")
    round_run_tokens = set()
    for row in rounds:
        if isinstance(row, dict):
            round_run_tokens.update(str(row.get("run_id") or "").split())
    if sent and run_id and round_run_tokens and run_id not in round_run_tokens:
        problems.append("审计轮次 run_id 不含本次运行 %s" % run_id)

    return {
        "complete": not problems,
        "problems": problems,
        "send_state": send_state,
        "sent": sent,
        "response_expected": sent,
        "ledger": str(ledger),
        "ledger_rows": len(rows),
        "call_ids": [str(r.get("call_id") or "") for r in rows],
        "bodies_verified": len(rows) - sum(1 for r in rows if not verify_body(r)),
        "audit_rounds": len(rounds),
        "run_ids": sorted(ledger_run_ids),
        "attempts": None if attempts is None else len(attempt_rows),
        "attempt_ids": [str(a.get("attempt_id") or "") for a in attempt_rows],
        "attempt_call_ids": attempt_call_ids,
    }


def _write_budget_receipt(out_root: Path, pkg_path: Path, pkg: dict, *,
                          revision: str, summary: dict, evidence: dict,
                          state_summary: "dict | None" = None,
                          write_errors: "list | None" = None) -> "tuple[str, str]":
    """把**本次实际发生的事**写在隔离根里；发送数与授权占用分开记录（S10）.

    不再写死 sends=1 / consumed=1：
      · 授权是否占用（authorization.claimed）；
      · 实际观察到的 SDK 尝试数（来自发送边界的 attempt 记录）；
      · 发送结果是否确定（send_state：not_sent / reserved / returned / failed / unknown）；
      · 逻辑调用记录数（logic_calls，**不能**当作发送次数）；
      · 审计状态 / 提交状态 / 证据是否完整。
    尝试记录不可读时 sends=null, sends_known=false，**不把「读不到」报成 0**。
    """
    budget = pkg.get("budget") if isinstance(pkg.get("budget"), dict) else {}
    remaining = budget.get("remaining")
    state = dict(state_summary or {})
    attempts_known = evidence.get("attempts") is not None
    sends = int(evidence.get("attempts") or 0) if attempts_known else None
    send_state = str(state.get("send_state") or evidence.get("send_state") or "unknown")
    remaining_after = (max(0, remaining - sends)
                       if (isinstance(remaining, int) and remaining >= 0
                           and isinstance(sends, int)) else None)
    # 复核 2026-10-30（M06-30）：**授权链**与**传输轨迹**分开报告。
    # evidence_complete 只表示"该核的对上了"，不能暗示响应内容齐全。
    _claimed = bool(state.get("claimed"))
    _attempts = int(state.get("attempts") or 0)
    _outcomes = int(state.get("outcomes") or 0)
    _attempt_ids = [x for x in (evidence.get("attempt_ids") or []) if x]
    # R05/F5（2026-10-30）：这里的"授权链"改为**消费核验结果**，不再自己按数量推断。
    # 计数只作派生读数：等数量、错身份同样不成立（见 validate_authorization_chain）。
    _chain_row = evidence.get("authorization_chain") or {}
    if "ok" in _chain_row:
        _authorization_chain = bool(_chain_row.get("ok"))
    else:
        _authorization_chain = (
            (_claimed if _attempts else True)
            and _attempts == _outcomes == len(_attempt_ids)
            and len(set(_attempt_ids)) == len(_attempt_ids))
    # 传输"可判定"与"响应完整"是两件事：failed/unknown 也有完整轨迹，但没有可用响应。
    _transport_trace_known = attempts_known and send_state in (
        "not_sent", "returned", "failed", "unknown")
    _response_complete = (send_state == "returned"
                          and int(evidence.get("bodies_verified") or 0) >= 1)
    row = {
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "package": str(pkg_path),
        "revision": revision,
        "provider": (pkg.get("audit_channel") or {}).get("provider"),
        "model": (pkg.get("audit_channel") or {}).get("model"),
        "authorized_total": budget.get("authorized_total"),
        "used_before": budget.get("used"),
        "remaining_before": remaining,
        "sends": sends,
        "sends_known": attempts_known,
        "send_state": send_state,
        "sends_source": "attempts" if attempts_known else "unknown",
        "remaining_after": remaining_after,
        "audit_status": summary.get("audit_status"),
        "verdict": summary.get("status"),
        "commit_status": summary.get("status"),
        "evidence_complete": evidence.get("complete"),
        "evidence_dimensions": {
            "authorization_chain": _authorization_chain,
            "transport_trace_known": _transport_trace_known,
            "response_complete": _response_complete,
            "chain_problems": list(_chain_row.get("problems") or []),
            "note": ("四个维度彼此独立：授权链可关联 ≠ 传输轨迹可判定 ≠ 响应完整 ≠ 审计有结论；"
                     "evidence_complete 只表示本次该核的项都核到了"),
        },
        "evidence_problems": list(evidence.get("problems") or []),
        "call_ids": list(evidence.get("call_ids") or []),
        "logic_calls": evidence.get("ledger_rows"),
        "attempt_ids": list(evidence.get("attempt_ids") or []),
        "authorization": {**state, "write_errors": list(write_errors or [])},
        "note": ("sends = 实际进入 SDK 发送边界的尝试数（attempt 记录）；"
                 "logic_calls = 逻辑调用台账行数，**不是**发送次数。"
                 "send_state=reserved 表示授权已被占用、但确认没有发送；"
                 "send_state=unknown 表示发送边界附近中断，次数/结果不明且授权不可重用。"),
    }
    path = out_root / "budget_receipt.json"
    try:
        path.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path), ""
    except OSError as exc:
        return "", "额度收据写入失败: %s" % exc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="冻结候选审计入口（不重新生成）")
    ap.add_argument("--package", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out-root", default="")
    ap.add_argument("--print-binding-digest", action="store_true",
                    help="只打印一次性授权的绑定摘要（打包/人工登记用；不建目录、不发送）")
    args = ap.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    pkg_path = Path(args.package)
    if not pkg_path.is_file():
        return _refuse("package_missing", "审计包不存在: %s" % pkg_path)
    pkg = json.loads(pkg_path.read_text(encoding="utf-8-sig"))
    obj = pkg.get("object") or {}
    if not obj.get("candidate") or not obj.get("revision"):
        return _refuse("package_incomplete", "审计包缺少 object.candidate / object.revision")

    if args.print_binding_digest:
        # 打包/人工登记用的只读出口：按**包内声明**计算绑定摘要（真实运行时还会用复算的
        # revision 再核一次）。不创建运行根、不写授权状态、不发送。
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from shared.audit_authorization import authorization_binding_digest
        # 模板按「已获授权」的口径算：max_sends 固定为 1（本包只支持一次性授权）。
        # 运行时用包内真实的 max_sends（必须为 1）复算，二者相等才放行。
        template_pkg = json.loads(json.dumps(pkg))
        template_pkg.setdefault("budget", {}).setdefault("new_authorization", {})[
            "max_sends"] = 1
        binding = _authorization_binding(template_pkg,
                                         revision=str(obj.get("revision") or ""))
        print(json.dumps({"authorization_binding": binding,
                          "assumed_max_sends": 1,
                          "binding_digest": authorization_binding_digest(binding)},
                         ensure_ascii=False, indent=2))
        return EXIT_COMMITTED

    checks = (
        ("candidate_identity", obj.get("candidate"), obj.get("candidate_sha256"), "候选"),
        ("novel_identity", obj.get("novel"), obj.get("novel_sha256"), "原文"),
        ("quality_identity", obj.get("quality_report"), obj.get("quality_sha256"), "质量报告"),
    )
    resolved_files = {}
    for code, rel, expected, what in checks:
        path, err = _check_file(root, rel, expected, what)
        if path is None:
            return _refuse(code, err)
        resolved_files[what] = path

    config = dict(pkg.get("effective_config") or {})
    unknown = sorted(set(config) - CONFIG_KEYS)
    if unknown:
        return _refuse("config_unknown_keys", "未知配置键: %s" % unknown)
    controls = pkg.get("single_send_controls")
    err = _validate_controls(controls)
    if err:
        return _refuse("send_controls_invalid", err)
    scope = pkg.get("scope")
    err = _validate_scope(scope)
    if err:
        return _refuse("scope_invalid", err)
    channel = dict(pkg.get("audit_channel") or {})
    if not channel.get("provider") or not channel.get("model"):
        return _refuse("audit_channel_missing",
                       "审计通道未显式声明 provider+model（不得沿用全局默认）")
    # S08：只放行**接线了发送守卫**的通道（deepseek / agent_plan）。换到未接线的
    # provider 会静默绕开一次性授权，必须**零发送拒收**，不能假装「其他通道也受保护」。
    if str(channel.get("provider")) not in AUDIT_CHANNEL_PROVIDERS:
        return _refuse("audit_channel_unsupported",
                       "受控入口只接线了 %s 的发送路径；%r 未接发送守卫，"
                       "拒绝运行（不得静默绕开一次性授权）"
                       % ("/".join(AUDIT_CHANNEL_PROVIDERS), channel.get("provider")),
                       audit_channel=[channel.get("provider"), channel.get("model")],
                       would_send=0)
    err = _validate_budget(pkg, require_authorization=False)
    if err:
        return _refuse("budget_invalid", err, would_send=0)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    if args.out_root:
        out_root = Path(args.out_root)
    else:
        prefix = "dryrun" if args.dry_run else "run"
        out_root = (root / ".p1a_exp" / "step01_delivery_20261028" / "audit_run"
                    / ("%s_%s" % (prefix, stamp)))
    err = _validate_out_root(out_root, root)
    if err:
        return _refuse("out_root_invalid", err)
    if not args.dry_run:
        if str(os.environ.get("STEP01_AUDIT_AUTHORIZED", "")).strip() != "1":
            return _refuse("authorization_required",
                           "缺少 STEP01_AUDIT_AUTHORIZED=1（审计通道为提议值，需人类确认）",
                           audit_channel=[channel["provider"], channel["model"]], would_send=0)
        # 授权规则里的「订阅套餐」核验需要读生效配置（_text_gen.config），而这只在
        # 下面 env 固定 + sys.path 就位后才可导入；故该检查移到运行根创建之后、
        # 任何发送/占用之前（见下方 _enforce_authorization_budget）。

    # ---- 先钉死 env（纯进程内，不建目录）；授权规则要读生效配置，故在这之后核 ----
    chapter = int(str(scope["chapter"]).strip())
    os.environ.update({k: str(v) for k, v in config.items()})
    os.environ.update({k: str(v) for k, v in controls.items()})
    os.environ.update({"NOVEL_NAME": scope["novel"], "BOOK_INDEX": str(scope["book_index"]),
                       "BOOK_NAME": scope["book_name"], "NOVEL_CHAPTER": str(chapter)})
    os.environ["AUDIT_TEXT_PROVIDER"] = str(channel["provider"])
    os.environ["AUDIT_TEXT_MODEL"] = str(channel["model"])
    os.environ["OUTPUT_BASE"] = str(out_root / "output")
    os.environ["STEP01_LOG_PATH"] = str(out_root / "step01.log")
    os.environ["TEXT_CALL_LEDGER"] = str(out_root / "step01_calls.jsonl")

    sys.path.insert(0, str(root))
    if not args.dry_run:
        # 复核 P1：`budget.unlimited` 必须核**实际**通道（provider/模型/端点），
        # 这需要导入生效配置；env 已在上面钉死，导入顺序不变。任何发送/占用之前。
        err = _validate_budget(pkg, require_authorization=True)
        if err:
            return _refuse("budget_exhausted", err, would_send=0)

    # ---- 创建全新运行根：身份/开关/范围/额度全部核完才落盘 ----
    out_root.mkdir(parents=True, exist_ok=False)

    import step01_script_gen.main as m
    from shared import paths as _paths
    from shared.quality_gate import compute_script_revision

    chapter_dir = Path(_paths.chapter_dir(chapter)).resolve()
    if out_root.resolve() not in chapter_dir.parents:
        return _refuse("isolation_failed",
                       "章节目录不在隔离根内: %s（隔离根 %s）" % (chapter_dir, out_root))
    for label, path in (("CHAPTER_DIR", Path(getattr(m, "CHAPTER_DIR", ""))),
                        ("SCRIPT_FILE", Path(getattr(m, "SCRIPT_FILE", ""))),
                        ("OUTPUT_FILE", Path(getattr(m, "OUTPUT_FILE", ""))),
                        ("STEP01_LOG_PATH", Path(os.environ["STEP01_LOG_PATH"])),
                        ("TEXT_CALL_LEDGER", Path(os.environ["TEXT_CALL_LEDGER"]))):
        if path and out_root.resolve() not in path.resolve().parents:
            return _refuse("isolation_failed", "%s 不在隔离根内: %s" % (label, path))

    candidate = json.loads(resolved_files["候选"].read_text(encoding="utf-8-sig"))
    novel_text = resolved_files["原文"].read_text(encoding="utf-8-sig")
    plan = candidate.get("chapter_outline")
    if not isinstance(plan, dict) or not plan:
        return _refuse("plan_missing",
                       "冻结稿缺少 chapter_outline（提交入口需要它做对白完整性检查）")
    if not (plan.get("dialogue_lines") is not None):
        return _refuse("plan_invalid",
                       "chapter_outline 缺少 dialogue_lines，无法做对白完整性检查")

    projected, errors = m.project_for_commit(candidate, novel_text, label="audit-entry")
    if errors:
        return _refuse("projection_failed", "; ".join(errors)[:800])
    revision = compute_script_revision(projected)
    if revision != str(obj["revision"]):
        return _refuse("revision_mismatch",
                       "待审 revision %s… != 包内声明 %s…"
                       % (revision[:16], str(obj["revision"])[:16]),
                       would_send=0)
    err = _validate_rule_identity(pkg)
    if err:
        return _refuse("rule_identity_mismatch", err, would_send=0)

    # ---- 授权绑定：对象 + 规则 + 通道 + 时长口径 + 发送开关 + 上限 ----
    try:
        from shared.audit_authorization import authorization_binding_digest, describe_state
        binding = _authorization_binding(pkg, revision=revision)
        binding_digest = authorization_binding_digest(binding)
    except Exception as exc:                                       # noqa: BLE001
        return _refuse("authorization_binding_failed",
                       "授权绑定摘要计算失败（零发送）: %s" % exc, would_send=0)

    guard = None
    if not args.dry_run:
        budget = pkg.get("budget") if isinstance(pkg.get("budget"), dict) else {}
        auth = budget.get("new_authorization") if isinstance(
            budget.get("new_authorization"), dict) else {}
        declared = str(auth.get("binding_digest") or "").strip()
        if not declared and not _unlimited_active(pkg):
            return _refuse(
                "authorization_binding_missing",
                "新授权缺少 binding_digest（不得只凭 authorization_id 放行）", would_send=0)
        if declared and declared != binding_digest:
            return _refuse(
                "authorization_binding_mismatch",
                ("新授权绑定摘要与当前对象/规则不符：包内 %s… != 复算 %s…——候选、revision、"
                 "规则、通道或发送开关已变，旧授权不得沿用"
                 % (declared[:16], binding_digest[:16])),
                binding={"declared": declared, "computed": binding_digest}, would_send=0)
        try:
            guard = _make_authorization(pkg, revision=revision, out_root=out_root)
        except ValueError as exc:
            return _refuse("authorization_identity_invalid",
                           "authorization_id 不合法（零发送）: %s" % exc, would_send=0)
        except Exception as exc:                                   # noqa: BLE001
            return _refuse("authorization_state_unreadable",
                           "授权登记不可用（零发送）: %s" % exc, would_send=0)
        try:
            prior_state = guard.read_state()
        except Exception as exc:                                   # noqa: BLE001
            return _refuse("authorization_state_unreadable", str(exc), would_send=0)
        prior_claim = prior_state.get("claim")
        if prior_claim:
            prior_summary = describe_state(prior_state)
            prior_summary["state_dir"] = str(guard.state_dir)
            stored = str((prior_claim or {}).get("binding_digest") or "")
            if stored and stored != guard.binding_digest:
                return _refuse(
                    "authorization_identity_mismatch",
                    ("授权 %s 已绑定不同的候选/规则/通道；同一 authorization_id 不得改绑后再次发送"
                     % guard.authorization_id),
                    authorization=prior_summary, would_send=0)
            return _refuse(
                "authorization_already_claimed",
                ("授权 %s 已被占用（状态 %s，run_id=%s）；同一授权累计至多一次发送，"
                 "不得换运行根/复制审计包重试"
                 % (guard.authorization_id, prior_summary["send_state"],
                    prior_summary.get("claim_run_id"))),
                authorization=prior_summary, would_send=0)
        if prior_state.get("attempts"):
            return _refuse(
                "authorization_state_corrupt",
                "存在发送尝试记录但没有占用凭据；按不确定状态拒收，不自动释放额度",
                authorization=describe_state(prior_state), would_send=0)

    prepared: dict = {}
    if not args.dry_run:
        prepared, err = _prepare_run_layout(chapter_dir)
        if err:
            return _refuse("preparation_failed", err, would_send=0)
    preview = _preview_authorization(pkg)
    print(json.dumps({"status": "VALIDATED", "revision": revision,
                      "candidate_sha256": obj.get("candidate_sha256"),
                      "chapter": chapter, "chapter_dir": str(chapter_dir),
                      "audit_channel": [channel["provider"], channel["model"]],
                      "plan_dialogue_lines": len(plan.get("dialogue_lines") or []),
                      "hook_included": isinstance(projected.get("hook"), dict),
                      "call_ledger": prepared.get("ledger", ""),
                      "authorization": preview,
                      "binding_digest": binding_digest,
                      "would_send": 0 if args.dry_run else 1}, ensure_ascii=False))
    if args.dry_run:
        return EXIT_COMMITTED

    # ---- 守卫只在**提交函数作用域**生效：前置拒收根本走不到发送边界（S09） ----
    from _text_gen.send_guard import bind_send_guard
    try:
        with bind_send_guard(guard):
            _script, summary = m._commit_step01_outputs(candidate, novel_text, plan=plan)
    except Exception as exc:                                       # noqa: BLE001
        # 提交函数自身抛错：不清授权、不重发；已有证据照常收集。
        summary = {"status": "ERROR", "detail": "%s: %s" % (type(exc).__name__, exc)}

    state_summary = guard.summary()
    state_summary.setdefault("state_dir", str(guard.state_dir))
    # 并发失败者：占用凭据不是本进程的 ⇒ 本次没有发送资格（守卫在 SDK 前已拒收）。
    if state_summary.get("claim_run_id") and state_summary["claim_run_id"] != guard.run_id:
        return _refuse("authorization_lost",
                       ("授权 %s 已被并发运行占用（run_id=%s）；本次零发送"
                        % (guard.authorization_id, state_summary.get("claim_run_id"))),
                       authorization=state_summary, would_send=0)

    keep = {k: summary.get(k) for k in (
        "status", "detail", "revision", "blocking", "audit_status", "repair_outcome",
        "audit_repair_rounds") if k in summary}
    artifacts = {}
    for name in ("script_result.json", "step01_quality.json", "step01_audit.json",
                 "step01_commit.json"):
        p = chapter_dir / name
        artifacts[name] = {"exists": p.is_file(),
                           "sha256": _sha256(p) if p.is_file() else ""}
    ledger = Path(prepared["ledger"])

    attempts = None
    attempts_error = ""
    try:
        attempts = guard.read_state()["attempts"]
        # 发送可能发生在工作线程里：占用已存在但 attempt 尚未可见时，做**有界**等待，
        # 避免把「刚写完还没读到」误报成「没有发送」。等待不改变任何发送决定。
        if not attempts and state_summary.get("claimed"):
            for _ in range(20):
                time.sleep(0.05)
                attempts = guard.read_state()["attempts"]
                if attempts:
                    break
    except Exception as exc:                                       # noqa: BLE001
        attempts_error = str(exc)

    evidence = _collect_evidence(
        chapter_dir, ledger, revision,
        send_state=str(state_summary.get("send_state") or "unknown"),
        attempts=attempts, run_id=guard.run_id)
    if attempts_error:
        evidence["problems"].append(attempts_error)
        evidence["attempts"] = None
        evidence["complete"] = False

    # R05/F5（2026-10-30）：**身份关联**与计数分开核。旧写法只看"attempt 数 == outcome 数、
    # attempt_id 不重复"——数量完全相同、把结果挂到另一条 attempt 上，照样判完整。
    from shared.audit_authorization import validate_authorization_chain
    try:
        _chain_state = guard.read_state()
    except Exception as exc:                                       # noqa: BLE001
        _chain_state = {}
        evidence["problems"].append("授权状态不可读（无法核验身份关联）: %s" % exc)
        evidence["complete"] = False
    _chain = validate_authorization_chain(_chain_state, expected={
        "authorization_id": guard.authorization_id,
        "binding_digest": binding_digest,
        "run_id": guard.run_id,
        "provider": (pkg.get("audit_channel") or {}).get("provider"),
        "model": (pkg.get("audit_channel") or {}).get("model"),
        "call_ids": [str(c) for c in (evidence.get("call_ids") or []) if c],
        "ledger_checked": bool(evidence.get("sent")),
    })
    evidence["authorization_chain"] = {"ok": not _chain, "problems": _chain}
    for _cp in _chain:
        evidence["problems"].append(
            "授权链[%s] %s：期望 %r，实际 %r（%s）"
            % (_cp["code"], _cp["path"] or _cp["ref"] or "-",
               _cp["expected"], _cp["actual"], _cp["reason"]))
    if _chain:
        evidence["complete"] = False

    # F4（2026-10-30 复核）：授权/结果留档写失败必须进 evidence.problems 并让退出码变 3，
    # 不能只藏在收据附属字段里——否则「提交成功 + outcomes 写失败」会被报成完整交付。
    _auth_write_errors = list(getattr(guard, "write_errors", []) or [])
    if _auth_write_errors:
        evidence["problems"].extend(str(x) for x in _auth_write_errors)
        evidence["complete"] = False

    result_err = guard.write_result({
        "status": summary.get("status"),
        "audit_status": summary.get("audit_status"),
        "revision": revision,
        "send_state": evidence.get("send_state"),
        "evidence_complete": bool(evidence.get("complete")),
        "evidence_problems": list(evidence.get("problems") or []),
        "out_root": str(out_root),
        "chapter_dir": str(chapter_dir),
        "attempt_ids": list(evidence.get("attempt_ids") or []),
        "call_ids": list(evidence.get("call_ids") or []),
    })
    if result_err:
        evidence["problems"].append(result_err)
        evidence["complete"] = False
    # 结果写入后 write_errors 可能再增加，复查一次
    for _err in list(getattr(guard, "write_errors", []) or []):
        if str(_err) not in evidence["problems"]:
            evidence["problems"].append(str(_err))
            evidence["complete"] = False

    state_summary = guard.summary()
    receipt, receipt_err = _write_budget_receipt(
        out_root, pkg_path, pkg, revision=revision, summary=summary,
        evidence=evidence, state_summary=state_summary,
        write_errors=guard.write_errors)
    if receipt_err:
        evidence["problems"].append(receipt_err)
        evidence["complete"] = False

    sends = evidence.get("attempts")
    remaining = (pkg.get("budget") or {}).get("remaining")
    remaining_after = (max(0, remaining - sends)
                       if (isinstance(remaining, int) and remaining >= 0
                           and isinstance(sends, int)) else None)
    result = {"status": summary.get("status"), "revision": summary.get("revision"),
              "summary": keep, "chapter_dir": str(chapter_dir), "artifacts": artifacts,
              "call_ledger": str(ledger), "call_rows": evidence["ledger_rows"],
              "evidence": evidence, "evidence_complete": bool(evidence["complete"]),
              "budget_receipt": receipt,
              "send_state": evidence.get("send_state"),
              "sends": sends,
              "authorization": state_summary,
              "budget": {"remaining_before": remaining,
                         "consumed": sends,
                         "sends_known": sends is not None,
                         "send_state": evidence.get("send_state"),
                         "remaining_after": remaining_after}}
    print(json.dumps(result, ensure_ascii=False))
    if summary.get("status") == "COMMITTED" and summary.get("revision") == revision:
        # 提交成立但**证据不完整**时不能用 0 冒充完整交付：保留已提交结果，退出码 3，
        # 由调用方按 evidence_complete=false 记账（不重发）。
        return EXIT_COMMITTED if evidence["complete"] else EXIT_BLOCKED
    return EXIT_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())