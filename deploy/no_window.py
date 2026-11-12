# This Python file uses the following encoding: utf-8
"""Windows 下阻止子进程弹出控制台黑窗。

背景
----
oasx.exe 是 Flutter 打包的 GUI 程序（PE 子系统 = 2，没有控制台），它用
toolkit/pythonw.exe 启动后端；多开配置时，配置进程又是 multiprocessing spawn
出来的 pythonw。pythonw 同样没有控制台。

Windows 的规则：一个没有控制台的进程 CreateProcess 一个控制台程序
（adb.exe / cmd.exe / tasklist.exe / python.exe ...），且没有带
CREATE_NO_WINDOW，系统就会给这个子进程**新建一个可见的控制台窗口**。
表现就是多开配置后不停往外弹 cmd 黑窗。

做法
----
本进程没有控制台时，给 subprocess.Popen 统一补 CREATE_NO_WINDOW。
有控制台时不生效（console.bat 手动调试照常能看输出），不影响排查问题。
只改 Popen.__init__，subprocess.run / call / check_output / check_call 内部
都走它，一处管全部，以后新写的调用也不用再逐个补。

用法
----
在进程入口尽早调用一次，重复调用无副作用：

    from deploy.no_window import install_no_window_patch
    install_no_window_patch()
"""
import os
import subprocess

_PATCHED = False


def _has_console() -> bool:
    """当前进程是否附带控制台窗口。判断不了就当作有，保持原有行为。"""
    try:
        import ctypes
        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:
        return True


def install_no_window_patch() -> bool:
    """给 subprocess.Popen 补 CREATE_NO_WINDOW。

    Returns:
        bool: True 表示本次真的打上了补丁；False 表示不需要（非 Windows /
              已有控制台 / 已打过）。
    """
    global _PATCHED
    if _PATCHED or os.name != 'nt':
        return False

    flag = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    if not flag:
        return False

    if _has_console():
        # 有控制台说明是命令行手动跑的，保持原样，方便看输出
        return False

    orig_init = subprocess.Popen.__init__

    def _no_window_init(self, *args, **kwargs):
        kwargs['creationflags'] = kwargs.get('creationflags', 0) | flag
        return orig_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _no_window_init
    _PATCHED = True
    try:
        from deploy.logger import logger
        logger.info('[no_window] 当前进程无控制台，已为所有 subprocess 补 CREATE_NO_WINDOW')
    except Exception:
        # 日志不可用（例如 installer 早期）不影响补丁生效
        pass
    return True
