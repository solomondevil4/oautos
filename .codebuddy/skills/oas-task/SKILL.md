---
name: oas-task
description: 新建或修改 OAS 单个玩法任务（tasks/<Name>/script_task.py）。当要新增一个玩法、某个玩法跑不通、给玩法加开关参数时使用。触发词：新增任务、玩法、script_task.py、ScriptTask、TaskEnd、任务不跑、任务失败、config_model 挂载。
---

# OAS 玩法任务开发

> 规则见 `.codebuddy/rules/03-task-dev.md`，本 Skill 只给**操作步骤和验证方法**。

## 适用场景

- 新增一个玩法
- 某个玩法跑不通 / 逻辑不对
- 给玩法加开关或参数

## 输入

玩法名（大驼峰） + 需求描述。

## 动手前必须先检查

1. `tasks/<Name>/script_task.py` 是否存在，**类名是否叫 `ScriptTask`**
   —— 这是硬编码约定：`script.py:459` 的 `run()` 用 `load_module` 按目录名拼路径加载，然后 `ScriptTask(config, device)`。
   目录名必须**大驼峰**，入口文件名必须 `script_task.py`，类名必须 `ScriptTask`。
2. 该任务的**父类列表**（决定继承了哪些能力）：
   ```bash
   grep -n "^class ScriptTask" tasks/<Name>/script_task.py
   ```
3. 是否已登记在 `module/config/config_model.py` + `config_menu.py`
4. **结束方式是否 `raise TaskEnd`**（用 `return` 会被判失败，累计 3 次后推送通知并 `exit(1)`）

## 新建任务：10 步

1. 建 `tasks/<大驼峰名>/`
2. 抄骨架，按复杂度选：
   - 最简单：`tasks/ActivityShikigami/script_task.py`（18 行）
   - 教科书级：`tasks/GotoMain/script_task.py`（46 行，含完整异常处理）
   - 标准战斗：`tasks/DyeTrifles/`（102 行）或 `tasks/GoryouRealm/`（97 行）
   - 大任务范例：`tasks/Chess/`（991 行，拆了 9 个 Mixin —— 大任务怎么组织看它）
3. 写 `config.py`，继承 `tasks/Component/config_base.py`
4. **在 `module/config/config_model.py` 挂两处**（🔴 A 级，需人工确认这两行）：
   ```python
   # 顶部 import 区（按分类注释块插入）
   from tasks.<Name>.config import <Name>
   # ConfigModel 类体内
   <snake_name>: <Name> = Field(default_factory=<Name>)
   ```
5. **在 `module/config/config_menu.py` 加菜单项**（🟠 B 级）：对应分类的 list 里加字符串 `'<Name>'`
6. 放素材 PNG 到 `tasks/<Name>/<子目录>/`
7. 写 `image.json` / `ocr.json`
8. 跑 `python dev_tools/assets_extract.py` —— **无参数，会全量重写 86 个 assets.py**
9. 写 `script_task.py`，**结束必须 `raise TaskEnd`**
10. **三处名字核对**：目录名 == `config_model.py` 字段的大驼峰形式 == `config_menu.py` 字符串

## 修改现有任务

- 先 `grep -n "^class ScriptTask"` 看父类，确认要改的方法是自己定义的还是继承来的
- 若继承自 `GeneralBattle` / `SwitchSoul` → 转 `oas-component`，**不要直接改组件**
- 只改必需的那几行（最小修改原则）

## 常见错误

| 错误 | 后果 |
|---|---|
| 用 `return` 结束任务 | 判为失败，3 次后退出程序 |
| 只改 `config_model.py` 忘了 `config_menu.py` | 任务"配了从来不跑" |
| 目录名不是大驼峰 | `load_module` 拼路径失败 |
| `run()` 里写死循环无超时出口 | 全仓库 440 处 `while True`，`DemonEncounter` 一个文件 15 处 |
| 手改 `assets.py` | 被生成器覆盖 |

## 禁止事项

- 不改 `assets.py`
- 不为修这一个玩法去改 `GeneralBattle` / `SwitchSoul`（一次影响 33 / 32 个任务）
- 不改任务的父类继承链（除非明确要新增能力）
- 不动 `module/atom/scatter.py`（半移植文件）

## 完成后如何验证

**静态**（AI 可做）：
```bash
python -c "import tasks.<Name>.script_task"
# 三处名字一致性
grep -rn "<Name>" module/config/config_model.py module/config/config_menu.py
```

**资源校验**（需一张 1280×720 的实机截图）：
```bash
python -c "
from dev_tools.assets_test import detect_image
from tasks.<Name>.assets import <Name>Assets
print(detect_image(r'<截图绝对路径>', <Name>Assets.I_XXX))
"
```

**实机**（AI 做不了，必须交给用户）：
把该任务设为 pending 跑一轮，看 `./log/YYYY-MM-DD_oas1.txt`。

⚠️ **当前环境无运行配置**（`config/` 只有 `template.json`）、**无 `log/` 目录** → 实机验证必须由用户完成，AI 必须明说"未实机验证"。
