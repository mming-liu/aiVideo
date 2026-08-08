---
name: seedance-audio
description: "音频参考：唇同步锁相机 + 一镜一说话人 + 音效具体命名（已被规则12.5/audio_cue覆盖）"
license: MIT
user-invocable: false
tags: [aiVideo, audio]
metadata:
  version: "1.0"
  synced-from: "seedance-2.0/skills/seedance-audio + references/audio-guide"
  source-version: "6.6.0"
  updated: "2026-08-08"
---

# 音频（seedance-audio）

> 已由规则 12.5 + audio_cue 字段 + 规则 0 覆盖主体。仅记录 3 个增量。

## 增量 1：唇同步稳定约束
对话镜头锁相机、台词一句一呼吸、避免转头/大面部动作/忙碌手势，口型优先。

## 增量 2：一镜一说话人
双人轮替"先 A 说…停顿…B 答"保持镜头稳定；避免同镜多人同时开口。

## 增量 3：音效具体命名
audio_cue 写"杯落桌面/刀鞘轻响"而非"声音"；中文（普通话）唇同步最强，台词短句即可。
