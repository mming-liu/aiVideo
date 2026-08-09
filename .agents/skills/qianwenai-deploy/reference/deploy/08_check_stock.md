# 库存检查（步骤 8）

Agent 直接执行 CLI 命令查询 ECS（及可选 RDS）的可用区库存。

---

## ECS 库存查询

```bash
aliyun ecs DescribeAvailableResource \
  --RegionId "$REGION" \
  --DestinationResource InstanceType \
  --InstanceType "$INSTANCE_TYPE" \
  --InstanceChargeType PostPaid
```

从返回 JSON 提取有库存的可用区：
`AvailableZones.AvailableZone[]` 中 `Status` 为 `Available` 或 `WithStock` 的 `ZoneId`。

---

## RDS 可用区验证（仅含 RDS 时）

对每个 ECS 有货的可用区，验证 RDS 规格是否支持：

```bash
aliyun rds DescribeAvailableClasses \
  --RegionId "$REGION" --ZoneId "$ZONE_ID" \
  --Engine MySQL --EngineVersion 8.0 \
  --Category Basic --DBInstanceStorageType cloud_essd \
  --CommodityCode bards --OrderType BUY
```

返回中包含 `$DB_INSTANCE_CLASS` → 该区 RDS 可用。取 ECS ∩ RDS 可用区交集。

---

## 判断逻辑

| 结果 | 动作 |
|------|------|
| ≥1 个可用区有货 | 记录 `ZONE_ID`（取第一个），继续 |
| 0 个可用区 | 给用户 2–3 个替代方案（换规格/换地域），附代价说明 |

---

## 替代方案建议

库存不足时 Agent 自行查询替代规格的库存：
- 同系列更大规格（如 `ecs.e-c1m2.xlarge`）
- 其他系列同配置（如 `ecs.g7.large`）
- 换地域（如 `cn-shanghai`、`cn-beijing`）
