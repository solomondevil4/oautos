---
description: OAS 的测试现状、无法验证时的处理，以及改动后的回归检查清单。
globs: ["**/*.py"]
alwaysApply: false
---

# 测试与回归规范

## 先接受现实：本项目几乎没有测试

全仓库只有 **7 个**测试相关文件，其中能算"真测试"的：

- `dev_tools/assets_test.py`（资源校验）
- `tasks/FrogBoss/test_frog_oas.py`
- `tasks/Component/Costume/costume_test.py` / `costume_test_shikigami.py` / `costume_test_battle.py`
- `tasks/Hyakkiyakou/utils/exp_test.py`
- `module/team_flow/mqtt_test.py`

**核心基础设施 0 测试。** 唯一有测试保护的区域是 `Costume` 组件和 `FrogBoss` 任务。

## 当前环境无法实机验证

- `config/` 下只有 `template.json`，**没有运行配置**
- `log/` 目录**不存在**
- 没有模拟器连接、没有识别服务

**→ AI 无法验证任何运行期行为。** 必须明确写"未实机验证"，并给出用户该怎么验。不许说"应该没问题"。

## 无法验证时的标准说法

```
本次改动未实机验证。
原因：<无运行配置 / 无模拟器连接 / 无识别服务>
需要你做的验证：
  1. ...（具体步骤）
  2. ...
如果第 1 步就失败，大概率是 <X>，回退方式：git checkout -- <文件>
```

## 能做的静态验证

```bash
# 语法/导入检查
python -c "import tasks.<Name>.script_task"

# 资源校验（无命令行参数，需调用函数；图片必须是 1280x720）
python -c "
from dev_tools.assets_test import detect_image
from tasks.<Name>.assets import <Name>Assets
print(detect_image(r'<截图绝对路径>', <Name>Assets.I_XXX))
"

# 算爆炸半径（改前必做）
grep -rl "<符号名>" --include=*.py . | wc -l
grep -rn "class ScriptTask(.*<组件>" --include=*.py tasks/ | wc -l
```

## 改动后的回归检查清单

### 第一步：算爆炸半径

改了 `GeneralBattle` → 影响 **33** 个继承任务
改了 `SwitchSoul` → **32** 个
改了 `GameUi` → **49** 个
改了识别素材 → 所有用同名素材的任务（对象全局共享）

### 第二步：对照高危清单

| # | 高危点 | 位置 |
|---|---|---|
| 1 | 识别对象跨任务污染 | `assets.py` 全局单例 + `atom/image.py:180-182` 回写 |
| 2 | 配置赋值即写盘 | `config_model.py:201` |
| 3 | 组件连锁 | `GeneralBattle` 33 / `SwitchSoul` 32 |
| 4 | 看门狗是类属性 | `device.py:29-33` |
| 5 | 无退出条件的循环 | 全仓库 440 处 `while True` |
| 6 | 吞异常 | 63 处，`AutoCheckinBigGod` 占 17 |
| 7 | 点击放 `and` 左边 | 现存 5 处 |
| 8 | **已知活 bug** | `tasks/GameUi/registry.py:82` `("page")` 漏逗号 |

### 第三步：列出必须回归的任务

- 改了 `GeneralBattle` → **至少测 3 个**继承它的任务，不能只测 1 个
- 改了 `GameUi` → 必须测**跨玩法跳转**
- 改了识别素材 → 测所有用到同名素材的任务

### 第四步：给回归顺序

先测最常用、最贵的玩法。

## 风险模式基线（改完不应变多）

| 模式 | 当前基线 | 最集中的文件 |
|---|---|---|
| `while 1` / `while True` | **440** | `DemonEncounter` 15、`BondlingFairyland` 14、`GeneralInvite` 13 |
| 硬 `sleep(数字)` | **101** | — |
| 裸/吞异常 | **63** | `AutoCheckinBigGod` **17** |
| 点击放 `and` 左边 | **5** | FrogBoss、GeneralInvite、SixRealms×2、ActivityExploration |

## 最该补测试的地方（如果要补）

1. `module/atom/image.py:154-182` 的 `roi_front` 回写污染——**性价比最高**
2. `module/config/config_model.py:201` 赋值即写盘
3. `tasks/GameUi/` 页面寻路
4. `Device` 看门狗（60 秒无进展 / 15 次点击里同一按钮 10 次）
5. `dev_tools/assets_extract.py` 生成器（它会重写 86 个文件）
6. 任务名三处一致性（写个脚本扫一遍即可）

## 禁止事项

- 用"代码看着没问题"代替回归测试
- 改了 A/B 级却不列回归清单
- 只测改动直接对应的那一个玩法就说"已验证"
- 用静态检查冒充回归测试
