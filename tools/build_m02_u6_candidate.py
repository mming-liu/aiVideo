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
                {"field_path": "frame_layouts.ff.%d.props" % killer_idx, "value": []}]},
            {"shot_id": "11", "updates": [
                {"field_path": "ff_desc", "value": f11},
                {"field_path": "frame_layouts.ff.%d.pose" % zhang_idx,
                 "value": "俯身未抬手"}]},
        ]
    }


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

    (OUT / "candidate.revised.json").write_text(
        json.dumps(revised, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "repair_diag.json").write_text(
        json.dumps({"diag": diag, "block": blocks}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    rows = _diff_rows(cand, revised)
    dec = next((r for r in diag if isinstance(r, dict) and "accepted" in r), {})
    lost = dec.get("postprocess_reverted") or {}
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
