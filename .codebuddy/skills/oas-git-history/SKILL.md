---
name: oas-git-history
description: OAS 版本考古与三方仓库对比，判断某个改动该不该合。当想知道某块代码为什么长这样、想从 runhey/AzurTian 移植功能、想确认本地和上游差多少时使用。触发词：git、commit、merge-base、上游、同步、移植、runhey、AzurTian、Hurry2、该不该合、落后多少提交。
---

# OAS 版本考古

> 规则见 `.codebuddy/rules/06-git.md`。

## 适用场景

- 想知道某块代码为什么长这样
- 想从 runhey / AzurTian 移植功能
- 想确认本地和上游差多少

## 输入

文件路径 / 功能名 / commit hash。

## 已实测的结论（别再重新猜）

| 事实 | 值 |
|---|---|
| 当前分支 / HEAD | `Forme` / `5567a78c`（2026-10-04） |
| origin / upstream | `solomondevil4/oautos` / `Hurry2/OnmyojiAutoScript` |
| **本地落后上游** | **12 个提交**（远端 `5af10aa6`，2026-10-07） |
| 三方共同祖先 | `923b37a2`（2025-11-15） |
| runhey | 祖源（2023-04，5365 star） |
| AzurTian | runhey 的 fork，**且是 Hurry2 的下游**（最后提交 = `Merge PR #296 from Hurry2/mine-pr`） |
| Hurry2 远端分支 | 只有 `Forme` 和 `ui_update`（**无 `self` 分支**） |

## 操作步骤

### 1. 先判断方向

**AzurTian 是下游 → 合并它的任何东西 = 回退三个月。**
实测它在 `module/` 全目录、`tasks/Component/`、`scheduler.py`、`anti_ban.py`、`exception.py`、`logger.py`、`device.py`、`navigator.py` 上与当前项目**零差异**。

### 2. 三方对比方法

```bash
cd /tmp/oas_arch/runhey          # 已建好的比较仓库（若被清理需重建）
git fetch --no-tags --filter=blob:none <仓库URL> <分支>:<本地引用名>
git merge-base <A> <B>                    # 算血缘
git rev-list --count <MB>..<A>            # 独有提交数
git show <ref>:<path> > <输出文件>        # 导出文件做 diff
```

⚠️ `raw.githubusercontent.com` **会被限流**，批量取文件优先用 `git show` 导出 git 对象。

### 3. 判断"该不该合"的三问

1. 这个功能 hurry2 是**没有**还是**主动删了**？
   （`instance_guard` 的教训：以为是"没有"，实为"合并过 import 但文件没跟来，随后主动清理"）
2. 合进来是**新增能力**还是**替换现有实现**？
   （替换现有实现 = 高危）
3. 对方是**上游**还是**下游**？

### 4. 已知的明确结论

| 来源 | 结论 |
|---|---|
| AzurTian 全部 | 🔴 **不合**（下游） |
| runhey `atom/image.py`（本地算图） | 🔴 不合，会废掉 RPC 架构 |
| runhey `ocr/rpc.py` | 🔴 不合（hurry2 版是 405 行线程池服务，更完整） |
| runhey `_wait_*` 内联等待 | 🔴 不合（hurry2 已抽成独立模块，合回去是倒退） |
| runhey 精简版 `logger.py` | 🔴 不合（hurry2 版 521 行更完整） |
| runhey `RuleClickExclude`（排除区随机点击） | 🟡 **唯一值得考虑**，hurry2 确实缺失，属新增能力不破坏架构 |
| runhey `I_CHECK_MAIN` roi_back 高度 61→74 | 🟡 风险低但**需实机确认**（hurry/azur=61，只有 runhey=74） |

## 常见错误

- 默认认为"更新的仓库更正确" → AzurTian 就是反例
- 以为 hurry2 "没有"某功能就等于"需要补" → 可能是试过不合适删了
- 用 raw.githubusercontent 批量下载（限流）

## 禁止事项

- 不确认血缘就合并
- **合并 AzurTian 的任何代码**
- 合并会替换现有实现的 runhey 代码（见上表）
- AI 擅自 `git pull` 同步上游（须用户决定）

## 完成后如何验证

- 能给出 merge-base + 独有提交数
- 能说明"hurry2 是否已包含"
- 给出明确的「合 / 不合 / 待确认」结论，含理由
