"""M02：用**实际修订入口** + 合法桩响应，在 U6 冻结候选上形成离线修订候选.

输入（全部冻结、只读）：
  baselines/2026-11-12-pre-layered/u6_frozen/candidate_d1a/candidate.json
  baselines/.../u6_frozen/u6_package/run_s02_20261111/output/**/step01_audit.blocked.json
  baselines/.../u6_frozen/u6_package/run_s02_20261111/output/**/_resume/novel_*.txt
输出：.pytest_tmp/m02_u6_out/{candidate.revised.json, repair_diag.json, diff.md}

桩响应按 S01 裁决的**最小合法修法**构造（不是模型原话，也不假装是）：
  #5 shot1 prompt  大特写 → 大远景
  #6 shot6 prompt  删掉重复的括号说明
  #2 shot7 lf_desc 酒壶滚落甲板（不再回到右手）
  #10 shot7 ff_desc 刺客双手空空（短刃留在心口）+ 同帧布局道具
  #4 shot11 ff_desc 首帧右手尚未抬起 + 同帧布局姿态
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "baselines/2026-11-12-pre-layered/u6_frozen"
CAND = BASE / "candidate_d1a/candidate.json"
PKG = BASE / "u6_package/run_s02_20261111/output/锦衣夜行/part_01_杀青州/chapter_001"
AUDIT = PKG / "step01_audit.blocked.json"
#: 冻结原文：U6 包快照没有把 `_resume/` 一起拷进 `baselines/`，所以按**显式声明的
#: 外部数据集**读取生产章节目录里那一份（S01 记录：14295 字节、名字即摘要前缀）。
#: 缺失即明确失败，不回退到「最新目录」或任何猜测路径。
NOVEL_CANDIDATES = (
    PKG / "_resume/novel_aba1adc22826.txt",
    ROOT / "output/锦衣夜行/part_01_杀青州/chapter_001/_resume/novel_aba1adc22826.txt",
)
OUT = ROOT / ".pytest_tmp/m02_u6_out"
#: 逐问题残余探针（U6 人工裁决集；`residual_probe` 字段）。
CONFIRMED_FIXTURE = ROOT / "tests/step01_script_gen/fixtures/u6_confirmed_issues.json"
TMP = Path(tempfile.mkdtemp(prefix="m02u6_"))
os.environ["OUTPUT_BASE"] = str(TMP / "output")
sys.path.insert(0, str(ROOT))

import step01_script_gen.main as m  # noqa: E402

CONFIRMED = [
    ("model_audit_PROP_CONTINUITY", "7", "lf_desc"),      # #2
    ("model_audit_FF_RESULT_LEAK", "11", "ff_desc"),      # #4
    ("model_audit_FIELD_CONFLICT", "1", "prompt"),        # #5
    ("model_audit_TEXT_REPETITION", "6", "prompt"),       # #6
    ("model_audit_STATE_CHAIN_CONFLICT", "10", "ff_desc"),  # #8
    ("model_audit_PROP_CONTINUITY", "4", "ff_desc"),      # #9
    ("model_audit_PROP_CONTINUITY", "7", "ff_desc"),      # #10
]

_DUP = "（寒光是独立物体，不是从角色身上长出来的）"


def _by_index(cand):
    return {str(s.get("index")): s for s in cand.get("shots") or []}


def build_payload(cand):
    s = _by_index(cand)
    s1, s6, s7, s11 = s["1"], s["6"], s["7"], s["11"]

    p1 = str(s1["prompt"]).replace("大特写", "大远景", 1)
    p6 = str(s6["prompt"]).replace(_DUP + _DUP, _DUP, 1)
    f7 = str(s7["ff_desc"]).replace("右手反握带血短刃", "双手空空", 1)
    l7 = str(s7["lf_desc"]).replace("右手酒壶倾倒酒水洒在甲板",
                                    "青瓷酒壶滚落甲板，酒水洒在甲板", 1)
    f11 = str(s11["ff_desc"]).replace("右手食指竖于唇前", "右手尚未抬起", 1)
    # 复核第四轮：帧描述改对后，同镜聚合 prompt 必须跟着改，否则下游请求仍带矛盾。
    p7 = (str(s7["prompt"])
          .replace("右手反握短刃（刃尖带血）", "双手空空", 1)
          .replace("右手酒壶倾倒酒水洒在甲板", "青瓷酒壶滚落甲板，酒水洒在甲板", 1))
    p11 = str(s11["prompt"]).replace("右手食指竖于唇前", "右手尚未抬起", 1)

    # 同帧布局：刺客手里的短刃要跟着 ff_desc 一起消失；首帧姿态跟着描述改。
    ff7 = s7["frame_layouts"]["ff"]
    killer_idx = next(i for i, e in enumerate(ff7) if e.get("name") == "神秘人")
    ff11 = s11["frame_layouts"]["ff"]
    zhang_idx = next(i for i, e in enumerate(ff11) if e.get("name") == "张十三")

    return {
        "shot_replacements": [
            {"shot_id": "1", "updates": [
                {"field_path": "prompt", "value": p1}]},
            {"shot_id": "6", "updates": [
                {"field_path": "prompt", "value": p6}]},
            {"shot_id": "7", "updates": [
                {"field_path": "ff_desc", "value": f7},
                {"field_path": "lf_desc", "value": l7},
                {"field_path": "prompt", "value": p7},
                {"field_path": "frame_layouts.ff.%d.props" % killer_idx, "value": []}]},
            {"shot_id": "11", "updates": [
                {"field_path": "ff_desc", "value": f11},
                {"field_path": "prompt", "value": p11},
                {"field_path": "frame_layouts.ff.%d.pose" % zhang_idx,
                 "value": "俯身未抬手"}]},
        ]
    }


def _shot_text(shot: dict) -> str:
    """镜头**全部相关字段**的合并文本（逐问题残余检查的扫描面）."""
    parts = [str(shot.get(k) or "") for k in
             ("prompt", "ff_desc", "lf_desc", "motion_desc", "narration_segment")]
    parts.append(json.dumps(shot.get("frame_layouts") or {}, ensure_ascii=False))
    return "\n".join(parts)


def _issue_residuals(fixture: dict, revised: dict, targets: list) -> list:
    """逐条原问题在**全部相关字段**上是否仍存在（复核第四轮要求）.

    探针来自夹具的 `residual_probe`（`shot_id` + `text`，可选 `and_text` /
    `max_count`）。这是 U6 回归数据的显式断言，不是生产判据。
    """
    shots = {str(s.get("index")): s for s in (revised.get("shots") or [])}
    target_ids = {str(t).replace("shot_", "") for t in (targets or [])}
    rows = []
    for issue in fixture.get("issues") or []:
        probe = issue.get("residual_probe")
        if not isinstance(probe, dict):
            continue
        sid = str(probe.get("shot_id") or "")
        shot = shots.get(sid) or {}
        text = _shot_text(shot) if shot else ""
        needle = str(probe.get("text") or "")
        count = text.count(needle) if needle else 0
        if probe.get("and_text"):
            count = min(count, text.count(str(probe["and_text"])))
        limit = int(probe.get("max_count", 0))
        residual = count > limit
        rows.append({
            "id": issue.get("id"),
            "shot_id": sid,
            "probe": needle + ("+" + str(probe["and_text"]) if probe.get("and_text") else ""),
            "hits": count,
            "residual": residual,
            "in_round": sid in target_ids,
        })
    return rows


class _Svc:
    def __init__(self, payload):
        self.text = json.dumps(payload, ensure_ascii=False)
        self.n = 0

    def generate(self, req):
        self.n += 1

        class _R:
            success = True
            error = ""
        _R.text = self.text
        return _R()


def _diff_rows(before, after):
    rows = []
    for s in before.get("shots") or []:
        idx = str(s.get("index"))
        a = next((x for x in after.get("shots") or []
                  if str(x.get("index")) == idx), None)
        if not isinstance(a, dict):
            rows.append((idx, "整镜缺失", "", ""))
            continue
        for k in ("ff_desc", "lf_desc", "motion_desc", "prompt"):
            if s.get(k) != a.get(k):
                rows.append((idx, k, str(s.get(k)), str(a.get(k))))
        for k in ("ff", "lf"):
            if (s.get("frame_layouts") or {}).get(k) != (a.get("frame_layouts") or {}).get(k):
                rows.append((idx, "frame_layouts.%s" % k,
                             json.dumps((s.get("frame_layouts") or {}).get(k), ensure_ascii=False),
                             json.dumps((a.get("frame_layouts") or {}).get(k), ensure_ascii=False)))
    return rows


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cand = json.loads(CAND.read_text(encoding="utf-8"))
    novel_path = next((p for p in NOVEL_CANDIDATES if p.exists()), None)
    if novel_path is None:
        raise SystemExit(
            "冻结原文缺失（已声明外部数据集）：%s"
            % " | ".join(str(p) for p in NOVEL_CANDIDATES))
    novel = novel_path.read_text(encoding="utf-8")
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    selected = [r for r in (audit.get("issues") or [])
                if (str(r.get("code")), str(r.get("shot_id")),
                    str(r.get("field_path"))) in CONFIRMED]
    issues = m._audit_payload_to_issues(
        {"issues": [dict(r, severity="visual_risk") for r in selected]})

    m.CHAPTER_DIR = TMP
    m.collect_structured_issues = lambda *a, **k: ([], False, "probe")
    for name in ("_metrics_inc", "_metrics_set", "_metrics_update"):
        setattr(m, name, lambda *a, **k: None)
    m.resolve_model_prompt_profile = lambda *a, **k: (lambda *a, **k: "PROMPT", "SYS")
    svc = _Svc(build_payload(cand))
    m.create_service = lambda *a, **k: svc

    diag, blocks = [], []
    revised = m._repair_shot_warnings(
        cand, novel, audit_issues=issues, max_rounds=1,
        repair_diag=diag, block_reasons=blocks,
        retry_state={}, audit_attempted=set(), defer_targets=set())

    # WP1c（2026-11-14）：统一走 shared.json_io.atomic_dump_json。手写
    # `json.dumps(...) + "\n"` 会给候选稿多写 1 个尾部换行，而生产侧
    # （step01 atomic_dump_json）不写——同一份内容两个 SHA-256，
    # 核对时只能靠 `dict` 相等去解释，属于自己制造的身份噪声。
    from shared.json_io import atomic_dump_json
    atomic_dump_json(OUT / "candidate.revised.json", revised,
                     ensure_ascii=False, indent=2)
    atomic_dump_json(OUT / "repair_diag.json", {"diag": diag, "block": blocks},
                     ensure_ascii=False, indent=2)

    rows = _diff_rows(cand, revised)
    # 复核第三轮：**投影后残余**——本轮接受的变化在 `project_for_commit` 之后是否
    # 仍然成立。此前只验证到「本轮被接受」，没有验证「落盘投影不撤销已接受修复」。
    projected, proj_errors = m.project_for_commit(revised, novel)
    proj_map = {str(s.get("index")): s for s in projected.get("shots") or []}
    proj_residual = []
    for idx, field, _before, after in rows:
        shot = proj_map.get(str(idx)) or {}
        if field.startswith("frame_layouts."):
            key = field.split(".")[1]
            now = (shot.get("frame_layouts") or {}).get(key)
            if json.dumps(now, ensure_ascii=False) != after:
                proj_residual.append((idx, field, "布局在投影后被改写"))
        else:
            if str(shot.get(field)) != after:
                proj_residual.append((idx, field, "字段在投影后被改写"))
    dec = next((r for r in diag if isinstance(r, dict) and "accepted" in r), {})
    lost = dec.get("postprocess_reverted") or {}
    # 复核第四轮：**逐问题残余**——已接受变化保留 ≠ 原问题解决。按夹具里的
    # `residual_probe` 在全部相关字段（prompt/ff/lf/motion/布局）上重新扫一遍。
    fixture = json.loads(CONFIRMED_FIXTURE.read_text(encoding="utf-8"))
    residuals = _issue_residuals(fixture, revised, dec.get("targets") or [])
    md = ["# U6 离线修订候选（M02，桩响应经实际入口）", "",
          "- 原稿：`%s`（sha256 `%s`）" % (
              CAND.relative_to(ROOT).as_posix(),
              hashlib.sha256(CAND.read_bytes()).hexdigest()),
          "- 冻结原文：`%s`（sha256 `%s`）" % (
              novel_path.relative_to(ROOT).as_posix() if novel_path.is_relative_to(ROOT)
              else str(novel_path),
              hashlib.sha256(novel_path.read_bytes()).hexdigest()),
          "- 原稿 revision：`%s`" % cand.get("script_revision"),
          "- 修订后 revision：`%s`" % (dec.get("revision_after")
                                       or revised.get("script_revision")),
          "- 桩调用次数：%d；block：%s" % (svc.n, blocks or "无"),
          "- 入轮目标：%s" % (dec.get("targets") or "—"),
          "- 接受：%s（resolved=%s，字段触达=%s）" % (
              dec.get("accepted"), dec.get("resolved"),
              dec.get("audit_field_touched")),
          "", "## 差异（只列发生变化的字段）", ""]
    if not rows:
        md.append("（无差异）")
    for idx, field, before, after in rows:
        md += ["### 镜 %s / `%s`" % (idx, field), "",
               "- 原：%s" % before, "- 新：%s" % after, ""]
    md += ["## 被后处理覆盖的授权改动", ""]
    if lost:
        md.append("下列字段在**合并后已经改对**，但被本轮后处理改写回原值；"
                  "接受判定只按整轮计，因此该轮仍然被接受。这一栏是本包新增的台账"
                  "（`postprocess_reverted`），此前没有任何记录。")
        md.append("")
        for tid, paths in sorted(lost.items()):
            md.append("- `%s`：%s" % (tid, ", ".join("`%s`" % p for p in paths)))
        md.append("")
        md.append("机制（已定位，`shot_1`）：`step01_script_gen/constraints/framing_jump.py` "
                  "在相邻镜景别跨度 > 2 时改写**前一镜**的 prompt。它用 "
                  "`_shot_size_level` 取景别，而该函数按词表顺序做**子串匹配**——"
                  "`大远景` 先命中 `远景`（词表里 `远景` 排在 `大远景` 之前），于是"
                  "「大远景」被判成 `远景`，与下一镜 `中近景` 相差 4 档触发改写；"
                  "随后 `prompt.replace('远景', '特写')` 作用在「大远景」上，"
                  "**恰好还原成「大特写」**。即：审计要求的一致性与相邻镜景别跨度"
                  "判据冲突，且子串匹配让冲突落在原值上。")
        md.append("")
        md.append("`shot_6` 的 prompt 同样被改写回原值（同一类 prompt 强化约束，"
                  "未逐一定位到具体函数）。按 M09 口径，本包**不改这两条判据**，"
                  "只把冲突记账并放进影子评估清单。")
    else:
        md.append("（无）")
    md.append("")
    md += ["## 投影后残余（`project_for_commit`）", ""]
    if proj_errors:
        md.append("投影报告错误：%s" % proj_errors)
    elif proj_residual:
        md.append("下列已接受的变化在投影后被改写：")
        md.append("")
        for idx, field, why in proj_residual:
            md.append("- 镜 %s / `%s`：%s" % (idx, field, why))
    else:
        md.append("本轮接受的 %d 处变化在投影后**全部**成立（逐字段比对）。" % len(rows))
    md.append("")
    md += ["## 逐问题残余（全部相关字段）", ""]
    if not residuals:
        md.append("（夹具未提供 `residual_probe`，本栏为空）")
    else:
        md.append("已接受的变化保留，**不等于**原问题解决。下表按夹具的 `residual_probe` "
                  "在 `prompt` / `ff_desc` / `lf_desc` / `motion_desc` / `frame_layouts` "
                  "全部相关字段上重新扫描：")
        md.append("")
        md.append("| 问题 | 镜头 | 探针 | 命中 | 结论 | 本轮是否入轮 |")
        md.append("|---|---|---|---|---|---|")
        for row in residuals:
            verdict = "**仍有残余**" if row["residual"] else "已消除"
            md.append("| %s | %s | `%s` | %d | %s | %s |" % (
                row["id"], row["shot_id"], row["probe"], row["hits"], verdict,
                "是" if row["in_round"] else "**未处理（未入轮）**"))
        md.append("")
        n_res = sum(1 for r in residuals if r["residual"])
        n_in = sum(1 for r in residuals if r["in_round"])
        md.append("小计：%d 条探针，已消除 %d，仍有残余 %d；其中本轮入轮 %d 条，"
                  "未入轮 %d 条（按 M02 的容量上限，留待下一轮）。"
                  % (len(residuals), len(residuals) - n_res, n_res, n_in,
                     len(residuals) - n_in))
        md.append("")
        md.append("> 「未入轮」是**未处理**，不是「已修复」。保护关系（旁白/说话人/"
                  "状态链/在场关系）由投影与 `validate_execution_changes` 另行核对，"
                  "不在本表里重复计。")
    md.append("")
    (OUT / "diff.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps({
        "calls": svc.n,
        "accepted": dec.get("accepted"),
        "reason": dec.get("reason"),
        "targets": dec.get("targets"),
        "touched": dec.get("audit_field_touched"),
        "revision_before": dec.get("revision_before"),
        "revision_after": dec.get("revision_after"),
        "changed_fields": len(rows),
        "out": str(OUT),
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
