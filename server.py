# This Python file uses the following encoding: utf-8
# Copy from https://github.com/LmeSzinc/AzurLaneAutoScript/gui.py


"""
在任何平台把当前 Python 进程（含子线程、子进程）切到北京时间。
• Linux/macOS/WSL 及 Win-Py 3.11+ → TZ='Asia/Shanghai' + time.tzset()
• Win-Py ≤ 3.10            → TZ='CST-8'       + _tzset()（POSIX 语法）
"""
import os, sys, time

if hasattr(time, "tzset"):
    # Unix 全系  /  Windows 3.11+ 走这条
    os.environ["TZ"] = "Asia/Shanghai"     # IANA 名称，glibc/Apple libc 都认识
    time.tzset()
else:
    # 只有旧 Windows 才会落到这里
    import ctypes
    os.environ["TZ"] = "CST-8"             # POSIX 字符串：UTC+8 且无 DST
    for dll in ("ucrtbase", "msvcrt"):     # 新旧 CRT 都试一遍
        try:
            ctypes.CDLL(dll)._tzset()
            break
        except (OSError, AttributeError):
            continue
        
import threading

from module.logger import logger
from module.image.rpc import ensure_image_server_ready, shutdown_image_server
from module.server.setting import State
from module.ocr.rpc import ensure_ocr_server_ready, shutdown_ocr_server
from module.device.click_statistics import ClickStatistics


def fun(ev: threading.Event):
    import argparse
    import asyncio
    import sys

    import uvicorn

    # 不知道干啥的照着抄就行了
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    State.restart_event = ev

    parser = argparse.ArgumentParser(description="Alas web service")
    parser.add_argument(
        "--host",
        type=str,
        help="Host to listen. Default to WebuiHost in deploy setting",
    )
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        help="Port to listen. Default to WebuiPort in deploy setting",
    )
    parser.add_argument(
        "-k", "--key", type=str, help="Password of alas. No password by default"
    )
    parser.add_argument(
        "--cdn",
        action="store_true",
        help="Use jsdelivr cdn for pywebio static files (css, js). Self host cdn by default.",
    )
    parser.add_argument(
        "--run",
        nargs="+",
        type=str,
        help="Run alas by config names on startup",
    )
    args, _ = parser.parse_known_args()

    host = args.host or State.deploy_config.WebuiHost or "0.0.0.0"
    port = args.port or int(State.deploy_config.WebuiPort) or 22270

    logger.hr("Launcher config")
    logger.attr("Host", host)
    logger.attr("Port", port)
    logger.attr("Reload", ev is not None)

    ensure_image_server_ready()
    ensure_ocr_server_ready()

    # ---- 本地自建：随 OAS 启动 Git 提交查看器（dev_tools/git_viewer.py）----
    # 只是本机便利工具，失败绝不影响 OAS 启动。
    # 脚本自身会探测 8765 是否已在运行，因此 OAS 重启不会产生重复实例。
    # 两处细节是必须的：① 优先用 toolkit/pythonw.exe（sys.executable 可能是 oasx.exe）；
    # ② 必须带 CREATE_NO_WINDOW，否则无控制台的 pythonw 进程 spawn 子进程会弹出黑窗。
    # 彻底移除：删除下面整个 try 块即可，不影响任何 OAS 功能。
    try:
        import subprocess

        _root = os.path.dirname(os.path.abspath(__file__))
        _viewer = os.path.join(_root, "dev_tools", "git_viewer.py")
        _pythonw = os.path.join(_root, "toolkit", "pythonw.exe")
        _exe = _pythonw if os.path.isfile(_pythonw) else sys.executable
        if os.path.isfile(_viewer):
            subprocess.Popen(
                [_exe, _viewer, "--no-browser", "--port", "8765"],
                cwd=_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                close_fds=True,
            )
    except Exception:
        logger.exception("Failed to start git_viewer")

    try:
        ClickStatistics.cleanup_old_statistics()
    except Exception:
        logger.exception("Failed to cleanup click statistics")
        
    try:
        uvicorn.run("module.server.app:fastapi_app",
                    host=host,
                    port=port,
                    factory=True,
                    log_config=None)
    finally:
        shutdown_image_server()
        shutdown_ocr_server()


if __name__ == "__main__":
    fun(None)
