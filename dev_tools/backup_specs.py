#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OAS 规范资产备份：把「不在 git 里、丢了没处找」的东西打成 zip。

备份什么（这些都被 .gitignore 排除或尚未提交）：
    .codebuddy/              rules + skills（AI 规则与技能）
    .workbuddy/memory/       长期记忆 + 日档
    dev_tools/oas_lint.py    代码自检脚本
    dev_tools/scaffold/      新任务骨架

不备份什么：
    .workbuddy/backup/       备份目录自身（避免递归）
    __pycache__ / *.pyc      编译缓存

用法：
    python dev_tools/backup_specs.py                # 打一份
    python dev_tools/backup_specs.py --list         # 看现有备份
    python dev_tools/backup_specs.py --dry-run      # 只报告不写盘
    python dev_tools/backup_specs.py --keep 60      # 保留 60 份（默认 30）
    python dev_tools/backup_specs.py --force        # 内容没变也打

设计要点：
    * 纯标准库，不 import 项目代码，任何 python 3.8+ 都能跑。
    * 每份 zip 内含 MANIFEST.txt（时间 / 分支 / 逐文件 sha256），
      既是内容指纹（用于「没变就不打」），也是以后比对哪份改了什么。
    * 只在有改动时生成，避免 30 份一模一样的包把目录塞满。
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKUP_DIR = ROOT / ".workbuddy" / "backup"
PREFIX = "specs"
MANIFEST_NAME = "MANIFEST.txt"

# (归档内前缀, 源路径)；源路径可以是文件或目录
SOURCES: list[tuple[str, Path]] = [
    (".codebuddy", ROOT / ".codebuddy"),
    (".workbuddy/memory", ROOT / ".workbuddy" / "memory"),
    ("dev_tools/oas_lint.py", ROOT / "dev_tools" / "oas_lint.py"),
    ("dev_tools/scaffold", ROOT / "dev_tools" / "scaffold"),
]

SKIP_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git"}
SKIP_FILES = {".DS_Store", "Thumbs.db", "desktop.ini"}
SKIP_SUFFIX = {".pyc", ".pyo", ".log"}


# ---------------------------------------------------------------- 收集


def collect() -> list[tuple[str, Path]]:
    """返回 [(归档内路径, 磁盘路径)]，按归档路径排序。"""
    items: list[tuple[str, Path]] = []
    for arc_prefix, src in SOURCES:
        if not src.exists():
            continue
        if src.is_file():
            items.append((arc_prefix, src))
            continue
        for p in sorted(src.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(src)
            if any(part in SKIP_DIRS for part in rel.parts):
                continue
            if p.name in SKIP_FILES or p.suffix in SKIP_SUFFIX:
                continue
            items.append((f"{arc_prefix}/{rel.as_posix()}", p))
    items.sort(key=lambda x: x[0])
    return items


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint(rows: list[tuple[str, Path, int, str]]) -> str:
    """所有文件「路径+内容哈希」拼起来的摘要，用来判断有没有变化。"""
    h = hashlib.sha256()
    for arc, _p, size, digest in rows:
        h.update(f"{arc}\0{size}\0{digest}\n".encode())
    return h.hexdigest()


# ---------------------------------------------------------------- 元信息


def git_info() -> tuple[str, str]:
    def run(*args: str) -> str:
        try:
            out = subprocess.run(
                ["git", *args],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=5,
            )
            return out.stdout.strip() if out.returncode == 0 else ""
        except Exception:
            return ""

    branch = run("rev-parse", "--abbrev-ref", "HEAD")
    head = run("rev-parse", "--short", "HEAD")
    return branch, head


def build_manifest(rows, fp: str) -> str:
    branch, head = git_info()
    total = sum(r[2] for r in rows)
    now = datetime.now().astimezone()
    lines = [
        "OAS specs backup",
        f"generated : {now.strftime('%Y-%m-%d %H:%M:%S %z')}",
        f"root      : {ROOT}",
        f"branch    : {branch or '?'} @ {head or '?'}",
        f"files     : {len(rows)}",
        f"bytes     : {total:,}",
        f"fingerprint: {fp}",
        "",
        f"{'path':<58} {'bytes':>8}  sha256[:12]",
        "-" * 82,
    ]
    for arc, _p, size, digest in rows:
        lines.append(f"{arc:<58} {size:>8,}  {digest[:12]}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- 现有备份


def existing() -> list[Path]:
    """按写入时间从旧到新排序。

    不能按文件名排：同一分钟内生成第二份会叫 `specs-YYYYMMDD-HHMM-2.zip`，
    而字典序里 `-` 小于 `.`，`-2` 会排到主文件前面，导致「保留最新」删错对象。
    """
    if not BACKUP_DIR.exists():
        return []
    zips = list(BACKUP_DIR.glob(f"{PREFIX}-*.zip"))
    return sorted(zips, key=lambda p: (p.stat().st_mtime, p.name))


def latest_fingerprint() -> str:
    zips = existing()
    if not zips:
        return ""
    try:
        with zipfile.ZipFile(zips[-1]) as zf:
            text = zf.read(MANIFEST_NAME).decode("utf-8", "replace")
    except Exception:
        return ""
    for line in text.splitlines():
        if line.startswith("fingerprint:"):
            return line.split(":", 1)[1].strip()
    return ""


def prune(keep: int) -> list[str]:
    zips = existing()
    removed = []
    for old in zips[:-keep] if keep > 0 else []:
        try:
            old.unlink()
            removed.append(old.name)
        except OSError as exc:
            print(f"  ! 删不掉 {old.name}: {exc}")
    return removed


# ---------------------------------------------------------------- 主流程


def cmd_list() -> int:
    zips = existing()
    if not zips:
        print(f"还没有备份。目录：{BACKUP_DIR}")
        return 0
    print(f"{'文件':<34} {'大小':>10}  {'文件数':>6}  指纹")
    print("-" * 78)
    for z in zips:
        try:
            with zipfile.ZipFile(z) as zf:
                text = zf.read(MANIFEST_NAME).decode("utf-8", "replace")
        except Exception:
            text = ""
        files = fp = "?"
        for line in text.splitlines():
            if line.startswith("files"):
                files = line.split(":", 1)[1].strip()
            elif line.startswith("fingerprint:"):
                fp = line.split(":", 1)[1].strip()[:12]
        print(f"{z.name:<34} {z.stat().st_size:>10,}  {files:>6}  {fp}")
    print("-" * 78)
    print(f"共 {len(zips)} 份，合计 {sum(z.stat().st_size for z in zips):,} 字节")
    return 0


def cmd_backup(keep: int, force: bool, dry_run: bool) -> int:
    files = collect()
    if not files:
        print("SUMMARY: 没有可备份的文件，检查路径是否对")
        return 1

    rows = []
    for arc, p in files:
        try:
            rows.append((arc, p, p.stat().st_size, sha256_of(p)))
        except OSError as exc:
            print(f"  ! 读不到 {arc}: {exc}")

    fp = fingerprint(rows)
    total = sum(r[2] for r in rows)

    if not force and fp and fp == latest_fingerprint():
        print(f"SUMMARY: 内容未变（{len(rows)} 文件 / {total:,} 字节，指纹 {fp[:12]}），跳过。"
              "要强制备份加 --force")
        return 0

    name = f"{PREFIX}-{datetime.now().strftime('%Y%m%d-%H%M')}.zip"
    target = BACKUP_DIR / name
    n = 1
    while target.exists():
        n += 1
        target = BACKUP_DIR / f"{PREFIX}-{datetime.now().strftime('%Y%m%d-%H%M')}-{n}.zip"
    name = target.name

    if dry_run:
        print(f"SUMMARY(dry-run): 会写 {name}，{len(rows)} 文件 / {total:,} 字节，指纹 {fp[:12]}")
        for arc, _p, size, _d in rows:
            print(f"  {arc}  {size:,}")
        return 0

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(rows, fp)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for arc, p, _size, _d in rows:
            zf.write(p, arc)
        zf.writestr(MANIFEST_NAME, manifest)

    packed = target.stat().st_size
    ratio = total / packed if packed else 0
    removed = prune(keep)
    left = len(existing())
    print(
        f"SUMMARY: {len(rows)} 文件 / {total:,} 字节 → {name} "
        f"({packed:,} 字节, {ratio:.1f}:1) | 保留 {left}/{keep}"
    )
    for r in removed:
        print(f"  已清理旧备份 {r}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="备份 .codebuddy/ 与 .workbuddy/memory/ 等不在 git 里的规范资产",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--keep", type=int, default=30, help="保留最近几份（默认 30）")
    ap.add_argument("--force", action="store_true", help="内容没变也生成")
    ap.add_argument("--dry-run", action="store_true", help="只报告不写盘")
    ap.add_argument("--list", action="store_true", help="列出现有备份")
    args = ap.parse_args(argv)

    if args.list:
        return cmd_list()
    return cmd_backup(keep=args.keep, force=args.force, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
