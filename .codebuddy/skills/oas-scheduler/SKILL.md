---
name: oas-scheduler
description: OAS 任务排队、定时、防风控与空闲策略。当任务不跑、跑的顺序不对、时间不对、想改任务间隔规则、或涉及防风控时使用。触发词：调度、不跑、排队、pending、waiting、间隔、下次运行、set_next_run、task_delay、FIFO、优先级、防风控、anti_ban、睡眠、runtime_controller、task_hoarding。
---

# OAS 调度系统

## 适用场景

- 任务不跑 / 跑的顺序不对 / 时间不对
- 想改任务的间隔规则
- 防风控相关

## 输入

症状 + 涉及的任务名。

## 必须先理解的机制

1. **双队列**：`module/config/config.py` 的 `update_scheduler()` 把任务分成
   - `pending`（已到期）
   - `waiting`（未到期）
2. **三种排序**（`module/config/scheduler.py`）：FIFO（先到先得）、PRIORITY（按优先级）、FILTER
3. **下次运行时间**：`task_delay()` / 任务侧 `set_next_run()`，支持成功间隔、失败间隔、服务器更新时间（含随机浮动）
4. **失败不在原地重试，而是"再排一次队"** —— 这是 OAS 的核心设计

## 关键位置

| 组件 | 文件 | 行 |
|---|---|---|
| 主循环（`while 1` 永不退出） | `script.py` | 479 `loop()` |
| 取下一个任务 | `script.py` | 360 `get_next_task()` |
| 执行单个任务 | `script.py` | 459 `run(command)` |
| 异常处理总闸 | `script.py` | 615 `_handle_task_exception()` |
| 运行前准备 / 空闲策略 | `module/script/runtime_controller.py` | 570 行 |
| 防风控 | `module/config/anti_ban.py` | — |

## 操作步骤

1. 先判断是**"没到期"**还是**"没进队"**还是**"排序靠后"** —— 这三者症状一样，原因完全不同
2. 运行前检查：`runtime_controller.py` 决定空闲时是回主界面、关游戏还是关模拟器
3. 防风控：`anti_ban.py` 管睡眠时段、每日活跃时长上限、强制长休息
4. 任务攒批 `task_hoarding`：空闲时把到期任务攒几分钟一起跑

## 常见错误

| 症状 | 真实原因 |
|---|---|
| 以为失败会在原地重试 | 实际是**重新排队**，按 `failure_interval` 再排期 |
| 改了间隔却没生效 | 可能是 `anti_ban` 的睡眠窗在起作用 |
| 混淆两种间隔 | "成功间隔"和"失败间隔"是两个字段 |
| 程序自己退出了 | 同一任务连续失败 **3 次** → 推送通知 + `exit(1)` |

## 禁止事项

- 改 `loop()` 的 `while 1` 结构
- 改 `anti_ban.py` 的默认值（防封号的关键参数）
- 动 `runtime_controller.py` 的关游戏 / 关模拟器策略（实测调过的）
- 为了"让任务立刻跑"去绕过防风控

## 完成后如何验证

- 看日志里 `pending` / `waiting` 队列内容
- 确认 `set_next_run()` 算出的时间符合预期
- **实机**观察至少 2 轮调度

⚠️ 当前环境无 `log/` 目录 → 无法查看队列日志，AI 必须明说"未实机验证"。
