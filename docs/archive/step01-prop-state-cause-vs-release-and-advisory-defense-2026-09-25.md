# step01 道具状态：起因帧与写出释放分离 + 清单防线（v6.127，2026-09-25）

本轮依据用户 2026-09-25 复核意见修订（前一轮见
[`step01-prop-state-timing-and-readonly-diff-2026-09-25.md`](step01-prop-state-timing-and-readonly-diff-2026-09-25.md)，
其中的 `resolve_release_at()` 与 `may_clear_following_props` 已按本轮决定改动）。

用户原文要点：

> 还不能把 `release_at=(6, "kf1")` 当作已定位的释放帧。代码目前取的是最早的**起因帧**；
> 原文相隔 2 句只证明"释放在起因之后"，不证明同帧。2050 在 shot7 写出的释放可能是正确
> 落点，尽管 shot4 的提前释放仍是错误；2106 没写释放，更无法从起因帧推出精确落点。
> 1. **不移除**……`may_clear_following_props` 的名字与当前含义冲突，建议从授权口径中移除；
>    `clear_candidates` 也只能是只读建议，不能充当执行许可。
> 2. 窗口暂留 3 句，但**仅作诊断筛选**。gap=2 和 gap=12 还不足以证明"≤3 就是同一因果事件"，
>    更不能据此确定释放帧。接生产前需跨章节验证，或改用明确的事件关联。
> 3. 帧文本匹配暂留诊断用；生产接线需要事件关联。分别记录 `cause_at` 与实际写出的
>    `release_at`；稿件没写释放时，后者保持未知，走重生／拒绝候选路径，不生成清除授权。
> 还有一处必须先补的防线：`precise_state_diff()` 的阻止分支没有覆盖"只在起因之后、但晚于
> 指定点才写释放"的状态，因此可能把真正释放前的持有列进 `clear_candidates`。建议先加这个
> 反例回归，并把"起因帧≠释放帧"的断言钉住；在此之前，继续保持不接选稿门、质量门和自动修复。

---

## 一、改了什么

| 项 | 改动 |
|---|---|
| **起因帧 ≠ 释放帧** | `resolve_release_at()` → **`locate_release()`**：`cause_at`（起因帧，需落在唯一镜头内且原文"先因后果"）与 `release_at`（稿件**实际写出**的释放，只看稿件、不做任何从起因到释放的推导）**分别记录**。稿件没写释放 → `release_known=False`、`release_at=None`，理由点明走**重生／拒绝候选**。 |
| **状态口径** | `release_verification()` 的状态改为相对**起因帧**的中性表述：`missing` / `written_timing_unknown`（无可用起因帧）/ `written_before_cause` / `written_at_cause_frame`（同帧 ≠ 同一事件，不作为通过）/ `written_after_cause`。参数由 `release_at` 改为 `cause_at` + 可选的 `reference`。 |
| **移除执行许可** | 删除 `may_clear_following_props`（用户：名实不符，从授权口径移除）。`still_holding_at_release_frame` → `still_holding_at_reference`。 |
| **清单防线** | `precise_state_diff()` 新增阻断条件：调用方显式给的 `release_at` **不等于稿件里最早写出的释放事件**时，`clear_candidates` 一律为空 + 原因（点名两个位置）。另加：释放落在起因帧本身 → 不给清单。`clear_candidates` 增 `advisory_only=True`，与 `apply=False` 一起表明**只读建议、不是执行许可**。 |
| **证据口径** | `residuals` / `descriptions_still_holding` 的基准点恒取**稿件里最早写出的释放**（`reference_kind=earliest_written_release`；无释放事件时退到 `cause_at`，标 `cause_at_release_missing`，只作证据）。 |
| **诊断口径注释** | `_CAUSE_WINDOW_SENTENCES=3` 标注为**仅诊断筛选**；模块头补「v6.127 之后仍未做的接线前提」（事件关联取代措辞匹配、跨章节验证、未接选稿门/质量门/自动修复）。 |
| `tools/diag_prop_anchor_generation.py` | 改用 `locate_release()`，报告分开输出 `cause_at` / `cause_usable` / `release_written_at` / `release_known`，删除 `may_clear_following_props`，`diff` 增 `advisory_only`。 |
| 测试 | 本模块 54 → **55** 条：新增「**起因帧≠释放帧**」断言、**防线反例回归**、同帧释放不给清单；旧断言（`may_clear_following_props`、`release_at=` 参数、旧状态名）全部改到新口径。 |

## 二、为什么（根因与证据）

1. **从起因帧推释放帧是错的推导**。原文相隔 2 句只给出"释放在起因之后"这一条顺序约束。
   诊断稿 `2050` 实测：起因帧 `(6,'kf1')`，稿件**实际写出**释放是在 `(7,'kf1')／(7,'lf')`
   —— 两者不同；`2106` 干脆没写释放，更无从推。上一轮把 `cause_at` 当 `release_at`
   报出去，是对外结论的过度声称。
2. **防线缺失会给出错误清单**。旧阻断分支只覆盖"提前/缺失/再次拾取"，没有覆盖
   "指定点早于真正写出的释放位置"：此时两点之间的帧仍被当作"释放之后"，会被列进
   `clear_candidates`——而它们其实是**释放之前**的合法持有。这是本轮先补的回归。
3. **`may_clear_following_props` 名实不符**：名字是"可以清后续 `props`"，实现要求
   "没有后续残留"，含义相反；任何一方按字面理解都会误用。按用户决定直接移除，
   改由只读建议字段承担（且不能充当执行许可）。

## 三、实跑读数（冻结稿 + 合成夹具，非估计）

| 项 | 读数 |
|---|---|
| 2050 钓杆 | `cause_at=(6,'kf1')`（`cause_usable=True`）；`release_written_at=**(7,'kf1')**` ≠ `cause_at`（本轮要钉住的那条） |
| | 状态 `written_before_cause`（提前事件 `(4,'ff')`）；基准点 `(4,'ff')`（最早写出的释放） |
| | `residuals=13`、`premature_residuals=13`、`descriptions_still_holding=3`、`clear_candidates=0`；原因=存在提前释放 |
| 2050 灯盏 | `cause_usable=False`（释放句与起因相隔 **12** 句 > 3，仅诊断筛选）；`release_written_at=(9,'kf1')` |
| | 状态 `written_timing_unknown`；`residuals=0`（9ff/9kf0 的**释放前持有不误报**）；`clear_candidates=0` |
| 2106 钓杆 | `cause_at=(6,'kf1')` 可用；`release_known=False`、`release_at=None`（**释放缺失 → 重生/拒绝候选**） |
| | 状态 `missing`；基准点 `(6,'kf1')`（`cause_at_release_missing`）；`residuals=29`；`clear_candidates=0` |
| 2106 灯盏 / 酒囊 | 分别因"无释放 + 起因窗口外"与"稿件无该道具记录"保持未知；`clear_candidates=0` |
| 防线反例 | 指定点 `(2,'ff')`（起因帧）而真正写出释放在 `(3,'kf0')` → `clear_candidates=[]`，原因点名两处；同一稿件**按真正写出的位置**作基准时清单恰为 1 条（证明不是一刀切阻断） |

## 四、影响面

- **仍不接** BEST-GUARD、**不接**质量门、**不自动改/删 `props`**（`apply=False`、
  `advisory_only=True`）；未增加审计次数、修复轮数、联动组上限。
- `may_clear_following_props` 字段**移除**（离线模块，无生产调用方；诊断工具同步更新）。
- 不动已接线的 `cross_field_checks._RELEASE_MARKERS`（离线多认的写法仍在 `_EXTRA_RELEASE_MARKERS`）。
- 不写盘、不发模型调用。

## 五、验证 / 回退

- 实跑：`pytest -q` → **2278 passed, 1 skipped**；本模块 **55** 条（含 3 条冻结稿验收线，
  在本机**实跑通过**而非 skip）。
- 回退：还原 `locate_release`/`release_verification`/`precise_state_diff` 三处为上一轮实现
  （`resolve_release_at` + `may_clear_following_props`），并同步回退诊断工具与测试；
  生产路径不受影响。

## 六、仍未做（用户已定，等接线前提）

1. **事件关联**：大纲节拍里的原文事件需要**可核对标识**再关联到稿件镜头；本轮仍是
   **帧文本匹配 + 句距窗口（≤3）**，且明确只作诊断。
2. **跨章节验证**因果窗口（或直接采用事件关联），不靠单章 gap=2 / gap=12 两个样本。
3. 选稿门（BEST-GUARD）、质量门、自动修复事务（需"可回滚的确定性事务"）。
