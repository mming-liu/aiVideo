# QC 驱动局部修复接入（v6.54.1：先修后抽）——按原计划执行

## 改动

### 1. `shared/image_qc.py`
- `QcResult` 加 `entities` 字段；`maybe_qc_sync` 把 L1 检测条目挂上（正常路径与回落路径都挂）。
- 新增 `build_repair_plan(shot, frame_kind, result) -> {"action": "edit"|"regenerate", ...}`：
  - 局部可修模式表（问题文案正则 → 修复指令 → bbox 选择器）：
    - 刀具接触/切入 → "刀具悬空、与人体保持明显距离" → 持刀者框 ∪ 躺卧者框
    - 红痕/伤口/血迹 → "去除身上红色痕迹与血迹" → 全部人物框并集
    - 发光 → "移除发光物与光效，刀具哑光金属" → 人物框并集
    - 双目紧闭/闭眼不符 → "让躺着的人闭上双眼" → 躺卧者框上部 45%（脸部）
    - 篷/遮盖物 → "移除顶棚篷布" → 船实体框上半
  - bbox 从 entities 取（0-1000 → /1000），并集；总区域 >70% 画幅 → regenerate
  - 任一 critical 不匹配局部模式 → regenerate（人数/复制/动作错/位置错=全局）
  - edit_prompt："修改图中红框区域：{各条修复指令}。红框外画面严格保持原样…"

### 2. `shared/vimax_bridge.py`
- `_maybe_qc_frame`：返回前把 QcResult 暂存 `shot["_qc_last"]`（bool 返回不变）。
- ff/lf 循环 QC-critical 分支：先 `build_repair_plan` → action=edit 时走 `_doodle_refine_first_frame`（每尝试最多 1 次）→ `_validate_frame` → 替换帧 → 复检（`_maybe_qc_frame`，尝试日志自动累计）→ 通过即 repaired（哈希不删，下次缓存命中即修好的图）；不 repaired → 原删帧重抽路径（末次保留留痕）。总尝试仍受 QC_MAX_ATTEMPTS 约束。

## 验收
1. 单测：修复计划分类（局部→edit 带并集 bbox；人数→regenerate；混合→regenerate；无实体→regenerate；eyes→脸区 bbox 数学）；561 回归。
2. 实测：用保留的"睁眼+刀接触"末版图跑 build_repair_plan（应出 edit 计划）→ 真调一次 doodle → 目视 + 复检。
3. 命中率记入 memory（edit 成功/失败回落/regen 三类计数）。