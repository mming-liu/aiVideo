# S08：离线验证与收益评阅

> 全部离线；未发起任何真实/付费模型或媒体生成调用。工程贯通与内容质量收益分开报告。
> 产物目录：`docs/step01-layered-execution/artifacts/s08/`。

## 1. 交付物索引

| 交付 | 路径 |
|---|---|
| 语义稿 vs 执行计划差异 | `artifacts/s08/semantic_execution_diff.md` / `.json` |
| 现行 vs 新责任对照 | `artifacts/s08/shadow/shadow_compare.md` / `.json` |
| 收益指标表 | `artifacts/s08/benefit_metrics.md` / `.json` |
| 未运行记录与真实验证建议 | `artifacts/s08/not_run.md` |
| 可播放样片（S06） | `artifacts/s06/sample.mp4` + `timeline_acceptance.json` |
| 责任与门禁现状（合并后已生效） | `S07-formal-gate-switch-checklist.md` |

## 2. 工程贯通读数（离线代理）

- 本包七个测试文件合并运行 **84 passed**（含 S02 原有 39 条 + 本轮新增 45 条）；
- 基线三族语义检查 `findings=[]`，0 误拒；
- 变异三族六个 `expected.codes` 全部自动检出，漏检 0；
- 受保护语义字段（事实/对白/旁白/声音身份）在候选构造中逐字保留；
- 真实入口证据：Step02a `--plan-only`/真实生成前、Step02b `build_plan`/
  `_submit_video_candidate`、Step03 plan-only/候选实测、Step04 合成前后，
  均有非测试调用点；
- 桩视频响应贯通 candidate 保存、accepted、重启恢复与 `resolve_candidate_inputs`
  消费；恢复不重复生成。

## 3. 内容质量收益

**未验证**。本包没有真实模型产出，也没有人工评阅真实成片：

- 语义检查器只证明六个结构化主张，不证明真实模型能拍出合法时间省略/画外声音；
- S06 样片只证明时间轴/媒体验收与可播放性，不代表真实镜头节奏或画面质量；
- 真实收益需在授权的 Step01/02a/02b/TTS 运行中，用冻结样本与评阅标准对照。

## 4. 未运行记录（摘要）

完整见 `artifacts/s08/not_run.md`。要点：

- Step01 真实生成/审计：未运行；建议限定 OpenCode Go 调用上限与止损；
- Step02a：未运行；先 plan-only 核帧数，再按帧数/重抽上限小范围执行；
- Step02b：未运行；每次付费调用须人工确认，不得用桩结果冒充；
- TTS：未运行；保留旧音频分支，先复用再比较；
- 正式产物采纳：未执行。合并后责任与门禁现状见 S07。

## 5. 复现

```powershell
$env:CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR = (Resolve-Path .pytest_tmp).Path + '\safe_state'
C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe -m pytest `
  tests/shared/test_execution_contract.py tests/shared/test_execution_semantics.py `
  tests/step02a_frame_gen/test_execution_plan.py `
  tests/step02_video_gen/test_execution_requirements.py `
  tests/step03_tts_audio/test_voice_preparation.py `
  tests/step04_merge/test_timeline_acceptance_s06.py `
  tests/integration/test_s07_shadow_and_candidate_revalidation.py -q `
  --basetemp=.pytest_tmp/s08_contract -p no:cacheprovider

C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe `
  tools/build_timeline_sample.py --out .pytest_tmp/s06
C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe `
  tools/shadow_compare_step01_blockers.py --out .pytest_tmp/s07
C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe `
  tools/build_s08_report.py --out docs/step01-layered-execution/artifacts/s08 `
  --sample .pytest_tmp/s06/sample.mp4
```
