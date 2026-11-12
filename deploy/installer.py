from deploy.no_window import install_no_window_patch

# 尽早生效：installer 阶段会连续调用 adb，被 GUI 拉起时不能弹黑窗
install_no_window_patch()

from deploy.patch import pre_checks

pre_checks()

from deploy.adb import AdbManager
from deploy.process import ProcessManager
from deploy.fluentui import FluentuiManager
from deploy.config import ExecutionError
from deploy.git import GitManager
from deploy.pip import PipManager


class Installer(GitManager, PipManager, AdbManager, FluentuiManager, ProcessManager):
    def install(self):
        try:
            self.git_install()
            self.process_kill()
            self.pip_install()
            self.adb_install()
        except ExecutionError:
            exit(1)


if __name__ == '__main__':
    Installer().install()
