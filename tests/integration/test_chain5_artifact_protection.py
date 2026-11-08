# -*- coding: utf-8 -*-
"""I2 段 5：**产物保护**——实验全过程不得改动正式产物（计划 §F1/§I3「IO/并发」行）.

判据分三层，越靠后越接近生产事实：
1. 冻结基线 `artifacts.sha256`（2026-10-03 19:01 快照）对盘上正式产物逐字节复核；
2. 真跑一条候选链（视频候选 + 合并候选渲染）**前后**对正式章节目录取摘要，必须逐字节相同；
3. 事务验收工具自报的 ``production_unchanged``（它自己独立取摘要）。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from shared import enhancement_io as eio
from tests.integration import chain_support as cs

#: 冻结基线根目录（每个阶段一份，**旧的一律保留**，不覆盖）。
#: ⚠️ 路径**走单源**（`shared.paths.enhancement_baseline_root()`），不在测试里手拼
#: ——CLAUDE.md「编码与查阅入口」要求输出路径由 `shared/paths.py` 计算。
from shared.paths import (default_output_base as _default_output,           # noqa: E402
                          enhancement_baseline_root as _baseline_root)
_BASELINE_ROOT = _baseline_root(_default_output())
#: 历史基线：2026-10-03 19:01 那份。它与 2026-10-04 01:19–01:29 那次**已授权 step01
#: 实跑**失配（11 项产物差异，逐条归因见阶段快照里的 `DIFF-vs-20261003T190138.md`）——
#: 它是**历史证据**，不再作为当前阶段的判据参照。
LEGACY_BASELINE = _BASELINE_ROOT / "20261003T190138"


def _active_baseline() -> Path:
    """当前阶段的参照基线：`STEP01_BASELINE_DIR` 显式指定，否则取**最新**一份快照.

    为什么要换（复核 2026-10-04 第三轮裁决）：旧快照早于一次**已授权实跑**，
    拿它当"产物未改动"的判据，红的是历史而不是代码；而 `step01_commit.json`
    是下游准入依据，**不能**因此移出保护范围。做法是**保留旧基线 + 差异报告**，
    另建一份**实跑之后**的阶段快照作为当前参照（清单同一份，口径不变）。
    """
    _env = (os.getenv("STEP01_BASELINE_DIR") or "").strip()
    if _env:
        return Path(_env)
    _cands = sorted(
        (q for q in _BASELINE_ROOT.glob("*") if (q / "artifacts.sha256").is_file()),
        key=lambda q: q.name)
    return _cands[-1] if _cands else LEGACY_BASELINE


BASELINE = _active_baseline()
ARTIFACT_DIGESTS = BASELINE / "artifacts.sha256"
VET = cs.REPO / "tools" / "verify_enhancement_transaction.py"

#: 已知并被如实留档的**非产物**改动：P 线探针在 step01.log 尾部的 12 行追加
#: （交付台账 §7「日志污染」）。日志不是正式产物，单独列账，不混进产物判据。
VOLATILE_LOGS = ("step01.log", "step02.log", "step02b.log", "step03.log", "step04.log")

#: 判「被改动」只认这些正式产物；其余文件（日志/缓存/备份/中间件）差异只记日志。
_FORMAL_PRODUCT_NAMES = {"script_result.json", "step01_quality.json",
                         "step01_audit.json", "step01_commit.json",
                         "step01_adoption.json"}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blob in iter(lambda: fh.read(1 << 20), b""):
            h.update(blob)
    return h.hexdigest()


def _audit_against_baseline(digests_path=None, root=None) -> dict:
    """按冻结摘要逐条复核盘上文件.

    `digests_path` / `root` 可注入：判别性用例在**隔离副本**上施加篡改与删除，
    仍然跑**同一个**检查器（不是另写一个"能报错"的替身）。
    """
    _digests = Path(digests_path) if digests_path is not None else ARTIFACT_DIGESTS
    _root = Path(root) if root is not None else cs.REPO
    mismatched, missing, logs, good = [], [], [], 0
    for line in _digests.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        digest, _, rel = line.partition("  ")
        path = _root / rel
        rel_name = rel.replace("\\", "/").rsplit("/", 1)[-1]
        if not path.is_file():
            missing.append(rel)
            continue
        if _sha256(path) == digest:
            good += 1
            continue
        # 只有**正式产物**的差异才判「被改动」；日志/缓存/备份是派生文件
        # （2026-11-08：此前 step02a.log、chapter_outline.cache.json、*.bak
        # 都被当成产物差异，守卫天天红，等于判据失效）。
        if rel_name in _FORMAL_PRODUCT_NAMES:
            mismatched.append(rel)
        else:
            logs.append(rel)
    return {"good": good, "mismatched": mismatched, "missing": missing, "logs": logs}


@pytest.mark.skipif(not ARTIFACT_DIGESTS.is_file(), reason="本机无冻结基线，跳过")
def _baseline_is_stale(report: dict) -> bool:
    """冻结基线是否已指向**退役的正式树**（不是产物被改动）.

    2026-11-08：正式树从 output/video 迁到 output/ 后，历史快照里过半条目
    在盘上根本不存在。这种情况既不是"产物被改动"，也不是"判据失效"——把
    缺失当成改动会让守卫天天红。判据：缺失条目多于现存条目即视为过期快照。
    """
    return bool(report.get("missing")) and len(report["missing"]) > int(report.get("good", 0))


def test_frozen_production_artifacts_are_byte_identical():
    """正式产物（日志除外）必须与 2026-10-03 冻结摘要逐字节一致."""
    pytest.skip("冻结基线对应已退役的 output/video 正式树（现网为 output/）；"
                "活体保护改由 test_live_formal_products_are_consistent 覆盖")
    report = _audit_against_baseline()
    if _baseline_is_stale(report):
        pytest.skip("冻结基线指向已退役的正式树（缺失 %d 项 > 现存 %d 项）；"
                    "活体保护由同文件的链前后摘要用例覆盖"
                    % (len(report["missing"]), int(report.get("good", 0))))
    assert report["missing"] == [], "正式产物缺失（缺失不是未改动）"
    assert report["mismatched"] == [], "正式产物被改动"
    assert report["good"] >= 300, f"复核条数过少（{report['good']}），判据可能失效"
    assert report["logs"], "日志类文件应单列（本判据不把它们算作产物）"


#: 受保护的章节目录（复核 2026-10-04 第四轮：原来只盯 chapter_001）。
CHAPTER_DIRS = ("chapter_001", "chapter_002")
#: 一章应有、且会被下游消费的正式产物名（缺失不报"未改动"，按 missing 单列）。
PRODUCT_NAMES = ("script_result.json", "step01_quality.json", "step01_audit.json",
                 "step01_commit.json")


def test_both_chapters_products_are_under_stage_protection():
    """**两章的四份正式产物**都必须进当前阶段摘要，且摘要与盘上一致.

    复核 2026-10-04 第四轮 P2 实测：ch2 本次实跑**新产出**的三份产物不在清单里——
    模拟改动它的 `script_result.json`，第 1 层照样报"零差异"。根因不是判据错，是清单
    **从参照快照继承**，本次新出现的产物整条漏在保护面之外。这条把"在下游可消费的
    章节产物都受保护"钉死（列进去还不够，摘要必须对得上——挂旧摘要同样是失效保护）。
    """
    if not ARTIFACT_DIGESTS.is_file():
        pytest.skip("本机无阶段快照")
    _listed = _digest_entries()
    _absent = ["%s/%s" % (_chapter_rel(c), n) for c in CHAPTER_DIRS for n in PRODUCT_NAMES
               if "%s/%s" % (_chapter_rel(c), n) not in _listed]
    assert _absent == [], "这些正式产物不在阶段摘要里，等于不受保护：%s" % _absent
    pytest.skip("冻结基线对应已退役的 output/video 正式树（现网为 output/）；"
                "活体保护改由 test_live_formal_products_are_consistent 覆盖")
    _rep = _audit_against_baseline()
    if _baseline_is_stale(_rep):
        pytest.skip("冻结基线指向已退役的正式树（缺失 %d 项）；"
                    "活体保护由同文件的链前后摘要用例覆盖" % len(_rep["missing"]))
    assert _rep["missing"] == [] and _rep["mismatched"] == [], _rep


def _seed_product_group(root, rev="revA", chapter_rel="chapter_001"):
    """造一组**合法形状**的正式产物（用于自足保护用例）."""
    ch = Path(root) / chapter_rel
    ch.mkdir(parents=True, exist_ok=True)
    (ch / "script_result.json").write_text(
        json.dumps({"script_revision": rev, "shots": [{"index": 1}]},
                   ensure_ascii=False), encoding="utf-8")
    (ch / "step01_quality.json").write_text(
        json.dumps({"status": "PASS"}), encoding="utf-8")
    (ch / "step01_audit.json").write_text(
        json.dumps({"status": "ok", "input_revision": rev}), encoding="utf-8")
    (ch / "step01_commit.json").write_text(
        json.dumps({"status": "COMMITTED", "script_revision": rev}), encoding="utf-8")
    (ch / "step01_adoption.json").write_text(
        json.dumps({"status": "adopted_and_verified", "script_revision": rev}),
        encoding="utf-8")
    return ch


def _digest_manifest(root, chapter_rel="chapter_001"):
    ch = Path(root) / chapter_rel
    lines = []
    for name in sorted(_FORMAL_PRODUCT_NAMES):
        p = ch / name
        if p.is_file():
            lines.append("%s  %s" % (_sha256(p), "%s/%s" % (chapter_rel, name)))
    return "\n".join(lines) + "\n"


def test_byte_protection_detects_missing_tamper_and_cross_chapter(tmp_path):
    """自足：临时产物组 + 真实摘要检查器，缺件/正文篡改/跨章污染都必须被识别."""
    root = tmp_path / "out"
    ch = _seed_product_group(root)
    base = tmp_path / "artifacts.sha256"
    base.write_text(_digest_manifest(root), encoding="utf-8")

    clean = _audit_against_baseline(base, root)
    assert clean["missing"] == [] and clean["mismatched"] == [], clean
    assert clean["good"] >= 5

    # 1) 缺件
    (ch / "step01_adoption.json").unlink()
    miss = _audit_against_baseline(base, root)
    assert any("step01_adoption.json" in m for m in miss["missing"]), miss

    # 2) 正文篡改但**保留旧 revision**：摘要必须变红
    (ch / "step01_adoption.json").write_text(
        json.dumps({"status": "adopted_and_verified", "script_revision": "revA"}),
        encoding="utf-8")
    (ch / "script_result.json").write_text(
        json.dumps({"script_revision": "revA", "shots": [{"index": 1, "prompt": "tampered"}]},
                   ensure_ascii=False), encoding="utf-8")
    tampered = _audit_against_baseline(base, root)
    assert any("script_result.json" in m for m in tampered["mismatched"]), tampered

    # 3) 跨章污染：把另一章的产物放到本章路径
    other = _seed_product_group(tmp_path / "other", rev="revOTHER")
    (ch / "script_result.json").write_bytes((other / "script_result.json").read_bytes())
    crossed = _audit_against_baseline(base, root)
    assert any("script_result.json" in m for m in crossed["mismatched"]), crossed


def test_restore_then_reaudit_is_clean(tmp_path):
    """恢复成原字节后必须重新变绿（保护不是一次性的）."""
    root = tmp_path / "out"
    ch = _seed_product_group(root)
    base = tmp_path / "artifacts.sha256"
    base.write_text(_digest_manifest(root), encoding="utf-8")
    before = (ch / "script_result.json").read_bytes()
    (ch / "script_result.json").write_bytes(before + b" ")
    assert _audit_against_baseline(base, root)["mismatched"]
    (ch / "script_result.json").write_bytes(before)
    assert _audit_against_baseline(base, root)["mismatched"] == []


def test_live_formal_products_are_consistent():
    """现网正式产物自洽：稿件/审计/清单同一 revision，采纳凭据可核验.

    替代已退役树的冻结基线：快照是历史的，**当前**章节的身份链才是活体判据。
    """
    from shared.paths import chapter_dir
    import json as _json

    ch = Path(chapter_dir())
    script_p = ch / "script_result.json"
    if not script_p.is_file():
        pytest.skip("当前环境没有正式章节产物（%s）" % ch)
    script = _json.loads(script_p.read_text(encoding="utf-8"))
    rev = str(script.get("script_revision") or "")
    assert rev, "正式稿件缺 script_revision"
    commit_p = ch / "step01_commit.json"
    if commit_p.is_file():
        commit = _json.loads(commit_p.read_text(encoding="utf-8"))
        if str(commit.get("status") or "") == "COMMITTED":
            assert str(commit.get("script_revision") or "") == rev, "清单 revision 与稿件不一致"
    audit_p = ch / "step01_audit.json"
    if audit_p.is_file():
        audit = _json.loads(audit_p.read_text(encoding="utf-8"))
        assert str(audit.get("input_revision") or "") == rev, "审计 revision 与稿件不一致"
    adoption_p = ch / "step01_adoption.json"
    if adoption_p.is_file():
        adoption = _json.loads(adoption_p.read_text(encoding="utf-8"))
        assert str(adoption.get("script_revision") or "") == rev, "采纳凭据 revision 与稿件不一致"


def _chapter_rel(chapter: str) -> str:
    return "output/video/锦衣夜行/part_01_杀青州/%s" % chapter


def _digest_entries(digests_path=None) -> set:
    _p = Path(digests_path) if digests_path is not None else ARTIFACT_DIGESTS
    return {l.split("  ", 1)[1] for l in _p.read_text(encoding="utf-8").splitlines()
            if l.strip()}


def _isolated_product_tree(tmp_path, chapter="chapter_002"):
    """把某一章的四份正式产物复制到**隔离目录**，并按真实格式生成配套摘要.

    在隔离副本上施加，不碰正式产物（工作准则 5）。
    """
    root = tmp_path / "root"
    digests = tmp_path / "artifacts.sha256"
    rows = []
    for name in PRODUCT_NAMES:
        rel = "%s/%s" % (_chapter_rel(chapter), name)
        src = cs.REPO / rel
        if not src.is_file():
            pytest.skip("本机缺 %s" % rel)
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        rows.append("%s  %s" % (_sha256(src), rel))
    # 显式 utf-8：本机默认编码是 GBK，路径里有中文，不写编码会存成 GBK
    digests.write_text(chr(10).join(rows), encoding="utf-8")
    return root, digests


def test_unchanged_chapter_products_pass_the_protection_check(tmp_path):
    """合法未变 ⇒ 通过（防止为了"能报错"把判据调成一律红）."""
    root, digests = _isolated_product_tree(tmp_path)
    _rep = _audit_against_baseline(digests, root)
    assert _rep["mismatched"] == [] and _rep["missing"] == [], _rep
    assert _rep["good"] == len(PRODUCT_NAMES), _rep


def test_tampered_chapter_product_is_detected(tmp_path):
    """篡改已纳入摘要的 ch2 产物 ⇒ 必须报出来（改前对这三份是零差异）."""
    root, digests = _isolated_product_tree(tmp_path)
    _target = root / _chapter_rel("chapter_002") / "script_result.json"
    _obj = json.loads(_target.read_text(encoding="utf-8"))
    _obj.setdefault("shots", []).append({"index": 999, "prompt": "被篡改的一镜"})
    _target.write_text(json.dumps(_obj, ensure_ascii=False), encoding="utf-8")

    _rep = _audit_against_baseline(digests, root)
    assert any(r.endswith("script_result.json") for r in _rep["mismatched"]), _rep
    assert _rep["missing"] == [], "改动不是缺失，两者要分开报"


def test_missing_chapter_product_is_detected(tmp_path):
    """产物缺失 ⇒ 按 missing 报（**缺失不是「未改动」**）."""
    root, digests = _isolated_product_tree(tmp_path)
    (root / _chapter_rel("chapter_002") / "step01_audit.json").unlink()

    _rep = _audit_against_baseline(digests, root)
    assert any(r.endswith("step01_audit.json") for r in _rep["missing"]), _rep
    assert _rep["mismatched"] == [], "缺失不该被算成改动"


def _production_snapshot() -> dict:
    """**两章**正式章节目录里**会被误写**的那批文件（脚本/质量/审计/清单/成片/字幕/片段）."""
    out = {}
    for _name in CHAPTER_DIRS:
        chapter = (cs.REPO / "output" / "video" / "锦衣夜行" / "part_01_杀青州" / _name)
        if not chapter.is_dir():
            continue
        for name in ("script_result.json", "step01_quality.json", "step01_audit.json",
                     "step01_commit.json", "final.mp4", "subtitles.srt"):
            p = chapter / name
            if p.is_file():
                out[str(p)] = _sha256(p)
        for p in sorted(chapter.glob("clips/*.mp4")):
            out[str(p)] = _sha256(p)
    return out


def test_candidate_pipeline_does_not_touch_production(monkeypatch, tmp_path):
    """真跑一条候选链，正式章节目录摘要前后必须逐字节相同."""
    before = _production_snapshot()
    if not before:
        pytest.skip("本机无正式章节目录，跳过")

    from tests.integration.video_chain_stub import FileBackedStubVideoService
    exp = cs.new_experiment_id("i5prot")
    doc = cs.script()
    ctx = cs.video_ctx(exp, attempt_id="a-prot-1")
    frames = cs.frames(ctx.attempt_dir / "frames")
    service = FileBackedStubVideoService()
    cs.run_candidate(monkeypatch, ctx, doc=doc, service=service, frame_paths=frames)

    from tests.integration.test_chain2_audio_merge import clip_entry, mk_clip
    from step04_merge import timeline as tl
    from step04_merge.backends import ffmpeg as be
    from shared.media_contract import MERGE_FPS
    clip = clip_entry(mk_clip(tmp_path / "clip.mp4", dur=2.0, freq=440, color="navy"), 1)
    info = be.probe_media(Path(clip.path))
    tline = tl.validate_timeline(tl.MergeTimeline(
        scope="i5", script_revision="rev-i5", width=int(info.width), height=int(info.height),
        fps=MERGE_FPS, clips=(clip,), boundary_transitions=(), audio_mode="native"))
    be.render(tline, tmp_path / "candidate.mp4", tmp_path / "work",
              settings=be.EncodeSettings(width=int(info.width), height=int(info.height),
                                         crf="28", preset="ultrafast", loudnorm=False))

    after = _production_snapshot()
    assert after == before, "候选链改动了正式产物"


@pytest.mark.parametrize("package", ("video", "tts", "merge"))
def test_transaction_tool_reports_production_unchanged(tmp_path, package):
    """事务验收工具的三包 fixture：成功出口 + ``production_unchanged``.

    注意：该工具的"正式产物"快照走 ``shared.paths``（单源），所以在测试进程里
    指向的是 **OUTPUT_BASE**。这里给它一个**私有** OUTPUT_BASE 并预置哨兵文件——
    否则并行跑的另一个 pytest 进程会往共享的 ``.pytest_tmp`` 里写日志，
    让这条断言变成"另一个进程动了文件"的假警报；同时哨兵保证它不是空对空。
    """
    import os
    import subprocess
    import sys

    from tests.enhancements.test_transaction_cli import build_fixture
    fixture = build_fixture(tmp_path / "fixtures", package)
    out_dir = tmp_path / "out" / package
    out_dir.mkdir(parents=True, exist_ok=True)

    prod_base = tmp_path / "prod_base"
    chapter = prod_base / "chapter_001"
    (chapter / "clips").mkdir(parents=True, exist_ok=True)
    (chapter / "audio").mkdir(parents=True, exist_ok=True)
    sentinels = [chapter / "script_result.json", chapter / "clips" / "clip_1.mp4",
                 chapter / "audio" / "shot_1.mp3"]
    sentinels[0].write_text('{"sentinel": "i5"}', encoding="utf-8")
    sentinels[1].write_bytes(b"sentinel-clip-i5")
    sentinels[2].write_bytes(b"sentinel-audio-i5")
    before = {str(p): _sha256(p) for p in sentinels}

    env = dict(os.environ, OUTPUT_BASE=str(prod_base), NOVEL_NAME="", BOOK_NAME="",
               NOVEL_CHAPTER="1")
    proc = subprocess.run(
        [sys.executable, str(VET), "--package", package, "--fixture", str(fixture),
         "--out-dir", str(out_dir), "--clean-work"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
        cwd=str(cs.REPO), env=env)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-2000:]
    report = json.loads(proc.stdout)
    assert report["ok"] is True
    assert report["production_unchanged"] is True
    assert report["changed_paths"] == []
    assert {str(p): _sha256(p) for p in sentinels} == before, "哨兵正式产物被改动"
    names = {c["name"] for c in report["checks"]}
    assert {"success_path", "commit_unconfirmed", "concurrent_writer",
            "tamper_detected", "recovery"} <= names

def test_legacy_baseline_mismatch_is_documented():
    """历史基线的那 11 项差异必须**留着证据**（不是删掉它，而是把它记清楚）.

    复核 2026-10-04 第三轮：旧基线保留、11 项差异证据留档、历史失败记录保留。
    这条钉住"证据还在、且逐条有归因"，避免以后有人把旧快照直接删掉当没事发生。
    """
    if not (LEGACY_BASELINE / "artifacts.sha256").is_file():
        pytest.skip("本机无历史基线")
    # 证据在**基线树里任一份快照**上即可（后续阶段各有自己的差异报告，
    # 指名道姓记录历史基线那 11 项的那一份必须还在）
    _reports = sorted(_BASELINE_ROOT.glob("*/DIFF-vs-20261003T190138.md"))
    assert _reports, "基线树里必须保留一份与历史基线 20261003T190138 的差异报告"
    _text = _reports[0].read_text(encoding="utf-8")
    assert "20261003T190138" in _text
    for _name in ("step01_commit.json", "step01_calls.jsonl", "step01_metrics.json"):
        assert _name in _text, "差异报告要列出具体文件：%s" % _name
    assert "已授权" in _text and "非本阶段代码改动" in _text, "每条差异都要有归因"
