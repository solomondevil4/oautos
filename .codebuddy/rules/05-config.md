---
description: OAS 配置系统规范：字段登记、自动写盘、生效时机，以及任务排队与防风控。
globs: ["module/config/**/*.py", "tasks/**/config.py", "config/**"]
alwaysApply: false
---

# 配置规范

## 本文件涉及的修改权限

| 文件 | 级别 |
|---|---|
| `module/config/config.py`、`config_model.py` | 🔴 **A 级**——禁止自主修改 |
| `module/config/config_menu.py`、`anti_ban.py`、`scheduler.py` | 🟠 **B 级**——改前列调用关系 |
| `tasks/<任务>/config.py` | 🟡 C 级——可改 |

**"给任务加一个字段"通常只需改 🟡 C 级的 `tasks/<任务>/config.py`；只有在任务尚未登记时才需要碰 A/B 级文件。**

## ⚠️ 赋值即写盘

`module/config/config_model.py:201` 的 `__setattr__` 让**任何配置字段被赋值就立刻触发一次磁盘写入**。

```python
# ❌ 循环内赋值 = 疯狂写磁盘
while True:
    self.config.xxx.yyy = value

# ✅ 循环外改，或先存局部变量
```

**这不是 bug，不要"优化"掉它。** 桌面 GUI 实时改参数的功能依赖这个行为。改成显式 `save()` 会让 GUI 改设置失效。

## 配置文件在哪

- 运行时配置：`./config/<配置名>.json`（默认 `oas1.json`），**不进 git**
- 模板：`config/template.json`
- **文件不存在时返回 `{}` 并全部用默认值**（`module/config/utils.py:48`）——所以"不报错"不代表配置是对的

## 新增字段三步

1. 在 `tasks/<任务>/config.py` 加字段（pydantic，继承 `tasks/Component/config_base.py`）
2. 若任务未登记，同步改 `module/config/config_model.py`（import + 类字段）和 `module/config/config_menu.py`（菜单字符串）
3. 代码里用 `self.config.<snake_task>.<字段>` 读

## 三处命名必须一致

```
tasks/Duel/              ← 目录名（大驼峰）
config_model.py 字段     ← duel: Duel = Field(default_factory=Duel)
config_menu.py 字符串    ← 'Duel'
```

**漏任何一处，任务就"配了从来不跑"，而且没有任何报错。** 字段名拼错也一样——pydantic 会静默用默认值（历史教训：`d1076e21`）。

## 改了配置不等于立刻生效

每轮任务结束后 `del_cached_property(self,'config')` 强制重新读盘，所以**界面改设置最多下一轮生效**。

交互改参数走 `gui_set_task()` 会立即存盘。

## 任务排队与调度（也在 `module/config/` 下）

- **双队列**：`config.py` 的 `update_scheduler()` 把任务分成 `pending`（已到期）和 `waiting`（未到期）
- **三种排序**（`scheduler.py`）：FIFO（先到先得）、PRIORITY（按优先级）、FILTER
- **下次运行时间**：`task_delay()` / 任务侧 `set_next_run()`，支持成功间隔、失败间隔、服务器更新时间（含随机浮动）
- **失败不在原地重试，而是"再排一次队"**——这是核心设计，别写成原地重试
- 连续失败 **3 次** → 推送通知 + `exit(1)`
- 防风控 `anti_ban.py`：睡眠时段、每日活跃时长上限、强制长休息。**默认值不要动**，这是防封号的关键参数
- 空闲策略 `module/script/runtime_controller.py`（570 行）：决定空闲时回主界面 / 关游戏 / 关模拟器

## 常见错误

| 现象 | 常见原因 |
|---|---|
| 字段读不到 | 只改了任务的 `config.py`，没在 `config_model.py` 挂 |
| 任务配了不跑 | 三处命名不一致 |
| 改了间隔不生效 | `anti_ban` 的睡眠窗在起作用 |
| 程序莫名退出 | 连续失败 3 次触发了 `exit(1)` |

## 禁止事项

- 改 `config_model.py` 的 `__setattr__` 自动写盘机制
- 改 `config/template.json` 已有结构（影响所有人的配置迁移）
- 手写 `config/*.json`（用户配置不进 git）
- 改 `anti_ban.py` 的默认值
- 改 `loop()` 的 `while 1` 结构
