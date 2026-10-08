---
name: oas-ocr
description: 新增或调试 OAS 读字规则（RuleOcr）。当需要读数字（层数、剩余次数、金币）、读文字（名字、章节），或 OCR 结果不对时使用。触发词：RuleOcr、OCR、读字、读数字、ocr.json、OcrMode、DIGIT、DIGITCOUNTER、DURATION、QUANTITY、22268、ppocr。
---

# OAS 读字规则（OCR）

> 规则见 `.codebuddy/rules/04-image-ocr.md`。

## 适用场景

- 需要读数字（层数、剩余次数、金币数）
- 需要读文字（名字、章节）
- OCR 结果不对 / 读出来是旧值

## 输入

任务名 + 要读什么 + 症状。

## 必须先理解的机制

- OCR 同样是**独立进程**：`module/ocr/rpc.py`，默认 `127.0.0.1:22268`
- **无本地兜底** —— 服务没起来时报的是 `ScriptError`，不是"读不到"
- 底层引擎 ppocronnx（`module/ocr/models.py`）
- 与图像服务一样由入口进程 spawn 拉起

## 核心决策：选识别模式

`module/ocr/base_ocr.py:37` 的 `OcrMode` 枚举共 **6 种**：

| 模式 | 值 | 用途 |
|---|---|---|
| `FULL` | 1 | 读一句话 |
| `SINGLE` | 2 | 读单个词 |
| `DIGIT` | 3 | 纯数字 |
| `DIGITCOUNTER` | 4 | 形如 `3/5` |
| `DURATION` | 5 | 形如 `01:23` |
| `QUANTITY` | 6 | 带单位的数量 |

**选错模式是 OCR 问题最常见的原因**（用整段模式读数字 → 混入汉字）。

## 操作步骤

1. 确认要读的内容适合哪种模式（见上表）
2. 在 `tasks/<任务>/ocr.json` 登记 ROI + 模式
3. 跑 `python dev_tools/assets_extract.py`（无参数，全量重写 86 个 assets.py）
4. 代码里用 `self.ocr_appear(O_XXX)` / `self.O_XXX.ocr(...)` 取值
5. **关键时序**：操作之后要读数字 → **必须 sleep + 重新截图**再读
   （经验库里 `8d167b55`、`f4b7498a` 两次提交都栽在这：操作完立刻读，读的是旧画面）

## 常见错误

| 症状 | 根因 |
|---|---|
| 读出来是操作前的旧值 | 操作完立刻读，没重新截图 |
| 数字读不全 | ROI 切到字的一部分（数字被切掉一半） |
| 数字里混进汉字 | 用 `FULL` 模式去读数字 |
| 报 `ScriptError` 而非"读不到" | OCR 服务没起来（不是识别问题） |

## 禁止事项

- 手改 `assets.py`
- 用正则硬凑 OCR 结果（应在**模式层**解决，不是事后补救）
- 给 OCR RPC 加 try/except 兜底（同图像服务，无兜底是刻意设计）
- 删除或改名已有的 `O_XXX`

## 完成后如何验证

**静态**（需 1280×720 实机截图）：
```bash
python -c "
from dev_tools.assets_test import detect_ocr
from tasks.<Name>.assets import <Name>Assets
print(detect_ocr(r'<截图绝对路径>', <Name>Assets.O_XXX))
"
```

**服务状态**：`deploy/config.py` 的 `OcrClientAddress = 127.0.0.1:22268`。

**实机**：打印 OCR 原始结果，逐字对比屏幕。AI 无法实机 → 必须明说。
