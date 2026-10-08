---
name: oas-architecture
description: OAS 架构导航与风险定级。当不知道代码在哪、要判断一个改动属于哪一级风险、需要解释某块代码是干什么的时候使用。是所有 OAS Skill 的入口，不确定先加载本 Skill。触发词：OAS 架构、代码在哪、这个文件干什么的、影响范围、爆炸半径、风险等级、A级、B级、module、tasks、目录结构。
---

# OAS 架构导航

> 项目根目录：`E:\yyshurry2fork\OnmyojiAutoScript`（分支 `Forme`，HEAD `5567a78c`）
> 全局规则在 `.codebuddy/rules/`，本 Skill 不重复规则，只讲**怎么定位**和**怎么定级**。

## 适用场景

- 第一次接触某个需求，不知道该动哪个文件
- 要判断改动属于 A/B/C/D 哪一级（决定 AI 能不能自主改）
- 需要向非程序员解释某块代码是干什么的
- 排查问题时需要确定"这可能是哪一层的事"

## 输入

需求描述，或一个文件路径 / 符号名。

## 动手前必须先做（硬门槛）

1. **确认工作目录是本项目**。本地有多个 OAS 副本（`E:\yysai`、`E:\oas pull request` 等），先 `git remote -v` 确认 upstream 是 `Hurry2/OnmyojiAutoScript`。
2. **符号是否真实存在** —— 用 Grep 核实，**不允许凭名字推断**。
3. **算引用面**：
   ```bash
   grep -rl "<符号名>" --include=*.py . | wc -l
   ```

## 分层地图（先判断在哪一层）

| 层 | 目录 | 干什么 |
|---|---|---|
| 通用机器 | `module/` | 跟阴阳师无关的底层能力：截图、识别、配置、调度、设备控制 |
| 游戏知识 | `tasks/<玩法>/` | 62 个玩法各自怎么点 |
| 复用零件 | `tasks/Component/` | 玩法之间共享的能力（战斗、邀请、房间、买东西、换御魂） |
| 导航基础设施 | `tasks/GameUi/` | 把游戏界面抽象成"页面 + 跳转路径"，Dijkstra 自动寻路 |
| 自动生成 | `tasks/*/assets.py`（86 个） | 识别资源表，**禁止手改** |

一句话总览：**OAS 每隔零点几秒截一张图，在图上找图/读字判断"现在在哪个界面"，然后点该点的地方。**

## 三个入口（殊途同归）

`gui.py`（桌面窗口）→ `server.py`（网页后台 22270）→ `script.py`（命令行）
三者最终都执行 `Script(config_name).loop()`。识别服务（图像 22269 / OCR 22268）由入口进程 spawn 拉起。

## 定级（决定 AI 权限）

| 级别 | 判据 | AI 权限 |
|---|---|---|
| 🔴 A | `module/atom/*`、`module/device/*`、`script.py`、`tasks/base_task.py`、`module/exception.py`、`module/config/config.py`、`config_model.py` | **禁止自主改**，人工逐行确认 |
| 🟠 B | `Component/{GeneralBattle,SwitchSoul,GeneralInvite,GeneralRoom,Buy}`、`tasks/GameUi/*`、`module/logger.py`、`config/{anti_ban,scheduler,config_menu}.py`、`module/{image,ocr}/rpc.py` | 改前必须列出调用关系清单 |
| 🟡 C | `tasks/<玩法>/script_task.py`、`config.py` | 可改，遵守三条硬约束 |
| 🟢 D | `GotoMain`(46行)、`ActivityShikigami`(18行)、`DyeTrifles`、`GoryouRealm`、`Component/Costume`、`FrogBoss` | 最安全，适合练手 |
| ⛔ 禁 | `tasks/*/assets.py`、`fluentui/`、`bin/` | 禁止编辑 |

**引用面实测基线（可直接引用，除非代码已变）**

| 符号 | 引用文件数 | 级别 |
|---|---|---|
| `module.logger` | 218 | 🔴 |
| `module.atom.image` | 146 | 🔴 |
| `module.atom.ocr` | 112 | 🔴 |
| `module.atom.click` | 107 | 🔴 |
| `module.exception` | 98 | 🔴 |
| `module.config.config` | 96 | 🔴 |
| `module.device.device` | 89 | 🔴 |
| `tasks.GameUi.page` | 75 | 🟠 |
| `tasks.Component.config_base` | 79 | 🟠 |
| `tasks.base_task` | 30 | 🔴 |

**继承深度（改这些会连锁）**：`GameUi` 49 个任务继承、`GeneralBattle` 33 个、`SwitchSoul` 32 个、`GeneralInvite` 13 个、`GeneralRoom` 11 个。

## 操作步骤

1. 按上表判断在哪一层、哪一级
2. 定位到**具体文件 + 行号**（不允许"大概在 config 里"）
3. 算引用面、查继承深度
4. 输出结论：`文件:行号 → 符号 → 级别 → 爆炸半径（N 个文件 / M 个任务）`
5. 若是 A/B 级 → 至少列出 3 个具体调用方路径，转人工确认

## 常见错误

- 把 `tasks/GameUi/` 当成某个玩法 —— 它是**导航基础设施**，49 个任务依赖
- 看到 `module/` 下陌生目录就当新功能 —— `module/team_flow/`（MQTT 多人协作）**是否在用待确认**
- 以为 `assets.py` 是配置文件 —— 2198 个 `RuleImage` + 302 个 `RuleOcr` 的自动生成资源表
- 用文件名推测功能（本项目已因此错判两次：`instance_guard`、`_exit_matcher`）

## 禁止事项

- 不读源码就回答"这个文件大概是…"
- 跨级操作：把 A 级当 C 级改
- 用"应该是"代替核实过的事实

## 完成后如何验证

- 能说出：改 X 影响 **N 个文件 / M 个任务**，N、M 是 grep 出的实数
- A/B 级能列出 ≥3 个具体调用方路径
- 若无法定位到行号 → 明说"未能定位"，不要给模糊答案
