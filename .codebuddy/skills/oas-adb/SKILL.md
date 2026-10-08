---
name: oas-adb
description: OAS 模拟器 / ADB / 设备连接与截图点击控制问题。当连不上模拟器、截图黑屏或报错、点击没反应或点歪、分辨率报错时使用。触发词：ADB、模拟器、连不上、黑屏、截图失败、点不动、点歪、分辨率、1280x720、minitouch、scrcpy、DroidCast、nemu_ipc、uiautomator2。
---

# OAS 设备控制

> 规则见 `.codebuddy/rules/09-device.md`。本 Skill 讲**分层排查**。

## 适用场景

- 连不上模拟器
- 截图黑屏 / 截图报错
- 点击没反应 / 点歪
- 分辨率报错

## 输入

症状 + 模拟器型号 + 报错信息。

## 必须先记住的硬事实

1. **分辨率强制 1280×720**，不匹配会报"请设置模拟器分辨率"
2. 截图方式共 **7 种**（ADB / DroidCast / scrcpy / uiautomator2 / 窗口后台 / nemu_ipc…），配置选 `auto` 时会跑测速选最快
3. `Device.__init__`（`device.py:35`）最多重试 4 轮；模拟器没开会自动 `emulator_start()`

## 分层定位（先判断是哪一层）

| 症状 | 去哪个文件 |
|---|---|
| 连不上 | `module/device/connection.py`（949 行：ADB 连接、serial 识别、`adb forward/reverse`、uiautomator2 安装、断线重连） |
| 模拟器起不来 | `module/device/emulator.py`（MuMu / 雷电 / 蓝叠 实例识别与启停） |
| 截图异常 | `module/device/screenshot.py`（7 种方式 + 分辨率/黑屏校验 + frame_id 注册） |
| 点击异常 | `module/device/control.py`（minitouch / ADB / scrcpy / Windows 窗口） |
| 总控 | `module/device/device.py` |

## 两个看门狗（改这块必须知道）

- **60 秒画面无进展** → `GameStuckError`
- **最近 15 次点击里同一按钮点了 10 次** → `GameTooManyClickError`

⚠️ 这两个记录是**类属性**（`module/device/device.py:29-33`：`detect_record` / `click_record` / `stuck_timer`），不是每个实例一份，多实例场景互相干扰。

**因此：给 Device 加新状态一律加在 `__init__` 里，不要照抄上面这种类属性写法。**

## 异常分级（`module/exception.py`，共 15 种）

| 类型 | 后果 |
|---|---|
| `TaskEnd` | **正常结束，不是错误** |
| `GameNotRunningError` / `GameStuckError` / `GamePageUnknownError` | 自动 `task_call('Restart')` 重登 |
| `EmulatorNotRunningError` | 模拟器没跑 |
| `ScriptError` / `RequestHumanTakeover` / 未知异常 | 推送后 `exit(1)` |

## 常见错误

- 以为是脚本 bug，实际是模拟器没开或分辨率不对
- 给 `Device` 新增属性时照抄类属性写法 → 埋雷
- 看到"assume connected"类日志就以为连上了
  （经验库 `a2f2bcff`：连接失败"假装成功"比报错更危险，正确做法是抛 `EmulatorNotRunningError` —— OAS 有自动重登自愈机制，报错反而能触发正确恢复）

## 禁止事项

- 给 `Device` 类新增类属性
- 改分辨率校验让它"通过"（应去改模拟器设置）
- 在 `control.py` 里加"点击失败就重试到成功"的逻辑（会被点击上限看门狗打断）
- 给连接失败加静默兜底

## 完成后如何验证

```bash
adb devices          # 能看到设备
```
- 截图能存下来且是 1280×720
- 实机点一次看是否响应

⚠️ **当前环境无模拟器连接记录、无 `log/` 目录** → AI 无法验证任何设备层行为，必须明说"未实机验证"，并给出用户可执行的验证步骤。
