---
name: oas-debug
description: OAS 出问题时的排查流程。当任务跑飞、卡死、报错、识别不对、想看某一步到底发生了什么时使用。触发词：报错、卡死、跑飞、日志、log、error、异常、TaskEnd、GameStuckError、Restart、调试、复现、get_images。
---

# OAS 问题排查

## 适用场景

- 任务跑飞、卡死、报错
- 识别不对
- 想看某一步到底发生了什么

## 输入

报错信息 / 日志片段 / 症状描述。

## 先接受这个现实（重要）

> ⚠️ **`script.py` 硬编码 `Script("oas1").loop()`，没有单任务命令行参数。**
> 全仓库只有三个入口：`gui.py`、`script.py`、`server.py`。
> **无法直接命令行单跑一个任务。**
>
> 单任务调试途径：
> ① 在配置里把该任务设为 pending 跑一轮；
> ② 或自写临时脚本调 `Script("oas1").run('<TaskName>')`（`script.py:459`）。

## 排查顺序（按这个来，别跳步）

### 1. 看日志
`./log/YYYY-MM-DD_<配置名>.txt`，默认 `oas1`

⚠️ **文件日志会连续重复行去重**（`module/logger.py:232`）
→ 循环里刷屏的日志在文件里只留一条。**别以为循环没跑。**

### 2. 看错误现场
`./log/error/<配置名>_<毫秒>/`（截图 PNG + 脱敏日志片段），7 天清理。
**先去看截图，不要靠猜。**

### 3. 判断异常类型（`module/exception.py`，共 15 种）

| 类型 | 含义 / 后果 |
|---|---|
| `TaskEnd` | **正常结束，不是错误** |
| `GameNotRunningError` / `GameStuckError` / `GamePageUnknownError` | 自动 `task_call('Restart')` 重登 |
| `ScriptError` / `RequestHumanTakeover` / 未知异常 | 推送后 `exit(1)` |
| `EmulatorNotRunningError` | 模拟器没跑 |
| `GameTooManyClickError` | 触发点击上限看门狗 |

### 4. 识别类问题
确认 **22269（图像）/ 22268（OCR）** 两个服务是否活着。
**没有本地兜底 —— 服务挂了一定报错，不会"识别不准"。**
分不清就先排除这一层。

### 5. 抓帧调试
`dev_tools/get_images.py` 会连续截图存到 `./log/temp/<时间>/`

### 6. 看门狗线索
- 60 秒画面无进展 = `GameStuckError`
- 最近 15 次点击里同一按钮点了 10 次 = `GameTooManyClickError`

## 常见错误

- 看到 `TaskEnd` 以为是报错（它是"我做完了"）
- 因为日志去重误判"循环没执行"
- 不看错误现场截图就去猜
- 把"服务没起来"当成"识别不准"

## 禁止事项

- **不看日志就改代码**
- 为了"让报错消失"去加 try/except 吞掉异常
- 复现不了就开始重构
- 用"大概是 X 的问题"下结论（必须定位到文件 + 行号）

## 完成后如何验证

- 能指出**具体是哪一步**、**哪个文件哪一行**出的错
- 修复后能说明怎么复现验证
- 若无法复现 → **明说"未能复现，以下为基于日志的推断"**
