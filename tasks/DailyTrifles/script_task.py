# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import copy
import json
from pathlib import Path
from time import sleep

import difflib
import numpy as np
from datetime import time, datetime, timedelta
from module.atom.image import RuleImage
from ppocronnx.predict_system import BoxedResult

from tasks.Component.config_base import Time
from tasks.DailyTrifles.page import (
    page_store_gift_room,
    page_friends_luck,
    page_guild_wish,
)

from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import (
    page_main,
    page_storage,
    page_summon,
    page_guild,
    page_mall,
    page_friends,
    page_courtyard_affairs,
)
from tasks.GameUi.default_pages import random_click
from tasks.DailyTrifles.config import DailyTriflesConfig
from tasks.DailyTrifles.assets import DailyTriflesAssets
from tasks.Component.Summon.summon import Summon
from tasks.RichMan.mall.navbar import MallNavbar

from module.logger import logger
from module.exception import TaskEnd
from module.base.timer import Timer
from tasks.DailyTrifles.config import SummonType, StorageStatsSource
import re
from typing import Any, Optional, List, Callable


class ScriptTask(MallNavbar, Summon, DailyTriflesAssets):
    """
    继承 MallNavbar 是为了复用商城导航原语 `_enter_special()`（进杂货铺 -> 点右侧竖排「特殊」），
    它内部每一步都有明确的成功判据，比本文件原来手抄的那段裸 `while 1` 稳。

    注意继承顺序：MallNavbar 排在 DailyTriflesAssets 前面，意味着 RichManAssets 的素材优先。
    已核对两个 assets 类的全部大写属性名，**零重名**，不会互相覆盖。
    """

    def run(self):
        con = self.config.daily_trifles.trifles_config
        # 每日召唤
        if con.one_summon:
            self.run_one_summon()
        if con.courtyard_affairs:
            self.run_courtyard_affairs()
        if con.pickup_email:
            self.run_pickup_email()
        if self.config.daily_trifles.guild_donate.enable:
            self.run_guild_donate()
        # 吉闻
        if con.luck_msg:
            self.run_luck_msg()
        # 商店签到 or 购买寿司
        if con.store_sign or con.buy_sushi_count > 0:
            self.run_store()
        # 纳物库统计
        if con.storage_stats:
            self.run_storage_stats()
        self.config.save()
        self.plan_next_dt()
        raise TaskEnd('DailyTrifles')

    def run_one_summon(self):
        logger.hr('daily summon', 2)
        if self.config.daily_trifles.today_is_done('summon'):
            logger.info('Today is done, skip')
            return
        self.goto_page(page_summon)
        config = self.config.daily_trifles.trifles_config
        if config.summon_type == SummonType.default:
            self.summon_one(draw_mystery_pattern=config.draw_mystery_pattern)
            self.check_time()
        elif config.summon_type == SummonType.recall:
            self.summon_recall()
        self.back_summon_main()
        self.config.daily_trifles.done_record.summon_dt = datetime.now()

    def check_time(self):
        config = self.config.daily_trifles.trifles_config
        now = datetime.now()
        next_run = now + self.config.daily_trifles.scheduler.success_interval
        # 检查是否跨月（next_run的月份与当前月份不同）
        if next_run.month != now.month:
            # 跨月重置神秘图案触发状态
            if not config.draw_mystery_pattern:
                config.draw_mystery_pattern = True
                logger.info(f"reset draw_mystery_pattern to True, next_run: {next_run}")
        else:
            # 如果还是在同一月份，则没必要再绘制神秘图案
            config.draw_mystery_pattern = False
        self.config.save()

    def summon_recall(self):
        """
        确保在召唤界面,每日召唤一次
        召唤结束后回到 召唤主界面
        :return:
        """
        list = [self.O_SELECT_SM2, self.O_SELECT_SM3, self.O_SELECT_SM4]
        count = 0
        while True:
            count += 1

            for i in range(len(list)):
                sleep(1)
                self.goto_page(page_summon)
                self.appear_then_click(self.I_UI_BACK_RED, interval=1)
                x, y = list[i].coord()
                self.device.click(x, y)
                sleep(1)
                self.screenshot()
                if self.appear(self.I_RECALL_TICKET):
                    break
                logger.info("Select preset group RECALL")

            self.screenshot()
            if self.appear(self.I_RECALL_TICKET):
                break
            if count >= 3:
                self.config.notifier.push(
                    title='今忆召唤抽卡失败', content='每日任务,今忆召唤抽卡失败!!!'
                )
                return

        logger.info('Summon one RECALL')
        self.wait_until_appear(self.I_RECALL_TICKET)
        while True:
            ticket_info = self.O_RECALL_TICKET_AREA.ocr(self.device.image)
            # 处理 None 和空字符串
            if ticket_info is None or ticket_info == '':
                ticket_info = 0
            else:
                # 使用正则表达式提取字符串中的数字
                match = re.search(r'\d+', ticket_info)
                if match:
                    ticket_info = int(match.group())
                else:
                    logger.warning(
                        f'Invalid ticket_info value: {ticket_info}, expected a numeric string'
                    )
                    ticket_info = 0  # 将无效值设置为默认值 0
            if ticket_info <= 0:
                logger.warning('There is no any one RECALL ticket')
                return
            # 某些情况下滑动异常
            self.S_RANDOM_SWIPE_1.name = 'S_RANDOM_SWIPE'
            self.S_RANDOM_SWIPE_2.name = 'S_RANDOM_SWIPE'
            self.S_RANDOM_SWIPE_3.name = 'S_RANDOM_SWIPE'
            self.S_RANDOM_SWIPE_4.name = 'S_RANDOM_SWIPE'
            while 1:
                self.screenshot()
                if self.appear(self.I_RECALL_ONE_TICKET):
                    break
                if self.appear_then_click(self.I_RECALL_TICKET, interval=1):
                    continue

            # 画一张票
            sleep(1)
            while 1:
                self.screenshot()
                if self.appear(self.I_RECALL_SM_CONFIRM, interval=0.6):
                    self.ui_click_until_disappear(self.I_RECALL_SM_CONFIRM)
                    break
                if self.appear(self.I_SM_CONFIRM_2, interval=0.6):
                    self.ui_click_until_disappear(self.I_SM_CONFIRM_2)
                    break
                if self.appear(self.I_RECALL_ONE_TICKET, interval=1):
                    # 某些时候会点击到 “语言召唤”
                    if self.appear_then_click(self.I_UI_CANCEL, interval=0.8):
                        continue
                    self.summon()
                    continue
            logger.info('Summon one success')

    def run_guild_donate(self):
        logger.hr('guild donate', 2)
        if self.config.daily_trifles.today_is_done('guild_donate'):
            logger.info('Today is done, skip')
            return
        self.goto_page(page_guild_wish)
        timeout_timer = Timer(2).start()
        while not timeout_timer.reached():
            self.screenshot()
            if self.appear(self.I_DT_GW_THANKS):
                self.ui_click(self.I_DT_GW_THANKS, self.I_DT_GW_THANKED, interval=0.8)
                timeout_timer.reset()
                continue
        self.appear_then_click(self.I_UI_BACK_RED)
        donate_datas: list = [
            (
                self.config.daily_trifles.guild_donate.guild_member_list_v,
                lambda: self.switch_select(
                    self.I_DT_GW_GUILD_MEMBER_SELECTED,
                    self.I_DT_GW_FRIEND_SELECTED,
                    self.I_DT_GW_SELECT_GUILD_MEMBER,
                ),
                self.config.daily_trifles.guild_donate.name_check,
            ),
            (
                self.config.daily_trifles.guild_donate.friend_list_v,
                lambda: self.switch_select(
                    self.I_DT_GW_FRIEND_SELECTED,
                    self.I_DT_GW_GUILD_MEMBER_SELECTED,
                    self.I_DT_GW_SELECT_FRIEND,
                ),
                self.config.daily_trifles.guild_donate.name_check,
            ),
        ]
        all_done = True
        for name_list, switch_func, name_check in donate_datas:
            # 分别执行两个名单，前一个名单失败也不能跳过后一个名单。
            donate_ret = self.donate(name_list, switch_func, name_check)
            all_done = all_done and donate_ret
        if self.config.daily_trifles.guild_donate.auto_get_rewards:
            self.guild_donate_get_reward()
        self.config.daily_trifles.done_record.guild_donate_finish = all_done
        self.goto_page(page_main)

    def guild_donate_get_reward(self):
        """领取捐赠碎片的奖励"""
        timeout_timer = Timer(3).start()
        has_reward = False
        while not timeout_timer.reached():
            self.screenshot()
            if self.appear(self.I_UI_BACK_RED, interval=0.6):
                break
            if self.appear(self.I_DT_GW_DONATE_RECORD_RED):
                self.ui_click(
                    self.I_DT_GW_DONATE_RECORD, self.I_UI_BACK_RED, interval=1.2
                )
                has_reward = True
                continue
        if not has_reward:
            logger.info('No reward can get, exit')
            return
        timeout_timer.reset()
        while not timeout_timer.reached():
            self.screenshot()
            self.ui_reward_appear_click()
            if self.appear_then_click(self.I_UI_CONFIRM, interval=0.6):
                continue
            # 受赠界面的一键感谢
            if self.appear_then_click(self.I_DT_GW_DONATE_RECORD_THANKS, interval=1.5):
                timeout_timer.reset()
                continue
            # 赠予界面的一键领取
            if self.appear_then_click(self.I_DT_GW_ONE_COLLECT, interval=1.5):
                timeout_timer.reset()
                continue
            # 切换到赠予界面
            if self.appear(self.I_DT_GW_DONATE_RED, interval=2.5) and not self.appear(
                self.I_UI_CONFIRM, interval=2.5
            ):
                self.appear_then_click(self.I_DT_GW_GIVE, interval=1.5)
                timeout_timer.reset()
                continue

        self.ui_click_until_disappear(self.I_UI_BACK_RED)

    def donate(
        self, name_list: List[str], switch_func: Callable, name_check: bool
    ) -> bool:
        """执行碎片捐赠流程

        :param name_list: 待捐赠碎皮的名称列表
        :param switch_func: 切换好友/阴阳寮/...的方法
        :param name_check: 是否使用ocr检查用户名
        :return: 是否全部捐赠成功 (出现检索名称后为空或者碎皮不足都是False, 仅全部捐成功才是True)
        """
        all_done = True
        for name in name_list:
            switch_func()
            self.swipe(self.S_DT_GW_OPEN_SEARCH, interval=1.2)  # 向下滑动拉出搜索框
            # 从按照交换搜索切换到按名称搜索
            self.switch_select(
                self.I_DT_GW_SEARCH_BY_NAME,
                self.I_DT_GW_SEARCH_BY_SWAP,
                self.I_DT_GW_SELECT_BY_NAME,
            )
            self.appear_then_click(self.I_DT_GW_CLEAR_SEARCH)  # 清除搜索框内容
            self.ui_click(
                self.C_DT_GW_INPUT_SEARCH, self.I_DT_GW_CONFIRM, interval=1.5
            )  # 点击搜索框
            self.click(self.C_DT_GW_CLICK_INPUT)  # 点击名称输入框
            # uiautomator2 通过 FastInputIME 传输 UTF-8 文本，并在必要时回退到 set_text
            logger.info(
                f'Inputting name using uiautomator2: {name}, waiting start and send'
            )
            self.device.u2.send_keys(name, clear=True)
            sleep(2)
            self.ui_click_until_disappear(
                self.I_DT_GW_CONFIRM, interval=1.5
            )  # 点击确定
            donate_btn = self.I_DT_GW_DONATE
            if name_check:  # 若有多个相同前缀名称, 则需要取出一样的或最相近的名称
                name_roi = self.find_target_name(name)
                if name_roi is None:
                    logger.warning(f'{name} check failed, maybe not wish or not find')
                    all_done = False
                    continue
                # 设置赠与按钮back与对应name同一行
                donate_btn.roi_back = [
                    name_roi[0] - 5,
                    name_roi[1] - 15,
                    850,
                    90,
                ]  # 设置赠与按钮back区域和对应name同一行
            self.I_DT_GW_FULL.roi_back = (
                donate_btn.roi_back
            )  # 设置已捐满标志back区域和赠与按钮同一行
            self.I_DT_GW_INSUFFICIENT.roi_back = (
                donate_btn.roi_back
            )  # 设置碎片不足标志back区域和赠与按钮同一行
            donate_ret = self.process_donate(donate_btn, name)
            all_done = all_done and donate_ret  # 有一次没成功则all_done永远False
        return all_done

    def process_donate(self, donate_btn: RuleImage, name: str) -> bool:
        """捐赠式神碎片
        TODO: 游戏有bug,搜索结束之后会突然神经展开全部寮友/好友,会有一瞬间识别到了好友,点击捐赠结果实际捐错人了

        :param name: 被捐方名称
        :param donate_btn: 赠与按钮
        :return: 捐赠是否成功(点击了捐赠/确定/识别到已满都认为成功)
        """
        timeout_timer = Timer(3).start()
        donated = False  # 判断是否已执行捐赠
        while not timeout_timer.reached():
            self.screenshot()
            if self.appear(self.I_DT_GW_SEARCH_EMPTY):
                logger.warning('Maybe not wish or not find, skip')
                if self.config.daily_trifles.guild_donate.notify_enable:
                    self.config.notifier.push(
                        title='好友搜索失败',
                        content=f'{name} 搜索失败, 没有搜索到对应用户, 无法捐赠',
                    )
                return False
            if not donated and self.appear_then_click(donate_btn, interval=2.5):
                timeout_timer.reset()
                # 点击后，等待所有弹窗消失（确认、奖励），然后重新定位
                post_click_timeout = Timer(3).start()
                while not post_click_timeout.reached():
                    self.screenshot()
                    # 处理确认弹窗
                    if self.appear(self.I_UI_CONFIRM, interval=0.6):
                        self.ui_get_reward(self.I_UI_CONFIRM, click_interval=1.5)
                        donated = True
                    # 如果没有弹窗了，退出子循环
                    if not self.appear(self.I_UI_REWARD) and not self.appear(
                        self.I_UI_REWARD
                    ):
                        break
                if donated:
                    # 处理了弹窗，视为捐赠成功
                    logger.info(f'Donate success for {name}!')
                    return True
                else:
                    # 子循环结束后，重新定位目标行（因为如果被搜索到的玩家有多个，被赠与方被赠送满了就会下沉到最后位置）
                    new_roi = self.find_target_name(name)
                    if new_roi is None:
                        logger.warning(f'{name} disappeared after donation, skip')
                        return False
                    # 更新三个按钮的 ROI
                    new_roi_back = [new_roi[0] - 5, new_roi[1] - 15, 850, 90]
                    donate_btn.roi_back = new_roi_back
                    self.I_DT_GW_FULL.roi_back = new_roi_back
                    self.I_DT_GW_INSUFFICIENT.roi_back = new_roi_back
                    logger.info(f"Updated ROI after donation attempt: {new_roi_back}")
                    continue

            if self.appear(self.I_DT_GW_INSUFFICIENT, interval=0.6):
                logger.warning('Not enough fragment to donate, skip')
                if self.config.daily_trifles.guild_donate.notify_enable:
                    self.config.notifier.push(
                        title='捐赠碎片不足',
                        content=f'捐给{name}的碎片不足, 请上线查看',
                    )
                return False
            if self.appear(self.I_DT_GW_FULL, interval=0.6):
                logger.info(f'Donate success!')
                donated = True
        return donated

    def find_target_name(self, name) -> List[int]:
        """寻找目标名称
        :param name: 名称
        :return: [x, y, w, h]
        """
        timeout_timer = Timer(3).start()
        name_roi: List[int] = None
        # TODO: 这里只找了第一页, 若相似名称过多后续需要添加翻页继续找功能
        while not timeout_timer.reached():
            self.screenshot()
            if self.appear(self.I_DT_GW_SEARCH_EMPTY):  # 空的直接退出
                if self.config.daily_trifles.guild_donate.notify_enable:
                    self.config.notifier.push(
                        title='好友搜索失败',
                        content=f'没有搜索到对应用户 {name}, 无法捐赠',
                    )
                return None
            text_results = self.O_DT_GW_NAME.detect_and_ocr(self.device.image)
            mx_similarity = 0.5
            for result in text_results:
                if result.ocr_text == name:
                    return self.extract_roi(result)  # 名称一模一样则直接返回
                similarity = difflib.SequenceMatcher(
                    None, result.ocr_text, name
                ).ratio()
                if similarity > mx_similarity:
                    mx_similarity = similarity
                    name_roi = self.extract_roi(result)
            if name_roi is None:
                continue
            return name_roi  # 找到了直接退出
        return name_roi

    def extract_roi(self, result: BoxedResult) -> list[int]:
        """从ocr结果提取对应的roi坐标"""
        x = self.O_DT_GW_NAME.roi[0] + result.box[0, 0]
        y = self.O_DT_GW_NAME.roi[1] + result.box[0, 1]
        w, h = result.box[1, 0] - result.box[0, 0], result.box[2, 1] - result.box[0, 1]
        return [x, y, w, h]

    def switch_select(self, target: RuleImage, other: RuleImage, select: RuleImage):
        """切换选中的元素"""
        while True:
            self.screenshot()
            if self.appear(target):
                break
            if self.appear_then_click(select, interval=0.6):
                continue
            if self.appear_then_click(other, interval=1.8):
                continue

    def run_luck_msg(self):
        logger.hr('luck msg', 2)
        if self.config.daily_trifles.today_is_done('luck_msg'):
            logger.info('Today is done, skip')
            return
        self.goto_page(page_friends_luck)
        logger.info('Start luck msg')
        check_timer = Timer(2)
        check_timer.start()
        while 1:
            self.screenshot()

            if self.appear_then_click(self.I_CLICK_BLESS, interval=1):
                continue
            if self.appear_then_click(self.I_ONE_CLICK_BLESS, interval=1):
                continue
            if self.ui_reward_appear_click():
                logger.info('Get reward of luck msg')
                break
            if check_timer.reached():
                logger.warning('There is no any luck msg')
                break

        # 主界面可能被浮层遮挡导致识别失败：先点红 × 清掉吉闻本身，直到看见主界面底部的「阴阳寮」
        self.ui_click_until_appear_or_timeout(
            self.I_UI_BACK_RED, stop=self.I_MAIN_GOTO_GUILD, interval=1, timeout=8
        )

        self.screenshot()
        if self.appear(self.I_MAIN_GOTO_GUILD):
            # 已经在主界面（或其上的浮层还在）：进「阴阳寮」再点黄箭头退回来，绕一圈把浮层破掉。
            #
            # NOTE 这里**不能只点一次**。2026-10-29 13:42 实测：主界面上压着一层
            # 「友情点 / 可在兑换商店中兑换奖励 / 获取途径：好友赠送」的物品说明面板，
            # 它是模态的 —— 第一下点击只会关掉它、不会触发底下的「阴阳寮」按钮。
            # 旧代码用 wait_until_appear_then_click 只点一次就往下走，结果人还停在主界面，
            # 而那个面板又把庭院识别素材 I_CHECK_MAIN_17 盖住（实测 0.3479 < 阈值 0.75，
            # 干净庭院是 0.9968），于是 goto_page(page_main) 每轮 Page detect miss，
            # 30 秒后 navigation stalled 直接报错重启。
            # 所以改成一直点，直到真的离开主界面（进了寮界面就看不到底部「阴阳寮」了）。
            enter_timer = Timer(8).start()
            entered = False
            while not enter_timer.reached():
                self.screenshot()
                if not self.appear(self.I_MAIN_GOTO_GUILD):
                    entered = True
                    break
                # interval 1.5s：8 秒内最多点 6 次，既够破两层浮层，也不会撞上
                # 设备层「同一按钮短时 ≥10 次」的 GameTooManyClickError 保护。
                self.appear_then_click(self.I_MAIN_GOTO_GUILD, interval=1.5)
            if not entered:
                logger.warning('luck msg: 点「阴阳寮」8 秒仍未离开主界面')

            # 从寮界面点黄箭头回主界面
            self.ui_click_until_appear_or_timeout(
                self.I_UI_BACK_YELLOW, stop=self.I_MAIN_GOTO_GUILD, interval=1, timeout=8
            )

        self.goto_page(page_main)
        self.config.daily_trifles.done_record.luck_msg_dt = datetime.now()

    def run_store(self):
        if self.check_store_all_done():
            logger.info('Store all done, skip')
            return
        self.goto_page(page_mall, confirm_wait=3)
        if self.config.daily_trifles.trifles_config.store_sign:
            self.run_store_sign()
        if self.config.daily_trifles.trifles_config.buy_sushi_count > 0:
            self.run_buy_sushi()
        self.goto_page(page_main)

    def run_store_sign(self):
        logger.hr("store sign", 2)
        if self.config.daily_trifles.today_is_done("store_sign"):
            logger.info("Today is done, skip")
            return
        self.config.daily_trifles.done_record.store_sign_dt = datetime.now()
        self.goto_page(page_store_gift_room)
        self.screenshot()
        self.appear_then_click(self.I_GIFT_RECOMMEND, interval=1)
        logger.info("Enter store sign")
        sleep(1)  # 等个动画
        self.screenshot()
        if not self.appear(self.I_GIFT_SIGN):
            logger.warning("There is no gift sign")
            return

        if self.ui_get_reward(self.I_GIFT_SIGN, click_interval=2.5):
            logger.info('Get reward of gift sign')

        self.screenshot()
        if self.appear(self.I_GIFT_SIGN_GOT):
            self.click(random_click(ltrb=(True, False, False, False)), interval=1.5)
            return

    def run_buy_sushi(self):
        logger.hr('store sushi', 2)
        if self.config.daily_trifles.today_is_done('sushi'):
            logger.info('Today is done, skip')
            return
        # 进入「特殊」（体力）页
        #
        # 2026-10-29 15:36 配置4 事故复盘：run_store_sign() 领完礼包签后直接返回、没回商城，
        # run_buy_sushi() 接手时人还停在「礼包屋」。原来这里是一段裸 while 1，三个判据在礼包屋上的
        # 实测值是：I_SIDE_CHECK_SPECIAL 0.9478 误命中（那其实是顶部体力寿司图标）、
        # I_SPECIAL_SUSHI 0.5638 未命中、I_SIDE_CHECK_ROOKIE_MALL 0.9928 误命中（那其实是顶部红勾玉
        # 图标——礼包屋比庭院多显示一个「4」和一个「+」，整排元素右移，把勾玉推到了固定 ROI x913 上）。
        # 于是走进第二支 ui_click(I_ROOKIE_GOTO_SPECIAL, stop=I_SIDE_CHECK_SPECIAL)，
        # 而 ui_click 第一句就是 if appear(stop): break —— stop 恒误命中，一个点击都不发就 continue，
        # 回到循环再判一次；60 秒零点击后设备层判 GameStuckError，重启游戏。
        #
        # 改法：换上上游 RichMan 里已经验证过的 _enter_special()：
        #   进杂货铺（ui_click 直到 I_MALL_SUNDRY_CHECK 出现；礼包屋实测 0.4727 -> 点击后 0.9421）
        #   -> 在右侧竖排列表里点「特殊」
        #   -> 用 I_SIDE_CHECK_SPECIAL 确认真的到了体力页（杂货铺 0.4934 不命中、体力页 0.9553 命中）
        # 每一步都有明确的「到没到」判据，而不是一段靠巧合成立的点击序列。
        #
        # 先确认自己在「商城的某个子页」里。
        #
        # 这里不用 goto_page(page_mall) 无条件回首页，也不用 I_CHECK_MALL 判断（实测那张素材在
        # 商城首页只有 0.5825、杂货铺 0.5806，低于 0.8 阈值，判不准）。改用一个实测很干净的判据
        # I_MALL_SUNDRY（底部「杂货铺」）：商城首页 0.9773、杂货铺 0.9149、体力页 0.9149、
        # 礼包屋 0.9496 都命中（礼包屋本来就是商城子页），庭院 0.2094 不命中。阈值 0.8。
        # 在商城里就不调 goto_page（省掉一次不必要的导航），真掉到庭院去了才回商城兜底。
        self.screenshot()
        if not self.appear(self.I_MALL_SUNDRY):
            self.goto_page(page_mall, confirm_wait=3)
        if not self._enter_special():
            logger.warning('store sushi: 没能进入商城「特殊」页，跳过购买体力')
            return

        def detect_buy_count(base_element) -> (int, int):
            # 返回count,price
            MAX_PRICE = 9999
            MAX_COUNT = 9999
            roi = copy.deepcopy(base_element.roi_front)
            roi[0] = roi[0] + roi[2]
            roi[1] = roi[1] + roi[3] - 45
            roi[2] = 75
            roi[3] = 45
            self.O_STORE_SUSHI_PRICE.roi = roi
            _price = self.O_STORE_SUSHI_PRICE.detect_text(self.device.image)
            # 保守策略，避免OCR错误购买
            try:
                _price = int(_price)
            except Exception as e:
                _price = MAX_PRICE

            if _price < 60:
                return 0, MAX_PRICE
            _count = (_price - 60) / 20
            return _count, _price

        roi = None
        # 购买体力
        #
        # 上游这里是裸 while 1：只要界面不在下面这两个预期状态（弹窗没关、还在加载、热更公告、
        # 或者干脆误停在别的页面），就会一直 screenshot + 模板匹配地空转，
        # 60 秒后被设备层的「长时间无有效操作」判定为 GameStuckError，整个任务被打断重启。
        #
        # 改成「无进展」计时器：每真正做成一步（领到一次体力 / 成功打开价格弹窗）就 reset，
        # 只有连续 30 秒一步都做不成才退出。正常购买不受影响，配置买很多次也不会被误杀。
        idle_timer = Timer(30).start()
        while 1:
            self.screenshot()
            # count, price = detect_buy_count(roi)
            # if count >= self.config.model.daily_trifles.trifles_config.buy_sushi_count:
            #     break
            logger.info(
                f"购买次数为: {self.config.daily_trifles.trifles_config.buy_sushi_count} 次"
            )
            if self.appear(self.I_STORE_COST_TYPE_JADE):
                count, price = detect_buy_count(self.I_STORE_COST_TYPE_JADE)
                if count >= self.config.daily_trifles.trifles_config.buy_sushi_count:
                    break
                if self.ui_click_until_disappear(self.I_UI_CANCEL_SAMLL):
                    logger.info(f"Maybe jade not enough, stop")
                    break
                if self.ui_get_reward(self.I_STORE_COST_TYPE_JADE, click_interval=2.5):
                    logger.info(f"Buy Sushi With {price} Jade")
                    idle_timer.reset()
                    continue

            if self.appear(self.I_SPECIAL_SUSHI):
                # 此处确定当前购买体力所需勾玉数量的位置,用于后续识别
                count, price = detect_buy_count(self.I_SPECIAL_SUSHI)
                if count >= self.config.daily_trifles.trifles_config.buy_sushi_count:
                    logger.info(f"已经购买 {count} 次, 退出购买")
                    break
                self.ui_click(
                    self.I_SPECIAL_SUSHI, stop=self.I_STORE_COST_TYPE_JADE, interval=2
                )
                idle_timer.reset()
                continue

            # 走到这里说明这一轮既没打开价格弹窗、也没识别到体力商品 —— 界面不在预期状态
            if idle_timer.reached():
                logger.warning(
                    'store sushi: 30 秒没有任何进展，界面不在体力页，跳过购买体力'
                )
                break
        self.config.daily_trifles.done_record.sushi_dt = datetime.now()

    def run_courtyard_affairs(self):
        """庭院事务"""
        logger.hr('courtyard affairs', 2)
        self.goto_page(page_main)
        timeout_timer = Timer(3).start()
        while not timeout_timer.reached():
            self.screenshot()
            if self.appear(self.I_ENTER_COURTYARD_AFFAIRS, interval=1.2):
                self.goto_page(page_courtyard_affairs)
                timeout_timer.reset()
                break
        if timeout_timer.reached():
            logger.info('Not have courtyard affairs, exit')
            return
        while True:
            self.screenshot()
            if self.appear(self.I_CHECK_IN_DAILY, interval=0.5):
                break
            if self.appear_then_click(self.I_ENTER_DAILY, interval=1):
                continue
        self.appear_then_click(self.I_ONE_COMPLETE, interval=1)
        self.goto_page(page_main)
        self.config.daily_trifles.done_record.courtyard_affairs_dt = datetime.now()

    def run_pickup_email(self):
        """领取邮件"""
        logger.hr('pick up email', 2)
        self.goto_page(page_main)
        timeout_timer = Timer(3).start()
        while not timeout_timer.reached():
            self.screenshot()
            if (
                self.appear_then_click(self.I_DT_HARVEST_MAIL_COPY2, interval=1.2)
                or self.appear_then_click(self.I_HARVEST_MAIL, interval=1.2)
                or self.appear_then_click(self.I_HARVEST_MAIL_COPY, interval=1.2)
            ):
                continue
            if self.appear_then_click(self.I_HARVEST_MAIL_CONFIRM, interval=1):
                continue
            if self.appear_then_click(self.I_HARVEST_MAIL_ALL, interval=2):
                timeout_timer.reset()
                continue
            if self.appear_then_click(self.I_READ_ALL_MAIL, interval=3):
                continue
        self.goto_page(page_main)
        self.config.daily_trifles.done_record.pickup_email_dt = datetime.now()

    def plan_next_dt(self):
        # 定时领体力（每天 12-14、20-22 时内各有 20 体力）
        now = datetime.now()
        # 如果时间在00:00-12:00之间则设定时间为当日 12 时
        if now.time() < time(12, 0):
            self.custom_next_run(
                task='DailyTrifles', custom_time=Time(12, 0), time_delta=0
            )
        # 如果时间在12:00-20:00之间则设定时间为当日 20 时
        elif time(12, 0) <= now.time() < time(20, 0):
            self.custom_next_run(
                task='DailyTrifles', custom_time=Time(20, 0), time_delta=0
            )
        # 如果时间在20:00-23:59之间则设定时间为次日 12 时
        else:
            self.custom_next_run(
                task='DailyTrifles', custom_time=Time(12, 0), time_delta=1
            )

    def check_store_all_done(self) -> bool:
        """判断商店任务是否都做完了, 做完了则不再进入商店"""
        if (
            self.config.daily_trifles.trifles_config.store_sign
            and not self.config.daily_trifles.today_is_done('store_sign')
        ):
            return False
        if (
            self.config.daily_trifles.trifles_config.buy_sushi_count > 0
            and not self.config.daily_trifles.today_is_done('sushi')
        ):
            return False
        return True

    # 标题栏 ROI 比数字宽，左右留出的纯背景空白列会让整框识别偶发丢掉首字符，
    # 识别前先按「每列最大亮度」裁掉两侧空白列，阈值取在背景与文字之间。
    TITLE_TRIM_THRESHOLD = 130
    TITLE_TRIM_PAD = 2

    @classmethod
    def trim_ocr_margin(cls, image, threshold: int = None, pad: int = None):
        """裁掉 image 左右两侧的纯背景空白列，各保留 pad 像素。"""
        threshold = cls.TITLE_TRIM_THRESHOLD if threshold is None else threshold
        pad = cls.TITLE_TRIM_PAD if pad is None else pad
        column_max = image.max(axis=2).max(axis=0)
        ink = np.where(column_max > threshold)[0]
        if not len(ink):
            return image
        x0 = max(0, int(ink[0]) - pad)
        x1 = min(image.shape[1], int(ink[-1]) + 1 + pad)
        return image[:, x0:x1]

    def read_title_value(self, ocr_rule, image) -> str:
        """读标题栏资源数值，只保留「数字(+可选小数)(+可选万/亿)」，无数字返回空串。"""
        x, y, w, h = ocr_rule.roi
        crop = ocr_rule.pre_process(image[y : y + h, x : x + w])
        crop = self.trim_ocr_margin(crop)
        raw, score = ocr_rule.model.ocr_single_line(crop)
        # 裁边后识别失败就退回原流程（含竖排文本的 detect_and_ocr 兜底）
        if raw and score >= ocr_rule.score:
            result = ocr_rule.after_process(raw)
        else:
            result = ocr_rule.ocr(image, log=False)
        matched = re.search(r'\d+(?:\.\d+)?[万亿]?', str(result))
        return matched.group(0) if matched else ''

    def run_storage_stats(self) -> dict:
        """纳物库统计（memory=读游戏内存32项；ocr=进纳物库截图识别8项）。"""
        logger.hr('storage stats', 2)
        source = self.config.daily_trifles.trifles_config.storage_stats_source
        if source == StorageStatsSource.memory:
            try:
                from tasks.DailyTrifles.game_memory import GameMemoryReader

                stats = GameMemoryReader(self.device).read()
            except Exception as e:
                logger.error(f'纳物库内存读取失败: {e}')
                return {}

            instance = re.sub(r'[^\w.-]', '_', self.config.config_name)
            now = datetime.now()
            folder = Path('./log/storage_stats') / instance / now.strftime('%Y-%m-%d')
            folder.mkdir(parents=True, exist_ok=True)
            json_file = folder / f'{now.strftime("%Y-%m-%d_%H-%M-%S")}.json'
            with open(json_file, 'w', encoding='utf-8') as f:
                json.dump(stats, f, ensure_ascii=False, indent=2)
            logger.info(f'纳物库统计已保存至 {json_file}')
            return stats
        return self._run_storage_stats_ocr()

    def _run_storage_stats_ocr(self) -> dict:
        """进纳物库截图识别 8 项：金币/体力/勾玉/神秘的符咒/大蛇的逆鳞/逢魔之魂/现世符咒/御札。"""
        self.goto_page(page_storage)
        self.ui_click_until_disappear(self.I_SWITCH_TO_RESOURCE, interval=0.5)
        self.screenshot()

        # 标题栏资源：金币 / 体力 / 勾玉（读固定 ROI + 剥离噪声）
        stats = {}
        for name, ocr_rule in (
            ('金币', DailyTriflesAssets.O_GOLD_COUNT),
            ('体力', DailyTriflesAssets.O_SUSHI_COUNT),
            ('勾玉', DailyTriflesAssets.O_JADE_COUNT),
        ):
            stats[name] = self.read_title_value(ocr_rule, self.device.image)

        # 仓库资源：模板定位图标 -> 点击 -> 读右侧「持有：N」固定 ROI
        items = (
            ('神秘的符咒', DailyTriflesAssets.I_BLUE_TICKET),
            ('大蛇的逆鳞', DailyTriflesAssets.I_GOLD_SKIN),
            ('逢魔之魂', DailyTriflesAssets.I_DEMON_SKIN),
            ('现世符咒', DailyTriflesAssets.I_PRESENT_WORLD_TICKET),
            ('御札', DailyTriflesAssets.I_RETURN_SOUL),
        )
        for name, icon in items:
            self.screenshot()
            if not self.appear(icon):
                # 页面上确实没有这个资源（例如现世符咒已用完）→ 记 0，别漏键
                logger.info(f'纳物库中未找到资源 {name}，记为 0')
                stats[name] = 0
                continue
            self.click(icon, interval=1)
            sleep(0.8)  # 等右侧面板刷新
            self.screenshot()
            stats[name] = DailyTriflesAssets.O_COMMON_COUNT.ocr(
                self.device.image, log=False
            )

        logger.info(f'纳物库统计结果: {stats}')

        # 统计结果落盘（不再保存截图）
        instance = re.sub(r'[^\w.-]', '_', self.config.config_name)
        now = datetime.now()
        folder = Path('./log/storage_stats') / instance / now.strftime('%Y-%m-%d')
        folder.mkdir(parents=True, exist_ok=True)
        timestamp = now.strftime('%Y-%m-%d_%H-%M-%S')
        json_file = folder / f'{timestamp}.json'
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
        logger.info(f'纳物库统计结果已保存至 {json_file}')

        self.goto_page(page_main)
        return stats


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas2')
    d = Device(c)
    t = ScriptTask(c, d)

    t.run_guild_donate()
