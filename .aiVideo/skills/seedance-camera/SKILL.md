---
name: seedance-camera
description: "摄像机运动规范，用于 aiVideo 分镜生成"
license: MIT
user-invocable: false
tags: [aiVideo, camera]
metadata:
  version: "1.0"
  synced-from: "seedance-2.0/ skills/seedance-camera"
---

# seedance-camera

摄像机运动规范。

## 镜头契约

每个镜头应声明：

| 字段 | 决定 |
|------|------|
| 景别 | extreme wide, wide, medium wide, medium, medium close-up, close-up, extreme close-up |
| 角度 | eye-level, low angle, high angle, overhead, profile, over-shoulder |
| 镜头感 | wide spatial energy, natural perspective, portrait compression |
| 相机支撑 | locked-off, handheld, slider, dolly, crane, gimbal |
| 运动 | push-in, pull-back, lateral track, orbit, pan, tilt, reveal |

## 运动语法

- **固定** (`locked-off`)：唇同步、产品身份、文字、logo、连续性锚点
- **推镜** (`dolly/push-in`)：发现、顿悟、亲密感、产品揭示
- **横移** (`lateral track`)：行进、队列、 choreography、前景层穿过
- **环绕** (`orbit`)：产品 hero、庄重主体
- **摇镜** (`pan/tilt`)：横向/纵向摇动
- **手持** (`handheld`)：真实感或紧张感

## 景别使用

| 景别 | 用途 | 注意 |
|------|------|------|
| 远景 | 规模、到达、环境 | 人脸小，logo会漂移 |
| 全景 | 动作、舞蹈、行列 | 动作简单可读 |
| 中景 | 对话、产品使用 | 角色广告好选择 |
| 近景 | 情感、表情、唇同步 | 相机稳定 |
| 特写 | 材质、食品、珠宝 | 避免大动作 |

## 连续性规则

- 保持同一 wardrobe、道具、光线方向、视线
- 每个 clip 结尾应能成为下一个首帧