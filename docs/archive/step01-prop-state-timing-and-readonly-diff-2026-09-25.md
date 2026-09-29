# step01 道具状态：时点定位 + 只读差异报告（v6.126，2026-09-25）

> ⚠️ **本文件已被 v6.127 修订**：用户复核后决定「起因帧 ≠ 释放帧」，故文件里的
> `resolve_release_at()`（把起因帧当释放帧）与 `may_clear_following_props`（从授权
> 口径移除）**都已改动**。最新口径见
> [`step01-prop-state-cause-vs-release-and-advisory-defense-2026-09-25.md`](step01-prop-state-cause-vs-release-and-advisory-defense-2026-09-25.md)。

用户 2026-09-25 的设计（原文摘要）：

> 建议转向确定性道具状态校验，但**先不要让模型再输出一套"每帧道具状态"**。现有
> `frame_layouts.*[].props` 已是逐帧持有记录；再造一份独立字段，会产生两个互相矛盾的来源。
> 缺的是一条可信的状态转移：什么道具、由谁持有、在哪一帧因什么事件离手。
> 1. **时点先定位，再判连续性。**……只有道具身份、起因、先后顺序和落点都能唯一核对时，
>    才得到 `release_at`。对不上就记"时点未知"，不猜 shot4，也不清 `props`。
> 2. **授权精确到叶子，但暂不自动改。**……先做只读差异报告。
> 3. **用冻结稿设验收线。** `2050` 必须识别"刺入前提前释放＋后续残留"；`2106` 必须识别
>    "释放缺失"，不能靠删布局过关；灯盏释放前的持有不得误报。再补"再次拾取、多次释放、
>    镜头列表乱序"反例。当前 `release_verification()` 只取找到的第一处释放，接生产门前还需
>    改为检查完整事件序列。

---

## 一、改了什么

| 文件 | 改动 |
|---|---|
| `step01_script_gen/prop_state_anchor.py` | **新增 v6.126 一节**（起因配对 / 时点定位 / 只读差异报告）：`cause_candidates()`、`cause_frames()`、`pair_release_with_cause()`、`resolve_release_at()`、`release_event_sequence()`、`pickup_or_transfer_frames()`、`precise_state_diff()`、`_exact_props_path()`、`_desc_field_path()`、`_holding_clause()`；常量 `_CAUSE_MARKERS`、`_CAUSE_WINDOW_SENTENCES=3`、`_PICKUP_MARKERS`、`_TRANSFER_MARKERS`、`_EXTRA_RELEASE_MARKERS`/`_ALL_RELEASE_MARKERS`。**改写** `release_verification()`（完整事件序列 + 四态增 `written_before_expected_point`）、`missing_release_issue()`（**不再要求"仍在持有"才报**）、`holding_frames()`/`release_frames()`（按道具**全部写法**匹配、按 `index` 排序）、`_frame_order()`（抽出 `_shots_in_order()`）。 |
| `tools/diag_prop_anchor_generation.py` | 每个候选锚点增报 `timing`（四条件与原因）、`release_events`（含 relation）、`diff`（只读差异报告）；串名扫描与帧序统计改用 `_ALL_RELEASE_MARKERS`（不再自己维护一份词表）。 |
| `tests/step01_script_gen/test_prop_state_anchor.py` | 40 → **54** 条：起因配对（源文真实验证）、四条件门、**冻结稿 2050/2106/灯盏验收线**、反例（多次释放 / 再次拾取 / 镜头列表乱序 / 删光 props 仍须报缺失）、只读差异报告（精确叶子 + 描述 + 授权面）。 |
| 顺手（小调整） | 删掉 `prop_state_anchor._order()`（本轮改造后已无任何引用，且工具用的是 `_frame_order`）——死代码，无行为影响。 |

## 二、为什么（根因与证据）

1. **"写了释放"曾被当成"写对了"。** `missing_release_issue` 只问缺没缺；提前释放（诊断稿
   `2050` 把「钓杆已脱手」写在中刃之前）在它下面同样是 `None`。→ 时点必须**另行定位**。
2. **只看第一处释放会丢事件。** `2050` 的钓杆有**两次**释放：shot4 ff（提前）与 shot7 kf1/lf
   （起因之后，「钓杆已滑入水中」）。旧实现取第一处 → 后一次整条丢掉。
3. **删布局曾是过关手段。** 旧 `missing_release_issue` 里 `if not holding: return None`：把
   `props` 全删光就不报了。`2106` 正是"原文有释放、稿件一处未写、布局铺了 42 帧持有"的形态
   ——叙述层欠账与布局里还有没有这条 `props` 无关。
4. **离线词表比已接线判据窄。** `cross_field_checks._RELEASE_MARKERS` 有「滑落」没有「滑入」、
   有「丢了」没有「丢下」→ `2050` 的 shot7「滑入水中」被当成"仍在持有"的描述，
   shot9「丢下灯盏」被当成"没写释放"（误报 `missing`）。
5. **起因不能一概而论。** 第 1 章里「钓杆被鱼拖走」紧跟中刃（相隔 2 句），而「张十三丢下灯盏」
   离中刃 12 句——若把"起因帧 = 释放点"套到灯盏上，释放点会被错记到刺入那一镜，
   于是 9ff/9kf0 的合法持有会被误报成"释放之后仍持有"。

## 三、验收读数（冻结稿实跑，非人工估计）

| 项 | 读数 |
|---|---|
| 原文起因 | 唯一一处：`刺进`（句 46）；`pair_release_with_cause(钓杆候选)` → `release_after_cause`，gap=2 ≤ 窗口 3 |
| **2050 钓杆** | `resolve_release_at` → `known=True`，`release_at=(6,'kf1')`（= 刺入帧） |
| | 事件序列 `[(4,'ff') 提前, (7,'kf1') 之后, (7,'lf') 之后]`；状态 `written_before_expected_point` |
| | `premature_residuals` **13** 条（4kf0→7kf0，精确到 `frame_layouts.<帧>.<条目>.props`） |
| | `descriptions_still_holding` = 7ff/7kf0；`clear_candidates=[]`；`blocked_reason`= 存在提前释放 |
| **2106 钓杆** | 起因帧 (6kf1, 6lf) → **同一镜头**，判为唯一落点 (6,'kf1')；状态 `missing` |
| | `still_holding_after` = **29** 条；`clear_candidates=[]`；`blocked_reason`= 稿件未写释放事件（缺失） |
| **2050 灯盏** | `known=False`（释放句与起因相隔 **12** 句 > 3，超出因果窗口）；`still_holding_after=[]`（9ff/9kf0 的持有**不误报**）；`clear_candidates=[]` |
| **酒囊（串名）** | `known=False`：「稿件里没有这件道具的任何记录」；诊断工具另报"稿里释放的是 `['酒壶']`" |

## 四、影响面

- **不接** BEST-GUARD、**不接**质量门、**不自动改/删 `props`**（`precise_state_diff` 恒
  `apply=False`，`read_only=True`）；未增加审计次数、修复轮数、联动组上限。
- **不动**已接线的 `_RELEASE_MARKERS`（往它加词会改变 `prop_possession_consistency` 的生产
  命中集，属判据口径变更）：离线多认的写法收在 `_EXTRA_RELEASE_MARKERS`，只影响本模块与诊断工具。
- 无生产路径依赖本模块（仍未接线）；不写盘、不发模型调用。

## 五、验证 / 回退

- 实跑：`pytest -q` → **2277 passed, 1 skipped**；本模块测试 **40 → 54** 条。
- 冻结稿验收线（2050 / 2106 / 灯盏）在本机**实跑通过**（非 skip）。
- 回退：删除 `prop_state_anchor.py` 的 v6.126 一节、还原 `release_verification` /
  `missing_release_issue` / `holding_frames` 的上一版实现、`git checkout`（本仓库未提交时
  按人工 diff 还原）诊断工具与测试文件即可；生产行为不受影响。

## 六、待用户定夺（本轮**未**自行决定）

1. **授权面口径**：`may_clear_following_props` 沿用 v6.125.3 的保守口径（要求"无残留"），
   而残留正是要清的对象；`clear_candidates` 则在时点核对后给出具体清单。两者口径不一致是
   **有意保留**的，属授权面问题——是否统一、统一成哪一种，需你定。
2. **因果窗口数值** `_CAUSE_WINDOW_SENTENCES=3`：本数据（gap=2 正例 / gap=12 反例）可分，
   但它是**判据数值**，是否调整需你定。
3. **起因定位方式**：离线用**帧文本匹配**找起因帧；你原文里的"用大纲节拍关联到镜头、帧"
   属生产接线步骤，本轮**未做**。
4. 仍未接线的下游：选稿门（BEST-GUARD）、质量门、自动修复事务（你要求"若以后批准自动修复，
   应是一次可回滚的确定性事务，而不是提高现有 6 镜 LLM 联动组上限"）。
