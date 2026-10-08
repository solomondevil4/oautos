---
description: 模拟器/ADB/设备控制与截图规范。含分辨率要求、看门狗机制和异常分级。
globs: ["module/device/**/*.py", "tasks/Script/**/*.py"]
alwaysApply: false
---

# 设备与 ADB 规范

## 硬要求：分辨率 1280×720

不匹配会直接报"请设置模拟器分辨率"。**不要改代码去"适应"别的分辨率**，应该去改模拟器设置。

## 四层职责

| 文件 | 干什么 |
|---|---|
| `module/device/device.py`（89 处引用） | `Device` 总类，组合 Platform + Screenshot + Control + AppControl |
| `module/device/connection.py`（949 行） | ADB 连接、serial 识别、`adb forward/reverse`、uiautomator2 安装、断线重连 |
| `module/device/screenshot.py` | 7 种截图方式 + 分辨率/黑屏校验 + `frame_id` 注册 |
| `module/device/control.py` | 点击 / 长按 / 滑动 / 拖拽（minitouch、ADB、scrcpy、Windows 窗口四种） |
| `module/device/emulator.py` | 模拟器实例识别与启停（MuMu、雷电、蓝叠等） |

## 连接行为

`Device.__init__`（`device.py:35`）最多重试 **4 轮**；模拟器没开会自动 `emulator_start()`。

截图方式配置选 `auto` 时会跑 `run_simple_screenshot_benchmark()` 测速选最快。

## 两个看门狗（改这块必须知道）

| 看门狗 | 触发条件 | 抛出 |
|---|---|---|
| 卡死检测 | **60 秒画面无进展** | `GameStuckError` |
| 点击次数 | 最近 **15 次**点击里同一按钮点了 **10 次** | `GameTooManyClickError` |

⚠️ 这两个记录是**类属性**（`device.py:29-33`），不是每个实例一份：

```python
class Device(Platform, Screenshot, Control, AppControl):
    _screen_size_checked = False
    detect_record = set()
    click_record = deque(maxlen=15)
    stuck_timer = Timer(60, count=60).start()
    stuck_timer_long = Timer(300, count=300).start()
```

**新状态一律加在 `__init__` 里，不要照抄上面这种类属性写法。**

## 异常分级（15 种，决定任务失败后怎么恢复）

| 类型 | 后果 |
|---|---|
| `TaskEnd` | **正常结束**，不是错误 |
| `GameNotRunningError` / `GameStuckError` / `GamePageUnknownError` | 自动 `task_call('Restart')` 重登游戏 |
| `ScriptError` / `RequestHumanTakeover` / 未知异常 | 推送通知后 `exit(1)` |

**不要为了"让报错消失"去加 try/except 吞掉异常。** OAS 有自动重登的自愈机制，报错反而能触发正确恢复。

历史教训（`a2f2bcff`）：原代码三次重试失败后 `return False` 并打印 "assume connected"——**连接失败"假装成功"比直接报错更危险**，后来改成抛 `EmulatorNotRunningError`。

## 排查顺序

1. 连不上 → `connection.py`（ADB / serial / 端口转发）
2. 起不来 → `emulator.py`（实例识别与启停）
3. 截图异常 → `screenshot.py`（7 种方式 + 分辨率/黑屏校验）
4. 点击异常 → `control.py`（四种控制方式）

## 常见错误

- 以为是脚本 bug，实际是模拟器没开或分辨率不对
- 看到 "assume connected" 之类日志就以为连上了
- 在 `control.py` 里加"点击失败就重试到成功"——会被点击次数看门狗打断

## 禁止事项

- 给 `Device` 类新增类属性
- 改分辨率校验让它"通过"（应改模拟器设置）
- 加"点击失败重试到成功"的循环
- 吞掉设备异常
