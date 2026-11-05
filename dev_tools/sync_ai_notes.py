#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把本地 AI 规范资产备份到 oascript/ai 孤立分支（异地备份）。

定位
    `oascript/ai` 是一条**孤立链**（首个提交无父），只装 AI 规则 / 技能 / 记忆
    的纯文本，不装游戏代码、不带上游几千个提交、不与本地 Forme 产生任何关系。
    OAS 更新器只看本地 Forme，所以这条分支对脚本运行零影响。

为什么不用普通分支
    从 Forme 拉一条普通分支会继承整仓历史和全部代码，还会跟运行分支分叉。
    本脚本用 `read-tree` + `commit-tree` 直接造 Git 对象，不建本地分支、
    不切分支、不动工作区、不动当前 index（用 GIT_INDEX_FILE 临时索引）。

白名单（多一个不收）
    .codebuddy/rules/**/*.md         AI 规则
    .codebuddy/skills/**/*.md        AI 技能
    .workbuddy/memory/*.md          长期记忆 + 日档（**仅顶层**）
    dev_tools/sync_ai_notes.py      本脚本自身，保证工具也有异地副本

    明确排除：.workbuddy/memory/automations/（定时任务运行记录，天天变、
    无备份价值）、.workbuddy/tmp/（427M 截图）、log/、任何二进制或密钥文件。

安全约束
    * 推送前对全部文本做凭据扫描，命中即拒绝推送（只报文件名与模式名，
      绝不把匹配到的内容打印出来）。
    * 新提交的父钉住远端当前 tip，天然快进；远端被并发改动时 push 会被
      非快进拒绝，脚本停下报错，**绝不使用 --force / --force-with-lease**。
    * 新旧 tree 相同则不产生空提交，直接报告「无变化，未推送」。

用法
    toolkit/python.exe dev_tools/sync_ai_notes.py --status     # 只看清单与远端 sha
    toolkit/python.exe dev_tools/sync_ai_notes.py --dry-run    # 预演，不提交不推送
    toolkit/python.exe dev_tools/sync_ai_notes.py --no-push    # 造提交但不推（调试）
    toolkit/python.exe dev_tools/sync_ai_notes.py              # 同步 + 推送

    成功时最后一行固定输出 `SUMMARY: ...`，供定时任务原样上报。
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

DEFAULT_REMOTE = "oascript"
DEFAULT_BRANCH = "ai"

# (相对目录, 是否递归, 后缀)。memory 不递归，就是要把 automations/ 排除掉。
INCLUDE = [
    (".codebuddy/rules", True, ".md"),
    (".codebuddy/skills", True, ".md"),
    (".workbuddy/memory", False, ".md"),
]
SELF_FILE = "dev_tools/sync_ai_notes.py"
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git"}

# 只报「哪一类」，不打印匹配内容，避免把疑似密钥打到日志里。
SECRET_PATTERNS = [
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("github_pat", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("openai_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("url_credentials", re.compile(r"https?://[^\s/:@]+:[^\s/@]+@")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]


# ---------------------------------------------------------------- git 封装
# 与 dev_tools/sync_snapshot.py 同款，纯标准库，不 import 项目代码。


class GitError(RuntimeError):
    pass


def _run(args, cwd, env=None, binary=False, check=True):
    cmd = ["git", "-c", "core.quotepath=false"] + list(args)
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, env=env)
    if check and p.returncode != 0:
        msg = (p.stderr or p.stdout).decode("utf-8", "replace").strip()
        raise GitError("git %s 失败：%s" % (" ".join(args), msg))
    return p.stdout if binary else p.stdout.decode("utf-8", "replace")


def git(args, cwd, env=None):
    return _run(args, cwd, env=env).strip()


def git_b(args, cwd, env=None):
    return _run(args, cwd, env=env, binary=True)


def find_repo_root(start=None):
    here = os.path.abspath(start or os.path.dirname(os.path.abspath(__file__)))
    p = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=here,
                       capture_output=True)
    if p.returncode != 0:
        raise SystemExit("当前目录不在 git 仓库内：%s" % here)
    return p.stdout.decode("utf-8", "replace").strip()


def remote_tip(root, remote, branch):
    """远端分支当前 sha；不存在返回空串。ls-remote 不会打印带 token 的 URL。"""
    out = git(["ls-remote", "--heads", remote, "refs/heads/%s" % branch], root)
    parts = out.split()
    return parts[0] if parts else ""


def ls_tree(ref, root):
    """返回 {路径: (mode, blob_sha)}。"""
    out = git_b(["ls-tree", "-r", "-z", ref], root)
    res = {}
    for rec in out.split(b"\0"):
        if not rec:
            continue
        meta, _, path = rec.partition(b"\t")
        parts = meta.split(b" ")
        if len(parts) != 3 or parts[1] != b"blob":
            continue
        res[path.decode("utf-8", "replace")] = (parts[0].decode("ascii"),
                                               parts[2].decode("ascii"))
    return res


# ---------------------------------------------------------------- 白名单


def collect(root):
    """收集白名单，返回 git 风格的相对路径（/ 分隔）。"""
    paths = []
    for rel, recursive, suffix in INCLUDE:
        base = root / rel
        if not base.is_dir():
            raise SystemExit("白名单目录不存在：%s" % rel)
        it = base.rglob("*") if recursive else base.glob("*")
        for p in sorted(it):
            if not p.is_file() or p.suffix != suffix:
                continue
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            paths.append(p.relative_to(root).as_posix())
    if (root / SELF_FILE).is_file():
        paths.append(SELF_FILE)
    return sorted(set(paths))


def scan_secrets(root, paths):
    """命中返回 [(文件, 模式名)]，不返回匹配内容。"""
    hits = []
    for rel in paths:
        text = (root / rel).read_text(encoding="utf-8", errors="replace")
        for name, pat in SECRET_PATTERNS:
            if pat.search(text):
                hits.append((rel, name))
    return hits


def build_map(root, paths, write_objects):
    flag = ["-w"] if write_objects else []
    res = {}
    for rel in paths:
        sha = git(["hash-object"] + flag + ["--", rel], root)
        res[rel] = ("100644", sha)
    return res


def build_tree(root, tip, added, updated, removed, new_map):
    idx = os.path.join(tempfile.gettempdir(), "oas_ai_notes_%d.idx" % os.getpid())
    if os.path.exists(idx):
        os.remove(idx)
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = idx
    try:
        git(["read-tree", tip] if tip else ["read-tree", "--empty"], root, env=env)
        for path in sorted(set(added) | set(updated)):
            mode, sha = new_map[path]
            git(["update-index", "--add", "--cacheinfo",
                 "%s,%s,%s" % (mode, sha, path)], root, env=env)
        for path in sorted(removed):
            git(["update-index", "--force-remove", "--", path], root, env=env)
        return git(["write-tree"], root, env=env)
    finally:
        if os.path.exists(idx):
            os.remove(idx)


# ---------------------------------------------------------------- 主流程


def main():
    ap = argparse.ArgumentParser(description="同步 AI 规范资产到 oascript/ai")
    ap.add_argument("--status", action="store_true", help="只看清单与远端 sha")
    ap.add_argument("--dry-run", action="store_true", help="预演，不提交不推送")
    ap.add_argument("--no-push", action="store_true", help="造提交但不推送")
    ap.add_argument("--remote", default=DEFAULT_REMOTE)
    ap.add_argument("--branch", default=DEFAULT_BRANCH)
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    root = Path(find_repo_root())
    paths = collect(root)
    tip = remote_tip(root, args.remote, args.branch)

    print("远端分支   : %s/%s" % (args.remote, args.branch))
    print("同步前 sha : %s" % (tip[:8] if tip else "(分支不存在，本次为初始化)"))
    print("白名单文件 : %d 个" % len(paths))

    if args.status:
        print("  " + "\n  ".join(paths[:5]) + "\n  ...")
        return 0

    hits = scan_secrets(root, paths)
    if hits:
        print("\n❌ 凭据扫描未通过，已停止（不打印匹配内容）：")
        for rel, name in hits:
            print("  %s -> 疑似 %s" % (rel, name))
        print("请先清理这些内容，或确认是误报后再调整 SECRET_PATTERNS。")
        return 1

    old_map = {}
    if tip:
        try:
            git(["cat-file", "-e", "%s^{commit}" % tip], root)
        except GitError:
            print("  本地缺少远端对象，先 fetch ...")
            git(["fetch", args.remote,
                 "refs/heads/%s:refs/tmp/ai-notes-fetch" % args.branch], root)
        old_map = ls_tree(tip, root)

    new_map = build_map(root, paths, write_objects=not args.dry_run)
    added = [p for p in new_map if p not in old_map]
    removed = [p for p in old_map if p not in new_map]
    updated = [p for p in new_map if p in old_map and new_map[p] != old_map[p]]
    print("文件差异   : +%d 新增 / ~%d 更新 / -%d 移除"
          % (len(added), len(updated), len(removed)))

    if not (added or removed or updated) and tip:
        print("✅ 内容未变，跳过，未推送。")
        print("SUMMARY: 无变化，未推送；%s/%s = %s；%d 个文件"
              % (args.remote, args.branch, tip[:8], len(paths)))
        return 0

    if args.dry_run:
        print("（--dry-run：仅预演，未构造提交、未推送）")
        print("SUMMARY: dry-run；将新建孤立链首个提交" if not tip
              else "SUMMARY: dry-run；将在 %s 之后追加 1 个提交" % tip[:8])
        return 0

    tree = build_tree(root, tip, added, updated, removed, new_map)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    if not tip:
        msg = "chore(ai-notes): 初始化 AI 规范资产孤立分支（%d 个文件）" % len(paths)
    else:
        msg = ("chore(ai-notes): %s · +%d ~%d -%d"
               % (stamp, len(added), len(updated), len(removed)))
    commit_args = ["commit-tree", tree]
    if tip:
        commit_args += ["-p", tip]
    commit_args += ["-m", msg]
    new_commit = git(commit_args, root)
    print("新提交     : %s  %s" % (new_commit[:8], msg))

    if args.no_push:
        print("（--no-push：已生成提交，未推送）")
        print("SUMMARY: --no-push；本地提交 %s 未推送" % new_commit[:8])
        return 0

    # 父提交就是远端 tip，天然快进；远端被并发改动时 git 自己会拒，绝不 force。
    git(["push", args.remote,
         "%s:refs/heads/%s" % (new_commit, args.branch)], root)
    after = remote_tip(root, args.remote, args.branch)
    if after != new_commit:
        print("❌ 推送后远端 sha 与预期不符：%s != %s，请人工检查"
              % (after[:8], new_commit[:8]))
        return 1

    print("同步后 sha : %s" % after[:8])
    print("✅ 完成：%d 个文件已备份到 %s/%s"
          % (len(paths), args.remote, args.branch))
    print("SUMMARY: %s → %s；+%d ~%d -%d；共 %d 个文件"
          % (tip[:8] if tip else "新建", after[:8],
             len(added), len(updated), len(removed), len(paths)))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GitError as e:
        print("\n❌ %s" % e)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
