# This Python file uses the following encoding: utf-8
# @author Hurry2
from time import sleep

from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_friends, page_main
from tasks.AddFriend.assets import AddFriendAssets
from module.logger import logger
from module.exception import TaskEnd


class ScriptTask(GameUi, AddFriendAssets):
    """
    添加好友:
    好友界面 -> 添加页签 -> 从系统推荐列表中点击加号添加
    列表点完后点击「换一换」刷新一批新的推荐, 继续添加
    添加次数达到配置上限后停止
    """

    # 换一换的最大点击次数(防死循环保险)
    MAX_REFRESH = 100
    # 连续无按钮无换一换的最大空闲轮数(每轮约1秒, 防卡死保险)
    MAX_IDLE = 60
    # 每次点击后等待界面响应的时间(秒), 放慢节奏避免游戏/框架跟不上
    WAIT_AFTER_CLICK = 1.5
    # 空闲时每轮等待时间(秒)
    WAIT_IDLE = 1.5

    def run(self) -> bool:
        # 1. 导航到好友界面
        self.goto_page(page_friends)
        # 2. 进入「添加好友」页: 出现搜索按钮即认为到位, 否则点「添加」页签
        while 1:
            self.screenshot()
            if self.appear(self.I_AF_CHECK_SEARCH):
                logger.info('AddFriend page reached (search button visible)')
                break
            if self.appear_then_click(self.I_AF_GOTO_ADD, interval=2):
                sleep(self.WAIT_AFTER_CLICK)
                continue
        # 3. 主循环: 点加号 -> 没了点换一换 -> 新一批继续
        limit = self.config.add_friend.add_friend_config.add_friend_count
        count = 0
        refresh_count = 0
        idle = 0
        while 1:
            self.screenshot()
            # 停止条件1: 达到配置的添加次数
            if count >= limit:
                logger.info(f'AddFriend count limit reached: {count}/{limit}')
                break
            # 停止条件2: 连续 MAX_IDLE 轮既无加号也无换一换(防卡死)
            if idle >= self.MAX_IDLE:
                logger.warning(f'AddFriend idle {self.MAX_IDLE} rounds, stop. count={count}')
                break
            # 优先处理好友申请确认弹窗(点加号后弹出, 必须先点申请再继续)
            if self.appear_then_click(self.I_AF_APPLY, interval=2):
                idle = 0
                logger.info('AddFriend apply sent, wait dialog close')
                sleep(self.WAIT_AFTER_CLICK)
                # 一次好友申请完成, 清空框架的点击记录
                # 本任务本来就是 加号<->申请 交替点击, 不清会误判为卡死(GameTooManyClickError)
                self.device.click_record_clear()
                continue
            # 点推荐列表的加号按钮
            if self.appear_then_click(self.I_AF_USER_ADD, interval=2):
                count += 1
                idle = 0
                logger.info(f'AddFriend add once, count={count}/{limit}')
                sleep(self.WAIT_AFTER_CLICK)
                continue
            # 加号没有了, 点换一换刷新推荐列表
            if self.appear_then_click(self.I_AF_REFRESH, interval=2):
                refresh_count += 1
                idle = 0
                logger.info(f'AddFriend refresh, refresh_count={refresh_count}')
                sleep(self.WAIT_AFTER_CLICK)
                # 停止条件3: 换一换次数超限(防死循环)
                if refresh_count >= self.MAX_REFRESH:
                    logger.warning(f'AddFriend refresh limit reached: {refresh_count}')
                    break
                continue
            # 什么都没点, 空闲计数
            idle += 1
            sleep(self.WAIT_IDLE)
        # 4. 收尾: 回主界面, 设置下次运行
        self.goto_page(page_main)
        success = count >= limit
        self.set_next_run('AddFriend', finish=True, success=success)
        raise TaskEnd


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)

    t.run()
