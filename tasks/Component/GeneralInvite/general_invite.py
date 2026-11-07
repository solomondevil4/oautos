# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from time import sleep
import numpy as np

from enum import Enum
from cached_property import cached_property
from datetime import timedelta, time
from module.atom.image import RuleImage

from module.base.timer import Timer
from tasks.GameUi.assets import GameUiAssets
from tasks.base_task import BaseTask
from tasks.Component.GeneralInvite.assets import GeneralInviteAssets
from tasks.Component.GeneralInvite.config_invite import InviteConfig, FindMode
from tasks.Component.GeneralBattle.assets import GeneralBattleAssets
from module.logger import logger


class FriendList(str, Enum):
    RECENT_FRIEND = 'recent_friend'
    GUILD_FRIEND = 'guild_friend'
    FRIEND = 'friend'
    OTHER_FRIEND = 'other_friend'


class RoomType(str, Enum):
    # 房间只可以两个人的： 探索
    NORMAL_2 = 'normal_2'
    # 房间可以两三个人的： 觉醒、御魂、日轮、石距（石距是单次没有锁定阵容）
    NORMAL_3 = 'normal_3'
    # 永生之海不一样
    ETERNITY_SEA = 'eternity_sea'
    # 经验妖怪和金币妖怪
    NORMAL_5 = 'normal_5'
    # 契灵之境
    BONDLING_FAIRYLAND = 'bondling_fairyland'


class GeneralInvite(BaseTask, GeneralInviteAssets):
    timer_invite = None
    timer_wait = None
    timer_emoji = None  # 等待期间如果没有操作的话，可能会导致长时间无响应报错

    def run_invite(self, config: InviteConfig, is_first: bool = False) -> bool:
        """
        队长！！身份。。。在组队界面邀请好友（ 如果开启is_first） 等待队员进入开启挑战
        请注意，返回的时候成功时是进入战斗了！！！
        如果是失败，那就是没有队友进入，然后会退出房间的界面
        :param config:
        :param is_first: 如果是第一次开房间的那就要邀请队员，其他情况等待队员进入
        :return:
        """
        if not self.ensure_enter():
            logger.warning('Not enter invite page')
            return False
        if is_first:
            _ = self.room_type
            self.timer_invite = Timer(20)
            self.timer_invite.start()
            self.ensure_room_type(len(config.friend_list.split('\n')))
            self.invite_friends(config)
        else:
            self.timer_invite = Timer(30)
            self.timer_invite.start()
            self.timer_emoji = Timer(20)
            self.timer_emoji.start()
        wait_second = config.wait_time.second + config.wait_time.minute * 60
        self.timer_wait = Timer(wait_second)
        self.timer_wait.start()
        while 1:
            self.screenshot()
            if self.timer_wait.reached():
                logger.warning('Wait timeout')
                return False
            if self.appear(self.I_MATCHING):
                logger.warning('Timeout, now is no room')
                return False

            if not self.is_in_room():
                continue

            if self.timer_emoji and self.timer_emoji.reached():
                self.timer_emoji.reset()
                self.appear_then_click(self.I_GI_EMOJI_1)
                self.appear_then_click(self.I_GI_EMOJI_2)

            # 点击挑战
            if self.room_check_can_fire(config):
                self.click_fire()
                return True
            if self.timer_invite and self.timer_invite.reached():
                if is_first:
                    logger.info('Invitation is triggered every 20s')
                    self.timer_invite.reset()
                else:
                    logger.info('Wait for 30s and invite again')
                    self.timer_invite = None
                self.invite_friends(config)
        return False

    def room_check_can_fire(self, config: InviteConfig) -> bool:
        fire = False  # 是否开启挑战
        # 如果这个房间最多只容纳两个人（意思是只可以邀请一个人），且已经邀请一个人了，那就开启挑战
        if self.room_type == RoomType.NORMAL_2 and not self.appear(self.I_ADD_2):
            logger.info('Start challenge and this room can only invite one friend')
            fire = True
        # 如果这个房间最多容纳三个人（意思是可以邀请两个人），且设定邀请一个就开启挑战，那就开启挑战
        elif self.room_type == RoomType.NORMAL_3 and len(config.friend_list_v) == 1 and not self.appear(self.I_ADD_1):
            logger.info('Start challenge and user only invite one friend')
            fire = True
        # 如果这个房间最多容纳三个人（意思是可以邀请两个人），且设定邀请两个就开启挑战，那就开启挑战
        elif self.room_type == RoomType.NORMAL_3 and len(config.friend_list_v) == 2 and not self.appear(self.I_ADD_2):
            logger.info('Start challenge and user invite two friends')
            fire = True
        # 如果这个房间是五人的，且设定邀请一个就开启挑战，那就开启挑战
        elif self.room_type == RoomType.NORMAL_5 and len(config.friend_list_v) == 1 and not self.appear(self.I_ADD_5_1):
            logger.info('Start challenge and user only invite one friend')
            fire = True
        # 如果这个房间是五人的，且设定邀请两个就开启挑战，那就开启挑战
        elif self.room_type == RoomType.NORMAL_5 and len(config.friend_list_v) == 2 and not self.appear(self.I_ADD_5_2):
            logger.info('Start challenge and user invite two friends')
            fire = True
        # 如果是永生之海
        elif self.room_type == RoomType.ETERNITY_SEA and not self.appear(self.I_ADD_SEA):
            logger.info('Start challenge and this is lock sea')
            fire = True
        # 契灵之境(两人间但队友在一号位)
        elif self.room_type == RoomType.BONDLING_FAIRYLAND and not self.appear(self.I_ADD_1):
            logger.info('Start challenge and this is bondling fairyland')
            fire = True
        return fire

    def invite_friends(self, config: InviteConfig, open_invite: bool = True, confirm_rule: RuleImage = None) -> bool:
        """
        邀请多个好友
        :param confirm_rule: 确认规则(邀请时的点击按钮：邀请/分享/...)
        :param config: 邀请配置
        :param open_invite: 是否需要在本方法内打开邀请界面
        :return: 邀请是否成功
        """
        logger.hr('Invite friends', 2)
        if not config.friend_list_v:
            logger.warning('No friend to invite')
            return False
        logger.info(f'Need invite friend list: {config.friend_list_v}')
        if not self._open_invite_panel_if_needed(open_invite):
            return True
        friend_class = self._read_friend_classes()
        selected_set: set[str] = set()
        scroll = bool(getattr(config, 'scroll_find_enable', False))
        if scroll:
            logger.info('Invite: 已开启下滑搜索好友')
        match config.find_mode:
            case FindMode.RECENT_FRIEND:
                self._select_recent_mode_friends(friend_class, config.friend_list_v, selected_set, scroll)
            case FindMode.AUTO_FIND:
                self._select_auto_mode_friends(friend_class, config.friend_list_v, selected_set, scroll)
        result = self._confirm_invite_and_validate(selected_set, config.friend_list_v, confirm_rule)
        if not result and getattr(config, 'notify_missing_enable', False):
            missing = [name for name in config.friend_list_v if name not in selected_set]
            self._notify_missing_friends(missing, config.friend_list_v)
        return result

    def ensure_enter(self) -> bool:
        """
        确认是否进入了组队界面
        :return:
        """
        logger.info('Ensure enter invite page')
        while 1:
            self.screenshot()
            if self.appear(self.I_ADD_2):
                return True
            if self.appear(self.I_ADD_5_4):
                return True
            if self.appear(self.I_LOCK_SEA):
                return True
            if self.appear(self.I_UNLOCK_SEA):
                return True
            # 修复三人组队卡住bug，#78
            # 增加左上角协战房间判断，存在就说明在组队界面
            if self.appear(self.I_GI_IN_ROOM):
                return True
            if self.appear(self.I_MATCHING):
                return False

    # 判断是否在房间里面
    def is_in_room(self, is_screenshot: bool = True) -> bool:
        """
        判断是否在房间里面
        :return:
        """
        if is_screenshot:
            self.screenshot()
        if self.appear(GeneralInviteAssets.I_GI_EMOJI_1):
            return True
        if self.appear(GeneralInviteAssets.I_GI_EMOJI_2):
            return True
        return False

    def exit_room(self) -> bool:
        """
        退出房间
        :return:
        """
        if not self.is_in_room():
            return True
        logger.info('Exit room')
        timeout_timer = Timer(5).start()
        while True:
            self.screenshot()
            if timeout_timer.reached():
                break
            if not self.is_in_room() and \
                    not self.appear_then_click(GeneralInviteAssets.I_GI_SURE, interval=0.8) and \
                    not self.appear(self.I_BACK_YELLOW):
                return True
            if self.appear_then_click(GeneralInviteAssets.I_GI_SURE, interval=0.5):
                continue
            if not self.appear(GeneralInviteAssets.I_GI_SURE) and self.appear_then_click(self.I_BACK_YELLOW, interval=0.8):
                self.wait_until_appear(GeneralInviteAssets.I_GI_SURE, wait_time=0.8)
                continue
            if not self.appear(GeneralInviteAssets.I_GI_SURE) and self.appear_then_click(self.I_BACK_YELLOW_SEA, interval=0.8):
                self.wait_until_appear(GeneralInviteAssets.I_GI_SURE, wait_time=0.8)
                continue
        return False

    def click_fire(self):
        while 1:
            self.screenshot()
            if not self.is_in_room(False):
                break
            if self.appear_then_click(self.I_FIRE, interval=1, threshold=0.7):
                continue
            if self.appear_then_click(self.I_FIRE_SEA, interval=1, threshold=0.7):
                continue

    @cached_property
    def room_type(self) -> RoomType:
        """
        只需要在队长进入的时候判断一次就可以了，任务后面之间使用

        :return:
        """
        self.screenshot()
        room_type = self.check_room_type(image=self.device.image)
        logger.info(f'Room type: {room_type}')
        return room_type

    def check_room_type(self, image: np.array = None, pre_type: RoomType = None) -> RoomType | None:
        """
        检查房间类型
        :param image:
        :param pre_type: 可以先指定这个类型，如果不指定，就自动检查
        :return:
        """

        def check_3(img) -> bool:
            appear = False
            if self.I_ADD_1.match(img) and self.I_ADD_2.match(img):
                appear = True
            return appear

        def check_2(img) -> bool:
            appear = False
            if not self.I_ADD_1.match(img) and self.I_ADD_2.match(img):
                appear = True
            return appear

        def check_5(img) -> bool:
            appear = False
            if self.I_ADD_5_1.match(img) and self.I_ADD_5_2.match(img) \
                    and self.I_ADD_5_3.match(img) and self.I_ADD_5_4.match(img):
                appear = True
            return appear

        def check_eternity_sea(img) -> bool:
            appear = False
            if self.I_LOCK_SEA.match(img) or self.I_UNLOCK_SEA.match(img):
                appear = True
            return appear

        def check_bondling_fairyland(img) -> bool:
            return self.I_ADD_1.match(img) and not self.I_ADD_2.match(img)

        room_type = None
        if pre_type is not None:
            match pre_type:
                case RoomType.NORMAL_2:
                    room_type = RoomType.NORMAL_2 if check_2(image) else None
                case RoomType.NORMAL_3:
                    room_type = RoomType.NORMAL_3 if check_3(image) else None
                case RoomType.NORMAL_5:
                    room_type = RoomType.NORMAL_5 if check_5(image) else None
                case RoomType.ETERNITY_SEA:
                    room_type = RoomType.ETERNITY_SEA if check_eternity_sea(image) else None
        if room_type:
            return room_type
        if room_type is None and check_2(image):
            room_type = RoomType.NORMAL_2
            return room_type
        if room_type is None and check_3(image):
            room_type = RoomType.NORMAL_3
            return room_type
        if room_type is None and check_5(image):
            room_type = RoomType.NORMAL_5
            return room_type
        if room_type is None and check_eternity_sea(image):
            room_type = RoomType.ETERNITY_SEA
            return room_type
        if room_type is None and check_bondling_fairyland(image):
            room_type = RoomType.BONDLING_FAIRYLAND
            return room_type
        return room_type

    def ensure_room_type(self, friend_number: int = None) -> bool:
        """
        确认设定的邀请人数是否会超出房间的最大
        :param friend_number: 这个输入的是用户选项中的invite_number
        :return:  如果超出了，就返回False
        """
        if friend_number == 2:
            if self.room_type == RoomType.NORMAL_2:
                # 整个房间就可以两个人，还邀请两个 这个是报错的
                logger.error('Room can only be one people, but invite two people')
                return False
            elif self.room_type == RoomType.ETERNITY_SEA:
                # 永生之海，只能邀请一个人
                logger.error('Room can only be one people, but invite two people')
                return False
            return True
        return True

    @cached_property
    def friend_class(self) -> list[str]:
        return ['好友', '最近', '跨区', '寮友', '蔡友', '路区', '察友', '区']

    @staticmethod
    def _normalize_friend_name_text(text: str) -> str:
        if text is None:
            return ''
        return str(text).replace(' ', '').replace('　', '').strip()

    def _find_exact_friend_area(self, rule, name: str) -> tuple[int, int, int, int] | None:
        target_name = self._normalize_friend_name_text(name)
        if not target_name:
            return None

        boxed_results = rule.detect_and_ocr(self.device.image)
        if not boxed_results:
            return None

        for result in boxed_results:
            ocr_text = self._normalize_friend_name_text(result.ocr_text)
            if ocr_text != target_name:
                continue
            box = result.box
            rec_x = box[0, 0]
            rec_y = box[0, 1]
            rec_w = box[1, 0] - box[0, 0]
            rec_h = box[2, 1] - box[0, 1]
            area = (
                int(rec_x + rule.roi[0]),
                int(rec_y + rule.roi[1]),
                int(rec_w),
                int(rec_h)
            )
            logger.info(f'Exact match friend "{name}" in {rule.name} at {area}')
            return area
        return None

    @staticmethod
    def _random_point_in_area(area: tuple[int, int, int, int]) -> tuple[int, int]:
        x, y, w, h = area
        w = max(1, int(w))
        h = max(1, int(h))
        if w == 1:
            click_x = x
        else:
            click_x = int(np.random.randint(x, x + w))
        if h == 1:
            click_y = y
        else:
            click_y = int(np.random.randint(y, y + h))
        return click_x, click_y

    def _wait_selected_appear(self, pre_cnt: int, timeout: float = 2) -> bool:
        """
        点击后等待选中动画结束。要求连续两帧都识别到选中，避免单帧滞后误判。
        """
        timer = Timer(timeout).start()
        selected_count = 0
        while not timer.reached():
            self.screenshot()
            if len(self.I_SELECTED.match_all_any(self.device.image, frame_id=self.device.image_frame_id)) >= pre_cnt + 1:
                selected_count += 1
                if selected_count >= 2:
                    return True
            else:
                selected_count = 0
        return False

    def _detect_select(self, name: str = None) -> bool:
        """
        在当前的页面检测是否有好友， 如果有就选中这个好友
        :return: 是否成功选中好友
        """
        if not name:
            return False
        max_retry = 3
        self.screenshot()
        pre_cnt = len(self.I_SELECTED.match_all_any(self.device.image, frame_id=self.device.image_frame_id))
        for _ in range(max_retry):
            self.screenshot()
            if len(self.I_SELECTED.match_all_any(self.device.image, frame_id=self.device.image_frame_id)) >= pre_cnt + 1:
                return True
            rule = self.O_FRIEND_NAME_1
            select_area = self._find_exact_friend_area(rule, name)
            if select_area is None:
                rule = self.O_FRIEND_NAME_2
                select_area = self._find_exact_friend_area(rule, name)
            if select_area is None:
                logger.info('Current page no exact friend')
                return False
            click_x, click_y = self._random_point_in_area(select_area)
            self.device.click(x=click_x, y=click_y, control_name=rule.name)
            if self._wait_selected_appear(pre_cnt):
                return True
        logger.warning(f'Find friend "{name}" but failed to select')
        return False

    def _get_invite_friend_list(self, config: InviteConfig) -> list[str]:
        """
        获取邀请名单。
        :param config: 邀请配置
        :return: 由配置拆分得到的好友名称列表
        """
        return config.friend_list.split('\n')

    def _open_invite_panel_if_needed(self, open_invite: bool) -> bool:
        """
        按需打开邀请面板。
        :param open_invite: 是否需要在当前流程中主动打开邀请面板
        :return: True 表示继续邀请流程，False 表示直接提前结束（保持当前兼容语义）
        """
        if not open_invite:
            return True
        logger.info('Click add to invite friend')
        no_click_timeout = Timer(5).start()
        click_timer = Timer(1)
        while True:
            self.screenshot()
            if no_click_timeout.started() and no_click_timeout.reached():
                logger.warning('Cannot invite friend, maybe already existing')
                return False
            if self.appear(self.I_LOAD_FRIEND) or self.appear(self.I_INVITE_ENSURE):
                return True
            if not click_timer.started() or click_timer.reached():
                clicked = self.appear_then_click(self.I_ADD_1) or \
                    self.appear_then_click(self.I_ADD_2) or \
                    self.appear_then_click(self.I_ADD_5_4) or \
                    self.appear_then_click(self.I_ADD_SEA)
                click_timer.reset()
                if clicked:
                    no_click_timeout.reset()

    @staticmethod
    def _normalize_friend_class_name(friend_class: str) -> str:
        """
        归一化好友分类文字，消除 OCR 近形字干扰。
        :param friend_class: OCR 识别到的分类文本
        :return: 归一化后的分类文本
        """
        mapping = {'蔡友': '寮友', '路区': '跨区', '察友': '寮友', '区': '跨区'}
        return mapping.get(friend_class, friend_class)

    def _read_friend_classes(self) -> list[str]:
        """
        识别当前可用的好友分类页签。
        :return: 分类列表，顺序与页签顺序一致
        """
        raw_list = [
            self.O_F_LIST_1.ocr(self.device.image).replace(' ', '').replace('、', ''),
            self.O_F_LIST_2.ocr(self.device.image).replace(' ', '').replace('、', ''),
            self.O_F_LIST_3.ocr(self.device.image).replace(' ', '').replace('、', ''),
            self.O_F_LIST_4.ocr(self.device.image).replace(' ', '').replace('、', '')
        ]
        friend_class = []
        for item in raw_list:
            if item is not None and item != '' and item in self.friend_class:
                friend_class.append(self._normalize_friend_class_name(item))
        logger.info(f'Friend class: {friend_class}')
        return friend_class

    def _switch_friend_class(self, index: int) -> None:
        """
        切换到指定索引的好友分类标签。
        :param index: 分类索引，0~3
        :return:
        """
        match index:
            case 0:
                self.ui_click(self.I_FLAG_1_OFF, self.I_FLAG_1_ON, interval=1.2)
            case 1:
                self.ui_click(self.I_FLAG_2_OFF, self.I_FLAG_2_ON, interval=1.2)
            case 2:
                self.ui_click(self.I_FLAG_3_OFF, self.I_FLAG_3_ON, interval=1.2)
            case 3:
                self.ui_click(self.I_FLAG_4_OFF, self.I_FLAG_4_ON, interval=1.2)

    # 邀请面板滚动参数（11-07 真机用 OAS 自身 swipe 标定，不能用 adb 探针）
    # 面板布局：两列各 4 行、行高约 86px，一屏 8 个好友；两列之间 x≈623~729 是空白走廊。
    # 实测 200px 位移约滚 3.5 行（逼近「滚动行数 ≥ 可视行数就会漏行」的门槛），
    # 120px 约滚 2 行，故取 150px 留安全余量。
    INVITE_SCROLL_X = 670
    INVITE_SCROLL_FROM_Y = 470
    INVITE_SCROLL_DIST = 150
    INVITE_SCROLL_MAX = 10
    INVITE_SCROLL_TOP_TIMES = 4

    def _scroll_friend_list(self, back: bool = False) -> None:
        """
        在邀请面板滚动一屏。
        :param back: False 列表向下翻（手指上滑），True 回到顶部（手指下滑）
        """
        x = self.INVITE_SCROLL_X
        y_from = self.INVITE_SCROLL_FROM_Y
        if back:
            self.device.swipe((x, y_from - self.INVITE_SCROLL_DIST), (x, y_from),
                              duration=0.25, control_name='GI_SCROLL_FRIEND_BACK')
        else:
            self.device.swipe((x, y_from), (x, y_from - self.INVITE_SCROLL_DIST),
                              duration=0.25, control_name='GI_SCROLL_FRIEND')

    def _scroll_friend_list_to_top(self) -> None:
        """面板会保留上次的滚动位置，邀请前先盲滑回顶，否则会漏掉排在前面的好友"""
        for _ in range(self.INVITE_SCROLL_TOP_TIMES):
            self._scroll_friend_list(back=True)
        sleep(0.5)
        self.device.click_record_clear()
        logger.info('Invite: 好友列表已回顶')

    def _friend_list_snapshot(self) -> str:
        """两列 OCR 文本拼成快照，用于判断「滑不动了＝到底」"""
        self.screenshot()
        names = []
        for rule in (self.O_FRIEND_NAME_1, self.O_FRIEND_NAME_2):
            for result in rule.detect_and_ocr(self.device.image) or []:
                name = self._normalize_friend_name_text(result.ocr_text)
                if name:
                    names.append(name)
        return '|'.join(sorted(names))

    def _pick_visible_friends(self, friend_list: list[str], selected_set: set[str]) -> None:
        """一次截图读两列所有名字，只对命中的名字点击，避免每个名字都重复 OCR"""
        self.screenshot()
        visible = set()
        for rule in (self.O_FRIEND_NAME_1, self.O_FRIEND_NAME_2):
            for result in rule.detect_and_ocr(self.device.image) or []:
                name = self._normalize_friend_name_text(result.ocr_text)
                if name:
                    visible.add(name)
        for name in friend_list:
            if name in selected_set:
                continue
            target = self._normalize_friend_name_text(name)
            if target and target in visible:
                if self._detect_select(name):
                    selected_set.add(name)

    def _select_current_page_friends(self, friend_list: list[str], selected_set: set[str],
                                     scroll: bool = False) -> None:
        """
        在当前分类页中尝试选择多个好友。
        :param friend_list: 目标好友名称列表
        :param selected_set: 已成功选中的好友集合（原地更新）
        :param scroll: 当前页找不到时是否下滑继续找（实测滚动 + 到底判定）
        :return:
        """
        if not scroll:
            for name in friend_list:
                if name not in selected_set and self._detect_select(name):
                    selected_set.add(name)
            return
        # 先在当前屏找：面板保留上次的滚动位置，目标好友常常就在眼前，
        # 这种情况不必先盲滑 4 次回顶（每次滑动 + 停顿约 1 秒）
        self._pick_visible_friends(friend_list, selected_set)
        if len(selected_set) == len(friend_list):
            logger.info(f'Invite: 当前屏已找全 {len(friend_list)} 个好友，跳过回顶')
            return
        # 没找全才回顶从头扫，避免漏掉排在前面的好友
        self._scroll_friend_list_to_top()
        self._pick_visible_friends(friend_list, selected_set)
        if len(selected_set) == len(friend_list):
            logger.info(f'Invite: 回顶后已找全 {len(friend_list)} 个好友，无需下滑')
            return
        previous = self._friend_list_snapshot()
        for index in range(self.INVITE_SCROLL_MAX):
            if len(selected_set) == len(friend_list):
                logger.info(f'Invite: 下滑 {index} 次后已找全 {len(friend_list)} 个好友')
                return
            self._scroll_friend_list()
            sleep(0.8)
            current = self._friend_list_snapshot()
            if current == previous:
                logger.info(f'Invite: 好友列表已到底（第 {index + 1} 次滑动无变化），停止下滑')
                return
            previous = current
            logger.info(f'Invite: 下滑第 {index + 1} 次，继续查找')
            self._pick_visible_friends(friend_list, selected_set)
        logger.warning(f'Invite: 下滑 {self.INVITE_SCROLL_MAX} 次仍未找全，停止下滑')

    def _notify_missing_friends(self, missing: list[str], friend_list: list[str]) -> None:
        """邀请名单里有找不到的好友时推送邮件"""
        if not missing:
            return
        logger.warning(f'Invite: 好友列表里没找到 {missing}（完整名单 {friend_list}）')
        try:
            self.config.notifier.push(
                title='组队邀请失败：好友未找到',
                content='配置：{}\n未找到：{}\n完整名单：{}'.format(
                    self.config.config_name,
                    '、'.join(missing),
                    '、'.join(friend_list),
                ),
            )
            logger.info('Invite: 已推送「好友未找到」邮件')
        except Exception as e:
            logger.warning(f'Invite: 推送邮件失败 {e}')

    def _select_recent_mode_friends(self, friend_class: list[str], friend_list: list[str],
                                    selected_set: set[str], scroll: bool = False) -> bool:
        """
        在“最近”分类下执行好友选择。
        :param friend_class: 当前可用分类列表
        :param friend_list: 目标好友名称列表
        :param selected_set: 已成功选中的好友集合（原地更新）
        :param scroll: 当前页找不到时是否下滑继续找
        :return: True 表示流程可继续，False 表示失败
        """
        logger.info('Find recent friend')
        if '最近' not in friend_class:
            logger.warning('No recent friend')
            return False
        recent_index = friend_class.index('最近')
        self._switch_friend_class(recent_index)
        sleep(0.5)
        logger.info('Now find friend in ”最近“')
        self._select_current_page_friends(friend_list, selected_set, scroll)
        return True

    def _select_auto_mode_friends(self, friend_class: list[str], friend_list: list[str],
                                  selected_set: set[str], scroll: bool = False) -> None:
        """
        自动遍历所有分类并尝试选择目标好友。
        :param friend_class: 当前可用分类列表
        :param friend_list: 目标好友名称列表
        :param selected_set: 已成功选中的好友集合（原地更新）
        :param scroll: 当前页找不到时是否下滑继续找
        :return:
        """
        for index in range(len(friend_class)):
            if len(selected_set) == len(friend_list):
                break
            self._switch_friend_class(index)
            sleep(0.5)
            logger.info(f'Now find friend in {friend_class[index]}')
            self._select_current_page_friends(friend_list, selected_set, scroll)

    def _confirm_invite_and_validate(self, selected_set: set[str], friend_list: list[str], confirm_rule: RuleImage = None) -> bool:
        """
        点击邀请确认并校验最终结果。
        :param selected_set: 已成功选中的好友集合
        :param friend_list: 目标好友名称列表
        :return: 全部选中返回 True，否则 False
        """
        logger.info('Click invite ensure')
        if not confirm_rule:
            confirm_rule = self.I_INVITE_ENSURE
        if not self.appear(confirm_rule):
            logger.warning('No appear invite ensure while invite friend')
        self.ui_click_until_disappear(confirm_rule)
        if len(selected_set) != len(friend_list):
            logger.warning('Cannot find friend')
            return False
        return True

    def invite_again(self, default_invite: bool=True) -> bool:
        """
        作为队长战斗胜利后再次邀请队友，
        :param default_invite:  是否勾选默认
        :return:
        """
        logger.info('Invite again')
        # 判断是否进入界面
        while 1:
            self.screenshot()
            if self.appear(self.I_GI_SURE):
                break
        # 如果勾选了默认邀请
        if default_invite:
            logger.info('Click default invite')
            while 1:
                self.screenshot()
                if self.appear(self.I_I_DEFAULT):
                    break
                if self.appear_then_click(self.I_I_NO_DEFAULT, interval=1):
                    continue
        else:
            logger.info('Click no default invite')
            while 1:
                self.screenshot()
                if self.appear(self.I_I_NO_DEFAULT):
                    break
                if self.appear_then_click(self.I_I_DEFAULT, interval=1):
                    continue

        # 点击确认
        logger.info('Click invite ensure')
        while 1:
            self.screenshot()
            if not self.appear(self.I_GI_SURE):
                break
            if self.appear_then_click(self.I_GI_SURE):
                continue

    def check_and_invite(self, default_invite: bool=True) -> bool:
        """
        队长战斗后 邀请队友
        :param default_invite:
        :return:
        """
        if not self.appear(self.I_GI_SURE):
            return False

        if default_invite:
            # 有可能是挑战失败的
            if self.appear(self.I_I_DEFAULT) or self.appear(self.I_I_NO_DEFAULT):
                logger.info('Click default invite')
                while 1:
                    self.screenshot()
                    if self.is_in_room(False):
                        break
                    if self.appear(self.I_I_DEFAULT):
                        break
                    if self.appear_then_click(self.I_I_NO_DEFAULT, interval=1):
                        continue
        # 点击确认
        while 1:
            self.screenshot()
            if self.is_in_room(False):
                break
            if not self.appear(self.I_GI_SURE):
                break
            if self.appear_then_click(self.I_GI_SURE, interval=1):
                continue

        return True

    def check_then_accept(self) -> bool:
        """
        队员接受邀请
        :return:
        """
        if not self.appear_accept():
            return False
        logger.info('Click accept')
        while True:
            self.screenshot()
            if self.is_in_room():
                return True
            # 被秒开
            # https://github.com/runhey/OnmyojiAutoScript/issues/230
            if self.appear(GeneralBattleAssets.I_EXIT):
                return False
            if self.appear_then_click(self.I_I_NO_DEFAULT, interval=1):
                continue
            if self.appear_then_click(self.I_GI_SURE, interval=1):
                continue
            if self.appear_then_click(self.I_I_ACCEPT_DEFAULT, interval=1):
                continue
            if self.appear_then_click(self.I_I_ACCEPT, interval=1) or \
                    self.appear_then_click(self.I_I_ACCEPT_APPRENTICE, interval=1):
                continue
        return True

    def appear_accept(self) -> bool:
        """出现邀请标志"""
        return self.appear(self.I_I_ACCEPT) or self.appear(self.I_I_ACCEPT_APPRENTICE)

    def wait_battle(self, wait_time: time) -> bool:
        """
        在房间等待,(要求保证在房间里面) 队长开启战斗
        如果队长跑路了，或者的等待了很久还没开始
        :return: 如果成功进入战斗（反正就是不在房间 ）返回 True
                 如果失败了，（退出房间）返回 False
        """
        self.timer_emoji = Timer(15)
        self.timer_emoji.start()
        wait_second = wait_time.second + wait_time.minute * 60
        self.timer_wait = Timer(wait_second)
        self.timer_wait.start()
        logger.info(f'Wait battle {wait_second} seconds')
        success = True
        while 1:
            self.screenshot()

            # 如果自己在探索界面或者是庭院，那就是房间已经被销毁了
            if self.appear(GameUiAssets.I_CHECK_MAIN) or self.appear(GameUiAssets.I_CHECK_EXPLORATION):
                logger.warning('Room destroyed')
                success = False
                break

            if self.timer_wait.reached():
                logger.warning('Wait battle time out')
                success = False
                break

            # 如果队长跑路了，自己变成了队长: 自己也要跑路
            if self.appear(self.I_FIRE) or self.appear(self.I_FIRE_SEA):
                logger.warning('Leader run away while wait battle and become leader now')
                success = False
                break

            # 判断是否进入战斗
            if self.is_in_room(is_screenshot=False):
                if self.timer_emoji.reached():
                    self.timer_emoji.reset()
                    self.appear_then_click(self.I_GI_EMOJI_1)
                    self.appear_then_click(self.I_GI_EMOJI_2)
            else:
                break

        # 调出循环只有这些可能性：
        # 1. 进入战斗（ui是战斗）
        # 2. 队长跑路（自己还是在房间里面）
        # 3. 等待时间到没有开始（还是在房间里面）
        # 4. 房间的时间到了被迫提出房间（这个时候来到了探索界面）
        if not success:
            logger.info('Leave room')
            self.exit_room()

        return success
