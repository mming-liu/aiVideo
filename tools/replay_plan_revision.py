# -*- coding: utf-8 -*-
"""**Step01 计划修订轮离线回放**（S02 工作包；零模型调用、零网络）。

用途：按冻结语料清单（`.tmp/step01_plan_corpus_20261121/corpus_manifest.json`）
回放 2026-11-21 真实运行里的修订轮，验证「同一份 `plan_in` + 同一条模型回复」
经**当前真实修订入口**仍得到与归档一致的裁决与计划指纹。

## 真实入口（不是自写增量应用）

- 修订轮：`step01_script_gen.outline.revise_plan_once(...)`（内部经
  `_replan_by_view` 装配请求、`apply_delta_view` / `complete_candidate` 应用、
  `create_service` 取模型）；
- 轮次裁决：`step01_script_gen.plan_revision.RevisionController.run`——控制器按
  `revision_fingerprint(plan)` 比较本轮输入/候选，决定 `applied` / `no_change` /
  `illegal` / `call_failed`；
- 验收链：复刻 `outline.run_plan_revision` 内部 `_accept` 的写法（
  `normalize_plan_ranges` + `content_verify.attach_verification` +
  `plan_acceptance_problems`），每轮候选都真跑一遍。
- 模型面：用猴子补丁把 `outline.create_service` 换成桩工厂，桩的 `.generate(request)`
  只回**冻结回复正文**（或 `response_override` 显式替换的正文）。一次回放只允许
  **恰好 1 次** generate；任何真实 Service 构造与 socket 连接都会显式失败。

## 回放口径（与冻结证据的对比）

- `request_chars` = `len(system_prompt) + len(user_prompt)`（口径同
  `shared.call_ledger._describe().prompt_chars`）；
- `system_prompt_sha256` / `user_prompt_sha256` = 两段 prompt 各自 UTF-8 字节的 sha256；
- `prompt_sha256` = `sha256("system:" + sys + "\\nuser:" + user)`（口径同
  `shared.call_ledger._describe().prompt_sha256`）；
- `replay_sha256` = `shared.revision_request_evidence.revision_request_evidence()`
  的稳定摘要（仅把 `limits.deadline_at` / `budget.remaining_sec` 两个易变预算字段掩码）；
- 归档请求里两个易变预算值由**注入的单调时钟**复刻（`RevisionBudget(clock=...)`，
  取 `deadline_at` / `remaining_sec` 的归档读数），因此请求正文可逐字比对。
  本工具**不猜**这些值：从归档请求 payload 的 `limits` 与回放轮证据的
  `request.volatile_budget` 读取，读不到即显式失败。

## 红线

1. **不发起真实模型调用、不发起网络请求**：`outline.create_service` 被桩替换，
   `_text_gen` 的工厂也换成显式报错；回放期间 `socket` 连接被阻断。
2. **不写生产 `output/`、不写生产台账**：回放根固定为隔离目录（默认
   `.tmp/s02_replay_work/output/round_<id>/`，不得落在 `./output` 内）；
   回放期间 `shared.call_ledger.set_ledger_path()` 指向隔离台账，
   `RevisionController` 的 `on_round` 只在内存里收集（不落到生产轮次台账）。
3. **不改生产代码、不改冻结语料、不提交 git**：本文件只读语料，产出只写隔离根。
4. **缺失/哈希不符/证据漂移显式失败**：清单、冻结回复、语料文件哈希任一不符即
   `ReplayError`，在**任何模型调用之前**退出；归档请求与当前代码重建的请求
   不一致同样显式失败（不把「回放不出来」冒充成「回放一致」）。

## CLI

```
python tools/replay_plan_revision.py \
    --manifest .tmp/step01_plan_corpus_20261121/corpus_manifest.json --round 2
```

stdout 为 JSON（含 `kind` / `fingerprint_in` / `fingerprint_out` / `expected` /
`conformance`）；退出码 0=回放且与期望一致，1=回放完成但不一致，2=输入/证据失败。
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 确定性回放：可选把**代码基线**钉在某个只读快照上（默认 = 当前工作树）。
#:
#: 背景：本工具与 S03/S04 等并行工作包共享工作树；那些包会改同一个修订入口的
#: 行为（拒收回灌、内容槽位分流、路由证据字段）。冻结语料是与**某个代码版本**
#: 一起冻结的——共享脏树漂移后，逐字请求身份对不上是**预期**的显式失败。
#: `--code-source head` 在隔离根里物化 `git archive HEAD` 快照并在子进程里回放：
#: 不碰生产代码、不碰工作树，只把「语料 + 代码版本」一起固定。
_SNAPSHOT_ENV = "S02_CODE_SNAPSHOT_ROOT"
_CODE_SOURCE_ENV = "S02_CODE_SOURCE"
_SNAPSHOT_ROOT = os.environ.get(_SNAPSHOT_ENV, "").strip()
if _SNAPSHOT_ROOT and Path(_SNAPSHOT_ROOT).is_dir():
    # 快照路径必须在仓库根**之前**，项目包才会从快照加载；仓库根仍保留给本工具。
    if str(ROOT) in sys.path:
        sys.path.remove(str(ROOT))
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(Path(_SNAPSHOT_ROOT).resolve()))
else:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

#: 快照里需要一起固定的项目包（纯 Python 入口与共享契约；工具本身仍从工作树运行）。
#: `.env.content` 是**受控配置输入**（渲染提示里的时长/通道值），必须随代码一起固定，
#: 否则快照里的 `shared.config` 会按仓库根找配置而落到默认值，提示正文就会漂移。
_SNAPSHOT_PATHS = ("step01_script_gen", "shared", "_text_gen", ".env.content")

#: 冻结语料清单的 schema（外部数据集的显式版本；不认识就失败，不猜字段）。
MANIFEST_SCHEMA = "step01.plan_corpus/1"

#: 回放报告 schema（本工具输出）。
REPLAY_SCHEMA = "step01.plan_revision_replay/1"

#: 隔离回放根（相对仓库）：所有写盘只允许落在这里面。
DEFAULT_OUTPUT_BASE = ROOT / ".tmp" / "s02_replay_work" / "output"

#: 生产输出根：回放根不得落在其中（红线 2）。
PRODUCTION_OUTPUT_ROOT = ROOT / "output"

#: 无归档预算读数（夹具模式）时的单轮预算形状。
_SINGLE_ROUND_MAX_CALLS = 6
_DEFAULT_DEADLINE_SEC = 600.0


class ReplayError(RuntimeError):
    """回放输入/证据/隔离失败（显式失败；此时**没有**发生模型调用）。"""


# ---------------------------------------------------------------------------
# 纯工具函数（不 import 项目代码，保证失败路径零副作用）
# ---------------------------------------------------------------------------
def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(str(text).encode("utf-8"))


def _read_json(path: Path, what: str):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise ReplayError("读不出%s：%s（%s）" % (what, path, exc))
    except ValueError as exc:
        raise ReplayError("%s不是合法 JSON：%s（%s）" % (what, path, exc))


def _is_within(path: Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def _resolve_input_path(corpus_root: Path, name: str) -> Path:
    """清单里的相对路径解析为冻结语料包内的文件；不许逃出语料根。"""
    raw = str(name or "").strip()
    if not raw:
        raise ReplayError("冻结语料清单里的路径为空")
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = Path(corpus_root) / candidate
    candidate = candidate.resolve()
    if not _is_within(candidate, corpus_root):
        raise ReplayError("语料路径逃出冻结语料根：%s" % raw)
    return candidate


def _isolated_output_base(output_base) -> Path:
    """确定隔离回放根并做红线检查（不得落在生产 output/ 内）。"""
    root = (Path(output_base).expanduser().resolve()
            if output_base else DEFAULT_OUTPUT_BASE.resolve())
    if _is_within(root, PRODUCTION_OUTPUT_ROOT):
        raise ReplayError("回放根落在生产 output/ 内（%s）——回放只写隔离目录" % root)
    if root.exists() and not root.is_dir():
        raise ReplayError("回放根不是目录：%s" % root)
    return root


#: 回放可信度依赖的代码文件——报告里记 sha256，定位「用哪版代码回放」。
def _code_identity() -> dict:
    """回放实际加载的代码身份（从 `__file__` 取，因此在快照模式下记的是快照代码）。"""
    import importlib

    out = {}
    for name in ("step01_script_gen.outline", "step01_script_gen.plan_revision",
                 "step01_script_gen.plan_feasibility",
                 "step01_script_gen.revision_route",
                 "step01_script_gen.content_verify",
                 "shared.presentation_contract", "shared.content_verification",
                 "shared.revision_request_evidence"):
        try:
            module = importlib.import_module(name)
            path = Path(str(getattr(module, "__file__", "") or ""))
            out[name] = _sha256_bytes(path.read_bytes()) if path.is_file() else ""
        except Exception:                                               # noqa: BLE001
            out[name] = ""
    return out


def _code_source_meta() -> dict:
    """本次回放用的是哪份代码：当前工作树，还是 `git archive` 的 HEAD 快照。"""
    snapshot = os.environ.get(_SNAPSHOT_ENV, "").strip()
    mode = os.environ.get(_CODE_SOURCE_ENV, "").strip() or ("snapshot" if snapshot
                                                           else "worktree")
    return {"mode": mode, "snapshot": snapshot}


def _git_head_ref() -> str:
    """只读取 `git rev-parse HEAD`（不提交、不改索引/工作树）。"""
    return _git_resolve_ref("HEAD")


def _git_resolve_ref(name: str) -> str:
    """把任意安全 ref/commit（含 `HEAD`）解析成完整 commit hash（只读 git）."""
    import subprocess

    _ref = str(name or "").strip()
    if not _ref:
        raise ReplayError("--code-source 的 git ref 不能为空")
    if _ref.startswith("-"):
        raise ReplayError("--code-source 不接受以 '-' 开头的 ref：%r" % _ref)
    try:
        done = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--verify", _ref + "^{commit}"],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReplayError("无法解析 git ref %r（--code-source 快照需要）: %s"
                          % (_ref, exc))
    ref = str(done.stdout or "").strip()
    if done.returncode != 0 or not ref:
        raise ReplayError("无法解析 git ref %r（--code-source 快照需要）: %s"
                          % (_ref, str(done.stderr or "").strip()[:300]))
    return ref


def _materialize_head_snapshot(dest: Path, ref: str) -> Path:
    """把 HEAD 的项目包导出成只读快照（写隔离根；不触碰工作树与 git 索引）。"""
    import subprocess
    import tarfile

    dest = Path(dest).resolve()
    marker = dest / ".env.content"
    if dest.exists():
        if marker.is_file():
            return dest
        # 旧的不完整快照（本工具自己的隔离产物）：删掉重物化。
        import shutil
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "-C", str(ROOT), "archive", "--format=tar", ref, "--",
           *_SNAPSHOT_PATHS]
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReplayError("物化代码快照失败（git archive %s）: %s" % (ref, exc))
    if done.returncode != 0:
        raise ReplayError("物化代码快照失败（git archive %s）: %s"
                          % (ref, str(done.stderr or b"").decode("utf-8", "replace")[:300]))
    try:
        with tarfile.open(fileobj=io.BytesIO(done.stdout)) as tar:
            tar.extractall(dest)
    except (tarfile.TarError, OSError) as exc:
        raise ReplayError("解包代码快照失败: %s" % exc)
    return dest


# ---------------------------------------------------------------------------
# 冻结语料加载与身份校验（任何不符都在模型调用之前失败）
# ---------------------------------------------------------------------------
def _verify_manifest_files(corpus_root: Path, files) -> list:
    """逐个校验清单声明的冻结文件（存在 + sha256）；返回核对读数。"""
    if not isinstance(files, dict):
        raise ReplayError("corpus_manifest.files 必须是对象")
    out = []
    for key in sorted(files):
        item = files[key]
        if not isinstance(item, dict):
            raise ReplayError("corpus_manifest.files.%s 必须是对象" % key)
        rel = str(item.get("path") or "").strip()
        want = str(item.get("sha256") or "").strip()
        if not rel or not want:
            raise ReplayError("corpus_manifest.files.%s 缺 path 或 sha256" % key)
        path = _resolve_input_path(corpus_root, rel)
        if not path.is_file():
            raise ReplayError("冻结语料文件缺失（%s）：%s" % (key, path))
        raw = path.read_bytes()
        got = _sha256_bytes(raw)
        if got != want:
            raise ReplayError("冻结语料文件哈希不符（%s）：%s != %s（%s）"
                              % (key, got, want, path))
        out.append({"key": key, "path": str(path), "sha256": got, "bytes": len(raw)})
    return out


def _load_rounds(manifest: dict, corpus_root: Path) -> list:
    """读清单声明的 `replan_rounds`（已过哈希校验）；未声明则返回空表。"""
    files = manifest.get("files")
    if not isinstance(files, dict) or "replan_rounds" not in files:
        return []
    rel = (files["replan_rounds"] or {}).get("path")
    path = _resolve_input_path(corpus_root, rel)
    rows = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReplayError("读不出冻结轮次记录：%s（%s）" % (path, exc))
    for no, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise ReplayError("冻结轮次记录第 %d 行不是合法 JSON：%s" % (no, exc))
        if not isinstance(row, dict):
            raise ReplayError("冻结轮次记录第 %d 行不是对象" % no)
        rows.append(row)
    return rows


def _round_row(rows: list, round_id: int):
    """取目标轮的归档记录：以存在 `fingerprint_in` 的行为准（首版行没有）。"""
    for row in rows:
        try:
            same = int(row.get("round")) == int(round_id)
        except (TypeError, ValueError):
            same = False
        if same and str(row.get("fingerprint_in") or "").strip():
            return row
    return None


def _prior_rejections(rows: list, round_id: int) -> list:
    """目标轮之前累积的**拒收记录**（控制器跨轮透传，进本轮请求）。"""
    out = []
    for row in rows:
        try:
            before = int(row.get("round")) < int(round_id)
        except (TypeError, ValueError):
            continue
        rej = row.get("rejection")
        if before and isinstance(rej, dict):
            out.append(rej)
    return out


def _frozen_verification_records(row, plan_in: dict) -> dict:
    """收集可复用的**冻结核验记录**（按 `plan_digest` 索引）。

    回放不重放核验模型调用（那是另一条调用、不在 S02 目标里），而是把归档
    核验记录按真实 `VerificationController.record_for()` 接口喂回去——这仍然走
    生产同一条 `attach_verification` 路径，只是不再花一次模型调用。缺记录时
    控制器按「没有核验」处理（报告里显式标注）。
    """
    records = {}
    sources = []
    if isinstance(row, dict) and isinstance(row.get("plan_out"), dict):
        sources.append(row["plan_out"])
    sources.append(plan_in)
    for plan in sources:
        if not isinstance(plan, dict):
            continue
        rec = plan.get("plan_verification")
        if not isinstance(rec, dict):
            continue
        digest = str(rec.get("plan_digest") or "").strip()
        if digest and digest not in records:
            records[digest] = rec
    return records


def _archived_context(row, plan_in: dict) -> dict:
    """把归档轮证据整理成回放上下文（缺什么就标什么，不补造）。"""
    row = row if isinstance(row, dict) else {}
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    request = evidence.get("request") if isinstance(evidence.get("request"), dict) else None
    return {
        "problems_in": (row.get("problems_in")
                        if isinstance(row.get("problems_in"), list) else None),
        "request": request,
        "plan_out": row.get("plan_out") if isinstance(row.get("plan_out"), dict) else None,
        "problems_out": (row.get("problems_out")
                         if isinstance(row.get("problems_out"), list) else None),
        "frozen_records": _frozen_verification_records(row, plan_in),
    }


def _extract_novel_from_archived_request(user_prompt: str) -> tuple:
    """从归档请求正文还原**本轮原文输入**（并回代核对原文块）。

    归档请求是 `_REVISION_NOVEL_PROMPT` 块 + 后续固定块的 `\\n` 拼接；原文块
    模板、增量说明块都取**生产代码里的同一份常量**，不在工具里另抄一份。
    返回 `(novel_text, meta)`；结构对不上就显式失败（不猜）。
    """
    from step01_script_gen import outline

    template = str(getattr(outline, "_REVISION_NOVEL_PROMPT", "") or "")
    prefix, sep, suffix = template.partition("{novel_text}")
    if not sep:
        raise ReplayError("生产代码缺少 _REVISION_NOVEL_PROMPT 的 {novel_text} 占位——"
                          "无法从归档请求还原原文")
    if not user_prompt.startswith(prefix):
        raise ReplayError("归档请求开头与当前原文模板不一致（代码/语料漂移）")
    delta_block = outline._render_duration_template(outline._PLAN_DELTA_INSTRUCTION)
    anchor = suffix + "\n" + delta_block
    idx = user_prompt.find(anchor, len(prefix))
    if idx < 0:
        raise ReplayError("归档请求里找不到原文块与增量说明块的边界（代码/语料漂移）")
    if user_prompt[idx:idx + len(anchor)] != anchor:
        raise ReplayError("归档请求的原文块边界不唯一/不一致")
    novel = user_prompt[len(prefix):idx]
    block = template.format(novel_text=novel)
    if not user_prompt.startswith(block):
        raise ReplayError("归档请求还原出的原文块回代不一致")
    from shared.config import PROMPT_MAX_CHARS
    limit = int(PROMPT_MAX_CHARS)
    if len(novel) >= limit:
        raise ReplayError("归档请求里的原文已达到 PROMPT_MAX_CHARS=%d 截断上限，"
                          "无法确认完整原文，回放受理链不可信" % limit)
    return novel, {"source": "archived_request", "chars": len(novel),
                   "sha256": _sha256_text(novel)}


def _archived_budget_readings(user_prompt: str, archived_request: dict) -> dict:
    """从归档请求还原**预算读数**（max_calls / calls_left / deadline_at / remaining_sec）。

    两个易变值取归档轮证据里的 `request.volatile_budget`（
    `limits.deadline_at` / `budget.remaining_sec`），稳定值从请求 payload 的
    `limits` 读取；缺任一项显式失败——**不按轮号推算**预算状态。
    """
    marker = '{\n "base_plan_hash":'
    start = user_prompt.find(marker)
    if start < 0:
        raise ReplayError("归档请求里找不到可编辑视图 payload（无法读取预算读数）")
    boundary = user_prompt.find("\nresponse_contract 是输出类型规则", start)
    if boundary < 0:
        raise ReplayError("归档请求 payload 缺少响应契约尾标记")
    try:
        payload = json.loads(user_prompt[start:boundary])
    except ValueError as exc:
        raise ReplayError("归档请求 payload 不是合法 JSON：%s" % exc)
    limits = payload.get("limits") if isinstance(payload.get("limits"), dict) else {}
    budget = payload.get("budget") if isinstance(payload.get("budget"), dict) else {}
    volatile = (archived_request or {}).get("volatile_budget")
    volatile = volatile if isinstance(volatile, dict) else {}
    try:
        max_calls = int(limits["max_calls"])
        calls_left = int(limits["calls_left"])
    except (KeyError, TypeError, ValueError):
        raise ReplayError("归档请求 payload 的 limits 缺 max_calls/calls_left")
    try:
        deadline_at = float(volatile["limits.deadline_at"])
        remaining_sec = float(volatile["budget.remaining_sec"])
    except (KeyError, TypeError, ValueError):
        raise ReplayError("归档轮证据缺 volatile_budget（limits.deadline_at / "
                          "budget.remaining_sec）——无法复刻逐字请求")
    if "remaining_calls" in budget and int(budget["remaining_calls"]) != calls_left:
        raise ReplayError("归档请求 payload 的 remaining_calls 与 limits.calls_left 不一致")
    return {"max_calls": max_calls, "calls_left": calls_left,
            "deadline_at": deadline_at, "remaining_sec": remaining_sec,
            "payload": payload}


def _response_text_for_round(target: dict, corpus_root: Path,
                             response_override) -> dict:
    """取本轮要回放的正文：冻结回复（核 sha256/字节数）或显式替换回复。"""
    call = target.get("call")
    call = call if isinstance(call, dict) else {}
    if response_override is not None:
        if not isinstance(response_override, str) or not response_override.strip():
            raise ReplayError("response_override 必须是非空字符串")
        raw = response_override.encode("utf-8")
        return {"text": response_override, "source": "override",
                "sha256": _sha256_bytes(raw), "bytes": len(raw), "path": "",
                "call_id": str(call.get("call_id") or ""),
                "finish_reason": "stop"}
    rel = str(call.get("response_file") or "").strip()
    if not rel:
        raise ReplayError("目标轮没有冻结回复文件（call.response_file 为空）")
    want_sha = str(call.get("response_sha256") or "").strip()
    if not want_sha:
        raise ReplayError("目标轮没有冻结回复哈希（call.response_sha256 为空）")
    path = _resolve_input_path(corpus_root, rel)
    if not path.is_file():
        raise ReplayError("冻结回复缺失：%s" % path)
    raw = path.read_bytes()
    got_sha = _sha256_bytes(raw)
    if got_sha != want_sha:
        raise ReplayError("冻结回复哈希不符：%s != %s（%s）" % (got_sha, want_sha, path))
    want_bytes = call.get("response_bytes")
    if isinstance(want_bytes, int) and int(want_bytes) != len(raw):
        raise ReplayError("冻结回复字节数不符：%d != %d（%s）"
                          % (len(raw), int(want_bytes), path))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReplayError("冻结回复不是合法 UTF-8：%s（%s）" % (path, exc))
    if not text.strip():
        raise ReplayError("冻结回复为空：%s" % path)
    return {"text": text, "source": "frozen", "sha256": got_sha,
            "bytes": len(raw), "path": str(path),
            "call_id": str(call.get("call_id") or ""),
            "finish_reason": str(call.get("finish_reason") or "")}


def _novel_for_round(target: dict, archived: dict) -> tuple:
    """确定本轮原文：归档请求还原优先；清单显式 `novel_text` 亦可（夹具模式）。"""
    explicit = target.get("novel_text")
    request = archived.get("request") or {}
    user_prompt = str(request.get("user_prompt") or "")
    if user_prompt:
        novel, meta = _extract_novel_from_archived_request(user_prompt)
        if isinstance(explicit, str) and explicit and explicit != novel:
            raise ReplayError("清单显式 novel_text 与归档请求还原的原文不一致")
        digest = ""
        context = (target.get("plan_in") or {}).get("source_revision_context")
        if isinstance(context, dict):
            digest = str(context.get("novel_digest") or "")
        if digest and _sha256_text(novel) != digest:
            raise ReplayError("归档原文与计划 source_revision_context.novel_digest 不一致")
        return novel, meta
    if isinstance(explicit, str):
        return explicit, {"source": "manifest.novel_text", "chars": len(explicit),
                          "sha256": _sha256_text(explicit)}
    raise ReplayError("缺少原文输入：既没有归档请求证据（evidence.request.user_prompt），"
                      "也没有 target.novel_text")


# ---------------------------------------------------------------------------
# 回放执行（导入项目代码后：隔离 IO + 桩服务 + 真实控制器）
# ---------------------------------------------------------------------------
class _NetworkBlocked(ReplayError):
    """回放期间出现网络连接（红线 1）。"""


@contextlib.contextmanager
def _no_network():
    """回放期间阻断 socket 连接（桩服务之外**任何**真实调用都显式失败）。"""
    real_connect = socket.socket.connect
    real_create = socket.create_connection

    def _blocked_connect(self, *args, **kwargs):
        raise _NetworkBlocked("回放禁止网络访问（socket.connect 被阻断）")

    def _blocked_create(*args, **kwargs):
        raise _NetworkBlocked("回放禁止网络访问（socket.create_connection 被阻断）")

    socket.socket.connect = _blocked_connect
    socket.create_connection = _blocked_create
    try:
        yield
    finally:
        socket.socket.connect = real_connect
        socket.create_connection = real_create


class _FrozenReplyService:
    """桩模型服务：只回冻结回复正文，绝不触网。

    `outline._replan_by_view` 从结果对象读 `success` / `text` / `error` /
    `finish_reason` / `call_id`（见 `_text_gen.models.TextGenerationResult`），
    这里构造等价对象并把冻结 `call_id` 带上，供轮次证据链对齐归档。
    """

    def __init__(self, reply: dict):
        self._reply = reply
        self.requests = []

    def generate(self, request):
        from _text_gen.models import TextGenerationResult

        self.requests.append(request)
        return TextGenerationResult(
            success=True, text=self._reply["text"], error=None,
            finish_reason=str(self._reply.get("finish_reason") or ""),
            call_id=str(self._reply.get("call_id") or ""))

    def generate_async(self, request):
        return self.generate(request)


def _make_budget(archived_budget: dict):
    """构造**单轮**回放预算；有归档读数时注入单调时钟复刻请求里的预算值。"""
    from step01_script_gen.plan_revision import RevisionBudget, STOP_CALLS

    if archived_budget:
        deadline_at = float(archived_budget["deadline_at"])
        remaining = float(archived_budget["remaining_sec"])
        observed = deadline_at - remaining
        max_calls = int(archived_budget["max_calls"])
        calls_left = int(archived_budget["calls_left"])
        # 控制器在请求**之前** spend，故归档请求里的 calls_left 已扣过本轮。
        used_before_round = max_calls - calls_left - 1
        if used_before_round < 0 or calls_left < 1:
            raise ReplayError("归档预算读数不自洽：max_calls=%d calls_left=%d"
                              % (max_calls, calls_left))
        base = RevisionBudget(max_calls=max_calls,
                              deadline_sec=deadline_at - observed,
                              run_deadline_at=deadline_at,
                              clock=lambda: observed)
        base.calls_used = used_before_round
    else:
        base = RevisionBudget(max_calls=_SINGLE_ROUND_MAX_CALLS,
                              deadline_sec=_DEFAULT_DEADLINE_SEC)

    # 一次回放只跑归档的那一轮：控制器先 spend 再请求；spend 过一次后强制收口。
    class _OneRoundBudget(RevisionBudget):
        _rounds_done = 0

        def spend(self):
            super().spend()
            self._rounds_done = int(getattr(self, "_rounds_done", 0)) + 1

        def stop_reason(self):
            if int(getattr(self, "_rounds_done", 0)) >= 1:
                return STOP_CALLS
            return super().stop_reason()

    one = _OneRoundBudget.__new__(_OneRoundBudget)
    one.__dict__.update(base.__dict__)
    one._rounds_done = 0
    return one


def _make_verification(archived_records: dict, novel: str, budget):
    """构造冻结核验记录控制器（真实 `VerificationController` + 只读记录桩）。"""
    if not archived_records:
        return None, {"mode": "absent", "records": 0, "loads": []}
    from shared.content_verification import VerificationController
    from step01_script_gen.content_verify import (VERIFY_RULE_VERSION,
                                                  novel_identity)

    loads = []

    def _load(view, digest):
        loads.append(str(digest))
        return archived_records.get(str(digest))

    controller = VerificationController(
        verify=_load,
        deadline_at=getattr(budget, "deadline_at", None),
        clock=getattr(budget, "_clock", None),
        rule_version=VERIFY_RULE_VERSION,
        require_coherence=True,
        novel_digest=novel_identity(novel)["digest"],
        log=lambda *a: None)
    return controller, {"mode": "frozen-records", "records": len(archived_records),
                        "loads": loads}


def _drive_single_round(*, plan_in: dict, novel: str, archived: dict,
                        archived_budget: dict, reply: dict,
                        prior_rejections: list) -> dict:
    """用真实修订入口 + 真实控制器驱动**一轮**，返回结构化读数（全部内存）。"""
    from step01_script_gen import outline
    from step01_script_gen.plan_feasibility import (apply_voice_order_projection,
                                                    normalize_plan_ranges)
    from step01_script_gen.plan_revision import (RevisionController,
                                                 revision_fingerprint)

    service = _FrozenReplyService(reply)
    budget = _make_budget(archived_budget)
    verification, verification_meta = _make_verification(
        archived.get("frozen_records") or {}, novel, budget)

    # 生产 `run_plan_revision` 的同一段前置：规范化 → 顺序投影 → 挂核验记录。
    plan_candidate = normalize_plan_ranges(plan_in)[0]
    plan_candidate, voice_order = apply_voice_order_projection(plan_candidate)
    if voice_order.get("changed") and isinstance(plan_candidate, dict):
        plan_candidate = dict(plan_candidate)
        plan_candidate.pop("plan_verification", None)
        plan_candidate.pop("verification_rejected", None)
    if verification is not None and isinstance(plan_candidate, dict):
        from step01_script_gen.content_verify import attach_verification
        plan_candidate = attach_verification(plan_candidate, verification)

    rounds = []

    def _accept(plan, novel_text=""):
        # 与 `outline.run_plan_revision._accept` 同一份验收链：规范化 + 挂核验 +
        # 唯一完整验收入口 `plan_acceptance_problems`。
        checked = normalize_plan_ranges(plan)[0]
        if verification is not None and isinstance(checked, dict):
            from step01_script_gen.content_verify import attach_verification
            checked = attach_verification(checked, verification)
        return outline.plan_acceptance_problems(checked, novel_text)

    def _revise(plan, problems, active_budget, rejections):
        # 历史拒收（归档口径）在本轮请求里继续透传，再由控制器追加本轮新拒收。
        return outline.revise_plan_once(
            plan, problems, active_budget,
            list(prior_rejections) + list(rejections or []),
            novel_text=novel, verification=verification)

    controller = RevisionController(
        accept=_accept, revise=_revise, budget=budget,
        on_round=lambda rec: rounds.append(rec), log=lambda *a: None)

    primary_factory = outline.create_service
    registry_factory = None
    service_module = None
    package_module = sys.modules.get("_text_gen")

    def _stub_factory():
        return service

    def _blocked_factory(*args, **kwargs):
        raise ReplayError("回放期间禁止构造真实文本服务（create_service 被阻断）")

    outline.create_service = _stub_factory
    try:
        from _text_gen import service as _service_mod
        service_module = _service_mod
        registry_factory = _service_mod.create_service
        _service_mod.create_service = _blocked_factory
        if package_module is not None and hasattr(package_module, "create_service"):
            package_module.create_service = _blocked_factory
    except Exception:                                                   # noqa: BLE001
        pass

    log_buffer = io.StringIO()
    try:
        with _no_network(), contextlib.redirect_stdout(log_buffer):
            result = controller.run(plan_candidate, novel)
    finally:
        outline.create_service = primary_factory
        if service_module is not None and registry_factory is not None:
            service_module.create_service = registry_factory
        if package_module is not None and registry_factory is not None \
                and hasattr(package_module, "create_service"):
            package_module.create_service = registry_factory

    if not rounds:
        raise ReplayError("控制器首轮验收直接通过（没有发生修订调用）——目标轮不可回放")
    if len(rounds) != 1:
        raise ReplayError("回放应只驱动 1 轮，实际 %d 轮（预算收口失效）" % len(rounds))
    if len(service.requests) != 1:
        raise ReplayError("回放应恰好发生 1 次模型请求，实际 %d 次" % len(service.requests))

    history = result.get("history") or []
    first = history[0] if history and isinstance(history[0], dict) else {}
    plan_out = result.get("plan")
    fp_in = revision_fingerprint(normalize_plan_ranges(plan_in)[0])
    fp_out = revision_fingerprint(plan_out) if isinstance(plan_out, dict) else ""
    return {
        "round_kind": str(first.get("kind") or ""),
        "round_detail": str(first.get("detail") or ""),
        "plan_out": plan_out,
        "problems_out": list(result.get("problems") or []),
        "fingerprint_in": fp_in,
        "fingerprint_out": fp_out,
        "plan_unchanged": bool(fp_out and fp_in == fp_out),
        "stop_reason": str(result.get("stop_reason") or ""),
        "calls_used": int(result.get("calls") or 0),
        "rounds": int(result.get("rounds") or 0),
        "call_id": str(first.get("call_id") or ""),
        "evidence": dict(first.get("evidence") or {}),
        "problems_in": list(rounds[0].get("problems_in") or []),
        "verification": verification_meta,
        "generated_requests": len(service.requests),
        "request": service.requests[0],
        "voice_order_changed": bool(voice_order.get("changed")),
        "log": [line for line in log_buffer.getvalue().splitlines() if line.strip()],
    }


# ---------------------------------------------------------------------------
# 对账（回放读数 vs 冻结证据/期望）
# ---------------------------------------------------------------------------
def _request_probe(request) -> dict:
    """把一次请求的**可对账**摘要（口径同 `shared.call_ledger._describe`）。"""
    system_prompt = str(getattr(request, "system_prompt", "") or "")
    user_prompt = str(getattr(request, "user_prompt", "") or "")
    return {
        "system_prompt_sha256": _sha256_text(system_prompt),
        "user_prompt_sha256": _sha256_text(user_prompt),
        "request_chars": len(system_prompt) + len(user_prompt),
        "prompt_sha256": _sha256_bytes(
            ("system:" + system_prompt + "\nuser:" + user_prompt).encode("utf-8")),
        "schema": str((getattr(request, "json_schema", None) or {}).get("name") or ""),
    }


def _request_evidence_section(*, rebuilt_request, evidence: dict, problems_in: list,
                              archived_request, manifest_evidence) -> dict:
    """请求证据对账：**请求逐字身份**必须与归档一致；清单派生诊断字段容忍代码版本漂移。

    口径（写进报告，供复核）：
    - **身份字段**（进入发给模型的请求正文）：`request_chars` /
      `system_prompt_sha256` / `user_prompt_sha256`，以及归档 `request` 的逐字比对
      （`system_prompt` / `user_prompt` / `prompt_sha256` / `replay_sha256` /
      `response_contract` / `volatile_budget`）。任一项不符 ⇒ `ReplayError`。
    - **诊断字段**（`route` / `route_decision` / `content_slots` / `problems_in`）：
      由当前代码从问题集派生的证据，**不进入模型请求正文**。共享工作树可能有
      并行工作包改动（例如 `route_decision` 新增 `deferred_codes`），冻结清单是
      旧代码时点产物；这类差异列进 `drift` 并保留新旧值，**不改变身份判定**，
      也不冒充「完全一致」。
    """
    if rebuilt_request is None:
        raise ReplayError("回放没有捕获到本轮请求对象——请求证据无法对账")
    rebuilt_parts = _request_probe(rebuilt_request)
    archived = archived_request if isinstance(archived_request, dict) else None
    out = {"checked": bool(archived), "match": None, "identity_match": None,
           "diagnostic_match": None, "rebuilt": rebuilt_parts, "frozen": None,
           "identity_checks": {}, "diagnostic_checks": {}, "drift": []}
    if archived:
        archived_user = str(archived.get("user_prompt") or "")
        archived_sys = str(archived.get("system_prompt") or "")
        out["frozen"] = {
            "system_prompt_sha256": _sha256_text(archived_sys),
            "user_prompt_sha256": _sha256_text(archived_user),
            "request_chars": len(archived_sys) + len(archived_user),
            "prompt_sha256": str(archived.get("prompt_sha256") or ""),
            "replay_sha256": str(archived.get("replay_sha256") or ""),
            "schema": str(archived.get("schema") or ""),
            "volatile_budget": dict(archived.get("volatile_budget") or {}),
        }
        rebuilt = evidence.get("request") if isinstance(evidence.get("request"),
                                                       dict) else {}
        equal_parts = {
            "system_prompt": str(rebuilt.get("system_prompt") or "") == archived_sys,
            "user_prompt": str(rebuilt.get("user_prompt") or "") == archived_user,
            "prompt_sha256": str(rebuilt.get("prompt_sha256") or "")
            == str(archived.get("prompt_sha256") or ""),
            "replay_sha256": str(rebuilt.get("replay_sha256") or "")
            == str(archived.get("replay_sha256") or ""),
            "response_contract": rebuilt.get("response_contract")
            == archived.get("response_contract"),
            "volatile_budget": rebuilt.get("volatile_budget")
            == archived.get("volatile_budget"),
        }
        out["equal_parts"] = equal_parts
        if not all(equal_parts.values()):
            raise ReplayError("回放请求与归档请求不逐字一致（代码/语料漂移）：%s"
                              % "、".join(k for k, v in equal_parts.items() if not v))

    identity = out["identity_checks"]
    identity["request_chars"] = (rebuilt_parts["request_chars"]
                                 == (manifest_evidence or {}).get("request_chars"))
    identity["system_prompt_sha256"] = (
        rebuilt_parts["system_prompt_sha256"]
        == str((manifest_evidence or {}).get("system_prompt_sha256") or ""))
    identity["user_prompt_sha256"] = (
        rebuilt_parts["user_prompt_sha256"]
        == str((manifest_evidence or {}).get("user_prompt_sha256") or ""))
    if manifest_evidence is not None:
        bad_identity = [k for k, v in identity.items() if not v]
        if bad_identity:
            raise ReplayError("请求身份（字符数/提示哈希）与冻结清单不一致：%s"
                              % "、".join(bad_identity))

    diagnostics = out["diagnostic_checks"]

    def _record_drift(field, frozen_value, rebuilt_value, *, added=None,
                      missing=None, changed=None):
        item = {"field": field, "frozen": frozen_value, "rebuilt": rebuilt_value}
        if added:
            item["added_keys"] = list(added)
        if missing:
            item["missing_keys"] = list(missing)
        if changed:
            item["changed_keys"] = list(changed)
        out["drift"].append(item)

    def _subset(field, frozen_value, rebuilt_value) -> bool:
        if isinstance(frozen_value, dict) and isinstance(rebuilt_value, dict):
            shared = [k for k in frozen_value if k in rebuilt_value]
            changed = [k for k in shared if frozen_value[k] != rebuilt_value[k]]
            missing = sorted(set(frozen_value) - set(rebuilt_value))
            added = sorted(set(rebuilt_value) - set(frozen_value))
            ok = not changed and not missing
            if changed or missing or added:
                _record_drift(field, frozen_value, rebuilt_value, added=added,
                              missing=missing, changed=changed)
            return ok
        if frozen_value != rebuilt_value:
            _record_drift(field, frozen_value, rebuilt_value)
            return False
        return True

    if isinstance(manifest_evidence, dict):
        diagnostics["route"] = _subset(
            "route", str(manifest_evidence.get("route") or ""),
            str(evidence.get("route") or ""))
        diagnostics["route_decision"] = _subset(
            "route_decision", manifest_evidence.get("route_decision"),
            evidence.get("route_decision"))
        rebuilt_slots = evidence.get("content_slots") or {}
        manifest_slots = manifest_evidence.get("content_slots") or {}
        diagnostics["content_slots"] = _subset(
            "content_slots",
            {k: manifest_slots.get(k) for k in ("applicable", "why", "state", "blocked")},
            {k: rebuilt_slots.get(k) for k in ("applicable", "why", "state", "blocked")})
        diagnostics["problems_in"] = _subset(
            "problems_in",
            [str(c) for c in (manifest_evidence.get("problems_in") or [])],
            [str((p or {}).get("code") or "") for p in (problems_in or [])])
        out["identity_match"] = all(identity.values())
        out["diagnostic_match"] = all(diagnostics.values())
        # `match` = **请求逐字身份**口径；派生诊断漂移单独列 `drift`。
        out["match"] = bool(out["identity_match"])
    return out


def _conformance(*, target: dict, run: dict, archived: dict, request_evidence: dict,
                 response: dict) -> dict:
    """把回放读数与「期望值/归档计划/归档残余」逐项对账；缺证据的项记 None。"""
    expected_kind = str(target.get("kind_expected") or "")
    expected_fp_in = str(target.get("fingerprint_in_expected") or "")
    expected_fp_out = str(target.get("fingerprint_out_expected") or "")
    result_kind = str(run.get("round_kind") or "")
    checks = {
        "kind": (result_kind == expected_kind) if expected_kind else None,
        "fingerprint_in": ((run.get("fingerprint_in") == expected_fp_in)
                           if expected_fp_in else None),
        "fingerprint_out": None,
        "plan_unchanged": None,
        "request_evidence": (request_evidence.get("match")
                             if request_evidence.get("checked") else None),
        "plan_matches_archive": None,
        "residuals": None,
        "call_id": None,
    }
    if expected_kind == "applied":
        if expected_fp_out:
            checks["fingerprint_out"] = run.get("fingerprint_out") == expected_fp_out
    elif expected_kind == "no_change":
        checks["plan_unchanged"] = bool(run.get("plan_unchanged"))
    archived_plan = archived.get("plan_out")
    if isinstance(archived_plan, dict):
        checks["plan_matches_archive"] = run.get("plan_out") == archived_plan
    elif expected_kind == "no_change":
        checks["plan_matches_archive"] = bool(run.get("plan_unchanged"))
    archived_problems = archived.get("problems_out")
    if isinstance(archived_problems, list):
        checks["residuals"] = (
            [str((p or {}).get("code") or "") for p in (run.get("problems_out") or [])]
            == [str((p or {}).get("code") or "") for p in archived_problems])
    if response.get("source") == "frozen":
        expected_call = str((target.get("call") or {}).get("call_id") or "")
        if expected_call:
            checks["call_id"] = run.get("call_id") == expected_call
    required = {k: v for k, v in checks.items() if v is not None}
    return dict(checks, all=bool(required) and all(required.values()))


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def replay(corpus_manifest, round_id: int, response_override=None, *,
           output_base=None, write_artifact: bool = True) -> dict:
    """回放**一个归档修订轮**并返回结构化报告（`ReplayError` = 输入/证据失败）。

    Args:
        corpus_manifest: 冻结语料清单路径（`corpus_manifest.json`）。
        round_id: 目标轮号（清单 `replay_targets[].round`）。
        response_override: 显式替换的正文（`str`）；`None` = 用冻结回复。
        output_base: 隔离回放根（默认 `.tmp/s02_replay_work/output`）；
            不得在 `./output` 内。
        write_artifact: 是否把报告写到 `<output_base>/round_<id>/replay_report.json`。

    Returns:
        回放报告 dict（JSON 可序列化）；`ok` 表示全部可核对项与期望一致。

    Raises:
        ReplayError: 清单缺失/损坏、文件哈希不符、冻结回复缺失/不符、原文或预算
            读数缺失、请求证据漂移等。**失败路径不发起模型调用。**
    """
    manifest_path = Path(corpus_manifest).expanduser()
    if not manifest_path.is_file():
        raise ReplayError("冻结语料清单缺失：%s" % manifest_path)
    manifest_path = manifest_path.resolve()
    manifest = _read_json(manifest_path, "冻结语料清单")
    if not isinstance(manifest, dict):
        raise ReplayError("冻结语料清单必须是 JSON 对象")
    schema = str(manifest.get("schema") or "")
    if schema != MANIFEST_SCHEMA:
        raise ReplayError("不认识的语料清单 schema：%r（期望 %s）"
                          % (schema, MANIFEST_SCHEMA))
    corpus_root = manifest_path.parent
    verified = _verify_manifest_files(corpus_root, manifest.get("files") or {})
    targets = manifest.get("replay_targets")
    if not isinstance(targets, list) or not targets:
        raise ReplayError("冻结语料清单没有 replay_targets")
    target = None
    for item in targets:
        if not isinstance(item, dict):
            continue
        try:
            same = int(item.get("round")) == int(round_id)
        except (TypeError, ValueError):
            same = False
        if same:
            target = item
            break
    if target is None:
        raise ReplayError("冻结语料清单里没有 round=%r 的回放目标（现有：%s）"
                          % (round_id, [x.get("round") for x in targets
                                        if isinstance(x, dict)]))
    plan_in = target.get("plan_in")
    if not isinstance(plan_in, dict) or not plan_in:
        raise ReplayError("目标轮缺少 plan_in（或不是非空 JSON 对象）")
    response = _response_text_for_round(target, corpus_root, response_override)
    rounds = _load_rounds(manifest, corpus_root)
    row = _round_row(rounds, round_id)
    archived = _archived_context(row, plan_in)
    if row is not None:
        row_plan = row.get("plan_in")
        if isinstance(row_plan, dict) and row_plan != plan_in:
            raise ReplayError("清单 plan_in 与冻结轮次记录 step01_replan_rounds 不一致")
        row_fp = str(row.get("fingerprint_in") or "")
        want_fp = str(target.get("fingerprint_in_expected") or "")
        if row_fp and want_fp and row_fp != want_fp:
            raise ReplayError("清单 fingerprint_in_expected 与冻结轮次记录不一致")
    # 隔离 IO 必须在导入项目配置之前落环境（进程内不写生产 output/台账）。
    output_dir = _isolated_output_base(output_base)
    old_output_base = os.environ.get("OUTPUT_BASE")
    old_ledger_env = os.environ.get("TEXT_CALL_LEDGER")
    isolated_ledger = output_dir / ("round_%d" % int(round_id)) / "calls" / "step01_calls.jsonl"
    output_dir.mkdir(parents=True, exist_ok=True)
    isolated_ledger.parent.mkdir(parents=True, exist_ok=True)
    os.environ["OUTPUT_BASE"] = str(output_dir)
    os.environ["TEXT_CALL_LEDGER"] = str(isolated_ledger)

    warnings = []
    if not archived.get("request"):
        warnings.append("未提供归档请求证据：请求逐字一致性未校验（夹具/显式声明模式）")
    if not archived.get("frozen_records"):
        warnings.append("未提供冻结核验记录：候选不带 plan_verification，本轮残余问题"
                        "可能与归档不同（revision_fingerprint 不含该记录）")
    if response["source"] == "override":
        warnings.append("使用显式替换回复（response_override）：裁决/指纹不作为归档一致性结论")
    if row is None:
        warnings.append("未声明或未找到 step01_replan_rounds 目标行：problems/拒收/"
                        "归档计划对账跳过")

    plan_input = json.loads(json.dumps(plan_in, ensure_ascii=False))
    ledger_restore = None
    try:
        # 原文/预算读数在这里才需要导入生产代码（隔离环境已就位）。
        novel, novel_meta = _novel_for_round(target, archived)
        archived_budget = {}
        if isinstance(archived.get("request"), dict):
            archived_budget = _archived_budget_readings(
                str(archived["request"].get("user_prompt") or ""), archived["request"])
        from shared.call_ledger import ledger_path, set_ledger_path
        ledger_restore = ledger_path()
        set_ledger_path(isolated_ledger)
        run = _drive_single_round(
            plan_in=plan_input, novel=novel, archived=archived,
            archived_budget=archived_budget, reply=response,
            prior_rejections=_prior_rejections(rounds, int(round_id)))
    finally:
        try:
            from shared.call_ledger import set_ledger_path
            set_ledger_path(ledger_restore)
        except Exception:                                               # noqa: BLE001
            pass
        if old_output_base is None:
            os.environ.pop("OUTPUT_BASE", None)
        else:
            os.environ["OUTPUT_BASE"] = old_output_base
        if old_ledger_env is None:
            os.environ.pop("TEXT_CALL_LEDGER", None)
        else:
            os.environ["TEXT_CALL_LEDGER"] = old_ledger_env

    rebuilt_request = run.pop("request", None)
    problems_in = run.get("problems_in") or []
    archived_problems = archived.get("problems_in")
    if isinstance(archived_problems, list) and problems_in != archived_problems:
        raise ReplayError("本轮验收链复算的 problems_in 与归档不一致（验收链漂移）："
                          "复算 %d 条 %s / 归档 %d 条 %s"
                          % (len(problems_in),
                             [str((p or {}).get("code") or "") for p in problems_in],
                             len(archived_problems),
                             [str((p or {}).get("code") or "")
                              for p in archived_problems]))
    request_evidence = _request_evidence_section(
        rebuilt_request=rebuilt_request, evidence=run["evidence"],
        problems_in=problems_in,
        archived_request=archived.get("request"),
        manifest_evidence=target.get("request_evidence"))
    conformance = _conformance(target=target, run=run, archived=archived,
                               request_evidence=request_evidence, response=response)
    if request_evidence.get("diagnostic_match") is False:
        warnings.append(
            "请求派生诊断字段与冻结清单存在代码版本漂移（不进入模型请求正文，"
            "不影响请求逐字身份），详见 request_evidence.drift：%s"
            % "、".join(str(item.get("field") or "") for item
                       in request_evidence.get("drift") or []))
    report = {
        "schema": REPLAY_SCHEMA,
        "corpus": {
            "manifest": str(manifest_path),
            "manifest_sha256": _sha256_bytes(manifest_path.read_bytes()),
            "source_run_id": str(manifest.get("source_run_id") or ""),
            "verified_files": verified,
        },
        "code_identity": _code_identity(),
        "code_source": _code_source_meta(),
        "round": int(round_id),
        "expected": {
            "kind": str(target.get("kind_expected") or ""),
            "fingerprint_in": str(target.get("fingerprint_in_expected") or ""),
            "fingerprint_out": str(target.get("fingerprint_out_expected") or ""),
        },
        "result": {
            "kind": run["round_kind"],
            "detail": run["round_detail"],
            "fingerprint_in": run["fingerprint_in"],
            "fingerprint_out": run["fingerprint_out"],
            "plan_unchanged": run["plan_unchanged"],
            "plan_equal_to_input": (run.get("plan_out") == plan_input
                                    if isinstance(run.get("plan_out"), dict) else False),
            "call_id": run["call_id"],
            "stop_reason": run["stop_reason"],
            "calls_used": run["calls_used"],
            "rounds": run["rounds"],
            "model_calls": int(run.get("generated_requests") or 0),
            "response_source": response["source"],
            "response_sha256": response["sha256"],
            "response_bytes": response["bytes"],
            "verification": run["verification"],
            "acceptance_problems": {
                "count": len(run["problems_out"]),
                "codes": [str((p or {}).get("code") or "")
                          for p in run["problems_out"]],
            },
        },
        "request_evidence": request_evidence,
        "conformance": conformance,
        "ok": bool(conformance.get("all")),
        "warnings": warnings,
        "inputs": {
            "plan_in_sha256": _sha256_text(json.dumps(plan_in, ensure_ascii=False,
                                                      sort_keys=True)),
            "novel": novel_meta,
            "response_file": response["path"],
            "archived_request_present": bool(archived.get("request")),
            "archived_plan_out_present": isinstance(archived.get("plan_out"), dict),
        },
        "isolation": {
            "output_base": str(output_dir),
            "ledger": str(isolated_ledger),
            "production_output_touched": False,
        },
        "log": run.get("log") or [],
    }
    if write_artifact:
        artifact_dir = output_dir / ("round_%d" % int(round_id))
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / "replay_report.json"
        artifact_path.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
        report["artifacts"] = {"report": str(artifact_path)}
    return report


def _cli_view(report: dict, include_log: bool) -> dict:
    """CLI stdout：去掉超长的请求正文，只留可核对的指纹/期望/裁决。"""
    out = {k: v for k, v in report.items() if k != "log"}
    out["log_lines"] = len(report.get("log") or [])
    if include_log:
        out["log"] = list(report.get("log") or [])
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Step01 计划修订轮离线回放（零模型调用，只读冻结语料）")
    parser.add_argument("--manifest", required=True,
                        help="冻结语料清单 corpus_manifest.json")
    parser.add_argument("--round", required=True, type=int, help="目标修订轮号")
    parser.add_argument("--response-override", default="",
                        help="显式替换的回复正文文件（utf-8）；缺省用冻结回复")
    parser.add_argument("--output-base", default="",
                        help="隔离回放根（默认 .tmp/s02_replay_work/output）")
    parser.add_argument("--code-source", default="worktree",
                        help="回放用哪份代码：worktree=当前工作树（默认）；"
                             "head=git HEAD 只读快照；或任意 git ref/commit（用于钉住 "
                             "冻结语料对应的代码基线，如 <commit>）。共享脏树有并行改动时"
                             "用快照做确定性回放")
    parser.add_argument("--include-log", action="store_true",
                        help="输出里带上被捕获的生产日志行")
    raw_argv = list(argv) if argv is not None else list(sys.argv[1:])
    args = parser.parse_args(raw_argv)

    # `--code-source head|<ref>`：在隔离根物化 git 快照，再用子进程回放。
    # 子进程从快照加载项目包（工作树一行不改）；父进程只负责转发 stdout/退出码。
    _cs = str(args.code_source or "worktree")
    if (_cs not in ("worktree", "")
            and not os.environ.get(_SNAPSHOT_ENV, "").strip()):
        import subprocess

        output_dir = _isolated_output_base(args.output_base or None)
        ref = _git_resolve_ref("HEAD" if _cs == "head" else _cs)
        _label = "HEAD" if _cs == "head" else "".join(
            ch if ch.isalnum() else "-" for ch in _cs)[:40]
        snapshot = _materialize_head_snapshot(
            output_dir / "code_snapshot" / ("%s-%s" % (_label, ref[:12])), ref)
        env = dict(os.environ)
        env[_SNAPSHOT_ENV] = str(snapshot)
        env[_CODE_SOURCE_ENV] = _cs
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            done = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), *raw_argv],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", env=env)
        except (OSError, subprocess.SubprocessError) as exc:
            print(json.dumps({"ok": False, "error": "replay_input_error",
                              "detail": "启动快照回放子进程失败：%s" % exc},
                             ensure_ascii=False), file=sys.stderr)
            return 2
        if done.stdout:
            sys.stdout.write(done.stdout)
        if done.stderr:
            sys.stderr.write(done.stderr)
        return int(done.returncode)

    override = None
    if args.response_override:
        path = Path(args.response_override).expanduser()
        if not path.is_file():
            print(json.dumps({"ok": False, "error": "replay_input_error",
                              "detail": "替换回复文件不存在：%s" % path},
                             ensure_ascii=False), file=sys.stderr)
            return 2
        override = path.read_text(encoding="utf-8")
    try:
        report = replay(args.manifest, args.round, override,
                        output_base=(args.output_base or None))
    except ReplayError as exc:
        print(json.dumps({"ok": False, "error": "replay_input_error",
                          "detail": str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 2
    print(json.dumps(_cli_view(report, bool(args.include_log)),
                     ensure_ascii=False, indent=1))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
