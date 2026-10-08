---
name: oas-code-review
description: OAS 代码改完后的自查清单。任何一次代码改动的收尾、提交前使用。触发词：review、自查、检查、提交前、改完了、过一遍、风险模式、命名一致性。
---

# OAS 改动自查

> 规则见 `.codebuddy/rules/01-python-style.md`、`08-ai-behavior.md`。

## 适用场景

- 任何一次代码改动的收尾
- 提交前

## 输入

改动清单（文件 + 行号）。

## 先检查两件事

1. **是否越级**（碰了 A/B 级文件？）—— 见 `oas-architecture` 的分级表
2. **是否违反红线**（见 `.codebuddy/rules/08-ai-behavior.md`）

## 检查表（逐条过，不要跳）

### 结构

- [ ] 任务结束是 `raise TaskEnd` 而不是 `return`？
- [ ] 没改 `assets.py`？
- [ ] 没在循环里改配置字段？
- [ ] 没给图像/OCR 的 RPC 调用加 try/except 兜底？
- [ ] 没给 `Device` 类新增类属性？

### 风险模式（当前仓库实测基线，改完不应变多）

| 模式 | 当前基线 | 最多的文件 |
|---|---|---|
| `while 1` / `while True` | **440** 处 | `DemonEncounter` 15、`BondlingFairyland` 14、`GeneralInvite` 13 |
| 硬 `sleep(数字)` | **101** 处 | — |
| 裸 / 吞异常 | **63** 处 | `AutoCheckinBigGod` **17** 处 |
| "点击放 `and` 左边" | **5** 处 | FrogBoss、GeneralInvite、SixRealms×2、ActivityExploration |

```bash
# 复查命令
grep -rn "while\s\+1\s*:\|while\s\+True\s*:" --include=*.py . | wc -l
grep -rn "except\s*:\|except Exception:" --include=*.py . | wc -l
```

- [ ] 新增的 `while True` 有**超时出口**？
- [ ] 新增了裸 `except` 吞异常？
- [ ] 把带副作用的函数放在了 `and` 左边？

```python
# ❌ 点击每次都会真执行，flag 闸门完全失效
if self.appear_then_click(BTN, interval=2) and flag == 0:
# ✅ 闸门放左边
if flag == 0 and self.appear_then_click(BTN, interval=2):
```

### 命名与一致性

- [ ] 任务名三处一致（目录 / `config_model.py` 字段 / `config_menu.py` 字符串）
- [ ] 新增的 `I_XXX` / `O_XXX` 没有和别处重名
- [ ] 目录名是大驼峰

### 调用关系

- [ ] 能列出受影响的其它任务
- [ ] 若改了组件，列出了全部继承任务

## 常见错误

- 只检查"语法对不对"，不检查"会不会影响别人"
- 自己 review 自己，一路全绿
- 说"应该没问题"而不是"我检查了 X、Y、Z"

## 禁止事项

- 跳过检查直接说完成
- 用"逻辑上应该是对的"代替实证

## 完成后输出

一份逐条打勾的检查表 + **未验证项清单**（这一段不能省）。

回复必须含四段：
```
【改了什么】   文件:行号 —— 改动内容
【影响范围】   受影响的任务/文件 + 级别
【如何验证】   具体命令或操作步骤
【未验证项】   没有就写"无"，但这一段不能省
```
