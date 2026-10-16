# -*- coding: utf-8 -*-
"""tools.enhancement_workers.protocol - 隔离 worker 的请求/响应文件协议（计划 §F5）.

## 为什么是"文件协议"而不是延迟 import

宿主是 Python 3.14 项目环境，PySceneDetect / WhisperX 只能在独立的 Python 3.12
工作区里跑（§F5：WhisperX 上游 ``>=3.10,<3.14``，装不进宿主）。协议把两边彻底
分开：宿主写 request JSON，用 ``subprocess`` **参数列表**拉起 worker，worker 写
response JSON，宿主读回并**核对身份**。

## 不变量（每条都有对应失败码）

- ``request_sha256``：响应必须回带请求的规范摘要；宿主核对不上 ⇒
  ``worker_request_digest_mismatch``（文件被改过 / 串了另一次请求）。
- ``worker`` + ``request_id``：响应身份必须与请求一致，否则
  ``worker_response_identity_mismatch``。
- **失败要分类**：超时 ``worker_timeout``、非零退出且无响应 ``worker_failed``、
  无响应文件 ``worker_no_response``、坏 JSON ``worker_response_unparsable``、
  schema 不符 ``worker_response_schema``。
- worker 的原始返回码另记 ``tool_exit_code``（§15「F退出码范围」）——它不参与
  enhancement 退出码映射，enhancement CLI 的 0/2/3/4/5/6/7 由
  :mod:`shared.enhancement_io` 定义。
- **不得继承模型凭据**（:func:`worker_env` 剥掉凭据类变量）；
- **不得自动联网**（``OFFLINE_ENV``：HF/transformers 离线模式等在 worker 进程内
  生效，模型缺失时 worker 报 unavailable，而不是去下载权重）。

本模块**只依赖标准库**：它同时被 3.14 宿主和 3.12 worker import，任何项目内
import 都会把宿主依赖（dotenv 等）带进隔离环境。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------- schema / worker 名

REQUEST_SCHEMA = "enhancement_worker_request.v1"
RESPONSE_SCHEMA = "enhancement_worker_response.v1"

#: worker 名 → 脚本文件名（宿主只按这张表找脚本，不猜路径）
WORKER_SCRIPTS = {
    "media_cuts": "media_worker.py",
    "whisperx_align": "alignment_worker.py",
}

#: worker 侧退出码（与 enhancement 的 0/2/3/4/5/6/7 **无关**，见 §15）
WORKER_EXIT_OK = 0
WORKER_EXIT_HANDLER_ERROR = 1
WORKER_EXIT_INVALID_REQUEST = 2
WORKER_EXIT_UNAVAILABLE = 3

#: 响应 status（worker 写）
STATUS_OK = "ok"
STATUS_UNAVAILABLE = "unavailable"
STATUS_ERROR = "error"

#: 宿主侧结果 status（response 的 error → failed）
HOST_OK = "ok"
HOST_UNAVAILABLE = "unavailable"
HOST_FAILED = "failed"

#: 运行时解释器环境变量与默认工作区名（§F5）
ENV_RUNTIME_PY312 = "ENHANCEMENT_PY312"
ENV_UV_PYTHON_DIR = "UV_PYTHON_INSTALL_DIR"
RUNTIME_DIRNAME = ".runtime"
RUNTIME_MEDIA_NAME = "enhancements-py312"
RUNTIME_WHISPERX_NAME = "whisperx-py312"

#: worker 进程的离线/无副作用环境（**不下载权重、不发遥测**）
OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
    "HF_HUB_DISABLE_PROGRESS_BARS": "1",
    "PYTHONNOUSERSITE": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONIOENCODING": "utf-8",
    "PIP_NO_INPUT": "1",
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
}

#: 凭据类环境变量：**一律不传给 worker**（F5：局部媒体/对齐 worker 不继承模型凭据）
_CREDENTIAL_RE = re.compile(
    r"(^|_)(API_?KEY|ACCESS_?KEY|SECRET|SECRET_?KEY|TOKEN|PASSWORD|PASSWD|"
    r"CREDENTIAL|CREDENTIALS|AUTH)(_|$)",
    re.IGNORECASE,
)


class WorkerError(Exception):
    """worker 协议层的结构化错误（宿主/worker 共用；字段与 §F3 错误 JSON 对齐）."""

    def __init__(self, code: str, message: str, *, stage: str = "worker", **detail):
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
        self.detail = detail

    def payload(self) -> dict:
        return {
            "code": self.code,
            "stage": self.stage,
            "message": self.message,
            "detail": dict(self.detail),
        }


# ---------------------------------------------------------------- 摘要与 JSON

def canonical_digest(obj) -> str:
    """规范摘要（key 排序 + 紧凑分隔 + UTF-8）——与 ``enhancement_io`` 同一算法."""
    blob = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def read_json_file(path) -> dict:
    """读 JSON 对象；坏 JSON / 非对象 ⇒ :class:`WorkerError`（不吞）."""
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkerError("json_read_failed", f"读不到 JSON: {p}（{exc}）") from exc
    try:
        data = json.loads(raw)
    except Exception as exc:
        raise WorkerError("json_unparsable", f"JSON 无法解析: {p}", raw_head=raw[:400]) from exc
    if not isinstance(data, dict):
        raise WorkerError("json_not_object", f"JSON 顶层不是对象: {p}")
    return data


def write_json_file(path, obj, *, indent: int = 2) -> Path:
    """原子写 JSON（同目录 .tmp → os.replace），返回落盘路径."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)
    return p


# ---------------------------------------------------------------- 运行时与进程环境

@dataclass(frozen=True)
class Runtime:
    """隔离解释器（绝对路径 + 来源，用于错误报告与证据留档）."""

    executable: str
    source: str

    def exists(self) -> bool:
        try:
            return Path(self.executable).is_file()
        except OSError:
            return False

    def payload(self) -> dict:
        return {"executable": str(self.executable), "source": self.source,
                "exists": self.exists()}


def _repo_root() -> Path:
    # tools/enhancement_workers/protocol.py → tools → <repo>
    return Path(__file__).resolve().parents[2]


def _venv_python(root: Path) -> Path | None:
    for rel in ("Scripts/python.exe", "bin/python", "Scripts/python3.exe", "bin/python3"):
        cand = Path(root) / rel
        if cand.is_file():
            return cand
    return None


def resolve_runtime(name: str = RUNTIME_MEDIA_NAME, *, repo_root=None, env=None,
                    explicit=None) -> Runtime | None:
    """定位隔离的 Python 3.12 解释器；**找不到就返回 None**（调用方如实报 unavailable）.

    顺序：显式传入 → ``ENHANCEMENT_PY312`` → ``<repo>/.runtime/<name>`` →
    uv 安装的 ``cpython-3.12*``。**不回退到宿主解释器**：worker 必须跑在隔离
    工作区里（§F5），拿不到就报 ``runtime_missing``，不静默降级。
    """
    if explicit:
        return Runtime(str(explicit), "explicit")
    env = dict(os.environ if env is None else env)
    hinted = (env.get(ENV_RUNTIME_PY312) or "").strip()
    if hinted:
        p = Path(hinted)
        if p.is_dir():
            p = _venv_python(p) or p
        return Runtime(str(p), "env:" + ENV_RUNTIME_PY312)
    root = Path(repo_root) if repo_root is not None else _repo_root()
    found = _venv_python(root / RUNTIME_DIRNAME / name)
    if found:
        return Runtime(str(found), f"venv:{RUNTIME_DIRNAME}/{name}")
    uv_dir = env.get(ENV_UV_PYTHON_DIR)
    if not uv_dir:
        appdata = env.get("APPDATA") or ""
        uv_dir = str(Path(appdata) / "uv" / "python") if appdata else ""
    if uv_dir and Path(uv_dir).is_dir():
        cands = sorted(Path(uv_dir).glob("cpython-3.12*/python.exe"))
        cands += sorted(Path(uv_dir).glob("cpython-3.12*/bin/python"))
        if cands:
            return Runtime(str(cands[-1]), "uv")
    return None


def worker_env(base: dict | None = None) -> dict:
    """worker 进程环境：剥掉凭据、强制离线、清掉会带进宿主代码的 PYTHONPATH."""
    env = dict(os.environ if base is None else base)
    for key in list(env):
        if _CREDENTIAL_RE.search(key):
            env.pop(key, None)
    env.pop("PYTHONPATH", None)
    env.update(OFFLINE_ENV)
    return env


def worker_argv(worker: str, request_path, response_path, *, interpreter,
                script_path=None) -> list:
    """宿主拉起 worker 的 argv（**参数列表**，绝不拼 shell 字符串）.

    ``script_path`` 显式给出脚本（隔离环境自定义安装路径 / 协议测试桩）；缺省按
    :data:`WORKER_SCRIPTS` 在本包目录里查表。
    """
    script = (Path(script_path) if script_path
              else Path(__file__).resolve().parent / _script_for(worker))
    return [str(interpreter), str(script),
            "--request", str(Path(request_path)), "--response", str(Path(response_path))]


def _script_for(worker: str) -> str:
    try:
        return WORKER_SCRIPTS[worker]
    except KeyError:
        raise ValueError(f"未知 worker: {worker!r}（合法: {sorted(WORKER_SCRIPTS)}）") from None


def package_version(dist_name: str) -> str | None:
    """已安装包的版本（取不到返回 None —— 不编造版本号）."""
    try:
        from importlib.metadata import PackageNotFoundError, version
    except Exception:  # pragma: no cover - 3.8+ 一定有
        return None
    try:
        return version(dist_name)
    except PackageNotFoundError:
        return None
    except Exception:
        return None


def runtime_payload(extra_packages: tuple = ()) -> dict:
    """worker 侧运行时信息（写进 response.runtime）."""
    pkgs = {}
    for name in ("scenedetect", "whisperx", "torch", "faster_whisper", *extra_packages):
        pkgs[name] = package_version(name)
    return {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "python_version_info": list(sys.version_info[:3]),
        "executable": sys.executable,
        "platform": sys.platform,
        "packages": pkgs,
    }


# ---------------------------------------------------------------- 宿主侧调用

@dataclass
class WorkerOutcome:
    """宿主侧一次 worker 调用的**完整**结果（失败也带证据，绝不静默吞）."""

    status: str
    worker: str
    request_id: str
    request_sha256: str
    result: dict | None = None
    error: dict | None = None
    runtime: dict | None = None
    tool_exit_code: int | None = None
    duration_sec: float | None = None
    request_path: str | None = None
    response_path: str | None = None
    stdout_digest: str | None = None
    stderr_digest: str | None = None
    stderr_tail: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == HOST_OK

    def payload(self) -> dict:
        return {
            "status": self.status,
            "worker": self.worker,
            "request_id": self.request_id,
            "request_sha256": self.request_sha256,
            "result": self.result,
            "error": self.error,
            "runtime": self.runtime,
            "tool_exit_code": self.tool_exit_code,
            "duration_sec": self.duration_sec,
            "request_path": self.request_path,
            "response_path": self.response_path,
            "stdout_digest": self.stdout_digest,
            "stderr_digest": self.stderr_digest,
            "stderr_tail": self.stderr_tail,
        }


def build_request(worker: str, payload: dict, *, request_id: str | None = None) -> dict:
    """构造 request 对象（``request_sha256`` 由 :func:`request_digest` 单独算）."""
    if worker not in WORKER_SCRIPTS:
        raise ValueError(f"未知 worker: {worker!r}（合法: {sorted(WORKER_SCRIPTS)}）")
    rid = str(request_id or f"{worker}-{int(time.time() * 1000)}-{os.getpid()}")
    return {
        "schema_version": REQUEST_SCHEMA,
        "worker": worker,
        "request_id": rid,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "payload": payload,
    }


def request_digest(request: dict) -> str:
    return canonical_digest(request)


def run_worker(worker: str, payload: dict, *, work_dir, runtime: Runtime | None = None,
               timeout_sec: float = 180.0, request_id: str | None = None,
               env_base: dict | None = None, repo_root=None,
               script_path=None) -> WorkerOutcome:
    """宿主调用一次 worker：写 request → subprocess → 读 response → 核身份.

    **不抛**工具类异常（超时/崩溃/坏 JSON 都是结果的一部分），调用方必须把
    outcome 分类进自己的 check 状态；只有调用方传了非法 worker 名这种编程错误
    才抛 ``ValueError``。
    """
    _script_for(worker)  # 未登记的 worker 名是调用方的编程错误，立刻抛（不静默降级）
    request = build_request(worker, payload, request_id=request_id)
    digest = request_digest(request)
    rid = request["request_id"]
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    request_path = work / f"{rid}.request.json"
    response_path = work / f"{rid}.response.json"
    write_json_file(request_path, request)

    outcome = WorkerOutcome(status=HOST_FAILED, worker=worker, request_id=rid,
                            request_sha256=digest,
                            request_path=str(request_path), response_path=str(response_path))

    rt = runtime if runtime is not None else resolve_runtime(
        RUNTIME_WHISPERX_NAME if worker == "whisperx_align" else RUNTIME_MEDIA_NAME,
        repo_root=repo_root, env=env_base)
    if rt is None or not rt.exists():
        outcome.status = HOST_UNAVAILABLE
        outcome.error = WorkerError(
            "runtime_missing",
            "找不到隔离的 Python 3.12 worker 解释器（§F5）；不降级到宿主解释器",
            runtime=(rt.payload() if rt is not None else None),
            expected_env=ENV_RUNTIME_PY312,
        ).payload()
        return outcome

    argv = worker_argv(worker, request_path, response_path, interpreter=rt.executable,
                       script_path=script_path)
    started = time.time()
    stdout = b""
    stderr = b""
    rc: int | None = None
    try:
        proc = subprocess.run(argv, shell=False, capture_output=True, timeout=float(timeout_sec),
                              env=worker_env(env_base), cwd=str(_repo_root() if repo_root is None
                                                                else repo_root))
        rc = proc.returncode
        stdout = proc.stdout or b""
        stderr = proc.stderr or b""
    except subprocess.TimeoutExpired as exc:
        outcome.status = HOST_FAILED
        outcome.tool_exit_code = None
        outcome.duration_sec = round(time.time() - started, 3)
        outcome.stdout_digest = sha256_text((exc.stdout or b"").decode("utf-8", "replace"))
        outcome.stderr_digest = sha256_text((exc.stderr or b"").decode("utf-8", "replace"))
        outcome.stderr_tail = (exc.stderr or b"").decode("utf-8", "replace")[-2000:]
        outcome.error = WorkerError(
            "worker_timeout", f"worker 超时（>{timeout_sec}s）已终止",
            argv=argv, timeout_sec=timeout_sec).payload()
        return outcome
    except OSError as exc:
        outcome.status = HOST_FAILED
        outcome.error = WorkerError("worker_spawn_failed", f"无法启动 worker 进程: {exc}",
                                    argv=argv).payload()
        return outcome

    outcome.tool_exit_code = rc
    outcome.duration_sec = round(time.time() - started, 3)
    outcome.stdout_digest = sha256_text(stdout.decode("utf-8", "replace"))
    outcome.stderr_digest = sha256_text(stderr.decode("utf-8", "replace"))
    outcome.stderr_tail = stderr.decode("utf-8", "replace")[-2000:]

    if not response_path.is_file():
        outcome.status = HOST_FAILED
        outcome.error = WorkerError(
            "worker_no_response",
            f"worker 未写出响应文件（rc={rc}）——按失败处理，不假定结果",
            argv=argv, returncode=rc).payload()
        return outcome

    try:
        response = read_json_file(response_path)
    except WorkerError as exc:
        outcome.status = HOST_FAILED
        outcome.error = WorkerError(
            "worker_response_unparsable", f"worker 响应无法解析: {exc.message}",
            argv=argv, returncode=rc,
            raw_head=(exc.detail or {}).get("raw_head")).payload()
        return outcome

    problems = validate_response(response, worker=worker, request_id=rid, digest=digest)
    if problems:
        outcome.status = HOST_FAILED
        outcome.error = WorkerError(
            problems[0]["code"], problems[0]["message"], argv=argv, returncode=rc,
            problems=problems).payload()
        return outcome

    outcome.runtime = response.get("runtime")
    if response.get("status") == STATUS_OK:
        outcome.status = HOST_OK
        outcome.result = response.get("result") or {}
    elif response.get("status") == STATUS_UNAVAILABLE:
        outcome.status = HOST_UNAVAILABLE
        outcome.error = response.get("error") or WorkerError(
            "worker_unavailable", "worker 报 unavailable（未给结构化原因）").payload()
    else:
        outcome.status = HOST_FAILED
        outcome.error = response.get("error") or WorkerError(
            "worker_error", "worker 报 error（未给结构化原因）").payload()
    if rc not in (None, WORKER_EXIT_OK) and outcome.status == HOST_OK:
        # 响应有效但 rc 非 0：如实记进 error.detail，不悄悄当成功
        outcome.error = WorkerError(
            "worker_exit_code", f"worker 返回码非 0（rc={rc}）但写出了 ok 响应",
            returncode=rc).payload()
        outcome.status = HOST_FAILED
        outcome.result = None
    return outcome


def validate_response(response: dict, *, worker: str, request_id: str, digest: str) -> list:
    """核响应身份：schema / worker / request_id / request_sha256 / status."""
    problems: list[dict] = []

    def add(code, message):
        problems.append({"code": code, "message": message})

    if response.get("schema_version") != RESPONSE_SCHEMA:
        add("worker_response_schema",
            f"响应 schema 不符: {response.get('schema_version')!r} != {RESPONSE_SCHEMA!r}")
    if response.get("worker") != worker:
        add("identity_mismatch", f"响应 worker 身份不符: {response.get('worker')!r} != {worker!r}")
    if str(response.get("request_id")) != str(request_id):
        add("identity_mismatch",
            f"响应 request_id 不符: {response.get('request_id')!r} != {request_id!r}")
    if response.get("request_sha256") is None and response.get("status") == STATUS_ERROR:
        # worker 壳自己就没接受这次请求（读不动/身份不符）——它没法回带摘要，
        # 如实报"请求未被接受"，不误报成"响应属于另一次请求"。
        add("worker_invalid_request",
            f"worker 未接受请求: {(response.get('error') or {}).get('message')}")
    elif response.get("request_sha256") != digest:
        add("worker_request_digest_mismatch",
            "响应回带的请求摘要与宿主发出的一致？否 —— 响应可能属于另一次请求或被改写")
    if response.get("status") not in (STATUS_OK, STATUS_UNAVAILABLE, STATUS_ERROR):
        add("worker_response_status", f"未知响应 status: {response.get('status')!r}")
    if response.get("status") == STATUS_OK and not isinstance(response.get("result"), dict):
        add("worker_response_result", "status=ok 但 result 不是对象")
    if response.get("status") in (STATUS_UNAVAILABLE, STATUS_ERROR) and not isinstance(
            response.get("error"), dict):
        add("worker_response_error", f"status={response.get('status')} 但缺少结构化 error")
    return problems


__all__ = [name for name in dir() if not name.startswith("_")]
