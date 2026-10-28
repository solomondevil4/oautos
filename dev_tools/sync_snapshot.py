#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把本地自建改动同步到 oautos/Forme 快照分支（层次一：整份文件）。

快照分支的定位
    `oautos/Forme` 是一条**孤立链**（首个提交无父），只存「我们相对 Hurry2
    上游改过的那些文件」的完整内容，不存上游代码，也不含上游历史。
    它能用来单文件恢复，但不能跑、不能提 PR、不能合上游。

收录规则（这是本脚本的全部价值所在）
    1. 候选集 = `git diff --name-only $(git merge-base 上游 源) 源`
       必须用 merge-base，不能用 `git diff 上游 源`。后者在源分支落后上游时，
       会把「上游新改的文件」误判成我们的改动。
    2. 逐个文件双保险：在源里的 blob 必须**不等于**上游 blob 才收录。
       相等说明我们没改（或已改回上游版），一律不收。
       这一条会自动清掉历史遗留 —— 例如鼬乐币换回上游版之后，
       tasks/RichMan/ 下 8 个文件已经和上游一模一样，本就该移出快照。
    3. 源里已删除的文件 → 从快照里删除。
    4. 快照集合 == 第 2 步筛出来的集合，多一个不留。

用法
    python dev_tools/sync_snapshot.py --status     # 只看清单，不动任何东西
    python dev_tools/sync_snapshot.py --dry-run    # 看会怎么改（不提交不推送）
    python dev_tools/sync_snapshot.py              # 同步 + 推送
    python dev_tools/sync_snapshot.py --no-push    # 只做本地提交，不推
    python dev_tools/sync_snapshot.py --src Forme  # 换个源分支

设计要点
    * 纯标准库，不 import 项目代码。
    * 只在快照链上**追加**提交（父提交 = 快照当前 tip），所以永远是快进，
      不需要 --force；推送时再用 --force-with-lease 钉住旧 sha 防并发。
    * 新树与旧树相同则直接跳过，不产生空提交。
    * 提交信息末尾写 `Self-Snapshot-Src: <源 sha>`，下次据此算「这次同步
      带来了哪些自建提交」，不用手工记。
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile

# ---------------------------------------------------------------- 默认参数

DEFAULT_SRC = "self"           # 自建改动所在的本地分支
DEFAULT_UPSTREAM = "origin/Forme"  # Hurry2 上游
DEFAULT_REMOTE = "oautos"      # 快照所在的远端
DEFAULT_BRANCH = "Forme"       # 快照分支名
TRAILER = "Self-Snapshot-Src"  # 提交信息里记录源 sha 的标记


# ---------------------------------------------------------------- git 封装


class GitError(RuntimeError):
    pass


def _run(args, cwd, env=None, binary=False):
    cmd = ["git", "-c", "core.quotepath=false"] + list(args)
    p = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        env=env,
    )
    if p.returncode != 0:
        msg = (p.stderr or p.stdout).decode("utf-8", "replace").strip()
        raise GitError("git %s 失败：%s" % (" ".join(args), msg))
    return p.stdout if binary else p.stdout.decode("utf-8", "replace")


def git(args, cwd, env=None):
    return _run(args, cwd, env=env).strip()


def git_b(args, cwd, env=None):
    return _run(args, cwd, env=env, binary=True)


def find_repo_root(start=None):
    here = os.path.abspath(start or os.path.dirname(os.path.abspath(__file__)))
    p = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=here, capture_output=True)
    if p.returncode != 0:
        raise SystemExit("当前目录不在 git 仓库内：%s" % here)
    return p.stdout.decode("utf-8", "replace").strip()


# ---------------------------------------------------------------- 树与集合


def ls_tree(ref, root):
    """返回 {路径: (mode, blob_sha)}，一次性读整棵树。"""
    out = git_b(["ls-tree", "-r", "-z", ref], root)
    res = {}
    for rec in out.split(b"\0"):
        if not rec:
            continue
        meta, _, path = rec.partition(b"\t")
        parts = meta.split(b" ")
        if len(parts) != 3:
            continue
        mode, typ, sha = parts
        if typ != b"blob":
            continue
        res[path.decode("utf-8", "replace")] = (
            mode.decode("ascii"),
            sha.decode("ascii"),
        )
    return res


def diff_names(base, head, root):
    out = git_b(["diff", "--name-only", "--no-renames", "-z", base, head], root)
    return [p.decode("utf-8", "replace") for p in out.split(b"\0") if p]


def classify(src_map, upstream_map, candidates):
    """把候选文件分成「该收」「不收」两类，并说明原因。"""
    keep, drop = {}, {}
    for path in candidates:
        if path not in src_map:
            drop[path] = "源分支里已删除"
            continue
        blob = src_map[path][1]
        up = upstream_map.get(path)
        if up is None:
            keep[path] = "上游没有此文件（我们新增的）"
        elif up[1] == blob:
            drop[path] = "与上游完全一致（不是我们的改动）"
        else:
            keep[path] = "与上游不同"
    return keep, drop


# ---------------------------------------------------------------- 快照操作


def remote_tip(root, remote, branch):
    out = git(["ls-remote", "--heads", remote, "refs/heads/%s" % branch], root)
    if not out:
        raise GitError("远端 %s 上没有分支 %s" % (remote, branch))
    return out.split()[0]


def ensure_object(root, sha, remote, branch):
    p = subprocess.run(
        ["git", "cat-file", "-e", "%s^{commit}" % sha],
        cwd=root,
        capture_output=True,
    )
    if p.returncode == 0:
        return
    print("  本地缺少快照对象，先从 %s 拉取 ..." % remote)
    git(["fetch", remote, "refs/heads/%s:refs/tmp/snapshot-fetch" % branch], root)
    git(["cat-file", "-e", "%s^{commit}" % sha], root)


def build_tree(root, tip, snapshot_map, keep, src_map):
    """以快照当前树为底，只保留 keep 里的文件，返回新 tree sha。"""
    idx = os.path.join(tempfile.gettempdir(), "oas_snapshot_%d.idx" % os.getpid())
    if os.path.exists(idx):
        os.remove(idx)
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = idx
    try:
        git(["read-tree", tip], root, env=env)
        changed = 0
        for path in sorted(keep):
            mode, sha = src_map[path]
            if snapshot_map.get(path) == (mode, sha):
                continue
            git(
                ["update-index", "--add", "--cacheinfo", "%s,%s,%s" % (mode, sha, path)],
                root,
                env=env,
            )
            changed += 1
        for path in sorted(set(snapshot_map) - set(keep)):
            git(["update-index", "--force-remove", "--", path], root, env=env)
            changed += 1
        tree = git(["write-tree"], root, env=env)
        return tree, changed
    finally:
        if os.path.exists(idx):
            os.remove(idx)


def prev_src(root, tip):
    """从快照提交信息里读上次同步的源 sha。"""
    try:
        body = git(["log", "-1", "--format=%B", tip], root)
    except GitError:
        return None
    m = re.search(r"^%s:\s*([0-9a-f]{7,40})\s*$" % re.escape(TRAILER), body, re.M)
    return m.group(1) if m else None


def new_commits(root, base, head):
    """列出 base..head 之间我们自己的提交（去掉合并提交）。"""
    if not base:
        return []
    p = subprocess.run(
        ["git", "merge-base", "--is-ancestor", base, head],
        cwd=root,
        capture_output=True,
    )
    if p.returncode != 0:
        return []
    out = git(["log", "--oneline", "--no-merges", "%s..%s" % (base, head)], root)
    return [ln for ln in out.splitlines() if ln.strip()]


def commit_message(keep, drop, added, updated, removed, commits, src_sha):
    lines = ["chore(snapshot): 同步自建改动快照", ""]
    lines.append("快照共 %d 个文件（新增 %d / 更新 %d / 移除 %d）。"
                 % (len(keep), len(added), len(updated), len(removed)))
    if added or updated or removed:
        lines.append("")
        for f in sorted(keep):
            if f in added:
                lines.append("  + %s" % f)
            elif f in updated:
                lines.append("  ~ %s" % f)
        for f in sorted(removed):
            lines.append("  - %s" % f)
    if commits:
        lines.append("")
        lines.append("自上次同步以来的自建提交：")
        for c in commits[:20]:
            lines.append("  %s" % c)
        if len(commits) > 20:
            lines.append("  ...（共 %d 条，仅列前 20）" % len(commits))
    if drop:
        lines.append("")
        lines.append("未收录（与上游一致或已删除）：%d 个" % len(drop))
    lines.append("")
    lines.append("%s: %s" % (TRAILER, src_sha))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 主流程


def main():
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass

    ap = argparse.ArgumentParser(
        description="把本地自建改动同步到 oautos/Forme 快照分支（只收我们改过的文件）"
    )
    ap.add_argument("--src", default=DEFAULT_SRC, help="自建改动所在分支（默认 %s）" % DEFAULT_SRC)
    ap.add_argument("--upstream", default=DEFAULT_UPSTREAM, help="上游引用（默认 %s）" % DEFAULT_UPSTREAM)
    ap.add_argument("--remote", default=DEFAULT_REMOTE, help="快照远端（默认 %s）" % DEFAULT_REMOTE)
    ap.add_argument("--branch", default=DEFAULT_BRANCH, help="快照分支（默认 %s）" % DEFAULT_BRANCH)
    ap.add_argument("--status", action="store_true", help="只列清单，不提交不推送")
    ap.add_argument("--dry-run", action="store_true", help="演练：显示会怎么改，不提交不推送")
    ap.add_argument("--no-push", action="store_true", help="做本地提交但不推送")
    ap.add_argument("--no-fetch", action="store_true", help="跳过 git fetch")
    args = ap.parse_args()

    root = find_repo_root()
    print("仓库：%s" % root)

    if not args.no_fetch:
        print("\n[1/6] 拉取上游引用 ...")
        try:
            git(["fetch", "origin", "--prune"], root)
        except GitError as e:
            print("  fetch 失败（继续用本地已有引用）：%s" % e)
    else:
        print("\n[1/6] 跳过 fetch")

    print("\n[2/6] 解析分支 ...")
    src_sha = git(["rev-parse", args.src], root)
    up_sha = git(["rev-parse", args.upstream], root)
    mb = git(["merge-base", args.upstream, args.src], root)
    print("  源    %-14s %s" % (args.src, src_sha[:8]))
    print("  上游  %-14s %s" % (args.upstream, up_sha[:8]))
    print("  分叉点%-13s %s" % ("", mb[:8]))
    ahead = len(git(["log", "--oneline", "%s..%s" % (args.upstream, args.src)], root).splitlines())
    print("  源分支相对上游独有 %d 个提交" % ahead)

    print("\n[3/6] 计算候选文件（merge-base..%s）..." % args.src)
    candidates = diff_names(mb, src_sha, root)
    src_map = ls_tree(src_sha, root)
    upstream_map = ls_tree(up_sha, root)
    keep, drop = classify(src_map, upstream_map, candidates)
    print("  候选 %d 个 → 收录 %d，剔除 %d" % (len(candidates), len(keep), len(drop)))

    if keep:
        print("\n  收录清单：")
        for f in sorted(keep):
            print("    + %s" % f)
    if drop:
        print("\n  剔除清单：")
        for f in sorted(drop):
            print("    - %-56s %s" % (f, drop[f]))

    print("\n[4/6] 读取快照分支 %s/%s ..." % (args.remote, args.branch))
    tip = remote_tip(root, args.remote, args.branch)
    ensure_object(root, tip, args.remote, args.branch)
    snapshot_map = ls_tree(tip, root)
    print("  当前快照 %s（%d 个文件）" % (tip[:8], len(snapshot_map)))

    added = sorted(set(keep) - set(snapshot_map))
    updated = sorted(f for f in keep if f in snapshot_map and snapshot_map[f] != src_map[f])
    removed = sorted(set(snapshot_map) - set(keep))
    unchanged = sorted(f for f in keep if f in snapshot_map and snapshot_map[f] == src_map[f])
    print("  新增 %d / 更新 %d / 移除 %d / 已相同 %d"
          % (len(added), len(updated), len(removed), len(unchanged)))
    for f in added:
        print("    + %s" % f)
    for f in updated:
        print("    ~ %s" % f)
    for f in removed:
        print("    - %s" % f)

    if not (added or updated or removed):
        print("\n✅ 快照已是最新，无需提交。")
        return 0

    if args.status or args.dry_run:
        print("\n（--%s 模式：未提交、未推送）" % ("status" if args.status else "dry-run"))
        return 0

    print("\n[5/6] 构造新快照提交 ...")
    tree, changed = build_tree(root, tip, snapshot_map, keep, src_map)
    old_tree = git(["rev-parse", "%s^{tree}" % tip], root)
    if tree == old_tree:
        print("✅ 树没有变化，跳过。")
        return 0
    commits = new_commits(root, prev_src(root, tip), src_sha)
    msg = commit_message(keep, drop, added, updated, removed, commits, src_sha)
    new = git(["commit-tree", tree, "-p", tip, "-m", msg], root)
    print("  新提交 %s（改动 %d 处）" % (new[:8], changed))

    if args.no_push:
        print("\n（--no-push：已本地提交，未推送）")
        return 0

    print("\n[6/6] 推送（快进 + lease 保护）...")
    git(
        [
            "push",
            args.remote,
            "%s:refs/heads/%s" % (new, args.branch),
            "--force-with-lease=refs/heads/%s:%s" % (args.branch, tip),
        ],
        root,
    )
    after = remote_tip(root, args.remote, args.branch)
    print("  %s: %s → %s" % (args.branch, tip[:8], after[:8]))
    if after != new:
        print("⚠ 推送后远端 sha 与预期不符，请检查")
        return 1
    print("\n✅ 完成：快照 %d 个文件，提交 %s" % (len(keep), new[:8]))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GitError as e:
        print("\n❌ %s" % e)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
