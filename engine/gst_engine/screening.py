"""Spine 三件套筛选规则。

依据 2026-11-05 与 11-06 对真实升级包中央目录的全量抽查：
  res/common/battle/knight_spine/       战斗态图集，1024x1024 与 1024x512，排除
  res/common/knight_spine/              角色立绘，2048x2048，保留
  res/common/unite_token_spine/         武器 Spine 动画，整体排除
  res/common/battle/effect_spine/       特效，排除
  res/common/battle/effect_spine_group/ 特效，排除
  res/common/battle/buff_spine/         特效，排除
背景层文件名带 _bg 或 _bj，与角色部件无关，排除。

武器判定按目录语义（用户 2026-11-06 提出）。全量 4318 个 atlas 里
unite_token_spine 恰好 33 个，ID 从 50090 到 50410，全部为武器；
knight_spine 的 402 个 ID 中没有武器形状编号。按目录排除与编号区间
当前等价，且不受官方以后新增编号上限影响。
"""
from __future__ import annotations

import re

TRIO = (".atlas", ".skel", ".png")
CHARACTER_DIRS = ("knight_spine",)
BACKGROUND = re.compile(r"_(bg|bj)\d*\.", re.IGNORECASE)


def is_target(name: str) -> bool:
    low = name.lower()
    if not low.endswith(TRIO):
        return False
    if BACKGROUND.search(low):
        return False
    if "/battle/" in low:
        return False
    return any(directory in low for directory in CHARACTER_DIRS)
