# -*- coding: utf-8 -*-
"""U6 图片 POST / 视觉 QC 预算的零调用核算（只读账本与源码常量）."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHAPTER = ROOT / "output" / "锦衣夜行" / "part_01_杀青州" / "chapter_001"
LEDGER = CHAPTER / "step02a_image_budget.json"

ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
used = int(ledger["used"])
limit = int(ledger["limit"])
planned_posts = 4
qc_max_attempts = int(os.getenv("QC_MAX_ATTEMPTS", "2") or "2")
repair_enabled = os.getenv("IMAGE_QC_REPAIR_ENABLED", "0")
no_repair = os.getenv("STEP02A_NO_REPAIR", "0")

result = {
    "ledger": {
        "path": str(LEDGER),
        "used": used,
        "limit": limit,
        "planned_posts": planned_posts,
        "cumulative_after": used + planned_posts,
        "expression": "%d/%d -> %d/%d" % (used, limit, used + planned_posts, limit),
    },
    "targets": [
        {"shot": 1, "frame": "ff", "max_posts": 2,
         "reason": "内容变更：shot 1 改为黄昏荷塘大远景；旧图哈希不再匹配"},
        {"shot": 1, "frame": "lf", "max_posts": 2,
         "reason": "内容变更：同上"},
        {"shot": 2, "frame": "ff", "max_posts": 0,
         "reason": "只重检 QC；图片与哈希保持不变（06ab04ab252884f8）"},
    ],
    "post_cap_basis": {
        "qc_max_attempts_env": qc_max_attempts,
        "max_retries_per_target": max(0, qc_max_attempts - 1),
        "image_qc_repair_enabled": repair_enabled,
        "step02a_no_repair": no_repair,
        "auto_i2i_repair_disabled_by_default": repair_enabled != "1",
        "note": ("QC_MAX_ATTEMPTS=2 ⇒ 每个目标最多 1 次初抽 + 1 次重抽 = 2 次 POST；"
                 "两个目标合计 4 次。IMAGE_QC_REPAIR_ENABLED 默认 0 ⇒ "
                 "_no_auto_repair() 为真，QC critical 不会触发 i2i 修图/doodle 二修，"
                 "否则会多出第 5 次 POST。"),
    },
    "qc_budget": {
        "max_calls": 5,
        "composition": "4 张新图各 1 次 + shot 2 FF 重检 1 次",
        "note": ("QC 是 ds-vision-skill 的视觉调用，不写入 step02a_image_budget.json；"
                 "每张图每次生成后自动 QC 一次；qc_call_failed 不自动重试。"
                 "若 QC 上限先用尽而仍有未审核的新图，不得记为完成交付。"),
    },
    "shot2_ff": {
        "frame_hash": "06ab04ab252884f8",
        "lf_hash": "eadf511bb726762a",
        "sha256_prefix": "4089f234ff475f7a",
        "qc_state": "qc_stale / legacy_unbound（U4 证据）",
        "recheck_mode": "缓存命中后只跑 QC，不产生 POST",
    },
    "stop_conditions": [
        "任一目标第 3 次 POST 被请求 ⇒ 立即停止（预算口径为每目标最多 2 次）",
        "累计 used 超过 35 ⇒ 立即停止并保留已生成图片",
        "QC 调用达到 5 次仍有新图未审核 ⇒ 停止，交付状态记未完成",
        "shot 2 FF 哈希变化 ⇒ 停止（说明图片被替换，不再是「只重检」）",
        "shot 2 LF 出现生成请求 ⇒ 停止（本包不生成 shot 2 LF）",
    ],
}

out = ROOT / ".pytest_tmp" / "u1_u5_closeout_20261110" / "u6_image_qc_budget.json"
out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
print(str(out))
