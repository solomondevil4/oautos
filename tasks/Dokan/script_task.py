# This Python file uses the following encoding: utf-8
# @brief    Ryou Dokan Toppa (阴阳竂道馆突破功能)
# @author   AzurTian
# @note     draft version without full test
import time

import re
from datetime import timedelta
from time import sleep

from future.backports.datetime import datetime

from module.base.timer import Timer
from module.exception import TaskEnd
from module.logger import logger
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Component.GeneralBattle.general_battle import GeneralBattle, ExitMatcher, BattleBehaviorScope, BattleContext, \
    BattleAction
from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.Component.config_base import Time
from tasks.Dokan.assets import DokanAssets
from tasks.Dokan.config import Dokan
import tasks.Dokan.page as pages
from tasks.GameUi.game_ui import GameUi
from tasks.GlobalGame.assets import GlobalGameAssets


def position_offset(src, offset: tuple):
    return src[0] + offset[0], src[1] + offset[1], src[2] + offset[2], src[3] + offset[3]


class DokanFinishedError(Exception):
    pass


class DokanNotStartedError(Exception):
    pass


class DokanNoMoneyError(Exception):
    """点「挑战」并「确定」后没有进入道馆 —— 判定为寮资金/挑战次数不足"""
    pass


class ScriptTask(GameUi, SwitchSoul, GeneralBattle, DokanAssets):
    attack_priority_selected: bool = False
    switch_member_soul_done: bool = False
    switch_owner_soul_done: bool = False
    dokan_owner_battle: bool = False  # 馆主战标识(会在多个可识别到馆主战的位置进行设置)
    first_master_killed: bool = False  # 一阵是否被击败(只有识别到二阵这个标识才会设置为true)
    found_dokan_cnt: int = 0  # 已经寻找道馆的次数
    conf: Dokan = None

    def _register_custom_pages(self) -> None:
        page_battle_result = self.navigator.resolve_page(pages.page_battle_result)
        if page_battle_result is None:
            return
        page_battle_result.recognizer = pages.any_of(self.I_RYOU_DOKAN_TOPPA_RANK, self.I_RYOU_DOKAN_WIN,
                                                     page_battle_result.recognizer)
        page_battle_result.priority = 75
        page_reward = self.navigator.resolve_page(pages.page_reward)
        if page_reward is None:
            return
        page_reward.recognizer = pages.any_of(self.I_RYOU_DOKAN_BATTLE_OVER, page_reward.recognizer)
        page_reward.priority = 75

    def _exit_matcher(self) -> ExitMatcher | None:
        return pages.any_of(self.I_RYOU_DOKAN_CENTER_TOP, self.I_RYOU_DOKAN_REMAIN_ATTACK_COUNT_DONE)

    def _get_battle_behavior_scopes(self, config: GeneralBattleConfig, battle_key: str) -> dict[str, BattleBehaviorScope]:
        scopes = super()._get_battle_behavior_scopes(config, battle_key)
        if battle_key == 'dokan_owner':
            scopes['green'] = BattleBehaviorScope.ROUND
        return scopes

    def _handle_in_battle(self, context: BattleContext, config: GeneralBattleConfig) -> BattleAction:
        count = self.conf.attack_count_config.attack_dokan_master_count()
        if self.appear(self.I_RYOU_DOKAN_BATTLE_MASTER_FIRST) or self.appear(self.I_RYOU_DOKAN_BATTLE_MASTER_SECOND):
            self.dokan_owner_battle = True  # 防止直接在战斗界面识别, 因此在这继续更新一次馆主战标识
        if self.dokan_owner_battle:
            if count <= 0:  # 不让打直接退
                return BattleAction.QUICK_EXIT
            if self.appear(self.I_RYOU_DOKAN_BATTLE_MASTER_SECOND):
                self.first_master_killed = True  # 别到二阵则说明一阵被击败了
                if count <= 1:  # 2阵但只允许打一次
                    return BattleAction.QUICK_EXIT
        return super()._handle_in_battle(context, config)

    def exit_battle(self, skip_first: bool = False) -> bool:
        """
            尝试退出战斗界面
            1. 普通战斗结束,连续点击即可退出
                a. 存在战斗奖励
                b. 战斗失败,
            2. 在战斗界面,但是战斗还未开始(右下角有准备按钮)
                需要点击左上角退出按钮,然后点击确定
            3. 馆主战斗过程中,寮友打败馆主,弹出框体,可点击空白区域取消该框体.
            综上,点击左上角退出按钮区域
        """
        logger.info("try to quit battle...")
        while True:
            self.screenshot()
            if self.appear(self.I_RYOU_DOKAN_CENTER_TOP):
                return True
            if self.appear(self.I_RYOU_DOKAN_QUIT_BATTLE_ENSURE):
                self.ui_click_until_disappear(self.I_RYOU_DOKAN_QUIT_BATTLE_ENSURE)
                return True
            self.screenshot()
            if not self.appear(self.I_RYOU_DOKAN_CENTER_TOP):
                self.click(self.C_DOKAN_BATTLE_QUIT_AREA, interval=3)
                continue
            self.wait_until_appear(self.I_RYOU_DOKAN_CENTER_TOP, True, 3)
        return False

    def before_run(self):
        self.dokan_owner_battle = False
        self.first_master_killed = False
        self.found_dokan_cnt = 0
        pages.page_dokan_rank = self.navigator.add_page(pages.Page(self.I_RYOU_DOKAN_TOPPA_RANK, priority=75, register=False))
        pages.page_dokan_rank.connect(pages.page_dokan, pages.random_click, key="page_dokan_rank->page_dokan")

    def run(self):
        self.before_run()
        self.conf = self.config.model.dokan
        if self.conf.dokan_config.monday_to_thursday and datetime.now().weekday() >= 4:
            logger.warning("weekend, exit")
            self.next_run(True)
            raise TaskEnd
        # 初始化相关动态参数,从配置文件读取相关记录,如果没有当天的记录则设置为默认值
        self.conf.attack_count_config.init_attack_count(callback=self.config.save)
        unknown_page_timer = Timer(10)
        self.goto_page(pages.page_dokan_map)
        try:
            while True:
                self.screenshot()
                current_page = self.get_current_page()
                match current_page:
                    case None:
                        self.device.click_record_clear()
                        self.device.stuck_record_clear()
                        time.sleep(0.5)
                    case pages.page_dokan_map:
                        self.run_on_dokan_map()
                    case pages.page_dokan:
                        self.run_on_dokan()
                    case pages.page_battle_prepare | pages.page_battle:
                        self.run_on_battle()
                    case pages.page_battle_result:
                        self.click(pages.random_click(ltrb=(False, False, True, False)), interval=1.5)
                    case _:
                        if not unknown_page_timer.started():
                            unknown_page_timer.start()
                        if unknown_page_timer.started() and unknown_page_timer.reached():  # 10秒都是非道馆界面, 则尝试回到道馆
                            unknown_page_timer = Timer(10)
                            self.goto_page(pages.page_dokan)
        except DokanFinishedError:
            is_dokan_activated = True
        except DokanNoMoneyError:
            # 寮资金/挑战次数不足：点「挑战」+「确定」也没能进道馆。
            # 这种情况 2 分钟后再试还是白点，所以直接把今天判完，明天再来。
            logger.warning('Dokan: 寮资金不足，本次道馆任务结束，今天不再重试')
            self.goto_page(pages.page_main)
            self.next_run(skip_today=True)
            raise TaskEnd
        except DokanNotStartedError:
            is_dokan_activated = False
        self.goto_page(pages.page_main)
        self.next_run(skip_today=False, is_dokan_activated=is_dokan_activated)
        raise TaskEnd

    def run_on_dokan(self):
        """道馆页面逻辑处理"""
        self.prepare_appear_cache([
            self.I_RYOU_DOKAN_GATHERING,
            self.I_RYOU_DOKAN_MASTER_BATTLE,
            self.I_RYOU_DOKAN_START_CHALLENGE,
            self.I_RYOU_DOKAN_CD,
            self.I_RYOU_DOKAN_ABANDONED_TOPPA_ABANDONED,
            self.I_RYOU_DOKAN_FAILED_VOTE_KEEP_BOUNTY,
            self.I_RYOU_DOKAN_FAILED_VOTE_BATTLE_AGAIN,
            self.I_RYOU_DOKAN_TODAY_ATTACK_COUNT,
            self.I_RYOU_DOKAN_REMAIN_ATTACK_COUNT_DONE,
            self.I_DOKAN_BOSS_WAITING
        ])
        self.device.stuck_record_clear()
        if self.appear(self.I_RYOU_DOKAN_GATHERING):  # 正在集结
            logger.debug(f"Dokan is gathering...")
            self.switch_priority()  # 选择优先级
            self.switch_soul_in_dokan('member')  # 切换馆员御魂
            self.device.click_record_clear()
            return
        if self.appear(self.I_DOKAN_BOSS_WAITING) or self.appear(self.I_RYOU_DOKAN_MASTER_BATTLE):  # 馆主战标识
            self.dokan_owner_battle = True
        if not self.appear(self.I_DOKAN_BOSS_WAITING) and self.appear(self.I_RYOU_DOKAN_START_CHALLENGE):  # 可挑战
            if self.dokan_owner_battle:  # 馆主战
                count = self.conf.attack_count_config.attack_dokan_master_count()
                if (count - (1 if self.first_master_killed else 0)) > 0:
                    logger.info("Start owner battle")
                    self.switch_soul_in_dokan('owner')
                    if self.click_until_in_battle():
                        self.run_general_battle(self.conf.dokan_owner_battle_conf, battle_key='dokan_owner')
                # 有权限且当前道馆突破 不再打馆主,直接放弃突破
                elif self.conf.dokan_config.try_start_dokan:
                    self.abandoned_toppa()
            else:  # 馆员战
                self.switch_soul_in_dokan('member')  # 防止进来晚了没有识别到集结导致错过切换御魂
                if self.click_until_in_battle():
                    self.run_general_battle(self.conf.dokan_member_battle_conf, battle_key='dokan_member')
            return
        if not self.appear(self.I_DOKAN_BOSS_WAITING) and self.appear(self.I_RYOU_DOKAN_CD):  # 可观战
            self.device.click_record_clear()
            self.start_cheering()
            return
        if self.appear_then_click(self.I_RYOU_DOKAN_ABANDONED_TOPPA_ABANDONED, interval=2):  # 放弃突破
            return
        if self.appear(self.I_RYOU_DOKAN_FAILED_VOTE_KEEP_BOUNTY, interval=2):  # 投票
            # 每天打一次的直接 选择 保留赏金,
            # 打两次的,第一次选择 继续挑战,第二次没有选项
            if self.conf.attack_count_config.daily_attack_count == 2:
                if self.appear(self.I_RYOU_DOKAN_FAILED_VOTE_BATTLE_AGAIN):
                    self.ui_click_until_disappear(self.I_RYOU_DOKAN_FAILED_VOTE_BATTLE_AGAIN)
            else:
                if self.appear(self.I_RYOU_DOKAN_FAILED_VOTE_KEEP_BOUNTY):
                    logger.info("Dokan challenge failed: vote for keep the awards")
                    self.ui_click_until_disappear(self.I_RYOU_DOKAN_FAILED_VOTE_KEEP_BOUNTY)
            return
        if self.appear(self.I_RYOU_DOKAN_TODAY_ATTACK_COUNT) or self.appear(self.I_RYOU_DOKAN_REMAIN_ATTACK_COUNT_DONE):
            logger.info("Dokan challenge finished, exit Dokan")
            self.update_remain_attack_count()  # 更新配置
            raise DokanFinishedError

    def click_until_in_battle(self) -> bool:
        """点击挑战直到进入战斗"""
        timeout_timer = Timer(5).start()
        while True:
            self.screenshot()
            if self.is_in_battle(False):
                return True
            if timeout_timer.reached():
                break
            self.appear_then_click(self.I_RYOU_DOKAN_START_CHALLENGE, interval=2)
        return False

    def run_on_dokan_map(self):
        """道馆地图页面逻辑处理"""
        # NOTE FINDING_DOKAN 这张素材其实是地图右侧的「推荐」竖排标签，不是「道馆未开启」的提示。
        # 实测（2026-10-29）：道馆已建立（16512，罗生门 4）命中 0.9424，道馆未建立（16384）也是 0.9424，
        # 两者完全一样 ⇒ 它只能当「当前就在道馆地图页」用，判不出道馆建没建。
        # 道馆到底建没建，改由 creat_dokan() 用「点完有没有弹确认框」来判断。
        # 副作用：下面 FOUND_DOKAN（道馆突破集结中）那个分支因此永远走不到，见报告。
        if self.appear(self.I_RYOU_DOKAN_FINDING_DOKAN):  # 当前在道馆地图页
            try_start_dokan = self.config.dokan.dokan_config.try_start_dokan
            if not try_start_dokan or self.found_dokan_cnt > 0:  # 未设置开启道馆/已经找过道馆但是没进去则退出
                raise DokanNotStartedError
            if self.update_remain_attack_count() <= 0:  # 可挑战次数为<=0,当作道馆成功完成
                raise DokanFinishedError
            # 只要配置里打开「尝试开启道馆」(try_start_dokan)，就点一次建立道馆。
            # 这里以前硬编码 `weekday() == 0`（只在周一建立），周中被攻破后整周都不会重建，
            # 实测 10-26（周一）与 10-29（周四）的表现完全对不上，故改为不限制星期几。
            # 道馆到底建没建，由 creat_dokan() 用「点完有没有弹确认框」来判断。
            self.creat_dokan()
            # 寻找合适道馆,找不到直接退出
            if not self.find_dokan(self.config.dokan.dokan_config.find_dokan_score):
                raise DokanNotStartedError
            # 寻找到道馆后等一会页面刷新
            self.wait_until_appear(self.I_RYOU_DOKAN_CENTER_TOP, True, 5)
            return
        if self.appear(self.I_RYOU_DOKAN_FOUND_DOKAN):  # 道馆已开启
            self.goto_page(pages.page_dokan)  # 跳转到道馆

    def run_on_battle(self):
        """处理战斗页面逻辑(正常来说是一定不会进入该逻辑的)"""
        if self.appear(self.I_RYOU_DOKAN_BATTLE_MASTER_FIRST) or \
                self.appear(self.I_RYOU_DOKAN_BATTLE_MASTER_SECOND):
            self.dokan_owner_battle = True
            self.first_master_killed = self.appear(self.I_RYOU_DOKAN_BATTLE_MASTER_SECOND)
            self.run_general_battle(self.conf.dokan_owner_battle_conf, battle_key='dokan_owner')
            return
        self.run_general_battle(self.conf.dokan_member_battle_conf, battle_key='dokan_member')

    def switch_priority(self):
        """选择优先级"""
        if self.attack_priority_selected:
            return
        self.goto_page(pages.page_dokan_priority)
        self.goto_page(pages.page_dokan)
        self.attack_priority_selected = True

    def find_dokan(self, score=4.6):
        """
        寻找符合条件的道馆进行挑战。
    
        参数:
        score (float): 赏金与人数比值的阈值，默认为4.6。
    
        返回:
        bool: 是否找到了符合条件的道馆并进行挑战。
        """
        self.found_dokan_cnt += 1
        # 刷新按钮点击次数
        num_fresh = 0
        # 备份一些重要的ROI区域，以便在循环中恢复
        backup = {'i_point_bounty': self.I_RIGHTPAD_POINT_BOUNTY.roi_back,
                  # 'o_dokan_rightpad_bounty':self.O_DOKAN_RIGHTPAD_BOUNTY.roi,
                  'i_point_people_num': self.I_CENTER_POINT_PEOPLE_NUMBER.roi_back}

        def restore_roi():
            self.I_RIGHTPAD_POINT_BOUNTY.roi_back = backup['i_point_bounty']
            self.I_CENTER_POINT_PEOPLE_NUMBER.roi_back = backup['i_point_people_num']

        def find_challengeable(ignore_score=False):
            """
                查找当前列表状态(一般为4个)中符合条件的道馆,并点击使其显示挑战按钮
            @param ignore_score: 是否忽略道馆系数限制, - True:   那么选择当前列表状态系数最低的那个,点击显示挑战按钮
                                                   - False:  如果存在系数符合条件的,点击并显示挑战按钮
                                                            如果全部不符合条件,不进行任何操作,返回时,不显示挑战按钮
            @type ignore_score: float
            @return:
            @rtype:
            """
            restore_roi()
            self.screenshot()
            bounty_list = self.find_all_element(self.I_RIGHTPAD_POINT_BOUNTY, (0, 0, 0, 50))
            logger.info(f'find elements list:{bounty_list}')
            # 默认最小分数
            min_score = 10
            idx_selected = -1
            for idx, item in enumerate(bounty_list):
                self.device.click_record_clear()
                logger.info(f"------start no.{idx} =={item}-----------")
                # 点击使挑战按钮消失的区域(C_DOKAN_CANCEL_SELECT_DOKAN), 点击可能点击到其他寮,
                # 因此需要在此处多点几次,直到挑战按钮消失,
                # 又因为出现挑战按钮动画时长较长,因此需要耗时
                self.screenshot()
                while self.appear(self.I_CENTER_CHALLENGE):
                    self.click(self.C_DOKAN_CANCEL_SELECT_DOKAN, interval=1.5)
                    self.wait_animate_stable(self.C_DOKAN_CANCEL_SELECT_DOKAN_CHECK_ANIMATE, interval=0.5, timeout=1.5)

                # 获取赏金金额
                self.O_DOKAN_RIGHTPAD_BOUNTY.roi = position_offset(item, (0, 0, 100, 0))
                bounty = self.O_DOKAN_RIGHTPAD_BOUNTY.ocr(self.device.image)
                tmp = re.search(r'(\d+)', bounty)
                if not tmp:
                    logger.warning(f"can't find bounty,item = {item},ocr bounty={bounty}")
                    continue
                bounty = float(tmp.group())
                # 扩大搜索区域,防止找不到
                self.I_RIGHTPAD_POINT_BOUNTY.roi_back = position_offset(item, (-10, -10, 20, 20))
                # Note: 道馆不可挑战时(被别的寮打了),8秒后跳过
                if not self.ui_click_until_appear_or_timeout(self.I_RIGHTPAD_POINT_BOUNTY, self.I_CENTER_CHALLENGE,
                                                             interval=1.5, timeout=8):
                    logger.info(f"can't find challenge button,idx={idx} item={item}")
                    # 道馆不可挑战,挑战按钮不会弹出 ,直接进行下一个
                    continue
                # 获取防守人数
                self.screenshot()
                if not self.appear(self.I_CENTER_POINT_PEOPLE_NUMBER):
                    logger.warning(f"can't find point people number image, item={item}")
                    continue
                self.O_DOKAN_CENTER_PEOPLE_NUMBER.roi = position_offset(
                    self.I_CENTER_POINT_PEOPLE_NUMBER.roi_front,
                    (0, 0, 0, 30))
                p_num = self.O_DOKAN_CENTER_PEOPLE_NUMBER.detect_text(self.device.image)
                tmp = re.search(r"(\d+)", p_num)
                if not tmp:
                    logger.warning(f"can't find people number in ocr result,item={item}, p_num={p_num}")
                    continue
                p_num = float(tmp.group())
                logger.info(f"bounty:{bounty},people_num:{p_num},score:{bounty / p_num}")
                item_score = bounty / p_num
                if item_score < min_score:
                    min_score = item_score
                    idx_selected = idx
                # 大于系数 或者 系数过小(文字识别错误导致)
                if item_score > score or item_score < 1.5:
                    logger.info("click to making challenge disappear")
                    continue
                if p_num < self.config.dokan.dokan_config.min_people_num:
                    logger.info("people num too small")
                    continue
                if bounty < self.config.dokan.dokan_config.min_bounty:
                    logger.info("bounty too small")
                    continue
                # 馆主不是修习等级的
                if not self.appear(self.I_CENTER_GUANZHU_XIUXI):
                    continue
                logger.info(f"find_dokan: bounty:{bounty},people_num:{p_num},score:{bounty / p_num}")
                return True
            # 在所有列表中都没有符合的,且忽略系数限制,那么就选择最低分数的那个,点击显示挑战按钮
            if ignore_score:
                x, y, w, h = bounty_list[idx_selected]
                while 1:
                    self.screenshot()
                    if self.appear(self.I_CENTER_CHALLENGE):
                        return True
                    self.device.click(x, y)
                    sleep(0.5)
            return False

        while num_fresh < self.config.dokan.dokan_config.find_dokan_refresh_count:
            for i in range(3):
                sleep(3)
                if find_challengeable():
                    logger.info("find challengeable dokan")
                    enter_result = self.challenge_and_enter_dokan()
                    if enter_result is None:
                        # 这个道馆点不动（刚被打穿/网络卡）是偶发，不扣次数、也不结束今天，
                        # 跳出本轮扫描去刷新列表，换一批道馆再找。
                        logger.warning('Dokan: 该道馆点「挑战」无反应，刷新列表后换一个')
                        break
                    if not enter_result:
                        # 没进道馆 = 寮资金/次数不足：不扣次数，点黄箭头退出，交给上层结束本次任务
                        restore_roi()
                        self.back_out()
                        raise DokanNoMoneyError
                    # 只有确认真的进了道馆，才更新可挑战次数
                    self.config.dokan.attack_count_config.del_attack_count(1, self.config.save)
                    # 恢复初始位置信息,防止下次使用出错
                    restore_roi()
                    return True
                # 滑动道馆列表
                self.swipe(self.S_DOKAN_LIST_UP)
            # 恢复初始位置信息,防止下次使用出错
            restore_roi()
            logger.info("=========refresh dokan list=========")
            self.refresh_dokan_list()
            num_fresh += 1
        # 刷新次数用完,仍未找到符合条件的道馆,选择当前列表(约4个)中系数最低的
        if find_challengeable(ignore_score=True):
            logger.warning("can't find challengeable dokan,select random one")
            enter_result = self.challenge_and_enter_dokan()
            if enter_result is None:
                # 兜底选中的道馆也点不动，说明这批列表整体不可挑战，交给上层重试
                logger.warning('Dokan: 兜底选中的道馆点「挑战」也无反应，放弃本次寻找')
                restore_roi()
                return False
            if not enter_result:
                restore_roi()
                self.back_out()
                raise DokanNoMoneyError
            # 只有确认真的进了道馆，才更新可挑战次数
            self.config.dokan.attack_count_config.del_attack_count(1, self.config.save)
            return True
        return False

    def creat_dokan(self) -> bool:
        """
        尝试建立道馆：只走「点建立道馆 -> 看有没有确认框」这一条路，按游戏反应分三种结局。
        调用前提：配置里 try_start_dokan 已打开（由 run_on_dokan_map 把关）。

        判据全部来自真机实测（2026-10-29）：
        - 16384（道馆未建立）：确认框「确定消耗 70 创建道馆?」弹出，
          CREATE_DOKAN_ENSURE 命中 **0.9999**；两个金色按钮实测为
          返回 (456,411,121,50)、建立 (763,411,121,50)，建立按钮中心 (823,436)。
        - 16512（道馆已建立，罗生门 4）：点「建立道馆」前后整屏像素差 **0.000**，
          一个弹窗都没有 —— 已建立时这个按钮在游戏里本来就是死的，不是素材问题。

        所以「点完有没有弹确认框」是判断道馆建没建的唯一可靠信号。
        注意确认框盖不住底部按钮栏，弹窗开着时 CREATE_DOKAN 仍能命中（0.9958），
        因此每轮循环**必须先判 ENSURE 再点 CREATE_DOKAN**，顺序不能换。

        已知盲区：建立道馆要消耗 **70 寮资金**，余额不足时点下去同样不弹确认框，
        表现为「和已建立一模一样」。本任务没有资金检测（上游也没有），
        所以「无弹窗」只能记成「可能已建立，也可能没钱」，不能当已建立的铁证。

        :return: True 表示本次确认到「建立成功」，False 表示未建立/无需建立
        """
        for attempt in range(3):
            self.screenshot()
            if self.appear(self.I_RYOU_DOKAN_DOKAN_INFO_CLOSE):
                # 建立成功后会直接弹道馆信息窗，关掉它就算完事
                self.ui_click_until_disappear(self.I_RYOU_DOKAN_DOKAN_INFO_CLOSE)
                logger.info('Dokan: 道馆建立成功')
                return True
            if self.appear(self.I_RYOU_DOKAN_CREATE_DOKAN_ENSURE):
                logger.info('Dokan: 道馆未建立（弹出确认框），点击「建立」')
                self.ui_click_until_appear_or_timeout(self.I_RYOU_DOKAN_CREATE_DOKAN_ENSURE,
                                                      stop=self.I_RYOU_DOKAN_DOKAN_INFO_CLOSE, interval=2, timeout=10)
                continue
            # 按钮都不在屏幕上，说明当前页面不是道馆地图，别白等 10 秒
            if not self.appear(self.I_RYOU_DOKAN_CREATE_DOKAN):
                logger.warning('Dokan: 界面上没有「建立道馆」按钮，跳过建立流程')
                return False
            if self.ui_click_until_appear_or_timeout(self.I_RYOU_DOKAN_CREATE_DOKAN,
                                                     self.I_RYOU_DOKAN_CREATE_DOKAN_ENSURE, interval=2, timeout=10):
                continue
            # 点了 10 秒都没等到确认框。注意有**两种**可能，别一口咬定是已建立：
            #   a) 道馆本来就建好了（按钮是死的）
            #   b) 寮资金不足 70（游戏里建立道馆要消耗 70 寮资金，余额不够同样点不出确认框）
            # 上游原版注释只承认 a，实测 16384 出现过余额 0 的情况，故这里把 b 也写出来。
            logger.info('Dokan: 点击「建立道馆」10 秒无弹窗（可能已建立，也可能寮资金不足 70，请看右上角余额）')
            return False
        logger.warning('Dokan: 建立道馆重试 3 轮仍未收敛，跳过')
        return False

    def challenge_and_enter_dokan(self) -> bool | None:
        """
        点「挑战」-> 点「确定」-> 确认真的进了道馆。

        实测（2026-10-29）：寮资金/挑战次数不足时，「挑战」和「确定」两个按钮照样能点下去，
        但点完界面原地不动，根本不会进道馆。上游原版的做法是照样扣一次攻击次数并当成成功，
        结果白扣次数 + 2 分钟后反复重试。这里补上「进没进道馆」的校验。

        判据用 I_RYOU_DOKAN_CHECK（顶部中间「道馆突破」四个字），image.json 里对它的描述
        写的就是「是否已经成功进入道馆」；page.py 的 page_dokan 也是拿它识别的，
        实测地图页只有 0.1408，远低于 0.8 阈值，不会把「没进」误判成「进了」。

        三种结局必须分开，不能一律当成「寮资金不足」：
        - 点「挑战」弹不出「确定」框：这个道馆本身点不动（刚被别的寮打穿、网络卡），
          是偶发，换一个道馆就行，不该让今天整个任务作废。
        - 点完「确定」确实没进道馆：这才是寮资金/挑战次数不足。
        - 15 秒（不是 8 秒）给进道馆的加载动画留出余量，避免加载慢被误判成没钱。

        :return: True=已经进道馆；False=确认没进去（寮资金/挑战次数不足）；
                 None=这个道馆点不动，换一个再试
        """
        # 点「挑战」直到「确定」出现。上游用的是 ui_click()，内部 while 1，
        # 按钮点不动会一直点到 GameTooManyClickError，这里换成带超时的版本。
        if not self.ui_click_until_appear_or_timeout(self.I_CENTER_CHALLENGE,
                                                     self.I_CHALLENGE_ENSURE,
                                                     interval=1, timeout=8):
            logger.warning('Dokan: 点「挑战」8 秒没有弹出「确定」框，这个道馆点不动，换下一个')
            return None
        # 点「确定」直到它消失。同样把 ui_click_until_disappear() 的无界 while 1 换成 8 秒上限。
        confirm_timer = Timer(8).start()
        while not confirm_timer.reached():
            self.screenshot()
            if not self.appear(self.I_CHALLENGE_ENSURE):
                break
            self.appear_then_click(self.I_CHALLENGE_ENSURE, interval=1)
        if self.wait_until_appear(self.I_RYOU_DOKAN_CHECK, True, 15):
            return True
        logger.error('Dokan: 点「挑战」并「确定」后 15 秒没有进入道馆，判定为寮资金/挑战次数不足')
        return False

    def back_out(self) -> bool:
        """
        点左上角黄箭头退出当前界面（资金不足时别停在原地），最多 3 次。
        退到哪一层不重要，上层 goto_page(page_main) 会兜底回庭院。
        """
        for _ in range(3):
            self.screenshot()
            if self.appear(self.I_CHALLENGE_ENSURE):  # 确认框还赖着，先点掉它
                self.appear_then_click(self.I_CHALLENGE_ENSURE, interval=1)
                continue
            if not self.appear_then_click(GlobalGameAssets.I_UI_BACK_YELLOW, interval=1.5):
                break
        return True

    def refresh_dokan_list(self) -> bool:
        """
        点「刷新」并点掉「确定」，刷新道馆列表。

        外层刷新次数由配置 find_dokan_refresh_count 控制（默认 7），这里管的是**单次刷新**：
        原来用 ui_click() + ui_click_until_disappear()，两者内部都是 while 1，
        免费刷新次数用完 / 刷新按钮变灰点不动时会一直点同一个位置，
        直到触发「10 次」保护抛 GameTooManyClickError（oas_lint S03 就报这两行）。
        现在两次点击各给 8 秒上限，点不动就跳过这一轮刷新，交给外层继续数次数。

        :return: True 刷新成功；False 点不动或确认框没点掉
        """
        if not self.ui_click_until_appear_or_timeout(self.C_DOKAN_REFRESH,
                                                     self.I_REFRESH_ENSURE,
                                                     interval=1, timeout=8):
            logger.warning('Dokan: 点「刷新」8 秒没弹确认框，跳过本次刷新')
            return False
        confirm_timer = Timer(8).start()
        while not confirm_timer.reached():
            self.screenshot()
            if not self.appear(self.I_REFRESH_ENSURE):
                logger.info("Refresh Done")
                return True
            self.appear_then_click(self.I_REFRESH_ENSURE, interval=1)
        logger.warning('Dokan: 「确定」框 8 秒没点掉，跳过本次刷新')
        return False

    def find_all_element(self, item, offset: tuple) -> list[tuple[int, int, int, int]]:
        """
        NOTE: 仅适配查找道馆列表
        在当前对象中查找所有匹配的项目，并返回它们的信息列表。

        此函数的目的是通过循环搜索和匹配给定的项目，并将匹配的项目信息存储到一个列表中。
        如果项目出现，则将其添加到列表中，并根据预定义的规则调整项目的位置。

        参数:
        - item: 需要查找的项目。
        - offset: 如果当前区域查找不到,扩大查找区域的大小

        返回值:
        返回一个包含所有匹配项目信息的列表。
        """
        res_list = []
        while 1:
            if (item.roi_back[0] + item.roi_back[2] > (1280 + offset[2])) or (
                    item.roi_back[1] + item.roi_back[3] > (720 + offset[3])):
                break
            if self.appear(item):
                res_list.append(item.roi_front.copy())
                # 刷新搜索区域,使用上个搜索结果的Y坐标作为起始点的Y坐标,搜索结果的高度作为起始搜索高度
                item.roi_back = position_offset(item.roi_back, (
                    0, item.roi_front[1] + item.roi_front[3] - item.roi_back[1], 0,
                    item.roi_front[3] - item.roi_back[3]))
            item.roi_back = position_offset(item.roi_back, offset)
        return res_list

    def start_cheering(self):
        if not self.conf.dokan_config.dokan_auto_cheering_while_cd:
            return
        cd_text = self.O_DOKEN_FAIL_CD.detect_text(self.device.image)
        match = re.search(r'\d+', cd_text)
        remain_seconds = int(match.group()) if match else None
        if not remain_seconds:
            logger.info(f'No remain seconds, exit cheering, cd_text[{cd_text}]')
            return
        cheering_timer = Timer(remain_seconds).start()
        logger.info(f"start cheering, remain seconds:{remain_seconds}s")
        while not cheering_timer.reached():
            self.screenshot()
            # 出现观战标志点击观战
            if self.appear_then_click(self.I_RYOU_DOKAN_CD, interval=2.5):
                sleep(1.5)  # 等待一下观战列表出现动画
                continue
            if self.is_in_battle(False):
                break
            # 寻找前往按钮并点击
            if self.list_appear_click(self.L_GOTO_CHEERING, interval=3, max_swipe=3):
                continue
            # 没有找到前往(全军覆没or没人上)则随机点击其他位置关闭弹窗
            self.click(pages.random_click(), interval=5)
        else:
            logger.warning('Enter battle to cheer failed')
            return
        logger.info('Enter battle to cheer')
        cheer_cnt = 0
        self.device.stuck_record_clear()
        self.device.stuck_record_add('PAUSE')
        while not cheering_timer.reached():
            self.screenshot()
            battle_end_page = self.detect_page_in(pages.page_battle_result, pages.page_reward, include_global=False)
            if battle_end_page is not None:  # 战斗结束则退出战斗交给上层处理
                logger.info(f'Cheer finish, count[{cheer_cnt}]')
                self.exit_battle(True)
                self.device.stuck_record_clear()
                return
            # 灰色助威
            if self.appear(self.I_RYOU_DOKAN_CHEERING_GRAY, interval=2):
                continue
            # 亮色助威
            if self.appear_then_click(self.I_RYOU_DOKAN_CHEERING, interval=2):
                cheer_cnt += 1
                logger.attr(cheer_cnt, f'cheer, count time[{cheering_timer.current():.1f}s]')
                self.device.stuck_record_clear()
                self.device.click_record_clear()
                self.device.stuck_record_add('PAUSE')
                continue
        # 助威时间到了且道馆还未结束, 则退出观战交给上层重新挑战
        self.exit_battle()

    def update_remain_attack_count(self) -> int:
        """
        根据道馆地图界面 或 打完道馆后 的寮境界面,更新配置信息
        @return:
        @rtype:
        """
        self.screenshot()
        count = -1
        if self.appear(self.I_RYOU_DOKAN_REMAIN_ATTACK_COUNT_ZERO) or self.appear(
                self.I_RYOU_DOKAN_REMAIN_ATTACK_COUNT_DONE):
            logger.info("I_RYOU_DOKAN_REMAIN_ATTACK_COUNT_ZERO/DONE found")
            count = 0
        elif self.appear(self.I_RYOU_DOKAN_REMAIN_ATTACK_COUNT_ONE):
            logger.info("I_RYOU_DOKAN_REMAIN_ATTACK_COUNT_ONE found")
            count = 1
        elif self.appear(self.I_RYOU_DOKAN_REMAIN_ATTACK_COUNT_TWO):
            logger.info("I_RYOU_DOKAN_REMAIN_ATTACK_COUNT_TWO found")
            count = 2
        else:
            logger.info("dont find REMAIN_ATTACK_COUNT PIC")
            count = -1
        self.config.dokan.attack_count_config.set_attack_count(count, self.config.save)
        return count

    def abandoned_toppa(self):
        if self.appear(self.I_DOKAN_ABANDONED_TOPPA):
            self.ui_click(self.I_DOKAN_ABANDONED_TOPPA, stop=self.I_DOKAN_ABANDONED_TOPPA_ENSURE)
            # 点击确认按钮后,会出现突破排名弹窗,需要点击空白区域关闭
            self.ui_click(self.I_DOKAN_ABANDONED_TOPPA_ENSURE, stop=self.I_RYOU_DOKAN_TOPPA_RANK)
            self.ui_click_until_smt_disappear(self.C_DOKAN_TOPPA_RANK_CLOSE_AREA,
                                              stop=self.I_DOKAN_ABANDONED_TOPPA_TITLE,
                                              interval=2)
        # 动画延时
        self.wait_until_appear(self.I_DOKAN_ABANDONED_TOPPA_TITLE, True, 5)
        # 投票放弃突破,一般来说,都主动放弃突破了,基本上点放弃没有错(虽然项目中做了继续的按钮图)
        if self.appear(self.I_DOKAN_ABANDONED_TOPPA_TITLE):
            if self.appear(self.I_RYOU_DOKAN_ABANDONED_TOPPA_ABANDONED):
                self.ui_click_until_disappear(self.I_RYOU_DOKAN_ABANDONED_TOPPA_ABANDONED)
        else:
            # 再战道馆, 保留赏金
            if self.appear(self.I_RYOU_DOKAN_FAILED_VOTE_KEEP_BOUNTY):
                self.ui_click_until_disappear(self.I_RYOU_DOKAN_FAILED_VOTE_KEEP_BOUNTY)

    def switch_soul_in_dokan(self, switch_type: str = None):
        if switch_type is None:
            return
        switch_soul_dict = {
            'member': {
                'group_team': self.config.dokan.dokan_member_switch_soul.switch_group_team,
                'group_team_name': [self.config.dokan.dokan_member_switch_soul.group_name,
                                    self.config.dokan.dokan_member_switch_soul.team_name]
            },
            'owner': {
                'group_team': self.config.dokan.dokan_owner_switch_soul.switch_group_team,
                'group_team_name': [self.config.dokan.dokan_owner_switch_soul.group_name,
                                    self.config.dokan.dokan_owner_switch_soul.team_name]
            }
        }
        switch_soul_done = getattr(self, f'switch_{switch_type}_soul_done', False)
        if switch_soul_done:
            return
        logger.hr('Start switch soul', 2)
        switch_soul = getattr(self.config.dokan, f'dokan_{switch_type}_switch_soul', None)
        switch_soul_by_name = getattr(self.config.dokan, f'dokan_{switch_type}_switch_soul_by_name', None)
        if switch_soul is not None and switch_soul.enable:
            self.goto_page(pages.page_shikigami_records)
            self.run_switch_soul(switch_soul_dict[switch_type]['group_team'])
            self.goto_page(pages.page_dokan)
        elif switch_soul_by_name is not None and switch_soul_by_name.enable:
            self.goto_page(pages.page_shikigami_records)
            self.run_switch_soul_by_name(switch_soul_dict[switch_type]['group_team_name'][0],
                                         switch_soul_dict[switch_type]['group_team_name'][1])
            self.goto_page(pages.page_dokan)
        else:
            logger.info('Skip switch soul')
        setattr(self, f'switch_{switch_type}_soul_done', True)

    def next_run(self, skip_today=False, is_dokan_activated=False):
        """
            设置下次运行时间
            该函数假定道馆时间设置为:每天固定时间尝试开启(例如:19:00),成功后设置为明天固定时间(例如19:00)
                                失败则在短时间(例如:2分钟)内再次尝试开启道馆任务
            此假定应该符合绝大多数人需求,如果存在其他需求,,,help yourself

        @param skip_today: 是否跳过今天,True->当作当天的道馆已成功打掉,False->无效
                            为了跳过周五->周天
        @type skip_today: bool
        @param is_dokan_activated:
        @type is_dokan_activated: bool
        @return:
        @rtype:
        """
        now = datetime.now()
        run_time: Time = self.config.model.dokan.dokan_config.dokan_run_time
        run_time_dt = datetime.combine(now.date(), run_time)
        if skip_today:
            if self.conf.dokan_config.monday_to_thursday and now.weekday() >= 4:  # 直接设置下周一的道馆时间
                self.set_next_run(task="Dokan", server=False,
                                  target=datetime.combine(now.date() + timedelta(days=7 - now.weekday()), run_time))
                return
            self.set_next_run(task="Dokan", server=False, target=datetime.combine(now.date() + timedelta(days=1), run_time))
            return
        # 道馆没有开启
        if not is_dokan_activated:
            # 在服务器时间之前,设置为服务器时间
            if now < run_time_dt:
                self.set_next_run(task="Dokan", server=False, target=run_time_dt)
                return
            # 在服务器时间之后,如超过1小时,则直接当作成功;未超过则当作失败
            if now - run_time_dt > timedelta(hours=1):
                self.set_next_run(task="Dokan", server=False,
                                  target=datetime.combine(now.date() + timedelta(days=1), run_time))
                return
            # 时间在道馆开启时间附近，failure_interval后执行
            self.set_next_run(task="Dokan", server=False, target=now + self.config.dokan.scheduler.failure_interval)
            return
        # 道馆已开启
        # 如果打两次,当前是第一次,设置为failure_interval后运行
        if self.config.dokan.attack_count_config.remain_attack_count == 1 and \
                self.config.dokan.attack_count_config.daily_attack_count == 2:
            self.set_next_run(task="Dokan", server=False, target=now + self.config.dokan.scheduler.failure_interval)
            return
        # 其余情况当作成功
        self.set_next_run(task="Dokan", server=False, target=datetime.combine(now.date() + timedelta(days=1), run_time))


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('日常1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.run()
