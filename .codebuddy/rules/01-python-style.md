---
description: OAS 的 Python 编码规范与本项目的具体代码禁忌。改任何 .py 文件前必读。
globs: ["**/*.py"]
alwaysApply: false
---

# Python 编码规范

## 文件头与作者注释

新文件沿用现有风格：

```python
# This Python file uses the following encoding: utf-8
# @author <作者>
# github https://github.com/<作者>
```

- 中文注释和文档字符串照写，项目本身就是中文注释为主
- 不要在已有文件里补作者注释、不要改别人的 `@author`

## 项目既有风格（照抄，不要另创）

| 场景 | 现有写法 |
|---|---|
| 配置字段 | `xxx: Xxx = Field(default_factory=Xxx)`（pydantic） |
| 方法返回值注解 | `def run(self) -> bool:`、`-> ExitMatcher \| None` |
| 延迟初始化 | `@cached_property` |
| 日志 | `from module.logger import logger` 然后 `logger.info(...)` |
| 找图对象 | `I_` 前缀，如 `I_CHECK_MAIN` |
| 读字对象 | `O_` 前缀，如 `O_REMAIN_COUNT` |
| 任务目录 | 大驼峰，如 `tasks/Duel/` |
| 配置字段（模型内） | 蛇形，如 `frog_boss: FrogBoss` |

## 五条代码禁忌（违反必出问题）

### 1. 带副作用的函数不能放在 `and` 左边

```python
# ❌ 点击每次都会执行，flag 闸门形同虚设
if self.appear_then_click(BTN, interval=2) and flag == 0:

# ✅ 闸门放左边
if flag == 0 and self.appear_then_click(BTN, interval=2):
```

现存 5 处此类写法（FrogBoss、GeneralInvite、SixRealms×2、ActivityExploration）。**不要新增第 6 处。**

### 2. 任务结束必须 `raise TaskEnd`，不能用 `return`

```python
# ❌ 会被判为失败，累计 3 次后推送通知并 exit(1)
def run(self):
    do_something()
    return

# ✅
def run(self):
    do_something()
    raise TaskEnd
```

### 3. 不在循环里改配置字段

`config_model.py:201` 的 `__setattr__` 让**任何字段赋值立刻写盘**。循环内赋值 = 疯狂写磁盘。循环外改，或先存局部变量。

### 4. 不给识别/OCR 的远程调用加 try/except 兜底

图像服务（22269）和 OCR 服务（22268）**没有本地兜底是刻意设计**。连不上直接抛 `ScriptError`，是为了让"服务没起来"这个故障可见。加兜底会掩盖真实故障。

### 5. 不给 `Device` 类新增类属性

`device.py:29-33` 现有的 `detect_record` / `click_record` / `stuck_timer` / `stuck_timer_long` / `_screen_size_checked` 都是**类属性**（全班共用），不是每个实例一份。新状态一律加在 `__init__` 里：

```python
# ❌ 全班共用
class Device(...):
    my_state = []

# ✅
def __init__(self, *args, **kwargs):
    self.my_state = []
```

## 无限循环必须有出口

全仓库现有 **440 处** `while 1:` / `while True:`。新增时必须带超时或明确的退出条件：

```python
# ✅ 用 Timer 控制
timeout = Timer(60).start()
while True:
    if timeout.reached():
        break
```

## 不要吞异常

现有 **63 处**裸/吞异常（`AutoCheckinBigGod` 一个文件占 17 处）。新代码不要用 `except Exception: pass`。确实要忽略时，至少 `logger.warning` 一行说明为什么忽略。

## 不要写死 `sleep`

现有 **101 处** `sleep(数字)`。新代码优先用 `module/base/timer.py` 的 `Timer` 或等待条件，而不是硬睡。
