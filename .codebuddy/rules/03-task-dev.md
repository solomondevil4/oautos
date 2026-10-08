---
description: 新建或修改一个 OAS 玩法任务的规范。含硬性命名约定、骨架选择和登记步骤。
globs: ["tasks/**/*.py"]
alwaysApply: false
---

# Task 开发规范

## 四条硬约定（调度器按此加载，错一条就跑不起来）

`script.py:459` 的 `run()` 是这样加载任务的：

```python
module_path = str(Path.cwd() / "tasks" / command / "script_task.py")
task_module = load_module("script_task", module_path)
task_module.ScriptTask(config=self.config, device=self.device).run()
```

所以：

1. 目录名必须是**大驼峰**（如 `Duel`）
2. 入口文件必须叫 **`script_task.py`**
3. 类必须叫 **`ScriptTask`**
4. 结束必须 **`raise TaskEnd`**

## 骨架怎么选

| 场景 | 抄哪个 | 行数 |
|---|---|---|
| 极简/只要跑个子模块 | `tasks/ActivityShikigami/script_task.py` | 18 |
| **教科书级（推荐先看）** | `tasks/GotoMain/script_task.py` | 46 |
| 标准战斗玩法 | `tasks/DyeTrifles/` 或 `tasks/GoryouRealm/` | 102 / 97 |
| 大型复杂玩法 | `tasks/Chess/`——拆成 9 个 Mixin，是全项目最好的大任务组织范例 | 991 |

62 个任务的规模分布：最大 `AutoCheckinBigGod` 1221 行、最小 `ActivityShikigami` 18 行。

## 新建任务 10 步

1. 建 `tasks/<大驼峰名>/`
2. 抄骨架
3. 写 `config.py`，继承 `tasks/Component/config_base.py`
4. 在 `module/config/config_model.py` 挂**两处**（🔴 **A 级**，需人工确认）：
   ```python
   # 顶部 import 区，按分类注释块插入
   from tasks.<Name>.config import <Name>
   # ConfigModel 类体内
   <snake_name>: <Name> = Field(default_factory=<Name>)
   ```
5. 在 `module/config/config_menu.py` 对应分类的 list 里加 `'<Name>'`（🟠 B 级）
6. 放素材图到 `tasks/<名>/<子目录>/`
7. 写 `image.json` / `ocr.json`
8. 跑 `python dev_tools/assets_extract.py`（**无参数，会重写全仓库 86 个 assets.py**）
9. 写 `script_task.py`，结束 `raise TaskEnd`
10. **三处名字核对**：目录名 == `config_model.py` 字段名的大驼峰 == `config_menu.py` 字符串

## 继承能力靠"混搭"，不是靠 import

任务类通过多继承拼装能力，例如斗技：

```python
class ScriptTask(GameUi, GeneralBattle, SwitchSoul, DuelAssets, SwitchOnmyoji):
```

**不要随意增删已有任务的父类继承链**——每加一个父类就引入一整套行为。

## 修某个玩法时

- **优先改这个玩法自己的代码**
- **不要去改 `GeneralBattle`（33 个任务继承）/ `SwitchSoul`（32 个）**
- 确实需要特殊行为 → 在该玩法里**重写（override）**方法，而不是在组件里加 `if task == 'X'`

## 常见错误

| 错误 | 后果 |
|---|---|
| 用 `return` 结束 | 判失败，3 次后推送通知并 `exit(1)` |
| 只改 `config_model.py` 忘了 `config_menu.py` | 任务"配了从来不跑" |
| 目录名不是大驼峰 | `load_module` 拼路径失败 |
| 新增 `while True` 无出口 | 卡死 |
| 手改 `assets.py` | 下次生成被覆盖 |

## `_exit_matcher()` 不是重复代码，不要合并

9 个任务各自实现了 `_exit_matcher()`，看着像复制粘贴，但**逐个读过**：每个只返回自己的一张标志图（`AreaBoss`→`I_AB_CLOSE_RED`、`Secret`→`I_SE_FIRE`、`Orochi`→`any_of(3张)`）。

**这是钩子模式，是正确设计。不要抽象它。**

真正重复的（可考虑抽象）是 Orochi / EternitySea / EvoZone / FallenSun 四个组队副本的 `run_leader` / `run_member` / `run_alone` / `is_room_dead` / `check_layer`。

但**抽象属 B 级操作且属重构范畴**：必须先经人工批准，再一个任务一个任务地抽，抽完立刻实测那个玩法。不批准的就不做。
