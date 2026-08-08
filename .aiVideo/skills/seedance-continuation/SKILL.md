---
name: seedance-continuation
description: "首尾帧衔接：贯穿元素carrier + 动作不重放 + lf承接下镜"
license: MIT
user-invocable: false
tags: [aiVideo, continuation]
metadata:
  version: "1.0"
  synced-from: "seedance-2.0/skills/seedance-continuation"
  source-version: "6.6.0"
  updated: "2026-08-08"
---

# 首尾帧衔接（seedance-continuation）

> 规则 13.5/13.6 已覆盖「ff=动作前一刻、lf=结束态、禁完成态动词进 ff」；规则 24 已实现跨镜动作重叠。此处只补首尾帧**内**的 3 条。

## 1. 贯穿元素（carrier）
每个 ff→lf 过渡选 **1 个不变元素**（剪影/光源/道具/机位/服装件），让它跨首尾帧位置恒定，作观众追踪锚，防主体变形。
> 例：刺客"刀尖"贯穿——ff 刀未出水、lf 刀已没入，但刀尖在画面位置恒定。

## 2. 动作只发生一次（不重放）
ff 已定格"潜伏水下"，motion/lf 不得再"潜伏"；**lf 是结果，不是第二遍动作**。

## 3. lf 作为下一镜基础
本镜 lf 的构图/主体位置/色温必须能被下一镜 ff 直接承接。场景/时间切换用"下一镜新开"，不承诺无缝。

## 检查
- 首尾帧姿态显著不同（防构图撞车，规则13.5）
- 贯穿元素存在（刀尖/光源/机位至少1个不变）
