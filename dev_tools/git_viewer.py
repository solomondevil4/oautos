#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地 Git 提交查看器 + 定时关机

纯标准库，不依赖项目代码，可直接用 toolkit/python.exe 或任意 Python 3.8+ 运行。

用法:
    python dev_tools/git_viewer.py                 # 默认 8765 端口，自动开浏览器
    python dev_tools/git_viewer.py --port 9000     # 指定端口
    python dev_tools/git_viewer.py --no-browser    # 不自动开浏览器

    # 不开网页也能用（跑完即退）
    python dev_tools/git_viewer.py --at 23:30      # 今天/明天 23:30 关机
    python dev_tools/git_viewer.py --in 90         # 90 分钟后关机
    python dev_tools/git_viewer.py --status        # 看还有多久关机
    python dev_tools/git_viewer.py --cancel        # 取消挂起的关机

安全约定:
    - 只监听 127.0.0.1，不接受外部访问
    - 目标时刻至少留 60 秒，最多 7 天，防止误触
    - 倒计时期间任意时刻都能取消（含最后 1 分钟）；网页打不开时
      双击 dev_tools/cancel_shutdown.bat 或 cmd 里 shutdown /a 同样有效
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# ---------------------------------------------------------------- 基础工具

SEP_FIELD = "\x1f"
SEP_LINE = "\x1e"

SHUTDOWN_AT = None          # epoch 秒；None 表示没有挂起的关机
SHUTDOWN_TASK = None        # 下发时的剩余秒数，仅用于展示
SHUTDOWN_MODE = None        # "at" 指定时刻 / "seconds" 倒计时
LOCK = threading.Lock()

# 计划落盘到临时目录，让「命令行设的」和「网页设的」互相看得见 ——
# 否则用 --at 23:30 设完再打开网页，页面会显示没有计划，非常危险。
STATE_FILE = os.path.join(tempfile.gettempdir(), "oas_git_viewer_shutdown.json")


def save_plan(at, task, mode):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"at": at, "task": task, "mode": mode}, f)
    except OSError:
        pass


def load_plan():
    """读回其它进程（或上次运行）设置的计划；已过期则视为没有。"""
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        at = float(d.get("at") or 0)
        if at > time.time():
            return at, d.get("task"), d.get("mode")
    except (OSError, ValueError, TypeError):
        pass
    return None, None, None


def clear_plan():
    try:
        os.remove(STATE_FILE)
    except OSError:
        pass


def find_repo_root(start=None):
    """从脚本所在目录向上找 .git，找不到就用当前工作目录。"""
    cur = os.path.abspath(start or os.path.dirname(os.path.abspath(__file__)))
    for _ in range(8):
        if os.path.isdir(os.path.join(cur, ".git")) or os.path.isfile(
            os.path.join(cur, ".git")
        ):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return os.getcwd()


REPO_ROOT = find_repo_root()

# 关键：本脚本经常以 pythonw.exe（无控制台）形式在后台跑。
# 那种情况下每个控制台子程序（git.exe / tasklist.exe / shutdown.exe）都会
# 分配一个**新的可见黑窗口**，前端每 1 秒轮询一次 /api/state 就会每秒闪现一个
# cmd 窗口。所有 subprocess 都必须带 CREATE_NO_WINDOW 才是真正的后台服务。
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0



def git(*args, timeout=30):
    """跑一条 git 命令，返回 (ok, stdout, stderr)。"""
    cmd = ["git", "-c", "core.quotepath=false"] + list(args)
    try:
        p = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=NO_WINDOW,
        )
        return p.returncode == 0, p.stdout, p.stderr
    except Exception as exc:  # noqa: BLE001
        return False, "", str(exc)


def shutdown_exe():
    """Windows 的 shutdown.exe 绝对路径，Git Bash 下调 subprocess 更稳。"""
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return os.path.join(root, "System32", "shutdown.exe")


def run_shutdown(args, timeout=20):
    """执行 shutdown.exe，返回 (returncode, message)。

    中文系统的 shutdown.exe 按 GBK 输出，用 UTF-8 解码会成乱码，必须用 gbk。
    """
    exe = shutdown_exe()
    try:
        p = subprocess.run(
            [exe] + list(args),
            capture_output=True,
            timeout=timeout,
            creationflags=NO_WINDOW,
        )
    except Exception as exc:  # noqa: BLE001
        return -1, f"调用 shutdown 失败: {exc}"
    raw = p.stderr or p.stdout or b""
    msg = raw.decode("gbk", errors="replace").strip()
    return p.returncode, msg


_OAS_CACHE = {"t": 0.0, "v": False}
_OAS_TTL = 10.0


def oas_running():
    """粗查 OAS 进程是否在跑，用于关机前提示。

    /api/state 每秒被前端轮询一次，如果每次都跑 tasklist 就是每秒起一个
    进程。这里缓存 10 秒，够用且几乎零开销。
    """
    if os.name != "nt":
        return False
    now = time.time()
    if now - _OAS_CACHE["t"] < _OAS_TTL:
        return _OAS_CACHE["v"]
    try:
        p = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq oasx.exe", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=NO_WINDOW,
        )
        running = "oasx.exe" in (p.stdout or "").lower()
    except Exception:  # noqa: BLE001
        running = False
    _OAS_CACHE["t"] = now
    _OAS_CACHE["v"] = running
    return running


# ---------------------------------------------------------------- API 实现


def api_branches():
    ok, out, err = git(
        "for-each-ref",
        "--sort=-committerdate",
        "--format=%(refname:short)%1f%(refname)%1f%(committerdate:format:%Y-%m-%d %H:%M)%1f%(subject)",
        "refs/heads",
        "refs/remotes",
    )
    if not ok:
        return {"ok": False, "error": err.strip() or "读取分支失败"}

    cur_ok, cur_out, _ = git("branch", "--show-current")
    current = cur_out.strip() if cur_ok else ""

    branches = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split(SEP_FIELD)
        if len(parts) < 4:
            continue
        short, full, when, subject = parts[0], parts[1], parts[2], parts[3]
        # refs/remotes/<远端>/HEAD 的短名会被压成「远端名」，必须按全名过滤
        if full.endswith("/HEAD"):
            continue
        kind = "local" if full.startswith("refs/heads/") else "remote"
        branches.append(
            {
                "name": short,
                "kind": kind,
                "date": when,
                "subject": subject,
                "current": kind == "local" and short == current,
            }
        )
    return {"ok": True, "branches": branches, "current": current}


def api_commits(ref, limit, query):
    fmt = SEP_FIELD.join(
        ["%H", "%h", "%an", "%ad", "%ar", "%s", "%D"]
    )
    args = [
        "log",
        f"--max-count={limit}",
        f"--format={fmt}{SEP_LINE}",
        "--date=format:%Y-%m-%d %H:%M",
    ]
    if query:
        args += ["--grep=" + query, "-i", "--all" if ref in ("", "*") else ref]
    elif ref and ref != "*":
        args += [ref]
    else:
        args += ["--all"]

    ok, out, err = git(*args)
    if not ok:
        return {"ok": False, "error": err.strip() or "读取提交失败"}

    commits = []
    for rec in out.split(SEP_LINE):
        rec = rec.strip("\n")
        if not rec.strip():
            continue
        parts = rec.split(SEP_FIELD)
        if len(parts) < 7:
            continue
        commits.append(
            {
                "sha": parts[0],
                "short": parts[1],
                "author": parts[2],
                "date": parts[3],
                "relative": parts[4],
                "subject": parts[5],
                "refs": [r.strip() for r in parts[6].split(",") if r.strip()],
            }
        )
    return {"ok": True, "commits": commits, "count": len(commits)}


def api_commit(sha):
    fmt = SEP_FIELD.join(["%H", "%h", "%an", "%ae", "%ad", "%s", "%b", "%P"])
    ok, out, err = git("show", "-s", f"--format={fmt}", "--date=format:%Y-%m-%d %H:%M", sha)
    if not ok:
        return {"ok": False, "error": err.strip() or "读取提交失败"}

    parts = out.split(SEP_FIELD)
    if len(parts) < 8:
        return {"ok": False, "error": "提交信息解析失败"}
    parents = [x for x in parts[7].split() if x]

    ok2, stat_out, _ = git("show", "--numstat", "--format=", sha)
    files = []
    total_add = total_del = 0
    if ok2:
        for line in stat_out.splitlines():
            if not line.strip():
                continue
            cols = line.split("\t")
            if len(cols) < 3:
                continue
            add, dele, path = cols[0], cols[1], cols[2]
            add_n = int(add) if add.isdigit() else 0
            del_n = int(dele) if dele.isdigit() else 0
            total_add += add_n
            total_del += del_n
            files.append(
                {
                    "path": path,
                    "add": add_n if add.isdigit() else None,
                    "del": del_n if dele.isdigit() else None,
                    "binary": not add.isdigit(),
                }
            )

    return {
        "ok": True,
        "commit": {
            "sha": parts[0],
            "short": parts[1],
            "author": parts[2],
            "email": parts[3],
            "date": parts[4],
            "subject": parts[5],
            "body": parts[6].strip(),
            "parents": parents,
            "files": files,
            "total_add": total_add,
            "total_del": total_del,
        },
    }


MIN_DELAY = 60               # 离目标时刻至少留 1 分钟，防止手滑点成立即关机
MAX_DELAY = 7 * 86400        # Windows /t 能设很远，但超过 7 天没意义


def parse_clock(hhmm, now=None):
    """'23:30' → 距离最近的那个未来时刻的 epoch 秒（今天没过就今天，过了算明天）。"""
    now = now if now is not None else time.time()
    s = str(hhmm or "").strip().replace("：", ":")
    if not s:
        raise ValueError("时刻为空")
    if s.count(":") != 1:
        raise ValueError("时刻格式应为 HH:MM")
    hh, mm = s.split(":")
    try:
        hh, mm = int(hh), int(mm)
    except ValueError:
        raise ValueError("时刻里的数字不合法")
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError("时刻超出范围")

    lt = time.localtime(now)
    target = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hh, mm, 0, 0, 0, -1))
    if target <= now:
        # 今天这个点已经过去，顺延到明天同一时刻。
        # 注意：只对「已过去」顺延；「接下来不到 1 分钟」要留给上层报错，
        # 否则用户想要 3 分钟后关机、结果被悄悄排到明天，非常危险。
        target = time.mktime(
            (lt.tm_year, lt.tm_mon, lt.tm_mday + 1, hh, mm, 0, 0, 0, -1)
        )
    return target


def fmt_target(epoch):
    """把目标时刻说人话：今天 23:30 / 明天 01:00 / 10-29 08:00。

    必须按本地日期比较，不能用 epoch//86400 —— 那是 UTC 日界，
    北京时间凌晨 0 点会被算成「今天」。
    """
    if not epoch:
        return ""
    lt = time.localtime(epoch)
    days = (datetime.date.fromtimestamp(epoch) - datetime.date.today()).days
    hm = "%02d:%02d" % (lt.tm_hour, lt.tm_min)
    if lt.tm_sec:
        # 滚轮选了秒，或者倒计时的落点不在整分，就带上秒，别让显示比实际粗
        hm += ":%02d" % lt.tm_sec
    if days <= 0:
        return "今天 " + hm
    if days == 1:
        return "明天 " + hm
    return "%02d-%02d %s" % (lt.tm_mon, lt.tm_mday, hm)


def api_shutdown(seconds=None, at=None, clock=None):
    """设置定时关机。三种入参任选其一：秒数、绝对 epoch、HH:MM 时刻。"""
    global SHUTDOWN_AT, SHUTDOWN_TASK, SHUTDOWN_MODE
    if os.name != "nt":
        return {"ok": False, "error": "只支持 Windows"}

    try:
        if clock:
            at = parse_clock(clock)
        if at is not None:
            target = float(at)
            delay = int(round(target - time.time()))
            mode = "at"
        elif seconds is not None:
            delay = int(seconds)
            target = time.time() + delay
            mode = "seconds"
        else:
            return {"ok": False, "error": "缺少时间参数"}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    if delay < MIN_DELAY:
        return {
            "ok": False,
            "error": f"距目标只剩 {max(delay, 0)} 秒，至少要留 {MIN_DELAY} 秒",
        }
    if delay > MAX_DELAY:
        return {"ok": False, "error": "最多只能设 7 天以内"}

    # 已有挂起的关机时，Windows 的 shutdown /s /t 会直接返回 1190（已在关闭中），
    # 想改时间必须先用 /a 清掉旧的。这里一律先取消再重设，让「改时间」能用。
    run_shutdown(["/a"])

    rc, msg = run_shutdown(["/s", "/t", str(delay)])
    if rc != 0:
        return {"ok": False, "error": msg or f"shutdown 返回 {rc}"}

    with LOCK:
        SHUTDOWN_AT = target
        SHUTDOWN_TASK = delay
        SHUTDOWN_MODE = mode
    save_plan(target, delay, mode)
    return {
        "ok": True,
        "seconds": delay,
        "at": target,
        "label": fmt_target(target),
        "mode": mode,
    }


def api_cancel_shutdown():
    """取消挂起的关机。Windows 的 /a 在倒计时最后一秒之前都有效。"""
    global SHUTDOWN_AT, SHUTDOWN_TASK, SHUTDOWN_MODE
    if os.name != "nt":
        return {"ok": False, "error": "只支持 Windows"}

    rc, msg = run_shutdown(["/a"])

    # 1116 = 没有正在进行的关机，也视为「已取消」状态
    with LOCK:
        had = SHUTDOWN_AT is not None
        SHUTDOWN_AT = None
        SHUTDOWN_TASK = None
        SHUTDOWN_MODE = None
    clear_plan()
    if rc == 0:
        return {"ok": True, "cancelled": True, "message": "已取消定时关机"}
    if rc == 1116:
        return {"ok": True, "cancelled": had, "message": "当前没有挂起的关机"}
    return {"ok": False, "error": msg or f"shutdown /a 返回 {rc}"}


def api_state():
    with LOCK:
        at = SHUTDOWN_AT
        task = SHUTDOWN_TASK
        mode = SHUTDOWN_MODE
    if not at:
        # 可能是命令行（另一个进程）设的，读落盘的计划
        at, task, mode = load_plan()
    now = time.time()
    remaining = int(round(at - now)) if at else None
    if remaining is not None and remaining < 0:
        remaining = 0
    return {
        "ok": True,
        "shutdown_at": at,
        "remaining": remaining,
        "task": task,
        "mode": mode,
        # 最后 60 秒前端要变红并提示「仍可取消」
        "critical": bool(remaining is not None and remaining <= 60),
        "label": fmt_target(at) if at else "",
        "oas_running": oas_running(),
        "repo": REPO_ROOT,
        "now": now,
    }


# ---------------------------------------------------------------- HTTP 服务

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Git 提交查看器</title>
<style>
:root{
  --bg:#0f1115; --panel:#171a21; --panel2:#1d212a; --line:#272c37;
  --text:#e6e9ef; --dim:#8b93a3; --accent:#5b9dff; --accent2:#3d7eff;
  --green:#3fb950; --red:#f85149; --yellow:#d29922; --radius:10px;
}
*{box-sizing:border-box}
html,body{height:100%}
body{
  margin:0; background:var(--bg); color:var(--text);
  font:14px/1.5 "Segoe UI","Microsoft YaHei",system-ui,sans-serif;
  display:flex; flex-direction:column; overflow:hidden;
}
header{
  display:flex; align-items:center; gap:12px; padding:10px 16px;
  background:var(--panel); border-bottom:1px solid var(--line); flex:0 0 auto;
}
header h1{font-size:15px; margin:0; font-weight:600; letter-spacing:.3px}
header .repo{color:var(--dim); font-size:12px; max-width:38ch; overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
.spacer{flex:1}
button,select,input{
  font:inherit; color:var(--text); background:var(--panel2);
  border:1px solid var(--line); border-radius:8px; padding:6px 11px; outline:none;
}
button{cursor:pointer; transition:.15s}
button:hover{border-color:#3a4252; background:#232833}
button.primary{background:var(--accent2); border-color:var(--accent2); color:#fff}
button.primary:hover{background:var(--accent)}
button.danger{background:#4a1d1d; border-color:#6b2b2b; color:#ffb4b4}
button.danger:hover{background:#5d2424}
button.ghost{background:transparent}
input:focus,select:focus{border-color:var(--accent)}
main{flex:1; display:flex; min-height:0}
aside{
  width:250px; flex:0 0 auto; border-right:1px solid var(--line);
  background:var(--panel); overflow-y:auto; padding:8px;
}
aside .group{color:var(--dim); font-size:11px; text-transform:uppercase;
  letter-spacing:.8px; padding:10px 8px 4px}
.br{
  padding:7px 9px; border-radius:8px; cursor:pointer; display:flex;
  align-items:center; gap:7px; margin-bottom:2px;
}
.br:hover{background:var(--panel2)}
.br.active{background:#1e2b45; box-shadow:inset 2px 0 0 var(--accent)}
.br .nm{flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:13px}
.br .dot{width:6px;height:6px;border-radius:50%;background:#3a4252;flex:0 0 auto}
.br.active .dot{background:var(--accent)}
.br .badge{font-size:10px;color:var(--dim)}
section{flex:1; display:flex; flex-direction:column; min-width:0}
.toolbar{
  display:flex; gap:8px; align-items:center; padding:10px 14px;
  border-bottom:1px solid var(--line); flex:0 0 auto;
}
.toolbar input{flex:0 1 260px}
.list{flex:1; overflow-y:auto; padding:6px 8px 40px}
.cm{
  display:grid; grid-template-columns:auto 1fr auto; gap:4px 12px;
  padding:9px 11px; border-radius:9px; cursor:pointer; align-items:baseline;
}
.cm:hover{background:var(--panel)}
.cm.sel{background:#1e2b45; box-shadow:inset 2px 0 0 var(--accent)}
.cm .sha{font-family:Consolas,monospace; color:var(--accent); font-size:12.5px}
.cm .subj{overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
.cm .meta{color:var(--dim); font-size:12px; white-space:nowrap}
.refs{display:flex; gap:5px; flex-wrap:wrap; margin-top:3px; grid-column:2/4}
.ref{
  font-size:10.5px; padding:1px 6px; border-radius:999px;
  background:#22303f; color:#8fc0ff; border:1px solid #2f4257;
}
.ref.head{background:#1f3a24; color:#7ee08a; border-color:#2c5233}
.detail{
  width:0; flex:0 0 auto; overflow:hidden; background:var(--panel);
  border-left:1px solid var(--line); transition:width .18s ease;
  display:flex; flex-direction:column;
}
.detail.open{width:420px}
.detail .dhead{padding:14px 16px; border-bottom:1px solid var(--line)}
.detail .dbody{flex:1; overflow-y:auto; padding:12px 16px 40px}
.detail h2{font-size:14px; margin:0 0 8px; line-height:1.45; font-weight:600}
.kv{display:grid; grid-template-columns:70px 1fr; gap:5px 10px; font-size:12.5px; color:var(--dim)}
.kv b{color:var(--text); font-weight:500; overflow-wrap:anywhere}
.files{margin-top:14px}
.file{
  display:grid; grid-template-columns:1fr auto; gap:8px; padding:5px 0;
  border-bottom:1px solid #20242d; font-family:Consolas,monospace; font-size:12px;
}
.file .p{overflow-wrap:anywhere; color:#c8cfdb}
.file .n{white-space:nowrap; color:var(--dim)}
.file .n i{font-style:normal; color:var(--green)}
.file .n s{text-decoration:none; color:var(--red)}
.body-text{
  margin-top:12px; padding:10px; background:var(--panel2); border-radius:8px;
  white-space:pre-wrap; font-size:12.5px; color:#c8cfdb; line-height:1.6;
}
.mask{
  position:fixed; inset:0; background:rgba(5,7,10,.62); display:none;
  align-items:center; justify-content:center; z-index:50;
}
.mask.show{display:flex}
.modal{
  width:430px; background:var(--panel); border:1px solid var(--line);
  border-radius:14px; padding:20px; box-shadow:0 20px 60px rgba(0,0,0,.5);
}
.modal h3{margin:0 0 6px; font-size:16px}
.modal p{margin:0 0 14px; color:var(--dim); font-size:13px; line-height:1.6}
.tabs{display:flex; gap:6px; background:var(--panel2); border:1px solid var(--line);
  border-radius:9px; padding:3px; margin-bottom:14px}
.tab{flex:1; text-align:center; padding:7px 0; border-radius:7px; cursor:pointer;
  font-size:13px; color:var(--dim); transition:.15s}
.tab:hover{color:var(--text)}
.tab.on{background:#1e2b45; color:#9ec5ff; box-shadow:inset 0 0 0 1px var(--accent)}
.preview{
  background:var(--panel2); border:1px solid var(--line); border-radius:9px;
  padding:10px 12px; font-size:13px; color:var(--text); margin-bottom:16px;
}
.preview b{color:var(--accent); font-family:Consolas,monospace}
.preview.bad{border-color:#6b2b2b; background:#2a1a1a; color:#ffb4b4}

/* 时/分/秒 三列滚轮（仿 OAS 时刻表） */
/* 高度 170 = 中间带 32 + 上下各 69。三项都要对齐这一组数字。 */
.picker{
  position:relative; display:flex; gap:2px; height:170px; margin-bottom:10px;
  background:var(--panel2); border:1px solid var(--line); border-radius:10px;
  overflow:hidden;
}
.pcol{
  flex:1; height:100%; overflow-y:auto; scroll-snap-type:y mandatory;
  padding:69px 0; box-sizing:border-box; scrollbar-width:none;
  -ms-overflow-style:none; overscroll-behavior:contain;
}
.pcol::-webkit-scrollbar{display:none}
.pitem{
  height:32px; line-height:32px; text-align:center; scroll-snap-align:center;
  font-size:14px; color:#6e7686; cursor:pointer; user-select:none;
  font-family:Consolas,monospace; transition:color .12s;
}
.pitem:hover{color:#a9b2c2}
.pitem.on{color:#e6e9ef; font-weight:600}
.pband{
  position:absolute; left:6px; right:6px; top:69px; height:32px;
  border-top:1px solid #33405a; border-bottom:1px solid #33405a;
  background:rgba(91,157,255,.07); border-radius:6px;
  pointer-events:none; z-index:1;
}
.picker::before,.picker::after{
  content:''; position:absolute; left:0; right:0; height:60px;
  pointer-events:none; z-index:2;
}
.picker::before{top:0; background:linear-gradient(180deg,#1d212a 12%,rgba(29,33,42,0))}
.picker::after{bottom:0; background:linear-gradient(0deg,#1d212a 12%,rgba(29,33,42,0))}
.phint{color:var(--dim); font-size:12px; text-align:center; margin-bottom:14px}

.count .hint{color:#e08c8c; font-size:12px}
.count.critical{
  background:#5a1414; border-color:#c93636; animation:pulse 1s infinite;
}
.count.critical .t{color:#fff}
@keyframes pulse{
  0%,100%{box-shadow:0 0 0 0 rgba(201,54,54,.7)}
  50%{box-shadow:0 0 0 9px rgba(201,54,54,0)}
}
.chips{display:flex; gap:8px; flex-wrap:wrap; margin-bottom:14px}
.chip{
  padding:7px 13px; border-radius:999px; background:var(--panel2);
  border:1px solid var(--line); cursor:pointer; font-size:13px;
}
.chip.on{background:#1e2b45; border-color:var(--accent); color:#9ec5ff}
.row{display:flex; gap:8px; align-items:center; margin-bottom:16px}
.row input{width:92px; text-align:center}
.acts{display:flex; gap:8px; justify-content:flex-end}
.warn{
  background:#3a2a12; border:1px solid #6b4d1c; color:#f0c674;
  padding:9px 11px; border-radius:8px; font-size:12.5px; margin-bottom:14px; line-height:1.55;
}
.count{
  position:fixed; right:20px; bottom:20px; background:#2a1a1a; border:1px solid #6b2b2b;
  color:#ffb4b4; padding:12px 16px; border-radius:12px; font-size:13px;
  display:none; align-items:center; gap:14px; box-shadow:0 10px 30px rgba(0,0,0,.5); z-index:60;
}
.count.show{display:flex}
.count .t{font-family:Consolas,monospace; font-size:17px; color:#fff}
.empty{color:var(--dim); text-align:center; padding:40px 10px; font-size:13px}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-thumb{background:#2b3140; border-radius:6px; border:2px solid var(--bg)}
::-webkit-scrollbar-thumb:hover{background:#3a4252}
::-webkit-scrollbar-track{background:transparent}
</style>
</head>
<body>
<header>
  <h1>Git 提交查看器</h1>
  <span class="repo" id="repo">—</span>
  <div class="spacer"></div>
  <button class="ghost" id="btnRefresh">刷新</button>
  <button class="danger" id="btnShutdown">定时关机</button>
</header>
<main>
  <aside id="branches"></aside>
  <section>
    <div class="toolbar">
      <select id="limit">
        <option value="50">最近 50 条</option>
        <option value="100" selected>最近 100 条</option>
        <option value="300">最近 300 条</option>
        <option value="1000">最近 1000 条</option>
      </select>
      <input id="search" placeholder="按提交信息搜索（留空看全部）">
      <div class="spacer"></div>
      <span class="repo" id="countLabel"></span>
    </div>
    <div class="list" id="list"><div class="empty">加载中…</div></div>
  </section>
  <div class="detail" id="detail">
    <div class="dhead">
      <h2 id="dSubj">—</h2>
      <div class="kv" id="dMeta"></div>
    </div>
    <div class="dbody" id="dBody"></div>
  </div>
</main>

<div class="mask" id="mask">
  <div class="modal">
    <h3>定时关机</h3>
    <p>到点后电脑会直接关机，正在跑的任务会被中断。设置后在关机前任意时刻都能取消。</p>
    <div id="oasWarn" class="warn" style="display:none">
      检测到 <b>oasx.exe</b> 正在运行，关机将中断正在执行的脚本任务。
    </div>

    <div class="tabs">
      <div class="tab on" data-mode="seconds">倒计时</div>
      <div class="tab" data-mode="clock">指定时刻</div>
    </div>

    <div id="paneSeconds">
      <div class="chips" id="chips">
        <div class="chip" data-v="15">15 分钟</div>
        <div class="chip" data-v="30">30 分钟</div>
        <div class="chip on" data-v="60">1 小时</div>
        <div class="chip" data-v="120">2 小时</div>
        <div class="chip" data-v="180">3 小时</div>
      </div>
      <div class="row">
        <span style="color:var(--dim)">自定义</span>
        <input id="customMin" type="number" min="1" max="10080" placeholder="分钟">
        <span style="color:var(--dim)">分钟后</span>
      </div>
    </div>

    <div id="paneClock" style="display:none">
      <div class="picker" id="picker">
        <div class="pcol" id="colH"></div>
        <div class="pcol" id="colM"></div>
        <div class="pcol" id="colS"></div>
        <div class="pband"></div>
      </div>
      <div class="phint">上下滚动选择时刻，已经过去的时刻按明天算</div>
    </div>

    <div class="preview" id="preview">将在 — 关机</div>

    <div class="acts">
      <button class="ghost" id="btnCancelSet">取消</button>
      <button class="primary" id="btnConfirmSet">确定关机</button>
    </div>
  </div>
</div>

<div class="count" id="countBox">
  <span id="countText">将在 <span class="t" id="countTime">--:--</span> 后关机</span>
  <button class="danger" id="btnAbort">取消关机</button>
</div>

<script>
var state = { ref: '*', query: '', limit: 100, sha: null, timer: null };

function $(id){ return document.getElementById(id); }
function esc(s){
  return String(s == null ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}
function api(path, opts){
  return fetch(path, opts).then(function(r){ return r.json(); });
}

/* ---------- 分支 ---------- */
function loadBranches(){
  return api('/api/branches').then(function(d){
    if(!d.ok){ $('branches').innerHTML = '<div class="empty">'+esc(d.error)+'</div>'; return; }
    var locals = d.branches.filter(function(b){ return b.kind === 'local'; });
    var remotes = d.branches.filter(function(b){ return b.kind === 'remote'; });
    var html = '<div class="br' + (state.ref === '*' ? ' active' : '') + '" data-ref="*">'
      + '<span class="dot"></span><span class="nm">全部提交</span></div>';
    html += '<div class="group">本地分支 (' + locals.length + ')</div>';
    locals.forEach(function(b){
      html += '<div class="br' + (state.ref === b.name ? ' active' : '') + '" data-ref="' + esc(b.name) + '" title="' + esc(b.subject) + '">'
        + '<span class="dot"></span><span class="nm">' + esc(b.name) + '</span>'
        + (b.current ? '<span class="badge">HEAD</span>' : '') + '</div>';
    });
    html += '<div class="group">远端分支 (' + remotes.length + ')</div>';
    remotes.forEach(function(b){
      html += '<div class="br' + (state.ref === b.name ? ' active' : '') + '" data-ref="' + esc(b.name) + '" title="' + esc(b.subject) + '">'
        + '<span class="dot"></span><span class="nm">' + esc(b.name) + '</span></div>';
    });
    $('branches').innerHTML = html;
    $('branches').querySelectorAll('.br').forEach(function(el){
      el.onclick = function(){
        state.ref = el.getAttribute('data-ref');
        closeDetail();
        loadBranches(); loadCommits();
      };
    });
  });
}

/* ---------- 提交列表 ---------- */
function loadCommits(){
  $('list').innerHTML = '<div class="empty">加载中…</div>';
  var url = '/api/commits?limit=' + state.limit + '&ref=' + encodeURIComponent(state.ref);
  if(state.query) url += '&q=' + encodeURIComponent(state.query);
  return api(url).then(function(d){
    if(!d.ok){ $('list').innerHTML = '<div class="empty">'+esc(d.error)+'</div>'; return; }
    $('countLabel').textContent = d.count + ' 条提交';
    if(!d.count){ $('list').innerHTML = '<div class="empty">没有匹配的提交</div>'; return; }
    var html = '';
    d.commits.forEach(function(c){
      var refs = '';
      if(c.refs.length){
        refs = '<div class="refs">' + c.refs.map(function(r){
          var cls = /HEAD/.test(r) ? 'ref head' : 'ref';
          return '<span class="' + cls + '">' + esc(r) + '</span>';
        }).join('') + '</div>';
      }
      html += '<div class="cm' + (state.sha === c.sha ? ' sel' : '') + '" data-sha="' + c.sha + '">'
        + '<span class="sha">' + esc(c.short) + '</span>'
        + '<span class="subj">' + esc(c.subject) + '</span>'
        + '<span class="meta">' + esc(c.author) + ' · ' + esc(c.date) + '</span>'
        + refs + '</div>';
    });
    $('list').innerHTML = html;
    $('list').querySelectorAll('.cm').forEach(function(el){
      el.onclick = function(){ openDetail(el.getAttribute('data-sha')); };
    });
  });
}

/* ---------- 详情 ---------- */
function closeDetail(){
  state.sha = null;
  $('detail').classList.remove('open');
  document.querySelectorAll('.cm.sel').forEach(function(e){ e.classList.remove('sel'); });
}

function openDetail(sha){
  state.sha = sha;
  document.querySelectorAll('.cm').forEach(function(e){
    e.classList.toggle('sel', e.getAttribute('data-sha') === sha);
  });
  $('detail').classList.add('open');
  $('dSubj').textContent = '加载中…';
  $('dMeta').innerHTML = '';
  $('dBody').innerHTML = '';
  api('/api/commit?sha=' + encodeURIComponent(sha)).then(function(d){
    if(!d.ok){ $('dSubj').textContent = d.error; return; }
    var c = d.commit;
    $('dSubj').textContent = c.subject;
    $('dMeta').innerHTML =
      '<span>提交</span><b>' + esc(c.sha) + '</b>' +
      '<span>作者</span><b>' + esc(c.author) + '</b>' +
      '<span>时间</span><b>' + esc(c.date) + '</b>' +
      '<span>父提交</span><b>' + (c.parents.length ? c.parents.map(function(p){ return p.slice(0,8); }).join(' + ') : '（首个提交）') + '</b>';
    var body = '';
    if(c.body){
      body += '<div class="body-text">' + esc(c.body) + '</div>';
    }
    body += '<div class="files"><div class="kv" style="margin-bottom:8px">'
      + '<span>变更</span><b>' + c.files.length + ' 个文件 '
      + '<i style="color:var(--green);font-style:normal">+' + c.total_add + '</i> '
      + '<i style="color:var(--red);font-style:normal">-' + c.total_del + '</i></b></div>';
    c.files.forEach(function(f){
      var n = f.binary ? '二进制' : '<i>+' + f.add + '</i> <s>-' + f.del + '</s>';
      body += '<div class="file"><span class="p">' + esc(f.path) + '</span><span class="n">' + n + '</span></div>';
    });
    body += '</div>';
    $('dBody').innerHTML = body;
  });
}

/* ---------- 关机 ---------- */
var pickMin = 60;
var pickMode = 'seconds';   // seconds 倒计时 / clock 指定时刻

/* 滚轮状态：时 / 分 / 秒 */
var PICK = { h: 0, m: 0, s: 0 };
var ITEM_H = 32;            // 必须与 CSS 的 .pitem height / .pcol padding 对应

function pad2(n){ return (n < 10 ? '0' : '') + n; }

/* 滚轮当前值 → Date。已经过去就顺延到明天。 */
function pickerDate(){
  var now = new Date();
  var t = new Date(now.getFullYear(), now.getMonth(), now.getDate(),
                   PICK.h, PICK.m, PICK.s, 0);
  if(t.getTime() <= now.getTime()) t.setDate(t.getDate() + 1);
  return t;
}

function fmtDate(d, withSec){
  if(!d) return '';
  var now = new Date();
  var d0 = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  var day = Math.round((new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime() - d0) / 86400000);
  var hm = pad2(d.getHours()) + ':' + pad2(d.getMinutes())
         + (withSec ? ':' + pad2(d.getSeconds()) : '');
  if(day <= 0) return '今天 ' + hm;
  if(day === 1) return '明天 ' + hm;
  return pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()) + ' ' + hm;
}

function fmtSpan(sec){
  sec = Math.max(0, Math.round(sec));
  var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  if(h > 0) return h + ' 小时' + (m > 0 ? ' ' + m + ' 分' : '');
  if(m > 0) return m + ' 分' + (s > 0 ? ' ' + s + ' 秒' : '');
  return s + ' 秒';
}

/* ---- 滚轮：建列 / 定位 / 读取 ---- */
function buildCol(el, count, unit){
  var html = '';
  for(var i = 0; i < count; i++){
    html += '<div class="pitem" data-v="' + i + '">' + pad2(i) + unit + '</div>';
  }
  el.innerHTML = html;
}

function scrollColTo(el, val){
  el.scrollTop = val * ITEM_H;   // padding-top 已把首项推到中间，故不用加偏移
}

function readCol(el){
  return Math.max(0, Math.min(el.querySelectorAll('.pitem').length - 1,
                              Math.round(el.scrollTop / ITEM_H)));
}

function syncColHL(el, val){
  el.querySelectorAll('.pitem').forEach(function(it){
    it.classList.toggle('on', parseInt(it.getAttribute('data-v'), 10) === val);
  });
}

function bindCol(el, key){
  var timer = null;
  function settle(){
    var v = readCol(el);
    PICK[key] = v;
    syncColHL(el, v);
    scrollColTo(el, v);          // 吸附到整格，防止停在半格
    refreshPreview();
  }
  el.addEventListener('scroll', function(){
    clearTimeout(timer);
    timer = setTimeout(settle, 90);
  });
  el.addEventListener('click', function(e){
    var it = e.target.closest ? e.target.closest('.pitem') : null;
    if(!it) return;
    var v = parseInt(it.getAttribute('data-v'), 10);
    PICK[key] = v;
    syncColHL(el, v);
    el.scrollTo({ top: v * ITEM_H, behavior: 'smooth' });
    setTimeout(refreshPreview, 120);
  });
}

var PICKER_READY = false;
function initPicker(){
  if(PICKER_READY) return;
  buildCol($('colH'), 24, ' 时');
  buildCol($('colM'), 60, ' 分');
  buildCol($('colS'), 60, ' 秒');
  bindCol($('colH'), 'h');
  bindCol($('colM'), 'm');
  bindCol($('colS'), 's');
  PICKER_READY = true;
}

function setPicker(h, m, s){
  initPicker();
  PICK.h = h; PICK.m = m; PICK.s = s;
  [[$('colH'), h], [$('colM'), m], [$('colS'), s]].forEach(function(p){
    syncColHL(p[0], p[1]);
    scrollColTo(p[0], p[1]);
  });
}

/* 实时预览「将在 xx 关机」，同时校验是否太近 */
function refreshPreview(){
  var box = $('preview');
  var target = null;
  if(pickMode === 'clock'){
    target = pickerDate();
  } else {
    if(!(pickMin > 0)){
      box.className = 'preview bad';
      box.textContent = '请填一个大于 0 的分钟数';
      return;
    }
    target = new Date(Date.now() + pickMin * 60000);
  }
  var left = (target.getTime() - Date.now()) / 1000;
  if(left < 60){
    box.className = 'preview bad';
    box.textContent = '距目标只剩 ' + fmtSpan(left) + '，至少要留 1 分钟';
    return;
  }
  box.className = 'preview';
  var withSec = (pickMode === 'clock');
  box.innerHTML = '将在 <b>' + esc(fmtDate(target, withSec)) + '</b> 关机（还有 ' + fmtSpan(left) + '）';
}

function setMode(mode){
  pickMode = mode;
  document.querySelectorAll('.tabs .tab').forEach(function(t){
    t.classList.toggle('on', t.getAttribute('data-mode') === mode);
  });
  $('paneSeconds').style.display = (mode === 'seconds') ? 'block' : 'none';
  $('paneClock').style.display = (mode === 'clock') ? 'block' : 'none';
  refreshPreview();
  if(mode === 'clock'){
    // display:none 时设 scrollTop 无效，必须等面板真的显示出来再滚
    setTimeout(function(){
      [[$('colH'), PICK.h], [$('colM'), PICK.m], [$('colS'), PICK.s]].forEach(function(p){
        scrollColTo(p[0], p[1]);
      });
    }, 20);
  }
}

function openModal(){
  pickMin = 60;
  pickMode = 'seconds';
  $('customMin').value = '';
  document.querySelectorAll('#chips .chip').forEach(function(c){
    c.classList.toggle('on', c.getAttribute('data-v') === '60');
  });
  // 滚轮默认停在「一小时后」的整分，跟 OAS 时刻表一样从合理值开始
  var n = new Date(Date.now() + 3600000);
  setPicker(n.getHours(), n.getMinutes(), 0);
  setMode('seconds');
  $('mask').classList.add('show');
  api('/api/state').then(function(d){
    $('oasWarn').style.display = d.oas_running ? 'block' : 'none';
  });
}
function closeModal(){ $('mask').classList.remove('show'); }

document.querySelectorAll('.tabs .tab').forEach(function(t){
  t.onclick = function(){ setMode(t.getAttribute('data-mode')); };
});

document.querySelectorAll('#chips .chip').forEach(function(c){
  c.onclick = function(){
    document.querySelectorAll('#chips .chip').forEach(function(x){ x.classList.remove('on'); });
    c.classList.add('on');
    pickMin = parseInt(c.getAttribute('data-v'), 10);
    $('customMin').value = '';
    refreshPreview();
  };
});
$('customMin').oninput = function(){
  var v = parseInt(this.value, 10);
  if(v > 0){
    pickMin = v;
    document.querySelectorAll('#chips .chip').forEach(function(x){ x.classList.remove('on'); });
  }
  refreshPreview();
};

$('btnShutdown').onclick = openModal;
$('btnCancelSet').onclick = closeModal;
$('mask').onclick = function(e){ if(e.target === this) closeModal(); };

$('btnConfirmSet').onclick = function(){
  var body = {};
  if(pickMode === 'clock'){
    var d = pickerDate();
    if(!d){ alert('滚轮时刻无效'); return; }
    body.at = Math.round(d.getTime() / 1000);   // 精确到秒，交给后端算剩余
  } else {
    if(!(pickMin > 0)){ alert('分钟数要大于 0'); return; }
    if(pickMin > 10080){ alert('最多只能设 7 天（10080 分钟）'); return; }
    body.seconds = Math.round(pickMin * 60);
  }
  api('/api/shutdown', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  }).then(function(d){
    if(!d.ok){ alert('设置失败：' + d.error); return; }
    closeModal();
    pollState();
  });
};

$('btnAbort').onclick = doCancel;

function doCancel(){
  api('/api/shutdown/cancel', { method: 'POST' }).then(function(d){
    if(!d.ok){ alert('取消失败：' + d.error + '\n\n可以手动在 cmd 里运行：shutdown /a'); return; }
    pollState();
  });
}

function two(n){ return (n < 10 ? '0' : '') + n; }

function pollState(){
  api('/api/state').then(function(d){
    if(d.repo) $('repo').textContent = d.repo;
    var box = $('countBox');
    if(d.remaining != null && d.remaining > 0){
      var h = Math.floor(d.remaining / 3600);
      var m = Math.floor((d.remaining % 3600) / 60);
      var s = d.remaining % 60;
      $('countTime').textContent = (h > 0 ? h + ':' : '') + two(m) + ':' + two(s);
      var label = d.label ? ('（' + d.label + '）') : '';
      if(d.critical){
        $('countText').innerHTML = '即将关机' + esc(label) + ' 还剩 '
          + '<span class="t">' + two(m) + ':' + two(s) + '</span>'
          + '<span class="hint">点右侧仍可取消</span>';
      } else {
        $('countText').innerHTML = '将在 <span class="t">' + $('countTime').textContent
          + '</span> 后关机' + esc(label);
      }
      box.classList.toggle('critical', !!d.critical);
      box.classList.add('show');
    } else {
      box.classList.remove('show', 'critical');
    }
  });
}

/* ---------- 初始化 ---------- */
var searchTimer = null;
$('search').oninput = function(){
  var v = this.value.trim();
  clearTimeout(searchTimer);
  searchTimer = setTimeout(function(){ state.query = v; loadCommits(); }, 320);
};
$('limit').onchange = function(){ state.limit = parseInt(this.value, 10); loadCommits(); };
$('btnRefresh').onclick = function(){ loadBranches(); loadCommits(); pollState(); };

loadBranches();
loadCommits();
pollState();
state.timer = setInterval(pollState, 1000);

// 支持 /?sha=<提交> 直接打开某条提交详情，方便收藏或分享
(function(){
  var m = /[?&]sha=([0-9a-fA-F]{7,40})/.exec(location.search);
  if(m) setTimeout(function(){ openDetail(m[1]); }, 260);
})();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "GitViewer/1.0"

    def _local_only(self):
        ip = self.client_address[0] if self.client_address else ""
        return ip in ("127.0.0.1", "::1", "localhost")

    def _send_json(self, obj, code=200):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _send_html(self, text, code=200):
        raw = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _body_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def log_message(self, fmt, *args):  # 静音，避免刷屏
        pass

    def do_GET(self):
        if not self._local_only():
            self._send_json({"ok": False, "error": "只允许本机访问"}, 403)
            return

        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self._send_html(INDEX_HTML)
            return
        if path == "/api/branches":
            self._send_json(api_branches())
            return
        if path == "/api/commits":
            try:
                limit = int((qs.get("limit") or ["100"])[0])
            except ValueError:
                limit = 100
            limit = max(1, min(limit, 2000))
            ref = (qs.get("ref") or ["*"])[0]
            query = (qs.get("q") or [""])[0].strip()
            self._send_json(api_commits(ref, limit, query))
            return
        if path == "/api/commit":
            sha = (qs.get("sha") or [""])[0]
            if not sha:
                self._send_json({"ok": False, "error": "缺少 sha"}, 400)
                return
            self._send_json(api_commit(sha))
            return
        if path == "/api/state":
            self._send_json(api_state())
            return

        self._send_json({"ok": False, "error": "未知路径"}, 404)

    def do_POST(self):
        if not self._local_only():
            self._send_json({"ok": False, "error": "只允许本机访问"}, 403)
            return

        path = urlparse(self.path).path
        if path == "/api/shutdown":
            body = self._body_json()
            seconds = at = clock = None
            try:
                if body.get("seconds") not in (None, ""):
                    seconds = int(body.get("seconds"))
            except (TypeError, ValueError):
                return self._send_json(
                    {"ok": False, "error": "seconds 不是合法数字"}
                )
            try:
                if body.get("at") not in (None, ""):
                    at = float(body.get("at"))
            except (TypeError, ValueError):
                return self._send_json({"ok": False, "error": "at 不是合法时间戳"})
            if body.get("clock"):
                clock = str(body.get("clock")).strip()
            self._send_json(api_shutdown(seconds=seconds, at=at, clock=clock))
            return
        if path == "/api/shutdown/cancel":
            self._send_json(api_cancel_shutdown())
            return

        self._send_json({"ok": False, "error": "未知路径"}, 404)


def pick_port(preferred):
    import socket

    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    return preferred


def probe_existing(port):
    """该端口上是否已经跑着本工具。

    双击 bat 第二次时，直接复用已有服务并打开浏览器，不要悄悄起第二个实例。
    通过 /api/state 的返回字段识别，避免把别人占用的端口误认成自己。
    """
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/state", timeout=1.5
        ) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        return isinstance(data, dict) and data.get("ok") is True and "oas_running" in data
    except Exception:  # noqa: BLE001
        return False


AUTOSTART_NAME = "OAS-GitViewer"
AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def api_autostart(action):
    """管理开机自启（写 HKCU 的 Run 项，不需要管理员权限）。

    用 pythonw.exe 作为目标，开机时不会闪黑窗口。
    """
    if sys.platform != "win32":
        return {"ok": False, "error": "开机自启只在 Windows 上支持"}
    try:
        import winreg
    except ImportError:
        return {"ok": False, "error": "当前 Python 缺少 winreg 模块"}
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_ALL_ACCESS
        )
    except OSError as exc:
        return {"ok": False, "error": f"打不开注册表 Run 项：{exc}"}
    try:
        if action == "off":
            try:
                winreg.DeleteValue(key, AUTOSTART_NAME)
                return {"ok": True, "message": "已取消开机自启"}
            except FileNotFoundError:
                return {"ok": True, "message": "本来就是关闭状态"}
        if action == "status":
            try:
                value, _ = winreg.QueryValueEx(key, AUTOSTART_NAME)
            except FileNotFoundError:
                return {"ok": True, "enabled": False, "command": ""}
            return {"ok": True, "enabled": True, "command": value}

        pyw = os.path.join(REPO_ROOT, "toolkit", "pythonw.exe")
        if not os.path.isfile(pyw):
            pyw = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "pythonw.exe")
        if not os.path.isfile(pyw):
            return {"ok": False, "error": f"找不到 pythonw.exe（最后尝试：{pyw}）"}
        target = os.path.join(REPO_ROOT, "dev_tools", "git_viewer.py")
        cmd = f'"{pyw}" "{target}" --no-browser --port 8765'
        winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, cmd)
        return {"ok": True, "message": "已开启开机自启", "command": cmd}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        winreg.CloseKey(key)


def free_port(preferred):
    """找一个能用的端口；被别的程序占用时把新端口明确告诉用户。"""
    port = pick_port(preferred)
    if port != preferred:
        print(f"[!] 端口 {preferred} 被其它程序占用了，已改用 {port}")
    return port


def _ensure_streams():
    """pythonw.exe 启动时 sys.stdout / sys.stderr 是 None，print 会直接崩。

    开机自启走的是无窗口解释器，所以这里把这两个流接管到
    %TEMP%/oas_git_viewer.log，静默启动也能事后查问题。
    返回需要保持打开的文件对象（None 表示无需接管）。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return None
    try:
        path = os.path.join(tempfile.gettempdir(), "oas_git_viewer.log")
        f = open(path, "a", encoding="utf-8", buffering=1)
    except Exception:  # noqa: BLE001
        class _Null:
            def write(self, *_a): pass
            def flush(self): pass
            def reconfigure(self, **_k): pass

        null = _Null()
        if sys.stdout is None:
            sys.stdout = null
        if sys.stderr is None:
            sys.stderr = null
        return None
    if sys.stdout is None:
        sys.stdout = f
    if sys.stderr is None:
        sys.stderr = f
    print(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 启动 pid={os.getpid()} =====")
    return f


def main():
    # sys.stdout 已接管时文件对象由它持有引用，不需要额外变量
    _ensure_streams()

    # 双击启动时窗口要能立刻看到字，否则会以为「点了没反应」
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass

    ap = argparse.ArgumentParser(
        description="本地 Git 提交查看器 + 定时关机",
        epilog="不开网页也能用：--at 23:30 定时关机、--in 90 分钟后关机、--cancel 立即取消",
    )
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--at", metavar="HH:MM", help="在今天/明天的这个时刻关机")
    ap.add_argument("--in", dest="in_min", type=int, metavar="分钟", help="多少分钟后关机")
    ap.add_argument("--cancel", action="store_true", help="取消挂起的关机后退出")
    ap.add_argument("--status", action="store_true", help="查看当前是否有挂起的关机后退出")
    ap.add_argument(
        "--autostart",
        choices=["on", "off", "status"],
        help="管理开机自启：on 开机自动启动、off 取消、status 查看",
    )
    args = ap.parse_args()

    # 开机自启管理
    if args.autostart:
        r = api_autostart(args.autostart)
        if not r.get("ok"):
            print("[!] " + str(r.get("error")))
            return 1
        if args.autostart == "status":
            if r.get("enabled"):
                print("[i] 开机自启：已开启")
                print("    " + str(r.get("command")))
            else:
                print("[i] 开机自启：未开启")
            return 0
        print("[+] " + str(r.get("message")))
        if r.get("command"):
            print("    " + str(r["command"]))
        return 0

    # 纯命令行模式：不启服务，跑完就退
    if args.cancel:
        r = api_cancel_shutdown()
        print(("[+] " if r.get("ok") else "[!] ") + str(r.get("message") or r.get("error")))
        return 0 if r.get("ok") else 1
    if args.at or args.in_min:
        r = api_shutdown(clock=args.at, seconds=(args.in_min * 60) if args.in_min else None)
        if r.get("ok"):
            print(f"[+] 已设置：将在 {r['label']} 关机（{r['seconds']} 秒后）")
            print("[i] 取消：运行本脚本加 --cancel，或 cmd 里 shutdown /a")
            return 0
        print("[!] " + str(r.get("error")))
        return 1
    if args.status:
        st = api_state()
        if st.get("shutdown_at"):
            print(f"[i] 已挂起：{st['label']} 关机（还剩 {st['remaining']} 秒）")
        else:
            print("[i] 当前没有挂起的关机")
        return 0

    if not os.path.isdir(os.path.join(REPO_ROOT, ".git")):
        print(f"[!] 没有在 {REPO_ROOT} 找到 .git，请把本脚本放在仓库目录内运行")
        return 2

    # 已经在跑就直接打开浏览器，不再起第二个实例（否则你会打开一个空端口）
    if probe_existing(args.port):
        url = f"http://127.0.0.1:{args.port}/"
        print(f"[i] 服务已经在运行：{url}")
        print("[i] 想重启就先关掉原来那个黑窗口，再双击本脚本")
        if not args.no_browser:
            webbrowser.open(url)
        return 0

    port = free_port(args.port)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"

    print("=" * 54)
    print("  Git 提交查看器")
    print(f"  仓库: {REPO_ROOT}")
    print(f"  地址: {url}")
    print("  关闭本窗口即停止服务")
    print("=" * 54)

    if not args.no_browser:
        def _open():
            ok = False
            try:
                ok = webbrowser.open(url)
            except Exception:  # noqa: BLE001
                ok = False
            if not ok:
                print(f"[!] 没能自动打开浏览器，请手动访问：{url}")

        threading.Timer(0.6, _open).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
