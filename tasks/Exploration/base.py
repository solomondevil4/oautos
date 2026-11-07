# This Python file uses the following encoding: utf-8
# @author AzurTian
import time
import numpy as np
from cached_property import cached_property
from datetime import timedelta, datetime
from module.atom.gif import RuleGif
from module.atom.image import RuleImage
from module.base.timer import Timer

from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.Component.GeneralRoom.general_room import GeneralRoom
from tasks.Component.GeneralInvite.general_invite import GeneralInvite
from tasks.Component.ReplaceShikigami.replace_shikigami import ReplaceShikigami
from tasks.Exploration.assets import ExplorationAssets
from tasks.Exploration.config import ChooseRarity, UpType, ExplorationLevel, AutoRotate, UserStatus, Exploration
from tasks.Exploration.rotation import normalize_index, parse_member_list, read_leader_state
from tasks.Component.GeneralBattle.general_battle import GeneralBattle, ExitMatcher, BattleContext, BattleAction
from tasks.GameUi.game_ui import GameUi
from tasks.Utils.config_enum import ShikigamiClass
import tasks.Exploration.page as pages

from module.logger import logger
from module.exception import TaskEnd, GameStuckError
from module.atom.animate import RuleAnimate
from typing import Optional


class BaseExploration(GameUi, GeneralBattle, GeneralRoom, GeneralInvite, ReplaceShikigami, SwitchSoul, ExplorationAssets):
    fire_monster_type: str = ''
    need_exit: bool = False
    user_status: UserStatus = UserStatus.ALONE
    wait_start_time: datetime = datetime.now()
    pre_page: pages.Page = None
    # 小号轮换：本轮结束后的下次运行时刻 / 本轮结束后要切去的角色
    _rotation_next_run: datetime = None
    _rotation_switch_target: str = None
    _rotation_poll_timer: Timer = None
    # 开局等待：设了值表示「还没建房，等到这个时刻再开局」
    _rotation_abort: datetime = None
    # 小号启动即切号：设 True 表示这次 run 只为切号，不进探索
    _rotation_boot_switch: bool = False
    # 距上次开局在这个间隔内，视为同一轮内的自动重排（换人/打突破回来），不再做开局等待
    ROTATION_OPENING_RECENT_GAP: timedelta = timedelta(minutes=5)

    def _exit_matcher(self) -> ExitMatcher:
        return pages.any_of(self.I_E_SETTINGS_BUTTON, self.I_E_AUTO_ROTATE_ON, self.I_E_AUTO_ROTATE_OFF)

    @cached_property
    def _config(self) -> Exploration:
        self.config.exploration.general_battle_config.lock_team_enable = True
        limit_time = self.config.exploration.exploration_config.limit_time
        self.limit_time: timedelta = timedelta(
            hours=limit_time.hour,
            minutes=limit_time.minute,
            seconds=limit_time.second
        )
        return self.config.model.exploration

    @cached_property
    def _match_end(self):
        return RuleAnimate(self.I_SWIPE_END)

    def pre_process(self):
        # 身份和轮换判定放最前面：开局等待时直接返回，免得白切一遍御魂、白开一遍加成。
        # 只提前 user_status（rotation_is_leader 要用），wait_start_time 仍留在原来
        # 的位置重置，保证不启用轮换时队友等待超时的计时起点和改动前一致。
        self.user_status = self._config.exploration_config.user_status
        self.rotation_prepare()
        if self._rotation_abort or self._rotation_boot_switch:
            return
        if self._config.switch_soul_config.enable:
            self.goto_page(pages.page_shikigami_records)
            self.run_switch_soul(self._config.switch_soul_config.switch_group_team)

        if self._config.switch_soul_config.enable_switch_by_name:
            self.goto_page(pages.page_shikigami_records)
            self.run_switch_soul_by_name(self._config.switch_soul_config.group_name,
                                         self._config.switch_soul_config.team_name)
        # 开启加成
        con = self.config.exploration.exploration_config
        if con.buff_gold_50_click or con.buff_gold_100_click or con.buff_exp_50_click or con.buff_exp_100_click:
            self.goto_page(pages.page_main)
            self.open_buff()
            if con.buff_gold_50_click:
                self.gold_50()
            if con.buff_gold_100_click:
                self.gold_100()
            if con.buff_exp_50_click:
                self.exp_50()
            if con.buff_exp_100_click:
                self.exp_100()
            self.close_buff()
        self.wait_start_time = datetime.now()  # 重置等待时间

    def post_process(self):
        # 先清掉挡在探索页上的残留确认框，否则后面每一次导航点击都会被它吃掉
        self.rotation_dismiss_popup()
        # 开局等待：还没建过房，直接排期走人，连退探索都不用做
        if self._rotation_abort:
            logger.info(
                f'Rotation: 开局等待中，{self._rotation_abort.strftime("%H:%M:%S")} 再回来建房邀请'
            )
            self.set_next_run(task='Exploration', success=True, finish=True, server=False,
                              target=self._rotation_abort)
            raise TaskEnd
        # 小号启动即切号：这次还没进过队伍，不能走退队那套（会白转 45 秒甚至空等到超时）
        if self._rotation_boot_switch:
            logger.info('Rotation: 小号启动即切号，跳过退队直接切')
            self.rotation_wait_game_ready()
            self.rotation_do_switch()
            self.set_next_run(task='Exploration', success=True, finish=True, server=False,
                              target=datetime.now().replace(microsecond=0) + timedelta(seconds=10))
            raise TaskEnd
        # 换人 / 切号：先把探索和队伍退干净，别让「是否退出队伍」的框把导航带偏
        if self._rotation_next_run or self._rotation_switch_target:
            self.rotation_leave_team()
        self.goto_page(pages.page_exploration)
        con = self._config.exploration_config
        if con.buff_gold_50_click or con.buff_gold_100_click or con.buff_exp_50_click or con.buff_exp_100_click:
            self.goto_page(pages.page_main)
            self.open_buff()
            self.gold_50(is_open=False)
            self.gold_100(is_open=False)
            self.exp_50(is_open=False)
            self.exp_100(is_open=False)
            self.close_buff()
        # 注意：轮换相关的重排一律带 server=False。
        # 否则 task_delay 里 server=True 的分支会把 target 覆盖成「明天 server_update」，
        # 换号/待命的重排全部失效（server_update 恰好是 09:00 时才侥幸不覆盖）。
        # 小号：队长点名换了人，先切号再结束本轮
        if self._rotation_switch_target:
            self.rotation_do_switch()
            self.set_next_run(task='Exploration', success=True, finish=True, server=False,
                              target=datetime.now().replace(microsecond=0) + timedelta(seconds=10))
            raise TaskEnd
        # 队长：换下一个小号，隔一段时间重新开局，等小号切号上线
        if self._rotation_next_run:
            self.set_next_run(task='Exploration', success=True, finish=True, server=False,
                              target=self._rotation_next_run)
            raise TaskEnd
        # 小号：待命超时 / 邀请失败退出，也要很快回来继续待命。
        # 不加这段会掉到最后那行默认排期（server=True → 明天），队伍当天就断了。
        if self.rotation_enabled() and self.user_status == UserStatus.MEMBER:
            back = datetime.now().replace(microsecond=0) + self.rotation_switch_wait()
            logger.info(f'Rotation: 小号本轮结束（待命超时或邀请失败），{back.strftime("%H:%M:%S")} 回来继续待命')
            self.set_next_run(task='Exploration', success=True, finish=True, server=False, target=back)
            raise TaskEnd
        self.set_next_run(task='Exploration', success=True, finish=False)
        raise TaskEnd

    def rotation_dismiss_popup(self) -> None:
        """关掉挡在探索页上的「是否邀请队友继续进行战斗？」确认框。

        打完最后一场后游戏在章节页上弹这个框，按钮是「取消 / 确定」。点「确定」
        会立刻再开一局，所以必须点「取消」。这个框没有被建模成独立页面，页面识别
        仍然给出 page_exp_entrance，于是 navigator 以为自己在章节页、按正常路径点
        返回，点击全被弹框吃掉：连撞三次 penalty（日志 00:02:52~00:03:10 的
        Transition cannot reach page_main penalty=1/2/3）后才改道
        page_realm_raid -> page_shikigami_records -> page_main，又白爬三次式神录，
        合计约 70 秒（00:02:46 -> 00:04:04）。

        实测素材匹配：I_UI_CANCEL 0.980 落点(438,405)，I_UI_CONFIRM 0.986 落点(666,406)。
        """
        for _ in range(3):
            self.screenshot()
            image = self.device.image
            # 两个按钮同时在，才是这种双选确认框；只有一个按钮的框不动它
            if not (self.I_UI_CANCEL.match(image) and self.I_UI_CONFIRM.match(image)):
                return
            # 双按钮框有两种，必须按所在页面区分，点错会越退越深：
            # 副本内 = 「是否邀请队友继续进行战斗」，点取消；
            # 探索页 = 「是否退出队伍」，点取消反而留在队里。
            page = self.get_current_page()
            if page not in (pages.page_exp_main, pages.page_exp_exit, pages.page_battle_team):
                logger.info(f'Rotation: 双按钮框出现在 {getattr(page, "name", page)}，不是「继续战斗」框，不动它')
                return
            logger.info('Rotation: 出现「是否邀请队友继续进行战斗」框，点取消退出')
            self.appear_then_click(self.I_UI_CANCEL, interval=0.6)
        self.screenshot()

    def rotation_leave_team(self) -> None:
        """换人前主动退出探索与队伍，一路退到庭院（page_main）为止。

        为什么终点必须是庭院、不是探索页：
        打完最后一场人还在队伍里，游戏会弹「是否邀请队友继续进行战斗？」。这个框
        没有被建模成独立页面，页面识别仍然给出当前页，navigator 以为自己在正常页面、
        照常点返回，点击全被弹框吃掉。旧实现退到探索页就收手，结果 Exploration 一
        结束、GotoMain 接手时框还在，又连撞三次 penalty，改道
        page_realm_raid -> page_shikigami_records -> page_main 白爬三次突破页和式神录，
        实测 69.3 秒（日志 01:31:39 -> 01:32:47）。

        终点判据 = 站到 page_main。到庭院说明副本、队伍、弹框全清干净，后续任务不会
        再撞。中途两种双按钮框必须按页面区分，点错会越退越深：
        - 副本内（章节页/退出口/组队页）：是「是否邀请队友继续进行战斗」，点取消
          （点确定会立刻再开一局）
        - 探索页（探索界面/入口页）：是「是否退出队伍」，点确定
          （点取消会留在队伍里，等于没退）
        进循环前清一次点击记录，返回键单独计数，避免短时连点触发 GameTooManyClickError。
        """
        logger.info('Rotation: 换人前先退出探索和队伍，目标回到庭院')
        # check_exit 可能在任意页面触发换人，先让战斗/结算流程收尾再动手，
        # 否则会在领奖页点到左上角返回、弹出菜单卡住
        guard = Timer(20).start()
        while not guard.reached():
            self.screenshot()
            if self.get_current_page() not in (
                pages.page_battle, pages.page_battle_prepare,
                pages.page_reward, pages.page_battle_result,
            ):
                break
            self.click(pages.random_click(), interval=0.8)

        # 副本内页面：这里的双按钮框是「继续战斗」，只能点取消
        in_dungeon = (pages.page_exp_main, pages.page_exp_exit, pages.page_battle_team)
        # 防卡死是按「同一素材短时点击次数」累计的，先清一次再开始点
        self.device.click_record_clear()
        timer = Timer(60).start()
        rounds = 0
        back_clicks = 0
        cancel_clicks = 0
        sure_clicks = 0
        exit_room_tries = 0
        while not timer.reached() and rounds < 80:
            rounds += 1
            self.screenshot()
            page = self.get_current_page()
            # 终点：站到庭院才算真的干净（副本、队伍、弹框全清）
            if page == pages.page_main:
                break
            if page is None:
                time.sleep(0.4)
                continue
            image = self.device.image
            # 双按钮框（取消 + 确定同时在）：按所在页面决定点哪一个
            has_dialog = self.I_UI_CANCEL.match(image) and self.I_UI_CONFIRM.match(image)
            if has_dialog:
                if page in in_dungeon:
                    if cancel_clicks < 6:
                        cancel_clicks += 1
                        logger.info(f'Rotation: 副本内遇到「是否邀请队友继续进行战斗」（{page.name}），点取消')
                        self.appear_then_click(self.I_UI_CANCEL, interval=0.8)
                        continue
                elif sure_clicks < 4:
                    sure_clicks += 1
                    logger.info(f'Rotation: 探索页遇到「是否退出队伍」（{page.name}），点确定')
                    self.appear_then_click(self.I_UI_CONFIRM, interval=0.8)
                    continue
            # 队伍房间：这里点返回只会弹「是否邀请队友继续进行战斗」，点取消关掉框后人还留在
            # 房间里，再点返回又弹 —— 实测空转 4 轮就把返回键额度耗光，之后既不能点返回、也
            # 没有别的素材可点，只能干等 60 秒总超时才进兜底（日志 01:58:35~01:59:26 那 45 秒
            # 完全空白就是这么来的）。兜底里的 exit_room() 实测 3 秒就退干净
            # （01:59:26~01:59:29 直接到 page_exp_entrance），所以提前在这里用。
            # 注意 I_GI_SURE 的 ROI(667,408,175,60) 与「继续战斗」框的确定按钮位置重合，
            # 必须先排除双按钮框，否则 exit_room 内部那一下点确定等于又开一局。
            if not has_dialog and exit_room_tries < 3 \
                    and page == pages.page_battle_team and self.is_in_room(False):
                exit_room_tries += 1
                logger.info(f'Rotation: 站在队伍房间（{page.name}），直接点退出队伍，第 {exit_room_tries} 次')
                if self.exit_room():
                    logger.info('Rotation: 退出队伍成功，继续退到庭院')
                else:
                    logger.warning('Rotation: 退出队伍未成功，改用返回键兜底')
                continue
            # 退出探索的确认框
            if self.appear_then_click(self.I_E_EXIT_CONFIRM, interval=0.8):
                sure_clicks += 1
                continue
            # 返回键兜底：上限 8 次，循环前已清点击记录，不会触发防卡死
            if back_clicks < 8 and self.appear_then_click(self.I_UI_BACK_YELLOW, interval=0.8):
                back_clicks += 1
                continue
            # 其他单按钮确认框
            if sure_clicks < 8 and self.appear_then_click(self.I_UI_CONFIRM, interval=0.8):
                sure_clicks += 1
                continue
            # 认不出画面就等一帧，别空转
            time.sleep(0.4)

        page = self.get_current_page()
        if page != pages.page_main:
            logger.warning(f'Rotation: 退到庭院未成功，当前页面 {getattr(page, "name", page)}，尝试兜底')
            if self.is_in_room(False):
                logger.info('Rotation: 仍在队伍房间，补退一次')
                self.exit_room()
            self.goto_page(pages.page_main)
            page = self.get_current_page()
        logger.info(
            f'Rotation: 退探索/退队伍结束，当前页面 {getattr(page, "name", page)}'
            f'（取消 {cancel_clicks} 次，确定 {sure_clicks} 次，返回 {back_clicks} 次，'
            f'退房 {exit_room_tries} 次，共 {rounds} 轮）'
        )

    # 打开指定的章节：
    def open_expect_level(self):
        swipeCount = 0
        config_exploration_level = self.config.exploration.exploration_config.exploration_level
        while True:
            # 判断有无目标章节
            self.screenshot()
            # 获取当前章节名
            results = self.O_E_EXPLORATION_LEVEL_NUMBER.detect_and_ocr(self.device.image)
            text1 = [result.ocr_text for result in results]
            exp_level_enum_list = []
            for txt in text1:
                try:
                    exp_level_enum_list.append(ExplorationLevel(txt))
                except ValueError as e:
                    logger.warning(f'convert {txt} failed')
            sorted(exp_level_enum_list, key=lambda x: x.get_index())  # Sort by index
            # 判断当前章节有无目标章节
            result = set(text1).intersection({config_exploration_level})
            # 有则跳出检测
            if self.appear(self.I_E_EXPLORATION_CLICK) or result and len(result) > 0:
                break
            if self.appear_then_click(self.I_UI_CONFIRM, interval=1):
                continue
            if self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=1):
                continue
            self.device.click_record_clear()
            if len(exp_level_enum_list) > 0:
                min_level = exp_level_enum_list[0]
                max_level = exp_level_enum_list[-1]
                if config_exploration_level.get_index() < min_level.get_index():
                    self.swipe(self.S_SWIPE_LEVEL_UP)
                elif config_exploration_level.get_index() > max_level.get_index():
                    self.swipe(self.S_SWIPE_LEVEL_DOWN)
            swipeCount += 1
            debug_info = f"Swiped {swipeCount} times, current exploration level: {text1}"
            logger.info(debug_info)
            if swipeCount >= 25:
                raise GameStuckError(
                    f"Swiped too many times ({swipeCount}), seems stuck in exploration level selection"
                )
            time.sleep(1)

        # 选中对应章节
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_UI_CONFIRM, interval=1):
                continue
            if self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=1):
                continue
            self.O_E_EXPLORATION_LEVEL_NUMBER.keyword = config_exploration_level
            if self.ocr_appear_click(self.O_E_EXPLORATION_LEVEL_NUMBER):
                self.wait_until_appear(self.I_E_EXPLORATION_CLICK, wait_time=3)
            if self.appear(self.I_E_EXPLORATION_CLICK):
                break
            if self.is_in_room():
                break

        return True

    def fill_shikigami(self):
        """填充式神(最后回到探索主界面)"""
        # 候补出战数量识别
        cu, res, total = self.O_E_ALTERNATE_NUMBER.ocr(self.device.image)
        if cu >= 40:
            logger.info("Alternate number is enough")
            self.goto_page(pages.page_exp_main)
            return
        choose_rarity = self._config.exploration_config.choose_rarity
        rarity = ShikigamiClass.N if choose_rarity == ChooseRarity.N else ShikigamiClass.MATERIAL
        self.click(self.C_CLICK_STANDBY_TEAM)  # 先点击候补出战区域
        self.switch_shikigami_class(rarity)  # 切换式神类别
        pre = -1
        while True:
            time.sleep(0.5)
            self.screenshot()
            if not self.appear(self.I_E_OPEN_SETTINGS):
                logger.warning('Opening settings failed')
                return
            cur, res, total = self.O_E_ALTERNATE_NUMBER.ocr(self.device.image)
            if cur >= 40:
                logger.info(f'Alternate number is enough, exit')
                break
            # 连续向后滑动超过6次还能识别到候补狗粮(1. 滑动的不够× 2. 没新狗粮了)
            if self.device.click_record.count(self.S_SWIPE_SHIKI_TO_LEFT.name) >= 6 or \
                    self.device.click_record.count(self.S_SWIPE_SHIKI_TO_LEFT_ONE.name) >= 6:
                if cur > 0: # 上了一部分狗粮, 先用着
                    logger.warning(f'Alternate number is not enough, current: {cur}')
                    break
                # 滑动很多次了, 结果也没成功上狗粮, 要么滑的不够(基本不可能)要么没狗粮(大概率)
                # TODO: 1. 增加选项狗粮不够时继续打 2. 去召唤界面换狗粮(这里还有问题是否去商店买厕纸)
                raise GameStuckError(f"Alternate number is not enough")
            # 识别到右侧候补狗粮, 则大幅度向右移动
            if self.appear(self.I_E_ROTATE_EXIST_RIGHT):
                self.swipe(self.S_SWIPE_SHIKI_TO_LEFT)
                continue
            # 识别到候补狗粮, 则滑动一部分
            if self.appear(self.I_E_RATATE_EXSIT):
                self.swipe(self.S_SWIPE_SHIKI_TO_LEFT_ONE)
                continue
            # 没识别到候补狗粮(没狗粮/已经全满级)导致不滑动了, 但是上狗粮后数量又没变
            if pre == cur:
                if cur > 0:  # 上了一部分狗粮, 先用着
                    logger.warning(f'Alternate number is not enough, current: {cur}')
                    break
                # TODO: 同上一个todo
                raise GameStuckError(f"Alternate number is not enough")
            pre = cur
            # 长按上狗粮
            self.click(self.L_ROTATE_1)
            self.device.click_record_clear()
        self.goto_page(pages.page_exp_main)

    # 找up按钮
    def search_up_fight(self, up_type: UpType = None) -> Optional[RuleImage | RuleGif]:
        up_type = self._config.exploration_config.up_type if up_type is None else up_type
        if up_type == UpType.ALL and self.appear(self.I_NORMAL_BATTLE_BUTTON):
            return self.I_NORMAL_BATTLE_BUTTON
        match up_type:
            case UpType.EXP:
                find_flag = self.I_UP_EXP
            case UpType.COIN:
                find_flag = self.I_UP_COIN
            case UpType.DARUMAA:
                find_flag = self.I_UP_DARUMA
            case _:
                find_flag = self.I_UP_EXP
        appear = self.appear(find_flag)
        if not appear:
            return None
        # logger.info(f'Found up type: {up_type} at  {find_flag.roi_front}')
        x, y, _, _ = find_flag.roi_front
        x_center, y_center = find_flag.front_center()
        roi_back_y = max(0, y - 300)
        roi_back_h = y - 20 - roi_back_y
        roi_back_x = max(0, x - 160)
        roi_back_w = min(1280, x + 200) - roi_back_x
        # self.I_NORMAL_BATTLE_BUTTON.roi_back = [roi_back_x, roi_back_y, roi_back_w, roi_back_h]
        # logger.info(f'It will search normal battle button at {roi_back_x, roi_back_y, roi_back_w, roi_back_h}')
        matches = self.I_NORMAL_BATTLE_BUTTON.match_all(
            image=self.device.image,
            threshold=0.9,
            roi=[roi_back_x, roi_back_y, roi_back_w, roi_back_h],
            frame_id=self.device.image_frame_id,
        )
        if not matches:
            return None
        distances = []
        for match in matches:
            x_match, y_match = match[1], match[2]
            distance = np.linalg.norm(
                np.array([x_center, y_center]) - np.array([x_match, y_match])
            )
            distances.append((distance, match))
        distances.sort(key=lambda x: x[0], reverse=False)
        match = distances[0][1]
        roi_front = list(match[1:])  # x,y,w,h
        self.I_NORMAL_BATTLE_BUTTON.roi_front = roi_front
        # logger.info(f"Found normal battle button at {roi_front}")
        self.fire_monster_type = 'normal'
        return self.I_NORMAL_BATTLE_BUTTON

    def activate_realm_raid(self, con_scrolls, con, current_page: pages.Page | None) -> None:
        # 判断是否开启突破票检测
        if not con_scrolls.scrolls_enable or current_page is None or \
                current_page not in (pages.page_exploration, pages.page_exp_entrance):
            return
        if current_page == pages.page_exp_entrance:
            cu, res, total = self.O_REALM_RAID_NUMBER1.ocr(self.device.image)
        else:
            cu, res, total = self.O_REALM_RAID_NUMBER.ocr(self.device.image)
        # 判断突破票数量
        if cu < con_scrolls.scrolls_threshold:
            return
        # 关闭加成
        if con.buff_gold_50_click or con.buff_gold_100_click or con.buff_exp_50_click or con.buff_exp_100_click:
            self.goto_page(pages.page_main)
            self.open_buff()
            self.gold_50(is_open=False)
            self.gold_100(is_open=False)
            self.exp_50(is_open=False)
            self.exp_100(is_open=False)
            self.close_buff()
        # 设置下次执行行时间
        logger.info("RealmRaid and Exploration  set_next_run !")
        next_run = datetime.now() + con_scrolls.scrolls_cd
        # 打突破回来属于同一轮内的自动重排，把「上次开局时间」预支到回来那一刻，
        # 免得队长打完突破重跑探索时又白等一次开局等待。
        if self.rotation_enabled():
            self._rotation_cfg.last_open_time = next_run.replace(microsecond=0).isoformat()
            self.config.save()
            logger.info('Rotation: 打突破期间不算新开局，回来不做开局等待')
        self.goto_page(pages.page_exploration)
        self.set_next_run(task='Exploration', success=False, finish=False, target=next_run)
        self.set_next_run(task='RealmRaid', success=False, finish=False, server=False, target=datetime.now())
        self.set_next_run(task='MemoryScrolls', success=False, finish=False, target=datetime.now())
        raise TaskEnd

    # ------------------------------------------------------------------
    # 小号轮换（1 对多）：队长固定不切号，小号位按名单轮流上
    # ------------------------------------------------------------------
    @property
    def _rotation_cfg(self):
        """轮换配置段的实时引用。

        不能用 `self._config`（cached_property）：`set_next_run` 内部会重建
        ConfigModel，缓存里的 exploration 就成了孤儿对象。往孤儿里写值不会报错，
        但 `config.save()` 序列化的是新 model，改动整份丢失——实测日志说
        「换第 2 个小号」，读回来还是第 1 个。
        `activate_realm_raid` 一次就调三次 set_next_run，所以券满去打突破回来必踩。
        """
        return self.config.model.exploration.rotation

    @property
    def _rotation_friend_list(self) -> str:
        """邀请名单的实时引用（同样绕开 cached_property）"""
        return self.config.model.exploration.invite_config.friend_list or ''

    def rotation_enabled(self) -> bool:
        return bool(self._rotation_cfg.enable)

    def rotation_is_leader(self) -> bool:
        return self.rotation_enabled() and self.user_status == UserStatus.LEADER

    def rotation_quota_reason(self) -> str:
        """本号配额是否到点：'count' 战斗次数 / 'time' 时间 / '' 未到"""
        if self.current_count >= self._config.exploration_config.minions_cnt:
            return 'count'
        if datetime.now() - self.start_time >= self.limit_time:
            return 'time'
        return ''

    def rotation_switch_wait(self) -> timedelta:
        """换号等待时长（给足小号切号 + 重新进游戏的时间）"""
        wait = self._rotation_cfg.switch_wait
        if not isinstance(wait, timedelta):
            return timedelta(minutes=2)
        return wait

    def rotation_opening_wait(self) -> timedelta:
        """开局等待时长（等小号切到名单里的角色再开始邀请）"""
        wait = self._rotation_cfg.opening_wait
        if not isinstance(wait, timedelta):
            return timedelta(minutes=2)
        return wait

    def rotation_needs_opening_wait(self) -> bool:
        """是否需要开局等待。

        判据不是「今天还没开过局」，而是「距上次开局已经隔了一段时间」：
        只要这一轮是重新被拉起的（开机、重启 OAS、隔天、手动点运行），小号很可能
        还没切到名单里的角色，好友列表里根本没有它，这时邀请必然空转（日志一片
        Cannot find friend），所以先等一会儿再建房。

        同一轮内部的自动重排不等。换人（`rotation_advance_leader`）和打突破
        （`activate_realm_raid`）都会把 last_open_time 预支到「下次该启动的时刻」，
        回来时算出来的间隔接近 0，自然就跳过了。换人本来已经等过 switch_wait，
        再叠一个 opening_wait 就是白等一倍。
        """
        if not self.rotation_is_leader():
            return False
        if self.rotation_opening_wait().total_seconds() <= 0:
            return False
        rot = self._rotation_cfg
        raw = (rot.last_open_time or '').strip()
        if not raw:
            # 从来没开过局（全新配置或手动清空）：这时小号一定还没就位
            return True
        try:
            last = datetime.fromisoformat(raw)
        except ValueError:
            logger.warning(f'Rotation: last_open_time 不是合法时间（{raw!r}），按需要等待处理')
            return True
        gap = datetime.now() - last
        if gap < self.ROTATION_OPENING_RECENT_GAP:
            if gap.total_seconds() < 0:
                logger.info('Rotation: 上次开局时间已预支到本轮之后（换人或打突破），不做开局等待')
            else:
                logger.info(
                    f'Rotation: 距上次开局 {int(gap.total_seconds())} 秒（同一轮内的自动重排），不做开局等待'
                )
            return False
        logger.info(f'Rotation: 距上次开局 {int(gap.total_seconds() // 60)} 分钟，先做开局等待')
        return True

    def rotation_member_idle_exit(self) -> timedelta:
        """小号空转多久算超时（等不到邀请也不能永远待命）"""
        idle = self._rotation_cfg.member_idle_exit
        if not isinstance(idle, timedelta):
            return timedelta(minutes=8)
        return idle

    def rotation_prepare(self) -> None:
        """开局：队长把当前该带的小号写进邀请名单，保证新一轮邀请的是对的人

        小号不走这里，改走 `rotation_member_boot_check`：小号启动第一件事就是
        确认该上的是不是自己，不是就立刻切，绝不先跑探索。
        """
        if not self.rotation_is_leader():
            self.rotation_member_boot_check()
            return
        rot = self._rotation_cfg
        members = parse_member_list(rot.member_list)
        if not members:
            logger.warning('Rotation: 小号名单为空，轮换不生效（按普通探索运行）')
            return
        index = normalize_index(rot.current_index, len(members))
        member = members[index]
        # 判据必须在锚定之前取：锚定后 current_member 就非空了
        need_wait = self.rotation_needs_opening_wait()
        friend_list = self._rotation_friend_list.strip()
        if rot.current_index != index or rot.current_member != member or friend_list != member:
            rot.current_index = index
            rot.current_member = member
            self.config.model.exploration.invite_config.friend_list = member
            logger.info(f'Rotation: 开局锚定小号 {member}（第 {index + 1}/{len(members)} 个）')
        else:
            logger.info(f'Rotation: 继续带 {member}（第 {index + 1}/{len(members)} 个）')
        rot.last_open_time = datetime.now().replace(microsecond=0).isoformat()
        self.config.save()
        if need_wait:
            wait = self.rotation_opening_wait()
            self._rotation_abort = datetime.now().replace(microsecond=0) + wait
            logger.info(
                f'Rotation: 开局先等 {int(wait.total_seconds())} 秒让小号切到 {member}，'
                f'{self._rotation_abort.strftime("%H:%M:%S")} 再建房邀请'
            )

    def rotation_member_boot_check(self) -> None:
        """小号：启动第一件事就确认该上的是不是自己，不是就立刻切号。

        原来切号判断藏在 `check_exit` 里，小号要先把探索循环跑起来、甚至进了队伍
        之后才会发现「队长点名的是别人」，白白折腾一轮才切。开机时小号往往停在
        名单外的角色上，越早切越好。

        这里不做任何等待：开局等待是队长的事（等小号就位），小号自己就是那个要
        就位的角色，直接切到目标号再待命即可。
        """
        if not self.rotation_enabled() or self.user_status != UserStatus.MEMBER:
            return
        # 启动这次必须真的读一次队长配置，别被 5 秒节流挡住
        self._rotation_poll_timer = None
        if not self.rotation_member_check():
            return
        target = (self._rotation_switch_target or '').strip()
        logger.info(f'Rotation: 小号启动即需切号到 {target}，本轮不进探索')
        self._rotation_boot_switch = True

    def rotation_wait_game_ready(self, timeout: int = 120) -> bool:
        """等游戏进到一个能识别的界面，再让切号动手。

        小号刚开机、或刚被 OAS 拉起时，游戏可能还停在加载页。这时直接调切号，
        `get_current_page()` 返回 None，切号内部那句 `goto_page(page_main)` 会走
        navigator 盲点确认，撞很多次才超时失败。先等到能识别出一个页面再切。
        """
        timer = Timer(timeout).start()
        while not timer.reached():
            page = None
            try:
                self.screenshot()
                page = self.get_current_page()
            except Exception as e:
                logger.warning(f'Rotation: 识别当前页面失败（{e}），稍后重试')
            if page is not None:
                logger.info(f'Rotation: 游戏已就绪，当前页面 {page}，开始切号')
                return True
            time.sleep(1.5)
        logger.warning(f'Rotation: 等 {timeout} 秒仍没识别出游戏界面，仍然尝试切号')
        return False

    def rotation_finish_round(self, member_total: int) -> None:
        """名单跑完一轮：指针归零，排到明天的服务器更新时间再来

        不循环重跑，否则第二轮开始小号体力都空了，队长会反复邀请失败空转。
        """
        rot = self._rotation_cfg
        members = parse_member_list(rot.member_list)
        rot.current_index = 0
        if members:
            rot.current_member = members[0]
            self.config.model.exploration.invite_config.friend_list = members[0]
        self.config.save()
        tomorrow = datetime.now().date() + timedelta(days=1)
        run_time = getattr(self._config.scheduler, 'server_update', None)
        if hasattr(run_time, 'hour'):
            target = datetime.combine(tomorrow, run_time)
        else:
            target = datetime.now().replace(microsecond=0) + timedelta(days=1)
        self._rotation_next_run = target
        logger.info(f'Rotation: {member_total} 个小号已跑完一轮，指针归零，排到 {target} 再来')

    def rotation_advance_leader(self, reason: str) -> None:
        """队长：本号配额到点，推进到下一个小号；名单走完则收工等明天"""
        rot = self._rotation_cfg
        members = parse_member_list(rot.member_list)
        if not members:
            logger.warning('Rotation: 小号名单为空，按普通结束处理')
            return
        index = normalize_index(rot.current_index, len(members))
        next_index = index + 1
        if next_index >= len(members):
            self.rotation_finish_round(len(members))
            return
        member = members[next_index]
        rot.current_index = next_index
        rot.current_member = member
        self.config.model.exploration.invite_config.friend_list = member
        wait = self.rotation_switch_wait()
        self._rotation_next_run = datetime.now().replace(microsecond=0) + wait
        # 把「上次开局时间」预支到下次启动那一刻：换人这轮已经等过 switch_wait，
        # 回来若再判一次开局等待就是白等一倍时间。
        rot.last_open_time = self._rotation_next_run.isoformat()
        self.config.save()
        reason_text = '战斗次数' if reason == 'count' else '时间'
        logger.info(
            f'Rotation: {reason_text}到点（已打 {self.current_count} 场），'
            f'换第 {next_index + 1}/{len(members)} 个小号 {member}，'
            f'{int(wait.total_seconds())} 秒后重新开局'
        )

    def rotation_poll_due(self) -> bool:
        """轮询节流：每 5 秒读一次队长配置，不要每帧都读盘"""
        if self._rotation_poll_timer is None:
            self._rotation_poll_timer = Timer(5)
            self._rotation_poll_timer.start()
            return True
        if self._rotation_poll_timer.reached():
            self._rotation_poll_timer.reset()
            return True
        return False

    def rotation_member_check(self) -> bool:
        """小号：队长点名换了人返回 True，本轮收尾后再切号"""
        if not self.rotation_enabled() or self.user_status != UserStatus.MEMBER:
            return False
        leader = (self._rotation_cfg.leader_config or '').strip()
        if not leader:
            if self._rotation_poll_timer is None:
                logger.warning('Rotation: 小号没填「队长所在配置」，无法跟随换人')
            return False
        if not self.rotation_poll_due():
            return False
        state = read_leader_state(leader)
        target = (state.get('member') or '').strip()
        if not target:
            logger.info(f'Rotation: 队长配置 {leader} 里还没有在带的小号，继续待命')
            return False
        rot = self._rotation_cfg
        assigned = (rot.assigned_member or '').strip()
        if not assigned:
            # 首次认领不能沿用当前登录的号：小号可能停在名单外的号上，而队长邀请的是
            # current_member，错位之后 assigned 又已写成目标名，就再也不会自愈。
            # 读不到「我现在是谁」的能力，所以无条件先切一次，宁可白切 40 秒。
            rot.assigned_member = target
            self.config.save()
            logger.info(f'Rotation: 小号首次认领指派 {target}，先切到该角色再待命')
            self._rotation_switch_target = target
            return True
        if assigned == target:
            return False
        logger.info(f'Rotation: 队长换人 {assigned} -> {target}，本轮结束后切号')
        self._rotation_switch_target = target
        return True

    def rotation_do_switch(self) -> bool:
        """小号：切到队长点名的角色（账号卡从本配置的切号名单里按角色名找）"""
        target_name = (self._rotation_switch_target or '').strip()
        if not target_name:
            return False
        account_switch = getattr(self.config, 'account_switch', None)
        accounts = []
        if account_switch is not None:
            accounts = [a for a in (account_switch.account_list or []) if a.is_valid()]
        target = next((a for a in accounts if a.character == target_name), None)
        if target is None:
            logger.warning(
                f'Rotation: 本配置的账号卡里没有 {target_name}，无法切号；'
                f'现有 {[a.character for a in accounts]}'
            )
            # 记为已认领，免得每轮都撞同一个错误刷屏
            self._rotation_cfg.assigned_member = target_name
            self.config.save()
            return False
        logger.hr(f'Rotation: 切号到 {target_name}', 2)
        from tasks.Component.SwitchAccount.switch_account import SwitchAccount
        ok = SwitchAccount(self.config, self.device, target).switchAccount()
        self._rotation_cfg.assigned_member = target_name
        if ok:
            for index, account in enumerate(accounts):
                if account.character == target_name:
                    account_switch.account_switch_config.current_index = index
                    break
            logger.info(f'Rotation: 已切到 {target_name}')
        else:
            logger.warning(f'Rotation: 切到 {target_name} 失败，下轮继续用当前号等队长邀请')
        self.config.save()
        return ok

    def check_exit(self, current_page: pages.Page | None) -> bool:
        # True 表示要退出这个任务
        # 开局等待：还没建房，直接收尾去排期，别先白请一次客
        if self._rotation_abort:
            return True
        # 小号启动即切号：别再往下跑到 activate_realm_raid 排突破
        if self._rotation_boot_switch:
            return True
        reason = self.rotation_quota_reason()
        # 小号在轮换模式下不按战斗次数/时间收工，改由「待命上限」管，否则队长还在打它就跑了
        if reason and self.rotation_enabled() and self.user_status == UserStatus.MEMBER:
            reason = ''
        # 队长：配额到点先换人（换不了就等同于普通结束）
        if reason and self.rotation_is_leader():
            self.rotation_advance_leader(reason)
            return True
        if reason == 'count':
            logger.info('Minions count is enough, exit')
            return True
        if reason == 'time':
            logger.info('Exploration time limit out, exit')
            return True
        if self.user_status == UserStatus.MEMBER:
            if self.rotation_enabled():
                if self.rotation_member_check():
                    return True
                if datetime.now() - self.wait_start_time >= self.rotation_member_idle_exit():
                    logger.info('Member idle time out, exit')
                    return True
            elif datetime.now() - self.wait_start_time >= self._config.invite_config.wait_time_v:
                logger.info('Member wait time out, exit')
                return True
        self.activate_realm_raid(self._config.scrolls, self._config.exploration_config, current_page)
        return False

    def fire(self, button) -> bool:
        """进入战斗(True:成功进入战斗/识别到退出弹窗, 否则False)
        这里之所以违反页面特性使用循环, 是因为由于怪物移动的原因可能导致一次点击会无法进入战斗,
        回到外循环之后由于UP旋转的特性可能导致识别不到怪物然后开始滑动, 导致错过的怪物更多
        因此这里使用贪心的思想, 只要识别到怪物一次就尽最大可能直接进入战斗, 保证尽可能有怪则打
        """
        max_tries = 4
        timeout_timer = Timer(10).start()  # 增加最大时间限制, 防止因未知因素引起无限等待
        while max_tries > 0 and not timeout_timer.reached():
            self.screenshot()
            cur_page = self.get_current_page()
            # 退出动画期间可能再次识别到怪物开始攻击, 因此取消退出
            if cur_page == pages.page_exp_exit:
                self.need_exit = False
                return True
            if cur_page in (pages.page_battle_prepare, pages.page_battle):
                return True
            if self.appear_then_click(button, interval=0.8):
                max_tries -= 1
                continue
        return False

    def switch_rotate(self) -> bool:
        """切换轮换类型并添加式神 True(执行了切换)/False"""
        match self._config.exploration_config.auto_rotate:
            case AutoRotate.yes:
                if self.appear(self.I_E_AUTO_ROTATE_OFF):  # 轮换关闭/式神不够了则需要打开并添加式神
                    self.click(self.C_CLICK_SETTINGS, interval=2)
                    return True
            case AutoRotate.no:  # 不是自动添加候补式神则关闭轮换
                if self.appear_then_click(self.I_E_AUTO_ROTATE_ON, interval=0.8):
                    return True
        return False

    def arrive_end(self) -> bool:
        """是否到达探索的最后方, 需要先调用截图(滑动超过6次直接判定已经到达底部)"""
        if self.device.click_record.count(self.S_SWIPE_BACKGROUND_RIGHT.name) >= 6:
            self.device.click_record_clear()
            return True
        return self._match_end.stable(self.device.image, refresh_after_stable=True, frame_id=self.device.image_frame_id)

    def get_fire_button(self) -> Optional[RuleImage | RuleGif]:
        """获取需要攻击的按钮"""
        if self.appear(self.I_BOSS_BATTLE_BUTTON):
            self.fire_monster_type = 'boss'
            return self.I_BOSS_BATTLE_BUTTON
        return self.search_up_fight()

    def collect_treasure_box(self) -> bool:
        """收集宝箱奖励"""
        if self.appear(self.I_E_REWARD_BOX_SMALL):  # 小宝箱
            logger.info('Treasure box small appear, get it.')
            self.ui_click(self.I_E_REWARD_BOX_SMALL, self.I_REWARD, interval=0.8)
            self.ui_click_until_disappear(self.I_REWARD, interval=0.8)
            return True
        if self.appear(self.I_E_REWARD_BOX_BIG):  # 大宝箱
            logger.info('Treasure box big appear, get it.')
            self.ui_click(self.I_E_REWARD_BOX_BIG, self.I_REWARD, interval=0.8)
            self.ui_click_until_disappear(self.I_REWARD, interval=0.8)
            return True
        return False

    def collect_paper_man_reward(self) -> bool:
        """收集小纸人奖励, 若未开启则自动退出"""
        # 已经打过boss了且设置了不收集小纸人奖励则直接返回
        if self.fire_monster_type == 'boss' and not self._config.exploration_config.collect_paper_reward:
            logger.info("Not collect paper doll reward")
            self.quit_exp_main()
            return True
        # 没打boss或者收集纸人奖励, 且出现了纸人则处理掉落奖励
        if self.appear(self.I_BATTLE_REWARD) and self._config.exploration_config.collect_paper_reward:
            self.ui_get_reward(self.I_BATTLE_REWARD)
            self.wait_start_time = datetime.now()  # 队友等待时间重置
            return True
        return False

    def quit_exp_main(self):
        """退出探索主界面(要求当前必须处于探索主界面, 不保证任何后续结果)"""
        self.need_exit = True
        self.appear_then_click(self.I_UI_BACK_YELLOW, interval=0.8)
        self.wait_start_time = datetime.now()  # 队友等待时间重置

    def collect_reward(self) -> bool:
        """处理掉落奖励(True表示进行了操作, False表示没有操作)"""
        return self.collect_treasure_box() or self.collect_paper_man_reward()

    def enter_team(self) -> bool:
        """进入战斗组队页面"""
        return self.create_room(self.I_EXP_CREATE_TEAM) and self.ensure_private() and self.create_ensure()

if __name__ == "__main__":
    from module.config.config import Config
    from module.device.device import Device

    config = Config('绘卷oas2')
    device = Device(config)
    t = BaseExploration(config, device)
    t.screenshot()
    t.fill_shikigami()
