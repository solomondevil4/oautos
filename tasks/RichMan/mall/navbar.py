# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import re
import time

from module.atom.click import RuleClick
from module.atom.image import RuleImage
from module.base.timer import Timer
from module.logger import logger

from tasks.GameUi.page import page_main, page_guild
from tasks.GameUi.game_ui import GameUi
from tasks.Component.Buy.buy import Buy
from tasks.RichMan.assets import RichManAssets



class MallNavbar(GameUi, RichManAssets):

    def _enter_consignment(self):
        """
        进入寄售屋
        :return:
        """
        self.ui_click(self.I_MALL_CONSIGNMENT, self.I_MALL_CONSIGNMENT_CHECK)

    def _enter_scales(self):
        """
        进入密卷屋 蛇皮
        :return:
        """
        self.ui_click(self.I_MALL_SCCALES, self.I_MALL_SCCALES_CHECK)

    def _enter_bondlings(self):
        """
        进入契灵
        :return:
        """
        self._enter_scales()
        self.ui_click(self.I_MALL_BONDLINGS_SURE, self.I_MALL_BONDLINGS_ON)

    def _enter_sundry(self):
        """
        进入杂货铺
        :return:
        """
        self.ui_click(self.I_MALL_SUNDRY, self.I_MALL_SUNDRY_CHECK)

    def _enter_special(self) -> bool:
        """
        进入特殊
        :return:
        """
        self._enter_sundry()
        pos = self.list_find(self.L_RM_NAVBAR, name='special', max_swipe=3)
        return self.click_and_check(pos, 'RM_NAVBAR_SPECIAL', self.I_SIDE_CHECK_SPECIAL)

    def click_and_check(self, pos, control_name, check_rule: RuleImage) -> bool:
        """
        点击并检查
        :param pos: 点击位置
        :param control_name: 控件名称
        :param check_rule: 检查规则
        :return: 是否点击并检查成功
        """
        if not pos:
            return False
        interval_timer = Timer(1.2)
        max_click_cnt = 5
        while max_click_cnt >= 0:
            self.screenshot()
            if self.appear(check_rule):
                return True
            if not interval_timer.started() or interval_timer.reached():
                self.device.click(x=pos[0], y=pos[1], control_name=control_name)
                max_click_cnt -= 1
                interval_timer.reset()
        return False

    def _enter_honor(self) -> bool:
        """
        进入荣誉 屋
        :return:
        """
        self._enter_sundry()
        img_names = ['honor', 'duel']
        pos = self.list_find(self.L_RM_NAVBAR, name=img_names, max_swipe=3)
        return self.click_and_check(pos, 'RM_NAVBAR_HONOR', self.I_SIDE_CHECK_HONOR)

    def _enter_friendship(self):
        """
        友情点
        :return:
        """
        self._enter_sundry()
        pos = self.list_find(self.L_RM_NAVBAR, name='friendship', max_swipe=3)
        return self.click_and_check(pos, 'RM_NAVBAR_FRIENDSHIP', self.I_SIDE_CHECK_FRIENDS)

    def _enter_medal(self):
        """
        勋章
        :return:
        """
        self._enter_sundry()
        pos = self.list_find(self.L_RM_NAVBAR, name='medal', max_swipe=3)
        return self.click_and_check(pos, 'RM_NAVBAR_MEDAL', self.I_SIDE_CHECK_MEDAL)

    def _enter_charisma(self):
        """
        魅力
        :return:
        """
        self._enter_sundry()
        pos = self.list_find(self.L_RM_NAVBAR, name='charm', max_swipe=3)
        return self.click_and_check(pos, 'RM_NAVBAR_CHARM', self.I_SIDE_CHECK_CHARISMA)

    def close_local_modal(self) -> bool:
        """
        关掉本商城体系特有的模态弹窗，供 back_mall 兜底时调用。

        默认没有（返回 False）；寄售屋这类有自己的确认弹窗的子类可以覆盖。
        模态窗盖在最上层时，黄箭头和红 X 都是点不到的，通用关闭动作也救不了，
        只能由知道那个弹窗长什么样的子类自己收拾。
        :return: 本次是否关掉了至少一个弹窗
        """
        return False

    def back_mall(self, retry: int = 4) -> bool:
        """
        返回商城

        不用 ui_click：它内部是 while 1，只要 I_CHECK_MALL 出不来就会一直点黄箭头，
        点满 10 次就撞框架的「同一按钮 ≥10 次」保护 →
        GameTooManyClickError 把整个任务打断（2026-10-25 17:52 douji4 实况：
        寄售屋详情页被「寄售券不足」弹窗盖住，红 X 点不动，最后黄箭头点到崩溃）。

        这里改成有限次数 + 每轮校验，点不出来就依次降级：
          1. 让子类关掉它自己的模态弹窗（close_local_modal）
          2. 交给导航层的通用兜底 close_unknown_pages（红 X / 黄箭头 / 蓝箭头等）
        都失败只返回 False，不再无限循环。
        :param retry: 点黄箭头的最大次数
        :return: 是否已回到商城页
        """
        # 本次最多点 4+2+3 次黄箭头，先清一次历史，确保不会叠加到 10 次触发保护
        self.device.click_record_clear()
        for _ in range(retry):
            self.screenshot()
            if self.appear(self.I_CHECK_MALL):
                return True
            # 判断不带 interval，interval 只给点击（冷却未到时点击会被跳过，不影响本轮）
            self.appear_then_click(self.I_UI_BACK_YELLOW, interval=1)
            time.sleep(1)

        # 兜底 1：子类自己的模态弹窗
        if self.close_local_modal():
            logger.info('back_mall: local modal closed, retry back arrow')
            for _ in range(2):
                self.screenshot()
                if self.appear(self.I_CHECK_MALL):
                    return True
                self.appear_then_click(self.I_UI_BACK_YELLOW, interval=1)
                time.sleep(1)

        # 兜底 2：导航层的通用关闭动作
        logger.warning('back_mall: back arrow failed, try close_unknown_pages')
        for _ in range(3):
            if not self.close_unknown_pages(skip_first_screenshot=False):
                break
            self.screenshot()
            if self.appear(self.I_CHECK_MALL):
                logger.info('back_mall: recovered by close_unknown_pages')
                return True

        self.device.click_record_clear()
        self.screenshot()
        ok = self.appear(self.I_CHECK_MALL)
        if not ok:
            logger.warning('back_mall: still not on mall page')
        return ok

    def mall_resource(self, index: int) -> int:
        """
        获取商城资源，
        :param index: 从左开始数
        :return:
        """
        match = {
            1: self.O_MALL_RESOURCE_1,
            2: self.O_MALL_RESOURCE_2,
            3: self.O_MALL_RESOURCE_3,
            4: self.O_MALL_RESOURCE_4,
            5: self.O_MALL_RESOURCE_5,
            6: self.O_MALL_RESOURCE_6,
        }
        self.screenshot()
        result = match[index].ocr(self.device.image)
        # match = re.search(r'\d+', result)
        # result = int(match.group())
        if not isinstance(result, int):
            logger.warning(f'Get mall resource {index} error, result: {result}')
        if result == 0:
            logger.warning(f'Get mall resource {index} error, result: {result}')
        return result

    def mall_check_money(self, index: int, least: int) -> bool:
        return self.mall_resource(index) >= least

if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = MallNavbar(c, d)

