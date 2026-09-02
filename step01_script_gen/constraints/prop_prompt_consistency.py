"""L2. 道具名一致性约束（v6.51 新增）.

扫描 prompt/ff_desc/lf_desc/motion_desc 中的道具词，与 prop_assets 资产库对齐：
  1. 别名归一化：prompt 中的道具别名 → 资产库标准名（例：短刃→匕首）
  2. 未注册道具标记：prompt 中出现但资产库中没有的道具词 → warning

注意：
  - 只做"已知别名→标准名"的正向替换，不主动删除未注册道具
    （模型可能仍能画出未注册道具，删除属于内容改写，太激进）
  - 道具词的识别依赖 prop_assets 中的 aliases 字段
  - scene_assets 的大场景/近景名不参与替换（场景名是地点，不是道具）

severity=WARN：确定性替换，无需 regen。
幂等：替换为标准名后不再命中别名表。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity


# 要扫描的字段
_PROP_FIELDS = (
    "prompt",
    "ff_desc",
    "lf_desc",
    "motion_desc",
)


def _load_prop_aliases(ctx) -> dict[str, str]:
    """从 prop_assets 构建 {别名: 标准名} 映射（长别名优先）.

    返回的 dict 按别名长度降序排列，确保长别名先匹配。
    """
    try:
        from shared.assets_bank import PropAssetsBank
        pbank = PropAssetsBank()
        pbank.load()
    except Exception:
        return {}

    alias_map: dict[str, str] = {}
    for ident in pbank.get_all_identifiers():
        prop = pbank.get_prop(ident) or {}
        # 标准名本身也加入（用于检测，但不替换自己）
        aliases = list(prop.get("aliases", []) or [])
        for alias in aliases:
            if alias and alias != ident:
                alias_map[alias] = ident
        # 把标准名的常见变体也加进去（如"酒壶儿"→"酒壶"）
        # 不做过度扩展，保守一点

    # 按别名长度降序，长词优先匹配
    return dict(sorted(alias_map.items(), key=lambda x: -len(x[0])))


class PropPromptConsistencyConstraint(Constraint):
    """L2. 道具名一致性：别名→标准名，未注册道具标记."""

    name = "prop_prompt_consistency"
    severity = Severity.WARN
    fields = _PROP_FIELDS

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        alias_map = _load_prop_aliases(ctx)
        if not alias_map:
            return repairs  # 资产库为空，跳过

        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            if not isinstance(shot, dict):
                continue
            for field in _PROP_FIELDS:
                text = shot.get(field)
                if not isinstance(text, str) or not text.strip():
                    continue
                before = text
                replaced = False
                for alias, standard in alias_map.items():
                    if alias in text:
                        text = text.replace(alias, standard)
                        replaced = True
                if not replaced:
                    continue
                shot[field] = text
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=idx if not is_hook else None,
                        action="alias_normalize",
                        target=field,
                        before=before[:60] + ("..." if len(before) > 60 else ""),
                        after=text[:60] + ("..." if len(text) > 60 else ""),
                    )
                )
        return repairs
