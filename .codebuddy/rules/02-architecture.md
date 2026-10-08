---
description: OAS 分层架构、模块职责与 A/B/C/D 修改权限分级。动手前先定级。
globs: ["**/*.py"]
alwaysApply: false
---

# OAS 架构规范

## 两层结构

| 层 | 目录 | 是什么 |
|---|---|---|
| **通用机器** | `module/`（226 文件） | 跟阴阳师无关：截图、识别、配置、调度、设备 |
| **游戏知识** | `tasks/`（62 个任务 + 公共组件） | 具体玩法怎么点 |

再细分：

- `tasks/Component/`（22 个）——玩法之间复用的零件
- `tasks/GameUi/`（143 文件）——**页面导航系统**，把界面抽象成"页面+跳转路径"，Dijkstra 自动寻路
- `tasks/<某玩法>/`——单个玩法，含 `script_task.py`（入口）、`config.py`（参数）、`assets.py`（资源，**自动生成**）

## 三个入口（殊途同归）

`gui.py`（桌面窗口，PySide6+QML）、`server.py`（网页后台，FastAPI，端口 22270）、`script.py`（命令行）。

三者最终都执行 `Script(config_name).loop()`。

> ⚠️ **`script.py` 硬编码 `Script("oas1").loop()`，没有单任务命令行参数。** 无法直接单跑一个任务。

## 修改权限分级（查到哪一级就按哪一级的规矩办）

| 级别 | 范围 | 规则 |
|---|---|---|
| 🔴 **A** | `module/atom/*`、`module/device/*`、`script.py`、`tasks/base_task.py`、`module/exception.py`、`module/config/config.py`、`module/config/config_model.py`、`module/atom/scatter.py` | **禁止 AI 自主修改**，必须人工逐行确认 |
| 🟠 **B** | `tasks/Component/{GeneralBattle,SwitchSoul,GeneralInvite,GeneralRoom,Buy}/*`、`tasks/GameUi/*`、`module/logger.py`、`module/config/{anti_ban,scheduler,config_menu}.py`、`module/{image,ocr}/rpc.py` | **改前必须列出调用关系清单** |
| 🟡 **C** | `tasks/<玩法>/script_task.py`、`config.py` | 可改，遵守三条硬约束 |
| 🟢 **D** | `tasks/GotoMain/`、`tasks/ActivityShikigami/`、`tasks/DyeTrifles/`、`tasks/GoryouRealm/`、`tasks/Component/Costume/`、`tasks/FrogBoss/` | 最安全 |
| ⛔ **禁** | `tasks/*/assets.py`（86 个）、`fluentui/`、`bin/` | **禁止编辑** |

## 引用面实测数据（用来判断爆炸半径）

| 符号 | 被引用文件数 |
|---|---|
| `module.logger` | **218** |
| `module.atom.image` | 146 |
| `module.atom.ocr` | 112 |
| `module.atom.click` | 107 |
| `module.exception` | 98 |
| `module.config.config` | 96 |
| `module.atom.long_click` / `swipe` / `list` | 各 90 |
| `module.device.device` | 89 |
| `module.base.timer` | 68 |
| `tasks.Component.config_base` | 79 |
| `tasks.GameUi.page` | 75 |
| `tasks.GameUi.game_ui` | 63（**49 个任务继承**） |
| `tasks.Component.config_scheduler` | 62 |
| `tasks.base_task` | 30 |

继承次数：`GameUi` **49**、`GeneralBattle` **33**、`SwitchSoul` **32**、`GeneralInvite` 13、`GeneralRoom` 11。

## 识别是远程服务，不在脚本里算

- 图像服务 `127.0.0.1:22269`（`module/image/rpc.py` + `runtime.py`）
- OCR 服务 `127.0.0.1:22268`（`module/ocr/rpc.py`，底层 ppocronnx）
- 由入口进程 spawn，受 `deploy/config.py` 的 `StartImageServer` / `ImageClientAddress` / `OcrClientAddress` 控制
- **没有本地兜底**：连不上直接抛 `ScriptError`，脚本跑不了

## 不要碰 `module/atom/scatter.py`

它开头自己写着：移植自 `self` 分支 `1a9a1f61`，**配套改造（上游 `24010e18`）尚未合入本分支**，这里是"最小适配"。

已核实：**Hurry2 远端只有 `Forme` 和 `ui_update` 两个分支，`self` 分支不存在**，上游正确版本拿不到。

**不要"完善"它、不要重构它。** 它现在能跑，动它必炸。

## 状态怎么传（改代码前必须理解）

- `config` 和 `device` 都是**全进程单例**，任务通过 `self.config` / `self.device` 直接用，不靠参数传
- **任务与任务之间不能传内存数据**，只能改配置文件或用 `task_call()` 间接触发
- 每轮任务结束后 `del_cached_property(self,'config')` 强制重新读盘

## 已知未解事项（不要当成既有设计去"保护"）

- `tasks/GameUi/registry.py:82` 写的是 `for module_name in ("page")`——**字符串不是元组**，导致 25 个 `tasks/*/page.py` 永远不会被加载。这是 bug，不是设计。runhey 已修（`3a6e56c2`），本项目未修
- `module/team_flow/`（MQTT 多人协作）是否还在用，**待确认**
