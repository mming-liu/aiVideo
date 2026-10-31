"""PowerShell 启动脚本验收：默认零发送，-Execute 才真实调用（2026-10-29）.

脚本保持「先零发送校验、失败即停、只有显式 -Execute 才进入真实分支」；授权身份
只做展示，不在这里生成。桩同样打在**底层 SDK**（复用入口测试的 sitecustomize），
因此脚本 → 入口 → 守卫 → provider 的整条路径都被执行。

R01（2026-10-30）修正两处验收边界：
  · 授权登记根隔离改由测试 shim 直接绑定 tmp_path —— 启动脚本会清掉
    STEP01_TEST_AUTH_ROOT，环境变量到不了子进程，旧写法只清理了一个从未被写入的
    fixtures 目录；现在断言登记确实落在临时根，且生产登记根前后不变。
  · 受控夹具（启动脚本 / 审计包）是**必交付物**：缺失必须失败，不再归类为
    "external dataset"；只有 pwsh 缺失才按平台依赖 skip。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "tests/step01_script_gen/fixtures/step01_audit_min"
SCRIPT = FIXTURE_DIR / "audit_run.ps1"
PACKAGE = FIXTURE_DIR / "audit_package.json"
PWSH = shutil.which("pwsh") or shutil.which("powershell.exe")

from shared.paths import AUDIT_AUTHORIZATION_ROOT as _PROD_AUTH_ROOT  # noqa: E402

pytestmark = pytest.mark.skipif(
    PWSH is None, reason="platform dependency: pwsh/powershell not available")


@pytest.fixture(scope="module", autouse=True)
def _controlled_fixtures_present():
    """启动脚本与审计包是受控交付夹具，缺失即失败（不是外部数据集）."""
    missing = [str(p) for p in (SCRIPT, PACKAGE) if not p.is_file()]
    assert not missing, (
        "controlled fixtures are required deliverables, not an external dataset: "
        + ", ".join(missing))


from tests.step01_script_gen.test_audit_entry_subprocess import (  # noqa: E402
    SITECUSTOMIZE, _binding_digest, _pin_stub_channel)


def _key(authorization_id: str) -> str:
    return "auth_" + hashlib.sha256(authorization_id.encode("utf-8")).hexdigest()[:32]


def _auth_dir(auth_root: Path, authorization_id: str) -> Path:
    return Path(auth_root) / "audit_authorizations" / _key(authorization_id)


def _prod_state_snapshot() -> list[str]:
    base = Path(_PROD_AUTH_ROOT) / "audit_authorizations"
    return sorted(p.name for p in base.iterdir()) if base.is_dir() else []


def _bind_test_auth_root(stub: Path, auth_root: Path) -> None:
    """测试 shim：把授权登记根绑到 tmp_path（生产代码不读环境变量）.

    启动脚本会 Remove-Item Env:STEP01_TEST_AUTH_ROOT，所以不能走环境变量；
    直接在 sitecustomize 里替换 shared.paths 的模块常量——这正是
    shared/paths.py 注释里指定的测试隔离方式。
    """
    stub.mkdir(exist_ok=True)
    (stub / "sitecustomize.py").write_text(
        SITECUSTOMIZE
        + "\n# R01: launcher strips STEP01_TEST_AUTH_ROOT; bind the temp root here.\n"
        + "from pathlib import Path as _AuthPath\n"
        + "import shared.paths as _auth_paths\n"
        + f"_auth_paths.AUDIT_AUTHORIZATION_ROOT = _AuthPath({str(auth_root)!r})\n",
        encoding="utf-8")


def _package(tmp_path, *, remaining=1, auth_id=None, corrupt=None):
    """构造本次启动用的包；授权 ID 每次唯一，测试结束随 tmp_path 一起丢弃."""
    data = _pin_stub_channel(json.loads(PACKAGE.read_text(encoding="utf-8-sig")))
    data["budget"]["remaining"] = remaining
    auth_id = auth_id or ("pytest-launch-" + uuid.uuid4().hex[:10])
    path = tmp_path / "package.json"
    if remaining > 0:
        data["budget"]["new_authorization"] = {
            "authorization_id": auth_id,
            "granted_by": "pytest-fixture",
            "granted_at": "2026-10-29T00:00:00+08:00",
            "max_sends": 1,
            "binding_digest": "",
        }
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        data["budget"]["new_authorization"]["binding_digest"] = _binding_digest(path)
    if corrupt:
        corrupt(data)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path, auth_id


def _run(tmp_path, *, execute=False, remaining=1, corrupt=None, auth_id=None):
    auth_root = Path(tmp_path) / "auth_root"
    auth_root.mkdir(exist_ok=True)
    _bind_test_auth_root(Path(tmp_path) / "stub", auth_root)
    pkg, auth_id = _package(tmp_path, remaining=remaining, auth_id=auth_id,
                            corrupt=corrupt)
    log = tmp_path / "sends.log"
    out = tmp_path / "out"
    env = dict(os.environ)
    env["PYTHONPATH"] = "%s%s%s" % (tmp_path / "stub", os.pathsep, ROOT)
    env["STEP01_STUB_MODE"] = "ok"
    env["STEP01_STUB_LOG"] = str(log)
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("STEP01_AUDIT_AUTHORIZED", None)
    env.pop("STEP01_TEST_AUTH_ROOT", None)
    cmd = [PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
           "-Package", str(pkg),
           "-OutRoot", str(out)]
    if execute:
        cmd.append("-Execute")
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    sends = len(log.read_text(encoding="utf-8").splitlines()) if log.is_file() else 0
    return proc, sends, out


def test_default_run_is_dry_only_and_sends_nothing(tmp_path):
    proc, sends, out = _run(tmp_path)
    assert proc.returncode == 0, (proc.stdout[-900:], proc.stderr[-400:])
    assert sends == 0, "无参数执行整份脚本必须零发送"
    assert "[OK] 零发送校验通过" in proc.stdout, proc.stdout[-400:]
    assert (out / "dryrun").exists(), "应留下零发送校验的运行根"
    assert not (out / "run").exists(), "不得创建真实运行根"
    assert not (tmp_path / "auth_root" / "audit_authorizations").exists(), (
        "dry-run 不得登记授权")


def test_dry_run_shows_unregistered_authorization(tmp_path):
    proc, sends, out = _run(tmp_path, remaining=0)
    assert proc.returncode == 0, proc.stdout[-600:]
    assert sends == 0
    assert "[INFO] 未登记新授权" in proc.stdout, proc.stdout[-800:]


def test_execute_shows_authorization_identity(tmp_path):
    proc, sends, out = _run(tmp_path, execute=True)
    assert "[INFO] 一次性授权" in proc.stdout, proc.stdout[-900:]
    assert "[INFO] 绑定摘要" in proc.stdout
    assert sends == 1
    assert proc.returncode == 0, (proc.stdout[-1200:], proc.stderr[-400:])


def test_execute_stops_when_dry_run_fails(tmp_path):
    def corrupt(data):
        data["object"]["candidate_sha256"] = "0" * 64
    proc, sends, out = _run(tmp_path, execute=True, corrupt=corrupt)
    assert proc.returncode == 2, (proc.stdout[-900:], proc.stderr[-400:])
    assert sends == 0, "零发送校验失败后不得进入真实调用"
    assert "[STOP]" in proc.stdout, proc.stdout[-400:]
    assert not (out / "run").exists()


def test_execute_with_valid_package_sends_exactly_once(tmp_path):
    before = _prod_state_snapshot()
    proc, sends, out = _run(tmp_path, execute=True)
    assert proc.returncode == 0, (proc.stdout[-1500:], proc.stderr[-500:])
    assert sends == 1, "真实调用必须恰好一次"
    run_dir = out / "run"
    assert run_dir.exists()
    commits = list(run_dir.rglob("step01_commit.json"))
    assert commits, "成功路径必须落提交清单"
    assert all(str(p).startswith(str(run_dir)) for p in commits)
    receipt = list(run_dir.rglob("budget_receipt.json"))
    assert receipt, "必须留下消费收据"
    row = json.loads(receipt[0].read_text(encoding="utf-8"))
    assert row["sends"] == 1 and row["send_state"] == "returned", row
    assert row["authorization"]["claimed"] is True

    # R01：登记必须落在测试临时根，生产登记根与受控夹具前后不变
    data = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    reg = _auth_dir(tmp_path / "auth_root", data["budget"]["new_authorization"]["authorization_id"])
    assert reg.is_dir(), "授权登记必须落在测试临时根（生产登记根不得被写入）"
    assert any(reg.iterdir()), "登记目录不能为空"
    assert _prod_state_snapshot() == before, "测试不得改写生产授权登记根"


def test_execute_twice_same_authorization_sends_once(tmp_path):
    # 两次启动共用同一个包文件与授权 ID；第一次的登记必须留在**同一个临时根**里，
    # 才能验收"已消费"（旧写法落在生产根，等于测试污染现场）。
    before = _prod_state_snapshot()
    proc1, sends1, out1 = _run(tmp_path, execute=True)
    assert proc1.returncode == 0 and sends1 == 1
    data = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    auth = data["budget"]["new_authorization"]
    auth_root = tmp_path / "auth_root"
    assert _auth_dir(auth_root, auth["authorization_id"]).is_dir()

    # 第二次：同一授权、另一个运行根（脚本仍会用登记根识别已消费）
    log = tmp_path / "sends2.log"
    out2 = tmp_path / "out2"
    env = dict(os.environ)
    env["PYTHONPATH"] = "%s%s%s" % (tmp_path / "stub", os.pathsep, ROOT)
    env["STEP01_STUB_MODE"] = "ok"
    env["STEP01_STUB_LOG"] = str(log)
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("STEP01_AUDIT_AUTHORIZED", None)
    env.pop("STEP01_TEST_AUTH_ROOT", None)
    proc2 = subprocess.run(
        [PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
         "-Package", str(tmp_path / "package.json"), "-OutRoot", str(out2),
         "-Execute"],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    sends2 = len(log.read_text(encoding="utf-8").splitlines()) if log.is_file() else 0
    assert proc2.returncode == 2, (proc2.stdout[-1200:], proc2.stderr[-400:])
    assert "authorization_already_claimed" in proc2.stdout, proc2.stdout[-800:]
    assert sends1 + sends2 == 1, "同一授权经脚本二次启动累计只能一次发送"
    assert _prod_state_snapshot() == before, "测试不得改写生产授权登记根"


def test_execute_refuses_when_budget_is_zero(tmp_path):
    """冻结包 remaining=0：-Execute 也不得发送（M09 额度行为）."""
    proc, sends, out = _run(tmp_path, execute=True, remaining=0)
    assert proc.returncode == 2, (proc.stdout[-1200:], proc.stderr[-400:])
    assert sends == 0, "剩余额度为 0 时 -Execute 也不得发送"
    assert "budget_exhausted" in proc.stdout, proc.stdout[-600:]
    assert not (out / "run").exists(), "额度拒收不得创建真实运行根"
