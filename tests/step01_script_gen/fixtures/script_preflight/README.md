# script_preflight 夹具（2026-11-14）

`tools/script_preflight.py` 的回归语料。按 AGENTS.md「内容无关性与夹具」：

- **数据在这里**：`packs/sample_packs.json` 提供人物、地点、道具、镜头编号、字段级变异与
  每例的预期判据；构造器与消费测试不认识任何具体作品。
- **机制在构造器**：`tests/step01_script_gen/script_preflight_builder.py` 只负责展开
  `frame_layouts` / `keyframes`、套用 `patches`、校验内容无关性与「只改声明字段」。
- **消费在测试**：`tests/step01_script_gen/test_script_preflight.py` 参数化跑每个
  `(pack, case)`，并锁住本文件的规范化摘要（`FROZEN`）。

## 怎么加/改一类案例

1. 在 `packs/sample_packs.json` 里新增一个 pack：
   - `id`、`family`、`code`（必须是工具真实产出的 code）、`scope`、`why`、`discriminating`；
   - `shots`：声明式镜头。`ff` / `lf` / `kfs` 可写 `"甲"` 或 `{"name":"甲","props":[...]}`；
   - `cases`：至少一个 `legal`（预期 `issues: []`）与一个 `defect`（`patches` + 预期 `issues`）。
2. `defect` 的 `patches` 只能改基线里**已存在**的路径，构造器会逐镜比对顶层字段，
   多改一个字段即报错（避免「顺手改了别处」导致归因失真）。
3. 用构造器重建，更新 `test_script_preflight.py` 里的 `FROZEN` 摘要，跑：

   ```bash
   python -m pytest tests/step01_script_gen/test_script_preflight.py -q
   ```

## 纪律

- **只读**：测试不得写回本目录。
- **匿名**：人物只用「甲/乙/丙」，地点与道具用通用名词；`FORBIDDEN_WORK_TERMS`
  会拦下具体作品词。
- **每族都要两侧**：只有 legal 没有 defect 的族等于没校准阈值，测试会直接红。
- 换小说 / 换章节 = 换或加 pack，**不改构造器、不改测试、不改工具**。
