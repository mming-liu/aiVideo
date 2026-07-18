"""兼容层：CharacterBank / StyleBank 实现已统一至 shared/character_bank.py.

历史上此文件与 shared/character_bank.py 是两份完全相同的拷贝，
分别被 shared/vimax_bridge.py 和 step01 导入——改一处漏一处极易产生分歧。
现保留此薄层仅做 re-export，实际实现只维护 shared/ 一份。
"""
from shared.character_bank import CharacterBank, StyleBank  # noqa: F401
