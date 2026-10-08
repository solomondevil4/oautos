---
name: oas-image
description: 新增或调试 OAS 找图规则（RuleImage）。当要新增按钮/标志识别、某个图认不出来或认错、换了皮肤界面后识别失效时使用。触发词：RuleImage、找图、识别不到、认错图、roi_front、roi_back、threshold、image.json、assets.py、模板匹配、图像服务、22269。
---

# OAS 找图规则

> 规则见 `.codebuddy/rules/04-image-ocr.md`。本 Skill 讲**机制 + 排查 + 验证**。

## 适用场景

- 新增一个按钮 / 标志的识别
- 某个图一直认不出来，或认错位置
- 换皮肤 / 换界面后识别失效

## 输入

任务名 + 素材名（`I_XXX`）+ 症状。

## 必须先理解的机制（这是本项目最大的特点）

**识别不在脚本进程里算，而是远程调用独立进程。**

- 截图后只上传一次，换回一个 `frame_id`；之后所有找图都靠这个编号远程调用
- 图像服务：`module/image/rpc.py` + `runtime.py`，默认 `127.0.0.1:22269`
- **没有本地兜底** —— `ensure_image_server_ready()` 连不上直接抛 `ScriptError`
- 服务端带帧缓存 + 模板缓存 + 多线程 worker

⚠️ **所以"识别不准"和"服务没起来"是两回事，排查时先分清。**

## 动手前必须先检查

1. 素材对象是否已被别处使用：
   ```bash
   grep -rn "I_<名字>" --include=*.py tasks/
   ```
   ⚠️ `assets.py` 里的 **2198 个 `RuleImage` 全是模块级全局单例**
2. `roi_front` 是否已被污染 —— `module/atom/image.py:178` 的 `_apply_match_result()` 在匹配成功后会把服务端算出的 `roi_front` **回写**到对象上
3. 素材文件是否真实存在（模板缺失/损坏时**反而返回"找到了"**）

## 操作步骤

1. 在 `tasks/<任务>/<子目录>/` 放素材 PNG
2. 在 `image.json` 登记 `roi_front` / `roi_back` / `threshold` / `method`
3. 跑 `python dev_tools/assets_extract.py`（**无参数，全量重写 86 个 assets.py**）
4. 代码里用 `self.appear(I_XXX)` / `self.appear_then_click(I_XXX)`
5. 调试时开日志看匹配分数

**规则类型不止找图**：`RuleImage`（找图）、`RuleClick`、`RuleLongClick`、`RuleSwipe`、`RuleList`（列表翻找）、`RuleGif`、`RuleAnimate`（判断动画是否停稳）、`ImageGrid`、`RuleScatter`（多边形正态散点）。

## 头号杀手：`roi_front` 跨任务污染

`assets.py` 里的对象是全局单例，匹配成功后位置被回写。
**A 任务在屏幕左边找到过这张图 → 对象记住左边 → B 任务照着左边点。**
跨任务位置污染，最隐蔽，**单任务测试永远测不出来**。

## 常见错误

| 症状 | 根因 |
|---|---|
| 疯狂点空气直到触发点击上限 | 模板图缺失/损坏时**反而返回"找到了"** |
| 查日志看不到"没找到" | `appear()` 匹配失败**静默返回 False 不打日志** |
| 时灵时不灵 | ROI 画在会飘动的 UI 上（气泡、红点） |
| 页面判断提前命中 | `any_of()` 里混入非唯一图 |
| 换个任务就点错位置 | `roi_front` 跨任务污染 |

## 禁止事项

- 手改 `assets.py`
- **给 RPC 调用加 try/except 兜底** —— 没有本地兜底是刻意设计，加兜底会掩盖"服务根本没起来"
- 调低 `threshold` 来"让它认出来"（治标）—— 正确做法是**换更稳定的标志物**
- 删除或改名已有的 `I_XXX`（可能被别的任务引用）
- 动 `module/atom/scatter.py`（半移植文件，配套的上游改造未合入）

## 完成后如何验证

**静态**（AI 可做，需一张 1280×720 实机截图）：
```bash
python -c "
from dev_tools.assets_test import detect_image
from tasks.<Name>.assets import <Name>Assets
print(detect_image(r'<截图绝对路径>', <Name>Assets.I_XXX))
"
```
> `assets_test.py` **没有命令行参数**，`__main__` 里是别人硬编码的路径 —— 必须调用函数。

**服务状态**：开关在 `deploy/config.py` 的 `StartImageServer`，地址 `ImageClientAddress = 127.0.0.1:22269`。

**实机**：跑到该界面，看日志里的匹配分数。AI 无法实机 → 必须明说。
