# This Python file uses the following encoding: utf-8
# @author @@AUTHOR@@
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main
from tasks.@@TASK_NAME@@.assets import @@TASK_NAME@@Assets
# 页面必须被显式 import 才注册：tasks/GameUi/registry.py:82 的 for module_name in ("page")
# 少个逗号，load_all_pages() 扫不到任何 page.py，全仓 29 个 page.py 都是这样被引入的。
# 用不到自定义页面时可以连同 page.py 一起删掉；留着也无害（只是给导航器多注册一个节点）。
from tasks.@@TASK_NAME@@.page import page_@@SNAKE_NAME@@  # noqa: F401

from module.base.timer import Timer
from module.exception import TaskEnd
from module.logger import logger


class ScriptTask(GameUi, @@TASK_NAME@@Assets):
    """@@TASK_DESC@@"""

    def run(self):
        self.goto_page(page_main)

        # 1) 进玩法。判据用"到了哪个页面"（I_CHECK），不要用"我点了哪个按钮"。
        if not self.enter():
            logger.warning('@@TASK_NAME@@: entry not found, skip this round')
            self.set_next_run(task='@@TASK_NAME@@', success=False)
            raise TaskEnd('@@TASK_NAME@@')  # 必须抛 TaskEnd，否则本次会被判失败

        logger.hr('@@TASK_NAME@@')
        # 2) 主体
        self.execute()

        # 3) 收尾：回主界面 + 排期 + 抛 TaskEnd
        self.goto_page(page_main)
        self.set_next_run(task='@@TASK_NAME@@', success=True, finish=True)
        raise TaskEnd('@@TASK_NAME@@')

    def enter(self) -> bool:
        """
        从主界面点进玩法。
        :return: True 已到玩法界面；False 这一轮放弃（由 run() 跳过）
        """
        timer = Timer(8).start()
        while 1:
            self.screenshot()  # 每轮必须截图：appear/appear_then_click 只读缓存帧
            if self.appear(self.I_CHECK):
                return True
            if self.appear_then_click(self.I_ENTRY, interval=1):
                continue
            if timer.reached():
                return False

    def execute(self):
        """主体。真要做的事写在这里，失败只 return，不往外抛。"""
        timer = Timer(30).start()
        while 1:
            self.screenshot()
            if self.appear(self.I_FINISHED):
                return
            if self.appear_then_click(self.I_CLAIM, interval=1):
                timer.reset()
                continue
            if timer.reached():
                logger.warning('@@TASK_NAME@@: nothing to do for 30s, give up')
                return


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.screenshot()
    t.run()

# 提交前必须: python dev_tools/oas_lint.py tasks/@@TASK_NAME@@   （要求 errors=0）
