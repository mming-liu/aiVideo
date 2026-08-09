# 数据库识别（步骤 5）

根据步骤 3 项目分析产出的 `db_signals` 判断是否需要 RDS。

---

## 判断逻辑

| 信号 | 动作 |
|------|------|
| MySQL 信号（`mysql`/`mysql2`/`sequelize`/`typeorm`/`prisma` + mysql） | AskUserQuestion：新建 RDS / 跳过自行配置 |
| 非 MySQL 数据库（postgres/redis/mongo 等） | 告知用户目前仅自动编排 MySQL RDS，其他需自行配置 |
| 无数据库信号 | 跳过此步骤，直接进入步骤 6 |

---

## RDS 规格选项（AskUserQuestion）

| 选项 | 规格 ID | 配置 | 参考价 |
|------|---------|------|--------|
| 入门型 | `mysql.n2e.small.1` | 1C2G | ≈ ¥0.10/时 |
| 通用型 | `mysql.n2.medium.1` | 2C4G | ≈ ¥0.20/时 |
| 性能型 | `mysql.n4.medium.1` | 4C8G | ≈ ¥0.39/时 |

---

## 产出

- `DB_INSTANCE_CLASS`：用户选择的 RDS 规格 ID（或为空 = 不创建 RDS）
- `DB_PASSWORD`：Agent 生成的随机密码（≥12 位，特殊字符仅 `!@%^*+=_-`），不输出到聊天

---

## 注意事项

- RDS 为 MySQL 8.0，按量付费
- 密码由 Agent 随机生成，通过环境变量传入 `generate_template.py`
- 选择跳过时，用户需自行配置外部数据库连接
