---
name: oas-component
description: 修改 OAS 公共组件 tasks/Component/（GeneralBattle、SwitchSoul、GeneralInvite、GeneralRoom、Buy 等）。当多个玩法有同一个毛病、需要在组件里新增能力、或修某个玩法时发现根因在组件时使用。触发词：公共组件、GeneralBattle、SwitchSoul、GeneralInvite、GeneralRoom、Buy、爆炸半径、继承、override、抽象。
---

# OAS 公共组件修改

> **这是全项目最容易踩坑的地方。** 组件一次改动会同时影响几十个任务。
> 规则见 `.codebuddy/rules/02-architecture.md`、`08-ai-behavior.md`。

## 适用场景

- 多个玩法出现同一个毛病（说明该改组件）
- 需要在组件里新增能力
- 修某个玩法时，发现根因在组件里

## 输入

组件名 + 症状 + 受影响的任务列表。

## 第一步：判断"该不该改组件"（先过这一关）

| 情况 | 做法 |
|---|---|
| **只有 1 个玩法有问题** | **改玩法，不改组件** |
| ≥3 个玩法有同样症状 | 才考虑改组件 |
| 某玩法需要特殊处理 | 在该玩法里 **override** 那个方法，**不要**在组件里写 `if task == 'X'` |

## 第二步：算爆炸半径（实测基线）

| 组件 | 引用文件数 | 继承任务数 | 行数 |
|---|---|---|---|
| `GeneralBattle` | **77** | **33** | 1411 |
| `SwitchSoul` | **75** | **32** | 399 |
| `GeneralInvite` | 29 | 13 | 897 |
| `Buy` | 22 | — | 252 |
| `GeneralRoom` | 15 | 11 | 251 |
| `SwitchAccount` | 10 | — | 650 |
| `Costume` | 10 | — | 726 |
| `GeneralBuff` | 8 | 3 | 369 |

```bash
grep -rl "<组件名>" --include=*.py tasks/          # 全部调用方
grep -rn "class ScriptTask(.*<组件名>" --include=*.py tasks/   # 继承方
```

## 第三步：选改动方式（按安全性排序）

1. **新增方法**（最安全）—— 不影响任何旧调用方
2. **新增可选参数**，默认值保持原行为 —— 次安全
3. **修改已有方法默认行为** —— 🔴 必须先说明会影响哪 33/32/13 个任务
4. **删除公开方法** —— ❌ 禁止（可能某处还在用）

## 两个已确认的判断（别再猜错）

✅ **`_exit_matcher()` 是钩子模式，不要抽象**
9 个任务各自实现，看着像复制粘贴，但逐个读过：每个只返回自己的一张标志图（`AreaBoss`→`I_AB_CLOSE_RED`、`Secret`→`I_SE_FIRE`、`Orochi`→`any_of(3张)`）。**这是正确设计。**

✅ **真正可抽象的只有组队副本模板**
`Orochi` / `EternitySea` / `EvoZone` / `FallenSun` 四个的 `run_leader`(76/66/83/99 行)、`run_member`(48/32/41/57)、`run_alone`(36/30/35/46)、`is_room_dead`(都是 8 行)、`check_layer`(都是 10 行) 高度雷同 → 可抽 `TeamRaidBase`。
**但抽象是 🟠 B 级操作：必须一个任务一个任务抽，抽完立刻实测那个玩法。**

## 常见错误

- 为修一个玩法在 `GeneralBattle` 里加特判 → 弄坏另外 32 个
- 把钩子方法误判成重复代码去合并
- 改了组件只测一个玩法就说"没问题"
- 在组件里 import 具体任务（会造成循环依赖）

## 禁止事项

- 不加说明地改 `GeneralBattle` / `SwitchSoul` 的默认行为
- 删除组件里的公开方法
- 组件里 import 具体任务
- 批量"优化"组件代码（属未经确认的重构）

## 完成后如何验证

1. 列出**全部**受影响任务名（`grep` 出的实数）
2. 至少说明其中 **3 个**分别该怎么测（选最常用的玩法）
3. 只测了 1 个 → 必须写"其余 N 个任务未验证"
4. 当前环境无法实机 → 明确写出"本次改动零实机验证，需用户测试：<清单>"
