# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import re
from time import sleep
from datetime import timedelta, datetime, time
from cached_property import cached_property

import numpy as np

from module.exception import TaskEnd
from module.logger import logger
from module.base.timer import Timer

from tasks.GameUi.page import page_main, page_mall
from tasks.GameUi.game_ui import GameUi
from tasks.RichMan.mall.friendship_points import FriendshipPoints
from tasks.MysteryShop.config import MysteryShop, ShopConfig
from tasks.MysteryShop.assets import MysteryShopAssets
from tasks.Component.GeneralInvite.general_invite import GeneralInvite
from tasks.Component.GeneralInvite.config_invite import InviteConfig

class ScriptTask(FriendshipPoints, MysteryShopAssets, GeneralInvite):

    # 神秘商店只在周三(2)、周六(5)开放，游戏机制如此，任务也只在这两天跑。
    OPEN_WEEKDAYS = (2, 5)
    # 判断「滑到底了」的画面平均像素差阈值。
    # 实测（16416）：有效滑动 21.6，到底后再滑 0.3~0.6，差 40 倍，5.0 两边都安全。
    SCROLL_MIN_DIFF = 5.0

    # 判断「商品列表是否已经在最顶部」的采样带：标题栏正下方 y=54~80 这一窄条。
    # 标题栏是固定遮罩，商品一旦滚动，每格卡片上方的「剩余购买次数」标签就会被它
    # 裁掉上半截，只剩一点笔画露在这条窄带里。
    # 实测 9 张截图（16416 自己商店、16512 自己商店、好友商店、刷新前后）：
    #   已在顶部：亮像素占比 0.24%（约 54 个像素，纯噪声）
    #   滚动过  ：亮像素占比 5.07% ~ 5.36%（1134 ~ 1190 个像素）
    # 差 22 倍；且这条窄带只受「是否滚动」影响，与商品内容、谁分享的商店都无关
    # （两个账号的顶部读数完全相同），取 2% 两边余量都足够。
    TOP_PROBE_BOX = (140, 54, 860, 26)
    TOP_PROBE_LEVEL = 80
    TOP_PROBE_MAX = 0.02
    # 回顶部最多上滑几次（有界）。实测一次上滑就够（整屏位移约 253 像素）。
    SCROLL_TOP_MAX = 2
    # 往下最多滑几次（有界）。整店最多 3 行 11 件，第一屏已显示 2 行，
    # 而 ms_scroll_down 的 y 轴跨度是 390~470 像素（约 2 行，且小于一屏可视高度 604，
    # 滑动前后窗口必有重叠、不会漏行），所以再滑 2 次一定到底。
    SCROLL_DOWN_MAX = 2

    # 点刷新后等确认框的时间。实测确认框 0.5~1 秒就出来；等不到就说明本日已刷新过。
    REFRESH_CONFIRM_TIMEOUT = 5

    def run(self):
        if self.start_time.weekday() not in self.OPEN_WEEKDAYS:
            logger.warning('Today is not MysteryShop day')
            self.next_time(False)
        self.goto_page(page_mall)
        self.ui_click(self.I_ME_ENTER, self.I_MS_SHARE)
        logger.info('Enter MysteryShop')
        con = self.config.mystery_shop
        # 神秘商人等级只在这里读一次。此刻站在自己的商店页（上面等的是 I_MS_SHARE），
        # 显示的才是自己的真实等级；好友页显示的是分享者的等级，一律不读也不用。
        # 这个值当整趟任务的开关：到上限就全程不买金币道具，否则全程照买。
        self.coin_level = self._shop_level() if con.shop_config.buy_coin_item else 0
        # 本趟任务是否已经尝试过刷新商店（每天只能花 5 勾玉刷 1 次，绝不重试）
        self.refresh_done = False
        if con.shop_config.buy_coin_item:
            if self.coin_level > 0:
                logger.info(f'MysteryShop: 神秘商人 Lv{self.coin_level}（上限 {con.shop_config.coin_max_level}）')
            else:
                logger.warning('MysteryShop: 进店读不到神秘商人等级，本任务不买金币道具')
        # ① 进店先分享一次：此刻分享的是「刷新前」的商店
        self.share(con.invite_config)

        # 自己的商店：**按设置扫完（先顶部、再一路滑到底）就花 5 勾玉刷新一次**，
        # 与「有没有买到配置里的道具」无关 —— 买到、买不到、一件没有，都要刷（用户 10-29 定死）。
        # 刷新后再分享一次 + 把刷新出来的商店再从头扫一遍。
        # 刷新前后在游戏里是两个独立商店，所以两次分享各算一次，各自对应一个店面。
        # self.shop_ok 为 False（回顶失败 / 弹窗卡死 / 下滑后离店）时不刷：
        # 画面已不可信，点刷新按钮只会把 5 勾玉浪费在错页面上。
        self.run_shop(con.shop_config)
        if self.shop_ok and self.refresh_shop(con.shop_config):
            self.share(con.invite_config)       # ② 刷新后再分享一次（对应刷新后的商店）
            self.run_shop(con.shop_config)      # 把刷新出来的新商品再扫一遍

        # 然后逐个逛好友分享的商店 —— 那边永远没有刷新选项，纯买
        while 1:
            if not self.next_one():
                break
            self.run_shop(con.shop_config)
        self.shop_reward()
        logger.info('Exit MysteryShop')
        self.back_mall()


        self.next_time(True)

    def next_one(self):
        """
        切换下一个好友的商店
        :return:
        """
        self.screenshot()
        if not self.appear(self.I_MS_NEXT):
            sleep(0.5)
            self.screenshot()
            if self.appear(self.I_MS_NEXT):
                pass
            else:
                logger.info('No next friend')
                return False

        own_page = self.appear(self.I_MS_SHARE)
        if own_page:
            timer = Timer(30).start()
            while 1:
                self.screenshot()
                if not self.appear(self.I_MS_SHARE):
                    break
                if timer.reached():
                    logger.warning('Next button is gone, stop switching friend')
                    return False
                if self.appear_then_click(self.I_MS_NEXT, interval=1):
                    timer.reset()
                    continue
            logger.info('Switch to next friend')
            return True

        present_friend = self.O_MS_FRIEND.ocr(self.device.image)
        timer = Timer(30).start()
        while 1:
            self.screenshot()
            next_friend = self.O_MS_FRIEND.ocr(self.device.image)
            if present_friend != next_friend:
                break
            if timer.reached():
                logger.warning('Next button is gone, stop switching friend')
                return False
            if self.appear_then_click(self.I_MS_NEXT, interval=2.5):
                timer.reset()
                continue

        logger.info('Switch to next friend')
        return True



    def run_shop(self, shop_config: ShopConfig = None):
        """
        购买当前商店：**先滑到顶部买一遍，再一路滑到底买一遍**（每进一个新页面都这么干）。

        实测（16416，Lv4，商店数量 6/6）整店有 11 个商品，一屏只显示 8 格：
        第一屏 樱饼/破碎的符咒/花笺/八岐大蛇鳞片/二星神乐铃/大蛇的逆鳞/吉运达摩/插画券，
        下滑后才露出第 3 行（六星式神转换符/被遗忘的绘本/御行达摩碎片）。
        所以一屏不够，必须往下扫；滑动幅度见 SCROLL_DOWN_MAX 的注释（y 轴大跨步，两次到底）。

        **进店必须先回顶**：滚动位置会被沿用下来 —— 花 5 勾玉刷新后位置不变（仍在
        第二屏），切到下一个好友的商店也是照着上一家店的位置显示（实测 23:5x 的好友页
        截图里第一行卡片被裁掉、标签只剩半截）。不从顶部开始就会漏掉第一行商品。
        回顶本身有风险（在顶部继续下拉 = 下拉关闭），所以由 _scroll_to_top() 先用
        图像判据确认「真的滚过」才滑。

        副作用：正常扫到底后把 self.shop_ok 置 True，run() 用它决定能不能去刷新。
        回顶失败 / 弹窗卡死 / 下滑后离店这三种情况保持 False —— 画面已不可信，
        此时不能点刷新按钮，否则 5 勾玉会花在错页面上。
        返回整店买到的总件数，只用于日志；**买 0 件不影响刷新**。
        """
        self.shop_ok = False
        if not self._scroll_to_top():
            logger.error('MysteryShop: 回不到商品顶部，本商店放弃')
            return 0
        bought = self._buy_screen(shop_config)              # 顶部这一屏
        # 一次下滑 = 一大步（见 ms_scroll_down 的 y 轴跨度），最多滑 SCROLL_DOWN_MAX 次。
        # 整店最多 3 行（11 件），第一屏已含 2 行，再滑 2 次必到底；
        # 画面不再变化（diff < SCROLL_MIN_DIFF）就说明到底了，提前收手，不做无用的空滑。
        for i in range(self.SCROLL_DOWN_MAX):
            if getattr(self, 'popup_stuck', False):
                # 弹窗盖在商品上时滑动与点击坐标全不可信，剩下的屏直接放弃
                logger.error('MysteryShop: 弹窗关不掉，本商店放弃（不再往下扫）')
                return bought
            if not self._scroll_down_one_page():
                break
            if not self._in_shop():
                # 滑动前还在这页，滑完不在了 => 下拉关闭之类的异常，绝不能在错页面上点道具
                logger.error('MysteryShop: 下滑后离开了商店页，本商店放弃')
                return bought
            bought += self._buy_screen(shop_config)
        self.shop_ok = True
        if bought:
            logger.info(f'MysteryShop: 本商店共买到 {bought} 件')
        else:
            logger.info('MysteryShop: 本商店没有买到配置里的道具')
        return bought

    def _can_buy_something(self, shop_config: ShopConfig) -> bool:
        """配置里是否勾了任何一项。一项都没勾时刷不刷新都没意义，别白花 5 勾玉。"""
        return any((shop_config.mystery_amulet, shop_config.black_daruma_scrap,
                    shop_config.shop_kaiko_3, shop_config.shop_kaiko_4,
                    shop_config.buy_coin_item))

    def refresh_shop(self, shop_config: ShopConfig) -> bool:
        """
        自己商店按设置扫完之后，**无条件**花 5 勾玉刷新一次（买没买到都一样）。
        返回 True 表示确实刷新了。

        只在这几种情况下不刷：
        - 本趟已经刷过一次（每天只允许刷 1 次）；
        - 配置里一项道具都没勾（刷了也没有能买的东西）；
        - 回不到商品顶部（新商品看不见，5 勾玉白花）；
        - 画面上的「5(仅1次)」刷新选项不存在（当日已刷 / 该店不可刷）。

        **刷新选项只在自己商店的第一屏**：下滑后的第二屏没有，好友页从来没有
        （用户 10-29 实测）。调用时人正站在第二屏（run_shop 刚扫到底），所以
        点刷新之前必须先用 _scroll_to_top() 回到第一屏；回不去就放弃刷新 ——
        刷新后位置不变，回不了顶新商品也看不见。

        判据全部来自真机实测（16512，23:52）：
        - 还能刷新时，点右下角刷新按钮会弹出带「确定」的确认框 —— 就是全局的
          I_UI_CONFIRM（roi 666,406,175,64），点它才真的刷新（扣 5 勾玉，商品全换）；
        - 本日已经刷新过时，点刷新按钮**完全没反应**，而「5(仅1次)」这个标签仍然挂着，
          所以标签不能当判据；「有没有弹确认框」才是唯一可靠信号。
        因此：没等到确认框 => 已刷新过 => 本趟任务不再尝试（每天只允许刷 1 次）。
        """
        if getattr(self, 'refresh_done', False):
            return False
        if not self._can_buy_something(shop_config):
            logger.info('MysteryShop: 配置里一项道具都没勾，不刷新商店')
            return False
        # 刷新按钮只在第一屏，先回顶再去找它；已在顶部则一次都不滑
        if not self._scroll_to_top():
            logger.warning('MysteryShop: 回不到商品顶部，跳过刷新')
            return False

        self.screenshot()
        if not self.appear(self.I_MS_REFRESH_5):
            logger.info('MysteryShop: 没有「5(仅1次)」刷新选项，不刷新商店')
            return False

        # 到这里才真正「尝试」过，一次任务最多试一次
        self.refresh_done = True
        logger.hr('Refresh shop', 3)
        self.click(self.C_MS_REFRESH)

        timer = Timer(self.REFRESH_CONFIRM_TIMEOUT).start()
        while 1:
            self.screenshot()
            if self.appear(self.I_UI_CONFIRM):
                break
            if timer.reached():
                logger.info('MysteryShop: 点刷新没弹确认框，说明本日已经刷新过，之后不再尝试')
                return False
            sleep(0.3)

        logger.info('MysteryShop: 花 5 勾玉刷新商店')
        self.click(self.I_UI_CONFIRM)

        # 等确认框消失（点确定后立刻关），再给商品重新加载留点时间
        timer = Timer(10).start()
        while 1:
            self.screenshot()
            if not self.appear(self.I_UI_CONFIRM) and not self.appear(self.I_UI_CANCEL):
                break
            if timer.reached():
                logger.warning('MysteryShop: 刷新确认框没关掉，放弃刷新后的商店')
                return False
            sleep(0.3)
        sleep(1.5)

        self.screenshot()
        if not self._in_shop():
            logger.error('MysteryShop: 刷新后不在商店页，放弃刷新后的商店')
            return False
        logger.info('MysteryShop: 商店已刷新')
        return True

    def _in_shop(self) -> bool:
        """
        当前是否还在神秘商店页。I_MS_NEXT（切下一个好友）在所有商店页都有，
        I_MS_SHARE 只有自己的商店有，任一命中即算在店内。
        """
        self.screenshot()
        return self.appear(self.I_MS_NEXT) or self.appear(self.I_MS_SHARE)

    def _buy_screen(self, shop_config: ShopConfig) -> int:
        """把当前这一屏能买的都买一遍，返回买到的件数。"""
        bought = 0
        if shop_config.mystery_amulet:
            while self.buy_mall_one(buy_button=self.I_MS_BLUE, buy_check=self.I_MS_CHECK_BLUE,
                                    money_ocr=self.O_MALL_RESOURCE_5, buy_money=85):
                bought += 1
        if shop_config.black_daruma_scrap:
            while self.buy_mall_one(buy_button=self.I_MS_BLACK, buy_check=self.I_MS_CHECK_BLACK,
                                    money_ocr=self.O_MALL_RESOURCE_5, buy_money=60):
                bought += 1
        if shop_config.shop_kaiko_3:
            while self.buy_mall_one(buy_button=self.I_MS_TAIKO_3, buy_check=self.I_MS_CHECK_TAIKO_3,
                                    money_ocr=self.O_MALL_RESOURCE_5, buy_money=45):
                bought += 1
        if shop_config.shop_kaiko_4:
            while self.buy_mall_one(buy_button=self.I_MS_TAIKO_4, buy_check=self.I_MS_CHECK_TAIKO_4,
                                    money_ocr=self.O_MALL_RESOURCE_5, buy_money=80):
                bought += 1
        # 金币道具（提升神秘商人等级），放在勾玉道具之后，避免挤占上面几项的点击次数
        if shop_config.buy_coin_item:
            bought += self.buy_coin_items(shop_config)
        return bought

    def _at_top(self) -> bool:
        """
        商品列表是否已经在最顶部（一次都没往下滚过）。

        判据见 TOP_PROBE_BOX 的注释：看标题栏正下方那条窄带里有没有被裁切的
        「剩余购买次数」标签笔画。这个方法只是「读」，不做任何点击或滑动。
        """
        self.screenshot()
        image = self.device.image
        gray = image[:, :, 0] * 0.299 + image[:, :, 1] * 0.587 + image[:, :, 2] * 0.114
        x, y, w, h = self.TOP_PROBE_BOX
        ratio = float((gray[y:y + h, x:x + w] > self.TOP_PROBE_LEVEL).mean())
        at_top = ratio < self.TOP_PROBE_MAX
        logger.info(f'MysteryShop: 顶部检测亮像素 {ratio * 100:.2f}% -> '
                    f'{"已在顶部" if at_top else "滚动过，需要回顶"}')
        return at_top

    def _scroll_to_top(self) -> bool:
        """
        把商品列表拉回最顶部，返回是否确认已在顶部。

        **这里绝不能用「滑一次试试」的做法**：这个界面在已经到顶时继续下拉等于
        下拉关闭，会一级级退出（商店 -> 商城 -> 庭院，23:17 真机踩过）。
        所以每次上滑之前都先用 _at_top() 确认真的滚过，读数说在顶部就一次都不滑。
        """
        for i in range(self.SCROLL_TOP_MAX):
            if self._at_top():
                if i:
                    logger.info(f'MysteryShop: 上滑 {i} 次回到商品顶部')
                return True
            logger.info(f'MysteryShop: 商品列表不在顶部，上滑回顶（第 {i + 1} 次）')
            self._swipe_and_diff(self.S_MS_SCROLL_UP)
            if not self._in_shop():
                # 上滑把商店关掉过（顶部继续下拉），此时后面所有点击坐标都不可信
                logger.error('MysteryShop: 上滑后离开了商店页，停止本商店')
                return False
        if self._at_top():
            logger.info(f'MysteryShop: 上滑 {self.SCROLL_TOP_MAX} 次回到商品顶部')
            return True
        logger.warning(f'MysteryShop: 上滑 {self.SCROLL_TOP_MAX} 次仍没回到顶部')
        return False

    def _scroll_down_one_page(self) -> bool:
        """
        下滑一大步（见 ms_scroll_down 的 roi：y 轴跨度约 390~470 像素）。
        画面几乎没变化就说明已经到底（或本来只有一屏），返回 False。

        实测阈值标定（16416）：有效滑动画面平均像素差 21.6，到底后再滑只有 0.3~0.6，
        差 40 倍，取 5.0 两边都不会误判。
        """
        if self._swipe_and_diff(self.S_MS_SCROLL_DOWN) < self.SCROLL_MIN_DIFF:
            logger.info('MysteryShop: 已经到底，没有第二屏')
            return False
        return True

    def _swipe_and_diff(self, swipe) -> float:
        """执行一次滑动，返回滑动前后画面的平均像素差（0 表示画面没动）。"""
        self.screenshot()
        before = self.device.image.copy()
        self.swipe(swipe)
        sleep(1.5)
        self.screenshot()
        diff = float(np.abs(before.astype(np.int16) - self.device.image.astype(np.int16)).mean())
        logger.info(f'MysteryShop: {swipe.name} 后画面变化 {diff:.1f}')
        return diff

    def _shop_level(self, retry: int = 3) -> int:
        """
        读神秘商人等级。Digit 模式读不到时返回 0（见 module/ocr/sub_ocr.py:133），
        而等级最低是 1，所以 0 一律当「本次读取失败」，不能当成「等级 0」。
        """
        for _ in range(retry):
            self.screenshot()
            level = self.O_MS_LEVEL.ocr(self.device.image, log=False)
            if isinstance(level, int) and level > 0:
                return level
            sleep(0.5)
        return 0

    def _left_times(self, index: int, retry: int = 3):
        """
        读第 index 格的「剩余购买次数」。读不到返回 None —— 绝不返回 0，
        否则会和「已售罄」混为一谈（.codebuddy/rules/11-task-code-patterns.md 第 9 条）。
        """
        ocr = getattr(self, f'O_MS_LEFT_{index}')
        for _ in range(retry):
            self.screenshot()
            text = ocr.ocr(self.device.image, log=False)
            if isinstance(text, str):
                match = re.search(r'(\d+)', text)
                if match:
                    return int(match.group(1))
            sleep(0.5)
        return None

    def buy_coin_items(self, shop_config: ShopConfig = None):
        """
        买金币标价的道具，用来把神秘商人等级顶上去。

        等级开关由 run() 进店时读的那一次决定（自己的商店页），这里不再重读：
        好友商店里买金币道具同样能提升等级，所以这个值对每个商店都有效；
        而好友页显示的等级是分享者的，读了只会干扰判断。
        """
        max_level = shop_config.coin_max_level
        level = getattr(self, 'coin_level', 0)
        if level <= 0:
            logger.debug('MysteryShop: 没有可用的神秘商人等级，跳过金币道具')
            return 0
        if level >= max_level:
            logger.info(f'MysteryShop: 神秘商人 Lv{level} 已达上限 {max_level}，本任务不买金币道具')
            return 0

        logger.hr('Buy coin items', 3)
        # 只扫当前这一屏：两屏的遍历由 run_shop() 负责，这里不自己滑动
        coins = self._coin_slots()
        if not coins:
            logger.info('MysteryShop: 当前屏没有金币道具，跳过')
            return 0

        bought = 0
        self.popup_stuck = False
        self.coin_shortage = False
        for index in coins:
            left = self._left_times(index)
            if left is None:
                logger.warning(f'MysteryShop: 第{index}格剩余次数读不到，跳过')
                continue
            if left <= 0:
                logger.info(f'MysteryShop: 第{index}格已售罄，跳过')
                continue
            logger.info(f'MysteryShop: 第{index}格为金币道具，剩余 {left} 次')
            if self._buy_coin_one(index):
                bought += 1
            # 同一 control_name 连点会被判 GameTooManyClickError（≥10 次），每格结束清一次
            self.device.click_record_clear()
            if self.popup_stuck:
                # 弹窗关不掉时后面每一格的坐标都会落在弹窗上，及时收手
                logger.warning('MysteryShop: 弹窗关不掉，本商店剩下的格子不再买')
                break
            if self.coin_shortage:
                # 金币不足：这个号换不出更多金币，后面每一格都会卡同一个窗口，直接结束
                logger.warning('MysteryShop: 金币不足，本商店剩下的金币格不再尝试')
                break

        logger.info(f'MysteryShop: 本屏金币道具购买 {bought} 件（进店时 Lv{level}）')
        return bought

    def _coin_slots(self) -> list:
        """
        截一次图，把 8 格全判一遍，返回「是金币道具」的格子编号列表（可能为空）。
        比逐格判断省 7 次截图。
        """
        self.screenshot()
        image = self.device.image
        return [i for i in range(1, 9) if self._is_coin_in(image, i)]

    def _is_coin_in(self, image, index: int) -> bool:
        """
        在给定截图上判断第 index 格是不是金币道具，只看价格牌左侧的金币图标。

        模板分区分度足够：实测金币格 0.975~1.000，勾玉格 0.27~0.40（16416 Lv4 与
        16512 Lv1 两套商店都成立）。旧版还叠了一层「价格牌红色占比 < 5%」的判据，
        结果把 16512 第 1 格（勋章 18 万金币，价格牌偏橙、红色 7.0%）误杀成勾玉道具，
        导致金币格一个都识别不到 —— 已删除，别再叠颜色判据。
        """
        return getattr(self, f'I_MS_COIN_{index}').match(image)

    def _buy_coin_one(self, index: int) -> bool:
        """
        点开第 index 格并买下 1 个。成功判据是「出现了获得奖励」。
        没出现就关弹窗，绝不在同一格反复点：
        - 出现「购买金币」（金币不足）窗口 → 点右上角 X 关掉，并中止本商店剩余金币格；
        - 其他情况 → 点弹窗左侧空白关掉。
        """
        click = getattr(self, f'C_MS_ITEM_{index}')
        self.click(click)
        sleep(1)

        # 1) 等购买弹窗出现（金币购买按钮 / 限购确认框，任一出现即认为弹窗已开）
        timer = Timer(8).start()
        while 1:
            self.screenshot()
            if self.appear(self.I_MS_COIN_BUY) or self.appear(self.I_UI_CONFIRM_SAMLL):
                break
            if timer.reached():
                logger.warning(f'MysteryShop: 第{index}格购买弹窗未出现，跳过')
                return False
            sleep(0.5)

        # 2) 有确认框先点确认，否则点购买按钮，直到奖励出现
        # 正常点一次购买，1~2 秒就出获得奖励；8 秒够用，不必等满 12 秒白耗时间
        timer = Timer(8).start()
        got_reward = False
        while 1:
            self.screenshot()
            if self.ui_reward_appear_click():
                got_reward = True
                break
            # 金币不足会弹出「购买金币」窗口。这个窗口点左侧空白关不掉，必须点右上角 X；
            # 而且它的出现说明这个号的勾玉换不成金币，继续买剩下的格子毫无意义，直接收工。
            if self.appear(self.I_MS_NO_COIN):
                logger.warning(f'MysteryShop: 第{index}格金币不足，关闭「购买金币」窗口，本次金币购买结束')
                if not self.close_coin_popup():
                    # X 都点不掉 => 窗口还盖在商品上，后续任何点击都不可信
                    self.popup_stuck = True
                self.coin_shortage = True
                return False
            if self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=1):
                timer.reset()
                continue
            if self.appear_then_click(self.I_MS_COIN_BUY, interval=2):
                timer.reset()
                continue
            if timer.reached():
                break
            sleep(0.5)

        # 3) 没出奖励（金币不足 / 已达上限等）→ 点左侧空白关闭弹窗
        if not got_reward:
            logger.warning(f'MysteryShop: 第{index}格未买到（未出现获得奖励）')
            if not self.close_popup():
                self.popup_stuck = True
        return got_reward

    def close_coin_popup(self, retry: int = 3) -> bool:
        """
        关闭「购买金币」（金币不足）弹窗 —— 只能点右上角 X（C_MS_CLOSE，938,138,41,35）。

        实测（16512 Lv1 商店）：点弹窗左侧空白**关不掉**这个窗口，弹窗照旧盖在商品上，
        随后每一格的模板匹配和点击坐标全错位，所以这里必须点 X 并确认窗口真的消失。
        判据用 I_MS_NO_COIN（窗口标题「购买金币」）是否消失。
        """
        for i in range(retry):
            self.click(self.C_MS_CLOSE)
            sleep(1)
            self.screenshot()
            if not self.appear(self.I_MS_NO_COIN):
                logger.info('MysteryShop: 「购买金币」窗口已关闭')
                return True
            logger.warning(f'MysteryShop: 「购买金币」窗口还在，再点一次 X（第 {i + 1} 次）')
        logger.error('MysteryShop: 「购买金币」窗口关不掉')
        return False

    def close_popup(self, retry: int = 3) -> bool:
        """
        点弹窗左侧空白把它关掉，并确认真的关上了。

        判据：商店页本来就没有 I_MS_COIN_BUY / I_UI_CONFIRM_SAMLL，两个都消失即弹窗已关。
        不确认的话，下一格的道具模板匹配会跑在弹窗画面上，随后的点击坐标也全错位。
        """
        # 「购买金币」窗口点空白关不掉，只能点右上角 X，交给 close_coin_popup()
        self.screenshot()
        if self.appear(self.I_MS_NO_COIN):
            return self.close_coin_popup()
        for i in range(retry):
            self.click(self.C_MS_BLANK)
            sleep(1)
            self.screenshot()
            if not self.appear(self.I_MS_COIN_BUY) and not self.appear(self.I_UI_CONFIRM_SAMLL):
                return True
            logger.warning(f'MysteryShop: 弹窗还在，再点一次空白（第 {i + 1} 次）')
        logger.error('MysteryShop: 弹窗关不掉')
        return False

    def share(self, invite_config: InviteConfig = None):
        logger.hr('Share', 3)
        if len(invite_config.friend_list_v) == 0:
            logger.info('Share is disabled')
            return
        self.ui_click(self.I_MS_SHARE, self.I_INVITE_ENSURE)
        self.invite_friends(invite_config, False, self.I_INVITE_ENSURE)

    def shop_reward(self):
        logger.info('Shop reward')
        self.screenshot()
        number = self.O_MS_RECORDS.ocr(self.device.image)
        if not isinstance(number, int):
            logger.warning('No shop reward')
            return
        logger.info(f'Shop reward {number}')
        if number >= 3:
            self.ui_get_reward(self.I_MS_REWARD_3)
            sleep(0.5)
        if number >= 5:
            self.ui_get_reward(self.I_MS_REWARD_5)
            sleep(0.5)
        if number >= 10:
            self.ui_get_reward(self.I_MS_REWARD_10)
            sleep(0.5)
        logger.info('Shop reward done')

    def next_time(self, success: bool = True):
        """
        重写：开放日（周三/周六）支持当天跑两趟。

        两个时刻都来自配置：
          time_of_mystery    第一趟，默认 00:00:00（= 开放日零点跑，与上游的实际行为一致）
          time_of_mystery_2  同一开放日的第二趟，默认 00:00:00（= 不启用，只跑一趟）

        任务结束时的判定：今天这个开放日的第二趟还没到点 → 排到今天第二趟；
        否则排到下一个开放日的第一趟。跑完第二趟自然就落到后者。

        上游的 success 分支这里不用了：它把 target_time 从 time 覆盖成 timedelta
        再去和 time(0,0,0) 比较，那个「00:00:00 表示不使用」的判断恒为 False，
        实际一直是按星期排到下一个开放日，本实现保持同样的结果。
        """
        con = self.config.mystery_shop.shop_config
        first: time = con.time_of_mystery
        second: time = con.time_of_mystery_2
        zero = time(hour=0, minute=0, second=0)
        now = datetime.now()

        if second != zero and first != zero and second <= first:
            logger.warning('MysteryShop: 第二次运行时间不晚于第一次，第二趟永远不会触发')

        # ① 今天还有第二趟没到点 → 排到今天第二趟
        if second != zero and now.weekday() in self.OPEN_WEEKDAYS:
            today_second = datetime.combine(now.date(), second)
            if now < today_second:
                logger.info(f'MysteryShop: 今天还有第二趟 {second.strftime("%H:%M:%S")}')
                self.set_next_run(task='MysteryShop', target=today_second)
                raise TaskEnd('MysteryShop')

        # ② 排到下一个开放日的第一趟
        target = self._next_open_datetime(now, first)
        logger.info(f'MysteryShop: 下次运行 {target.strftime("%Y-%m-%d %H:%M:%S")}')
        self.set_next_run(task='MysteryShop', target=target)
        raise TaskEnd('MysteryShop')

    def _next_open_datetime(self, after: datetime, target_time: time) -> datetime:
        """严格晚于 after 的、最近的开放日（周三/周六）的 target_time。"""
        for delta in range(0, 8):
            day = (after + timedelta(days=delta)).date()
            if day.weekday() in self.OPEN_WEEKDAYS:
                candidate = datetime.combine(day, target_time)
                if candidate > after:
                    return candidate
        # range(0, 8) 覆盖完整一周，必含一个开放日，正常走不到这里
        return datetime.combine((after + timedelta(days=7)).date(), target_time)


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.screenshot()

    # t.run_shop(t.config.mystery_shop.shop_config)
    t.run()



