---
name: seedance-lighting
description: "光线设计规范，用于 aiVideo 分镜生成"
license: MIT
user-invocable: false
tags: [aiVideo, lighting]
metadata:
  version: "1.0"
  synced-from: "seedance-2.0/skills/seedance-lighting"
---

# seedance-lighting

光线设计应描述物理光源和过渡，而非抽象美感。

## 光线契约

每个镜头应声明：
- 主光源方向
- 色温
- 阴影行为
- 反射行为
- 是否有过渡

## 光源选择

| 类型 | 用途 |
|------|------|
| 实用灯 (practical lamp) | 室内、亲密感、可见动机 |
| 窗光 (window light) | 自然主义、食品、生活场景 |
| 轮廓光 (rim light) | 分离主体 |
| 硬光 (hard light) | 黑色电影、锐利阴影 |
| 软光 (soft light) | 美颜、皮肤、产品 |
| 动态光 | 场景需要可见变化 |

## 色温词汇

| 中文 | 用途 |
|------|------|
| 暖色烛光 | 古风、温馨 |
| 冷色月光 | 夜晚、孤独 |
| 暖色实用灯 | 室内、夜景 |
| 冷蓝月光轮廓光 | 武侠、夜行 |
| 体积光穿过薄雾 | 戏剧性空气感 |

## 情绪词汇转换

用户说"温暖""孤独""紧张"时，给出具体光源：

| 情绪 | 改写为光源 |
|------|-----------|
| 温暖 | 暖色烛光、橙红灯笼 |
| 孤独 | 冷蓝月光、远景单灯 |
| 紧张 | 侧逆光、晃动阴影 |
| 浪漫 | 柔和窗光、花瓣散落 |