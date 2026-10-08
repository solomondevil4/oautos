---
name: oas-config
description: OAS 配置系统读写与新增字段。当要给任务加开关参数、配置不生效、配置丢失或被覆盖时使用。触发词：config、配置、config_model、config_menu、template.json、字段不生效、pydantic、Scheduler、赋值即写盘、gui_set_task。
---

# OAS 配置系统

> 规则见 `.codebuddy/rules/05-config.md`。

## 适用场景

- 给任务加一个开关 / 参数
- 配置不生效
- 配置丢失 / 被覆盖

## 输入

任务名 + 字段名 + 期望行为。

## 必须先记住的三条（最容易踩）

1. **`config_model.py:201` 的 `__setattr__` 让任何字段赋值立刻写盘**
   → 在循环里改配置字段 = 疯狂写磁盘
2. **每轮任务结束后 `del_cached_property(self, 'config')`** 强制重新读盘
   → 界面改设置**最多下一轮生效**，不是立即生效
3. **配置文件不存在时返回 `{}` 并全部走默认值**（`module/config/utils.py:48-49`）
   → 所以"不报错"不代表配置对

## 权限分级（先确认要动哪一级）

| 操作 | 级别 |
|---|---|
| 改 `tasks/<任务>/config.py`（加字段） | 🟡 C |
| 改 `module/config/config_menu.py`（菜单） | 🟠 B |
| 改 `module/config/config_model.py`（登记） | 🔴 A |

> 给已有任务加字段通常只碰 C 级；**只有首次登记任务**才需要碰 A/B。

## 操作步骤

1. 在 `tasks/<任务>/config.py` 加字段（pydantic，继承 `tasks/Component/config_base.py`）
2. 若任务未登记 → 同步改 `config_model.py`（🔴 A）+ `config_menu.py`（🟠 B）
3. 代码里用 `self.config.<snake_task>.<字段>` 读
4. **写字段时注意**：赋值即写盘，别放循环里
5. 交互改参数走 `gui_set_task()` 会立即存盘

## 常见错误

| 症状 | 根因 |
|---|---|
| 字段读不到 | 只改了任务的 `config.py`，没在 `config_model.py` 挂 |
| 字段名拼错不报错 | pydantic 静默忽略或用默认值（经验库 `d1076e21`） |
| 磁盘 IO 暴涨 | 在循环里改配置 |
| 改了配置没立刻生效 | 要下一轮才生效 |
| 任务"配了从来不跑" | 三处名字不一致（目录 / `config_model` 字段 / `config_menu` 字符串） |

## 禁止事项

- 改 `config_model.py` 的 `__setattr__` 自动写盘机制（GUI 实时改参依赖它）
- 改 `config/template.json` 的已有结构（会影响所有用户的配置迁移）
- 手写 `config/*.json`（用户配置不进 git）
- 在循环里写配置字段

## 完成后如何验证

**静态**：
```bash
python -c "from module.config.config_model import ConfigModel; c=ConfigModel(); print(c.<task>.<field>)"
# 确认字段名的"大驼峰形式" == 任务目录名
grep -rn "<Name>" module/config/config_model.py module/config/config_menu.py
```

**实机**：GUI 里能看到该字段、改了能存住、下一轮生效。AI 无法实机 → 明说。
