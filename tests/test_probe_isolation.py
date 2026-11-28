# -*- coding: utf-8 -*-
"""探针隔离启动器（复核 2026-10-07 三轮）：用**实际污染涉及的导入与日志入口**验证.

事故现场是"导入 `step01_script_gen.main` 时模块级 logger 绑到了产线日志"——所以验证
不能只看"字典字段对不对"，要真的走那条导入 + 写一行，并且**正常与抛异常两条路径**
都核正式产物摘要、都恢复进程上下文。
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.probe_isolation import (ProbeIsolationError, block_external_transport,   # noqa: E402
                                   isolated_probe)
from shared.log_utils import current_log_dir                                       # noqa: E402
from shared.paths import (clips_dir, script_file, step01_audit_file,               # noqa: E402
                          step01_commit_file, step01_log_file, step01_quality_file)
from shared.tee_logger import get_logger                                           # noqa: E402

_KEYS = ("OUTPUT_BASE", "STEP01_LOG_PATH", "TEXT_CALL_LEDGER")


def _digest_of(path) -> str:
    _p = Path(path)
    return hashlib.sha256(_p.read_bytes()).hexdigest() if _p.is_file() else ""


def test_the_probe_isolates_the_log_entry_and_restores_the_context(tmp_path):
    """**事故里那条路**：导入时按解析路径绑定 logger + 写一行 ⇒ 落在隔离根，产线不动."""
    _prod_log = step01_log_file()                    # 进隔离之前（= 本进程原本的日志路径）
    _prod_before = _digest_of(_prod_log)
    _env_before = {k: os.environ.get(k) for k in _KEYS}
    _ctx_before = current_log_dir()
    _root = tmp_path / "probe_root"
    with isolated_probe(label="t", root=_root) as _probe:
        _paths = _probe.assert_isolated()
        _l = get_logger(step01_log_file(), console_level="INFO")
        _l.verbose("PROBE-MARK-ISOLATED")
        assert Path(_paths["step01 日志"]).is_file(), _paths
        assert "PROBE-MARK-ISOLATED" in Path(_paths["step01 日志"]).read_text(
            encoding="utf-8")
        assert str(tmp_path) in str(_paths["调用台账"]), _paths
    # 退出之后：上下文恢复 + 原日志一个字节没动
    assert {k: os.environ.get(k) for k in _KEYS} == _env_before
    assert current_log_dir() == _ctx_before
    assert _digest_of(_prod_log) == _prod_before, "探针把原来的日志写了"


def test_the_exception_path_still_restores_the_context(tmp_path):
    """**抛异常那条路径**也要恢复进程上下文（旧事故的形态就是"以为还了，其实没还"）."""
    _env_before = {k: os.environ.get(k) for k in _KEYS}
    _ctx_before = current_log_dir()
    with pytest.raises(RuntimeError, match="探针中途失败"):
        with isolated_probe(label="t2", root=tmp_path / "probe_root") as _probe:
            _probe.assert_isolated()
            raise RuntimeError("探针中途失败")
    assert {k: os.environ.get(k) for k in _KEYS} == _env_before
    assert current_log_dir() == _ctx_before


def test_a_production_write_inside_the_probe_is_detected(tmp_path):
    """探针期间**碰了正式产物** ⇒ 退出时检出并报错（不是"事后 grep 才发现"）."""
    _prod_log = step01_log_file()
    _prod_log.parent.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ProbeIsolationError, match="正式产物被改动"):
        with isolated_probe(label="t3", root=tmp_path / "probe_root"):
            with open(_prod_log, "a", encoding="utf-8") as _f:
                _f.write("VIOLATION-MARK\n")
    # 还原：把这一行截掉（测试自己的产物目录，不是真产线）
    _lines = _prod_log.read_text(encoding="utf-8").splitlines(keepends=True)
    if _lines and _lines[-1].strip() == "VIOLATION-MARK":
        _prod_log.write_text("".join(_lines[:-1]), encoding="utf-8")


def test_external_transport_is_blocked_and_the_control_group_recovers():
    """探针只许离线：非回环 `connect` 直接抛；**撤掉注入**后恢复正常（对照组）."""
    with pytest.raises(ProbeIsolationError):
        with block_external_transport():
            socket.socket().connect(("93.184.216.34", 80))
    # 回环放行（本地桩服务照常）：连不上是 `ConnectionRefused`，不是隔离错误
    with block_external_transport():
        _s = socket.socket()
        _s.settimeout(0.3)
        try:
            _s.connect(("127.0.0.1", 1))
        except OSError as _exc:
            assert not isinstance(_exc, ProbeIsolationError), _exc
        finally:
            _s.close()
    # 对照组：撤掉注入 ⇒ 不再是隔离错误（联网成功或普通网络错误都算"恢复"）
    _s2 = socket.socket()
    _s2.settimeout(0.3)
    try:
        _s2.connect(("93.184.216.34", 80))
    except OSError as _exc:
        assert not isinstance(_exc, ProbeIsolationError), "对照组仍被拦着"
    finally:
        _s2.close()


def test_a_fresh_process_importing_main_binds_the_isolated_log(tmp_path):
    """**子进程**里做一次真实探针：导入 `step01_script_gen.main`（模块级 logger 绑路径）.

    这条是事故的**原样复现**：只有在"导入产品包"这一步之后才能看出绑定对不对。
    """
    _root = tmp_path / "sub_root"
    _script = textwrap.dedent('''
        import json, sys
        sys.path.insert(0, r"{root}")
        from tools.probe_isolation import isolated_probe
        with isolated_probe(label="sub", root=r"{probe}") as probe:
            import step01_script_gen.main as m
            m._logger.verbose("PROBE-SUBPROCESS-MARK")
            print(json.dumps({{k: str(v) for k, v in probe.isolated_paths().items()}},
                             ensure_ascii=False))
    ''').format(root=str(_ROOT), probe=str(_root))
    _r = subprocess.run([sys.executable, "-c", _script], capture_output=True, text=True,
                        encoding="utf-8", errors="replace", cwd=str(_ROOT))
    assert _r.returncode == 0, (_r.stdout[-400:], _r.stderr[-800:])
    _paths = json.loads([x for x in _r.stdout.strip().splitlines() if x.startswith("{")][-1])
    for _what, _p in _paths.items():
        assert _root.as_posix() in Path(_p).as_posix(), (_what, _p)
    assert "PROBE-SUBPROCESS-MARK" in (_root / "logs" / "step01.log").read_text(
        encoding="utf-8")

# ---------------------------------------------------------------------------
# ⑦ 保护集合 / 核验语义（复核 2026-10-07 四轮）
#
# 三处实测形态：① **提交清单不在保护集合里**（在隔离目录把它从 COMMITTED 改成 BLOCK，
# 启动器仍报 `production_unchanged=True`）；② **启动准备失败不还原上下文**（保护性 try
# 只圈了正文，入口断言一失败，环境 / 日志上下文 / 台账三项全没还原）；③ **摘要采集把
# "读不动"折成"没有这个文件"**（注入读失败并改写文件，仍报"未变化"、"改动 []"）。
# ---------------------------------------------------------------------------
_MANIFEST_TEXT = '{"status": "COMMITTED", "revision": 1}'


@pytest.fixture
def manifest():
    """沙箱章节目录里的**提交清单**（用完还原原状：这些用例只借它证明保护集合）."""
    _p = step01_commit_file()
    _had, _old = _p.exists(), (_p.read_bytes() if _p.exists() else None)
    _p.parent.mkdir(parents=True, exist_ok=True)
    _p.write_text(_MANIFEST_TEXT, encoding="utf-8")
    yield _p
    if _had:
        _p.write_bytes(_old)
    elif _p.exists():
        _p.unlink()


def _fail_reads_of(monkeypatch, target, state):
    """让某个受保护文件的 `read_bytes` 抛 `PermissionError`（**读不动**，不是不存在）."""
    _real = Path.read_bytes
    _t = Path(target)

    def _boom(self, *a, **k):
        if state.get("on") and Path(self) == _t:
            raise PermissionError("注入：读不动")
        return _real(self, *a, **k)

    monkeypatch.setattr(Path, "read_bytes", _boom)


def test_the_protected_set_covers_the_commit_manifest(manifest):
    """保护集合必须含**提交清单**与三份正式产物（复核四轮 P1）."""
    from tools.probe_isolation import protected_paths
    _set = [Path(x) for x in protected_paths()]
    for _p in (manifest, script_file(), step01_audit_file(), step01_quality_file()):
        assert _p in _set, (str(_p), [str(x) for x in _set])


@pytest.mark.parametrize("how", ["rewrite", "add", "delete"])
def test_every_kind_of_change_to_a_protected_artifact_is_detected(tmp_path, how,
                                                                  manifest):
    """**改写 / 新增 / 删除**三种变化都要检出（只比路径集合会漏掉改写）."""
    _scratch = clips_dir() / "_probe_scratch.bin"
    _scratch.parent.mkdir(parents=True, exist_ok=True)
    _want = str(manifest) if how == "rewrite" else str(_scratch)
    if how == "delete":
        _scratch.write_text("scratch", encoding="utf-8")
    _seen = {}
    try:
        with pytest.raises(ProbeIsolationError, match="正式产物被改动"):
            with isolated_probe(label="t-change",
                                root=tmp_path / "probe_root") as _probe:
                _seen["h"] = _probe
                if how == "rewrite":
                    manifest.write_text('{"status": "BLOCK"}', encoding="utf-8")
                elif how == "add":
                    _scratch.write_text("scratch", encoding="utf-8")
                else:
                    _scratch.unlink()
        _changed = _seen["h"].changed_paths
        assert any(_want in _x for _x in _changed), (how, _changed)
        assert _seen["h"].production_unchanged is False
        # **确认改动**：不进未知，也不让核验变成"未完成"
        assert _seen["h"].unknown_paths == [], _seen["h"].unknown_paths
        assert _seen["h"].verification_incomplete is False
    finally:
        if _scratch.exists():
            _scratch.unlink()


def test_an_untouched_production_set_is_not_a_false_positive(tmp_path, manifest):
    """**未变化不许误报**（合法对照）：保护集合扩大之后依然只在真变化时报警."""
    with isolated_probe(label="t-clean", root=tmp_path / "probe_root") as _probe:
        _probe.assert_isolated()
    assert _probe.production_unchanged is True, _probe.errors
    assert _probe.changed_paths == [], _probe.changed_paths
    assert _probe.verification_incomplete is False
    assert _probe.unknown_paths == [], _probe.unknown_paths
    assert _probe.errors == []
    assert str(manifest) in _probe.protected, _probe.protected


def test_a_read_failure_before_the_probe_refuses_to_start(tmp_path, monkeypatch,
                                                          manifest):
    """前置采集**读不动** ⇒ 不进探针（正文一行不跑），且进程上下文原样."""
    _fail_reads_of(monkeypatch, manifest, {"on": True})
    _env_before = {k: os.environ.get(k) for k in _KEYS}
    _ctx_before = current_log_dir()
    _ran = []
    with pytest.raises(ProbeIsolationError, match="摘要无法采集"):
        with isolated_probe(label="t-before", root=tmp_path / "probe_root"):
            _ran.append(1)
    assert _ran == [], "前置采集失败却仍然进了探针"
    assert {k: os.environ.get(k) for k in _KEYS} == _env_before
    assert current_log_dir() == _ctx_before


def test_a_read_failure_at_the_exit_is_not_reported_as_unchanged(tmp_path, monkeypatch,
                                                                 manifest):
    """退出采集**读不动 ⇒ 保护核验未完成**：不许报"未变化"（复核四轮 P2）."""
    _state = {"on": False}
    _fail_reads_of(monkeypatch, manifest, _state)
    _seen = {}
    with pytest.raises(ProbeIsolationError, match="保护核验"):
        with isolated_probe(label="t-after", root=tmp_path / "probe_root") as _probe:
            _seen["h"] = _probe
            manifest.write_text('{"status": "BLOCK"}', encoding="utf-8")
            _state["on"] = True                     # 改完就让它读不动
    _h = _seen["h"]
    assert _h.production_unchanged is False, "读不动被当成了未变化"
    assert _h.verification_incomplete is True
    assert any(str(manifest) in _x for _x in _h.unreadable_paths), _h.unreadable_paths
    assert any("保护核验" in _x for _x in _h.errors), _h.errors
    # **改了但它读不动** ⇒ 仍然核不出来 ⇒ 只算未知，不算"确认改动"（五轮分账）
    assert _h.changed_paths == [], _h.changed_paths
    assert str(manifest) in _h.unknown_paths, _h.unknown_paths
    monkeypatch.undo()
    manifest.write_text(_MANIFEST_TEXT, encoding="utf-8")


def test_both_evidences_survive_when_the_body_also_raised(tmp_path, monkeypatch,
                                                          manifest):
    """正文抛异常 + 退出核验也失败 ⇒ **两份证据都留**（原异常不被隔离报告顶掉）."""
    _state = {"on": False}
    _fail_reads_of(monkeypatch, manifest, _state)
    _seen = {}
    with pytest.raises(RuntimeError, match="探针正文失败") as _ei:
        with isolated_probe(label="t-both", root=tmp_path / "probe_root") as _probe:
            _seen["h"] = _probe
            manifest.write_text('{"status": "BLOCK"}', encoding="utf-8")
            _state["on"] = True
            raise RuntimeError("探针正文失败")
    assert _seen["h"].production_unchanged is False
    assert _seen["h"].changed_paths == [], _seen["h"].changed_paths
    assert str(manifest) in _seen["h"].unknown_paths, _seen["h"].unknown_paths
    assert any("保护核验" in _x for _x in _seen["h"].errors), _seen["h"].errors
    _notes = getattr(_ei.value, "__notes__", None)      # 3.11+：报告挂在原异常的 notes 上
    if _notes is not None:
        assert any("保护核验" in _x for _x in _notes), _notes


def test_a_failed_setup_still_restores_the_context(tmp_path, monkeypatch):
    """**启动准备失败**（入口断言就抛）⇒ 正文一行没跑，环境 / 日志上下文 / 台账**三项都还原**.

    复核四轮 P2 的形态：保护性 `try` 只圈住正文，前置断言失败时三项上下文全没还原。
    """
    from tools import probe_isolation as _pi
    from shared import call_ledger as _cl

    def _boom(self):
        raise _pi.ProbeIsolationError("启动准备失败（注入）")

    monkeypatch.setattr(_pi.ProbeHandle, "assert_isolated", _boom)
    _env_before = {k: os.environ.get(k) for k in _KEYS}
    _ctx_before = current_log_dir()
    _led_before = _cl.ledger_path()
    _ran = []
    with pytest.raises(_pi.ProbeIsolationError, match="启动准备失败"):
        with isolated_probe(label="t-init", root=tmp_path / "probe_root"):
            _ran.append(1)
    assert _ran == [], "启动准备失败却仍然进了正文"
    assert {k: os.environ.get(k) for k in _KEYS} == _env_before
    assert current_log_dir() == _ctx_before
    assert _cl.ledger_path() == _led_before

# ---------------------------------------------------------------------------
# ⑧ 读不动 ⇒ **未知**，不是"已改动"（复核 2026-10-07 五轮）
#
# 实测形态：只注入读取失败（**字节未变**）也得到 `changed_paths=[step01_commit.json]`
# ——"无法核验"被同时写成"确认改动"，与 §80.6 的分账口径不符。安全行为（不宣布通过）
# 是对的，但分类要分开：`changed_paths` 只装**确认改动**（前后都读得出来且摘要不同），
# 读不动进 `unknown_paths`，并让 `verification_incomplete=True`。
# ---------------------------------------------------------------------------
def _fail_dir_listing(monkeypatch, target, state):
    """让某个受保护**目录**的 `iterdir` 抛 `PermissionError`（目录不可读）."""
    _real = Path.iterdir
    _t = Path(target)

    def _boom(self, *a, **k):
        if state.get("on") and Path(self) == _t:
            raise PermissionError("注入：目录不可读")
        return _real(self, *a, **k)

    monkeypatch.setattr(Path, "iterdir", _boom)


def test_a_read_failure_alone_is_unknown_not_a_change(tmp_path, monkeypatch, manifest):
    """**只读失败、字节未变** ⇒ 未知，不是改动；但仍不许宣布"未变化"（五轮原形态）."""
    _state = {"on": False}
    _fail_reads_of(monkeypatch, manifest, _state)
    _seen = {}
    with pytest.raises(ProbeIsolationError, match="保护核验") as _ei:
        with isolated_probe(label="t-unknown", root=tmp_path / "probe_root") as _probe:
            _seen["h"] = _probe
            _state["on"] = True                  # 之后读不动；文件**一个字节都没改**
    _h = _seen["h"]
    assert _h.changed_paths == [], _h.changed_paths
    assert _h.unknown_paths == [str(manifest)], _h.unknown_paths
    assert _h.verification_incomplete is True
    assert _h.production_unchanged is False
    assert "正式产物被改动" not in str(_ei.value), _ei.value   # 不许写成"确认改动"
    _rec = [x for x in _h.unreadable if x["path"] == str(manifest)]
    assert _rec and _rec[0]["kind"] == "file" and _rec[0]["error"], _h.unreadable


def test_an_unreadable_directory_lists_its_members_as_unknown(tmp_path, monkeypatch):
    """**目录不可读** ⇒ 它自己与**它下面的成员**都列为未知，不是"被删掉/被改动"."""
    _dir = clips_dir()
    _member = _dir / "_probe_scratch.bin"
    _dir.mkdir(parents=True, exist_ok=True)
    _member.write_text("scratch", encoding="utf-8")
    _state = {"on": False}
    _fail_dir_listing(monkeypatch, _dir, _state)
    _seen = {}
    try:
        with pytest.raises(ProbeIsolationError, match="保护核验"):
            with isolated_probe(label="t-dir", root=tmp_path / "probe_root") as _probe:
                _seen["h"] = _probe
                _state["on"] = True
        _h = _seen["h"]
        assert _h.changed_paths == [], _h.changed_paths
        assert str(_member) in _h.unknown_paths, _h.unknown_paths
        assert str(_dir) in _h.unreadable_paths, _h.unreadable_paths
        assert any(x["path"] == str(_dir) and x["kind"] == "dir" for x in _h.unreadable), \
            _h.unreadable
        assert _h.production_unchanged is False
    finally:
        _member.unlink()



# ---------------------------------------------------------------------------
# ⑨ 环境快照口径（2026-10-08 自查）：把"两次不是同一批路径"报成改动，是诊断缺陷
#
# 实测形态：`.env.*` 的键在**快照之后**才进 `os.environ`（`shared.paths` 导入时
# `load_dotenv`）⇒ 退出还原把它们**弹掉** ⇒ `chapter_dir()` 少一层目录 ⇒ 第二次摘要
# 解析出另一批路径 ⇒ 报"14 处改动"，而字节一个都没动。安全方向没错（没有宣布"未变化"），
# 但那是**核验未完成**，不是"确认改动"。
# ---------------------------------------------------------------------------
def test_a_fresh_process_importing_a_dotenv_loading_module_sees_no_fake_change(
        tmp_path, manifest):
    """**子进程**（未预先导入 `shared.paths`）里导入会拉起 `.env.*` 的模块 ⇒ 仍报"未变化".

    ⚠️ 先放一个**受保护文件**：保护集合为空时，"两次都是空的"会让这条用例恒绿——
    那样它既测不到口径漂移，也测不到真实改动。
    """
    _root = tmp_path / "sub_env"
    _script = textwrap.dedent('''
        import json, sys
        sys.path.insert(0, r"{root}")
        from tools.probe_isolation import isolated_probe
        with isolated_probe(label="subenv", root=r"{probe}", block_transport=False) as probe:
            from _text_gen import agent_plan_provider          # noqa: F401
        print(json.dumps({{"changed": probe.changed_paths, "unknown": probe.unknown_paths,
                           "incomplete": probe.verification_incomplete,
                           "unchanged": probe.production_unchanged}}, ensure_ascii=False))
    ''').format(root=str(_ROOT), probe=str(_root))
    # **真实的探针进程**：`.env.*` 还没加载（那些键不在进程环境里）——这正是出问题的那种
    # 起点。`OUTPUT_BASE` 保留（= 本用例的沙箱），否则保护集合为空、这条用例恒绿。
    _env = {k: v for k, v in os.environ.items()
            if k not in ("NOVEL_NAME", "BOOK_NAME", "BOOK_INDEX", "NOVEL_CHAPTER",
                         "STEP01_PRESENTATION_MODE")}
    _r = subprocess.run([sys.executable, "-c", _script], capture_output=True, text=True,
                        encoding="utf-8", errors="replace", cwd=str(_ROOT), env=_env)
    assert _r.returncode == 0, (_r.stdout[-400:], _r.stderr[-600:])
    _got = json.loads([x for x in _r.stdout.strip().splitlines() if x.startswith("{")][-1])
    assert _got == {"changed": [], "unknown": [], "incomplete": False,
                    "unchanged": True}, _got
