---
description: OAS 的 Git 规范：分支、remote、三方仓库血缘与"什么能合什么不能合"。
globs: ["**/*"]
alwaysApply: false
---

# Git 规范

## 当前身份

| 项 | 值 |
|---|---|
| 分支 | `Forme`（唯一本地分支） |
| HEAD | `5567a78c`（2026-10-04） |
| origin | `solomondevil4/oautos`（自己的 fork） |
| upstream | **`Hurry2/OnmyojiAutoScript`（基线）** |
| 落后上游 | **12 个提交**（远端 `5af10aa6`，2026-10-07） |

## 三方仓库：能用哪个

| 仓库 | 地位 | 能不能合 |
|---|---|---|
| **Hurry2** | **基线** | ✅ 这是标准答案 |
| **runhey** | 祖源，共同祖先 `923b37a2`（2025-11-15） | 🟡 **仅参考**，几乎不合 |
| **AzurTian** | runhey 的 fork，**且是 Hurry2 的下游** | 🔴 **一律不合** |

**AzurTian 是下游的证据**：它最后一条提交是 `Merge PR #296 from Hurry2/mine-pr`。实测它在 `module/` 全目录、`tasks/Component/`、`scheduler.py`、`anti_ban.py`、`exception.py`、`logger.py`、`device.py`、`navigator.py` 上与本项目**零差异**。合并它的任何东西 = 回退三个月。

**Hurry2 远端只有两个分支**：`Forme` 和 `ui_update`（`85e88b4f`，性质不明）。**不存在 `self` 分支**——尽管 `module/atom/scatter.py` 的注释提到了它。

## 判断"该不该合"的三问

1. **对方是上游还是下游？**（下游直接放弃）
2. **是新增能力还是替换现有实现？**（替换现有实现 = 高危，默认不合）
3. **hurry2 是"没有"还是"主动删了"？**
   - 教训：`module/config/instance_guard.py` 一度以为是"hurry2 没有"，实际是**合过 import 但文件没跟来，随后被 `3e1c68f7` 清理**——是主动放弃，不是缺失

## 明确不合清单

- ❌ AzurTian 的**全部**代码
- ❌ runhey 的 `module/atom/image.py`——它是**在脚本里本地算图**，会废掉 hurry2 的独立识别服务架构
- ❌ runhey 的 `module/ocr/rpc.py`——210 行简易版 vs hurry2 的 405 行线程池版，是降级
- ❌ runhey 的 `_wait_*` 内联等待——hurry2 已抽成独立模块，合回去是倒退
- ❌ runhey 的精简版 `logger.py`——370 行 vs 521 行

## 唯一值得考虑的 runhey 增量

`RuleClickExclude`（排除区随机点击，78-212 行）——hurry2 确实缺这个能力（全仓库搜不到 `RuleClickExclude` / `coord_in_excluded`）。它解决的是"结算页随机点击误点到真按钮"。属**新增子类**，不破坏现有架构。

次优先：`I_CHECK_MAIN` 的 roi_back 高度 `61 → 74`（runhey `606517be`，庭院下移适配）。若你的庭院页识别正常就不用动。**待实机确认。**

## 三方对比怎么做

`raw.githubusercontent.com` 会被限流。**优先用 git 对象导出**：

```bash
# 在临时比较仓库里
git fetch --no-tags --filter=blob:none <仓库URL> <分支>:<本地引用名>
git merge-base <A> <B>                     # 算血缘
git rev-list --count <MB>..<ref>           # 独有提交数
git show <ref>:<path> > <输出文件>          # 导出文件做 diff
```

## 提交与同步

- **AI 不擅自 pull / push / commit**。同步上游由用户决定
- 落后上游时先看内容再决定：`git log --format="%h | %ad | %s" --date=short HEAD..upstream/Forme`
- 落后清单里含两条斗技修复：`66497f5 fix(Duel)传参修正`、`dfa04643 fix(Duel)修复未切换自动就秒退造成的卡死`
- 同步后需重新确认 `module/atom/scatter.py` 和 `tasks/GameUi/registry.py` 的状态

## 提交信息风格

沿用现有：`<type>(<范围>): <说明>`，如 `fix(Duel)传参修正`、`feat`、`refactor`、`perf`、`chore`。
