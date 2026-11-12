# S08：未运行记录与真实验证建议

本包全程离线，以下真实调用**均未运行**：

| 项目 | 未运行原因 | 真实验证建议 |
|---|---|---|
| Step01 真实生成/审计 | 需 OpenCode Go 授权与调用上限 | 先冻结语义稿/审计输入，限定调用上限与止损；逐 run_id 核对新稿、BEST-GUARD 选稿与提交 |
| Step02a 图片生成 | 付费媒体生成需用户单独授权 | 先 plan-only 核帧数，再按明确帧数与重抽上限小范围执行 |
| Step02b 视频生成 | 每次付费调用须人工确认 | 逐镜列明模型/时长/费用，一次确认一次提交；不得用本包桩结果冒充 |
| TTS 真实合成 | 保留旧音频分支，未获授权 | 先复用旧音频与身份；新分支实测时长后再比较 |
| 正式门禁切换 | 只产出清单，需用户裁决 | 按 S07 清单逐项确认严重度/责任/回退 |
| 正式产物采纳 | 本包不采纳 | 切换批准后另走正式提交与回读 |

**可复现离线命令**：

```powershell
C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe -m pytest tests/shared/test_execution_contract.py tests/shared/test_execution_semantics.py tests/step02a_frame_gen/test_execution_plan.py tests/step02_video_gen/test_execution_requirements.py tests/step03_tts_audio/test_voice_preparation.py tests/step04_merge/test_timeline_acceptance_s06.py tests/integration/test_s07_shadow_and_candidate_revalidation.py -q --basetemp=.pytest_tmp/s08_contract -p no:cacheprovider
C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe tools/build_timeline_sample.py --out .pytest_tmp/s06
C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe tools/shadow_compare_step01_blockers.py --out .pytest_tmp/s07
```
