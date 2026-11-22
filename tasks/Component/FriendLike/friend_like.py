# This Python file uses the following encoding: utf-8
"""好友界面「给指定好友点赞」公共组件。

从 tasks/AssistBattle/script_task.py 原样搬出（11-22，用户要求一键协战与百鬼夜行共用
同一份实现），逻辑一行没改，只做了三处适配：

  1. 名单来源从 `self.conf.daily_config.friend_like_names_v` 改成子类提供的
     `friend_like_names_v` 属性 —— 百鬼夜行的配置树上没有 daily_config。
  2. 单份名单上限 200 人、滚动上限 20 -> 100、单组超时 60s -> 900s，
     并把握列表的滑动从 default 快速甩动改成 drag 按住慢拖，
     解决「只能扫到约 1/3 好友、必须置顶才点得到」的老问题（用户 11-22 要求）。
     11-22 真机实测(16384，同区 199 人)：default 因惯性一次跨 3~4 屏，
     中间约 11 行好友从不出现在任何一帧里，12 次「到底」只读到 74 条文本；
     改 drag 后 61 次到底、相邻两屏重叠平均 3.2 条、去重读到 152 条。
  3. 素材改为 FriendLikeAssets 自带一份定义（png 仍指向原有文件，不复制图片），
     因为百鬼夜行不继承 AssistBattleAssets。

宿主类必须能提供：screenshot / appear / appear_then_click / click / swipe / device /
goto_page / get_current_page，以及 `friend_like_names_v` 属性。
"""
import numpy as np
from difflib import SequenceMatcher
from time import sleep

from module.base.timer import Timer
from module.exception import (
    GamePageUnknownError,
    GameStuckError,
    GameTooManyClickError,
    RequestHumanTakeover,
)
from module.logger import logger
from tasks.Component.FriendLike.assets import FriendLikeAssets
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.page import page_friends, page_main


class FriendLike(FriendLikeAssets):
    """给指定好友点赞：进好友页 → 同区扫完 → 切跨区扫完 → 没找到就放弃。

    公开入口只有 `run_friend_like()`；其余方法都是它的内部步骤。
    """

    @property
    def friend_like_names_v(self) -> list[str]:
        """要点赞的好友名列表，由宿主的配置给。

        子类必须覆盖；这里故意抛 NotImplementedError 而不是返回空列表 ——
        返回空列表会安静地「一个都不点」，排查起来要翻日志才知道是配置没接上。
        """
        raise NotImplementedError(
            f'{type(self).__name__} 必须实现 friend_like_names_v 才能使用点赞功能'
        )

    # ----------------------------------------------------------------------
    #  给指定好友点赞
    #  界面实证(16512, 11-05)：好友组与跨区组互斥展开，标题行固定不随列表滚动；
    #  点赞图标位置固定 (617,140)，白/橙两态模板互认仅 0.075。
    #  中文 OCR 有误字(「过山时雨」读成「过山时而」)，故模糊匹配 + 点开后二次核对。
    # ----------------------------------------------------------------------
    # 单份名单最多认多少人（用户 11-22 要求：同区/跨区各最多 200 个 ID，也就是全扫）。
    # 超过截断并告警，避免有人误贴几千行把一次点赞拖成十几分钟。
    FRIEND_LIKE_MAX_NAMES = 200
    # 向下滚动的上限。好友列表一屏约 5 行(行高 84px，列表 ROI 高 450)。
    # 11-22 实测(16384，同区 199 人)压住惯性后每次滚约 3.3 行，61 次到底，故给到 100
    # 留一倍余量；200 人满员时按 3.3 行/次算约 62 次。
    # 原来 20 次只能覆盖约 1/3 好友列表，名单里的好友排在后面就永远扫不到(用户 11-22 反馈)。
    FRIEND_LIKE_MAX_SCROLL = 100
    # 每组扫描的墙钟上限，纯粹防死循环(列表卡住不动、弹窗连点)。
    # 不能当业务限制用：名单 200 人全都要点赞时，光点开核对就要好几分钟。
    # 11-22 实测：扫完同区 199 人约 153s；名单里放 20~30 人、逐个点开核对点赞
    # 每人约 5~8s，合计 300~400s，仍在 900s 内。正常路径靠「pending 空」和
    # 「滚到底」两个判据提前退出，不会跑满这个时间。
    FRIEND_LIKE_TIMEOUT = 900
    # 回顶专用上限，与扫描上限分开。从庭院进好友页时游戏会恢复上次滚动位置，
    # 11-05 实测最坏停在列表底部（190 个同区好友约 40 屏）。
    # 回顶用 S_FRIEND_LIST_UP_FAST（快速甩动）—— 回顶不需要逐行覆盖，
    # 一甩约 16 行，13 次就能回到顶，比用 drag 逐步滑（61 次、150s）省两个多分钟。
    # 回顶不怕滑过头：到顶后列表不再动，Q搜索 会一直稳定可见。
    FRIEND_LIKE_MAX_TO_TOP = 40
    # 列表区域滑前/滑后的平均像素差小于此值 = 这次滑动没让列表动 = 到顶或到底。
    # 沿用 MysteryShop.SCROLL_MIN_DIFF 的量级；只裁左侧列表 ROI，避开右侧聊天区动画。
    FRIEND_LIKE_EDGE_DIFF = 5.0
    FRIEND_LIKE_SWIPE_SETTLE = 0.8
    # 列表最顶部那一行的「Q搜索」搜索框是判「真的到顶」的硬标志。
    # 16512 实测(11-05)：反向下滑 4 次到顶时 OCR 稳定读到 Q搜索(0.99)，
    # 起始/中部各帧完全没有；而到顶那一帧画面差反而最大(30.4)，
    # 所以回顶不能只靠像素差，否则会滑满 20 次仍判不出到顶、漏掉顶部好友。
    FRIEND_LIKE_TOP_MARKER = '搜'
    FRIEND_LIKE_TOP_BOX_Y = 60

    def _friend_list_crop(self):
        x, y, w, h = self.O_FRIEND_LIST.roi
        return self.device.image[y:y + h, x:x + w].copy()

    @staticmethod
    def _friend_list_diff(before, after) -> float:
        if before is None or after is None or before.shape != after.shape:
            return 255.0
        return float(np.abs(
            before.astype(np.int16) - after.astype(np.int16)
        ).mean())

    def _swipe_friend_list(self, swipe) -> float:
        """滑一次，返回列表区域滑前/滑后的画面差（MysteryShop._swipe_and_diff 同款）"""
        self.screenshot()
        before = self._friend_list_crop()
        self.swipe(swipe)
        self.device.click_record_clear()
        sleep(self.FRIEND_LIKE_SWIPE_SETTLE)
        self.screenshot()
        diff = self._friend_list_diff(before, self._friend_list_crop())
        logger.info('好友列表 %s 后画面变化 %.1f', swipe.name, diff)
        return diff

    def _friend_list_at_edge(self, swipe) -> bool:
        """这次滑动有没有把列表推到边界。

        疑似没动时再抓连续两帧复核，避免把「惯性里的一次短暂停顿」当成到底
        （EasySecret.wait_list_stable 的同类做法）。
        """
        if self._swipe_friend_list(swipe) >= self.FRIEND_LIKE_EDGE_DIFF:
            return False
        self.screenshot()
        first = self._friend_list_crop()
        sleep(0.3)
        self.screenshot()
        second = self._friend_list_crop()
        return self._friend_list_diff(first, second) < self.FRIEND_LIKE_EDGE_DIFF

    def _friend_list_at_top(self) -> bool:
        """列表是否真的在顶部：只有最顶部才有一行「Q搜索」搜索框。

        复用 O_FRIEND_LIST 的 Full OCR，不新增素材。Q搜索 也可能被读成
        「Q搜素」「0搜索」，故只取「搜」字并限制文本长度与位置：
        实测它在列表 ROI 内 y≈15，中部/底部不会出现。
        """
        self.screenshot()
        results = self.O_FRIEND_LIST.detect_and_ocr(self.device.image)
        if not results:
            return False
        for result in results:
            text = self._normalize_friend_name(result.ocr_text)
            if self.FRIEND_LIKE_TOP_MARKER not in text or len(text) > 6:
                continue
            if result.box[0, 1] <= self.FRIEND_LIKE_TOP_BOX_Y:
                return True
        return False

    def _friend_list_to_top(self) -> bool:
        """把列表拉回顶部：以「Q搜索」出现为硬标志，连续两帧画面静止兜底。

        进好友页时列表常停在上次的滚动位置（切号/切组都会保留），
        不先回顶就会漏掉排在顶部的好友。兜底只防 OCR 偶发读不出，
        不额外滑动、也不把「滑动没让列表动」单独当成到顶。
        """
        still = 0
        for _ in range(self.FRIEND_LIKE_MAX_TO_TOP):
            if self._friend_list_at_top():
                logger.info('好友列表已回到顶部（看到 Q搜索 搜索框）')
                return True
            diff = self._swipe_friend_list(self.S_FRIEND_LIST_UP_FAST)
            still = still + 1 if diff < self.FRIEND_LIKE_EDGE_DIFF else 0
            if still >= 2:
                logger.info('好友列表已回到顶部（连续两帧画面静止，Q搜索 未读出）')
                return True
        logger.warning('好友列表回顶次数用完，没看到 Q搜索，顶部好友可能扫不到')
        return False

    @staticmethod
    def _normalize_friend_name(text) -> str:
        return str(text or '').replace(' ', '').replace('　', '').strip()

    @classmethod
    def _friend_name_match(cls, ocr_text, target: str) -> bool:
        a = cls._normalize_friend_name(ocr_text)
        b = cls._normalize_friend_name(target)
        if not a or not b:
            return False
        # 分组标题(含 / 或冒号)与「5小时前」这类时间文本不是好友名
        if any(ch in a for ch in '/:：') or a.endswith('前'):
            return False
        if a == b or b in a or a in b:
            return True
        return SequenceMatcher(None, a, b).ratio() >= 0.65

    # 列表行内固定点击 x：只用 OCR 的 y 判断是哪一行，x 固定落在名字区中部。
    # **不能直接用 OCR box 的水平中心**：OCR 会把一个名字拆成两段（「Gym」+「.」），
    # 或者把右侧状态文字并进来，box 中心会漂到 x≈465（列表右边缘），点击落到行外，
    # 结果点开的是相邻好友。11-22 实测一个「Gym」因此连续 15 轮点开核对失败、白烧 4 分钟，
    # 而同一次运行里正常命中的 x 都在 240~285。
    # 列表 ROI 是 x=140 宽 360（140~500），行内整行可点，x=300 在名字区中间。
    FRIEND_ROW_CLICK_X = 300

    def _friend_list_hits(self, pending: set) -> list:
        """当前帧左侧列表里匹配到的 (目标名, 点击x, 点击y)"""
        results = self.O_FRIEND_LIST.detect_and_ocr(self.device.image)
        if not results:
            return []
        hits = []
        for result in results:
            box = result.box
            x = self.FRIEND_ROW_CLICK_X
            y = int(
                box[0, 1] + self.O_FRIEND_LIST.roi[1]
                + (box[2, 1] - box[0, 1]) / 2
            )
            for name in pending:
                if self._friend_name_match(result.ocr_text, name):
                    hits.append((name, x, y))
                    break
        return hits

    FRIEND_LIKE_NAME_CONFIRM = 6
    # 点开好友失败时最多点几次：回顶后列表仍在弹性回弹时，第一下点击常被
    # 游戏当成「停止滚动」吞掉（16480/16512 实测都是第一下失败、第二下就中）。
    FRIEND_LIKE_PICK_RETRY = 2

    def _pick_friend(self, name: str, x: int, y: int) -> bool:
        """点开指定好友并确认右侧名字换成目标，失败时按新坐标重试。

        每次重试前都重新截图 + OCR 定位，绝不复用上一帧的 (x, y)：
        列表一旦移位，旧坐标就会点到相邻好友身上。
        所有尝试都要过 `_verify_friend_chat_name` 才算成功，不会给错人点赞。
        点之前先清掉「好友羁绊提升」遮罩：弹窗盖着时点列表坐标等于点在弹窗上。
        """
        self._dismiss_friendship_popup(f'点开好友 [{name}] 前')
        for attempt in range(1, self.FRIEND_LIKE_PICK_RETRY + 1):
            if attempt > 1:
                self.screenshot()
                hits = [h for h in self._friend_list_hits({name}) if h[0] == name]
                if hits:
                    _, x, y = hits[0]
                    logger.info('重试点开好友 [%s]：重新定位到 (%d,%d)', name, x, y)
                else:
                    logger.info(
                        '重试点开好友 [%s]：当前帧列表里没看到它，仍用原坐标 (%d,%d)',
                        name, x, y
                    )
            self.device.click(x=x, y=y, control_name='friend_pick')
            self.device.click_record_clear()
            sleep(0.6)
            if self._verify_friend_chat_name(name):
                if attempt > 1:
                    logger.info(
                        '第 %d 次点击后右侧名字对上 [%s]，重试成功', attempt, name
                    )
                return True
            if attempt < self.FRIEND_LIKE_PICK_RETRY:
                logger.warning(
                    '第 %d/%d 次点开 [%s] 后右侧名字对不上，准备重试',
                    attempt, self.FRIEND_LIKE_PICK_RETRY, name
                )
            else:
                logger.warning(
                    '第 %d/%d 次点开 [%s] 后仍对不上，重试次数用尽，放弃该好友',
                    attempt, self.FRIEND_LIKE_PICK_RETRY, name
                )
        return False

    def _verify_friend_chat_name(self, target: str) -> bool:
        """点进好友后，等右侧资料区顶部名字变成目标名（最多 6 帧 × 0.3s）。

        不用「固定 sleep 一次 + 读一帧」：界面切换有动画，可能读到上一个好友的名字，
        轻则误跳过、重则点错人的赞。也不依赖选中行的黄色外圈 —— 那圈位置随行变化、
        颜色又接近界面金色装饰，用可直接 OCR 的文本判据更稳。
        """
        for _ in range(self.FRIEND_LIKE_NAME_CONFIRM):
            self.screenshot()
            if self._friend_name_match(
                self.O_FRIEND_CHAT_NAME.ocr(self.device.image), target
            ):
                return True
            sleep(0.3)
        return False

    # ----------------------------------------------------------------------
    #  「好友羁绊提升」遮罩弹窗
    #  出现时机(11-09 用户反馈)：从庭院点进好友页时、以及点赞让羁绊值达标时，
    #  一次流程里可能连弹多次。弹窗盖住整个好友界面，不点掉它会有两个连锁故障：
    #    - 左侧列表 OCR 读到的是弹窗正文，找不到目标好友，白滑到上限后放弃；
    #    - 点赞图标被盖住，_friend_like_state 一律 unknown，把成功误报成失败。
    #  判据 = 复用 DailyTrifles 的 I_FRIENDSHIP_UP(弹窗右上角粉紫 X，27x28)：
    #  11-09 实测 7 帧正常好友页(好友/最近/选中/详情/聊天)全部不命中，弹窗帧命中。
    #  兜底 = OCR 标题含「提升」：中文 OCR 把「羁绊」读成「藕」或整字漏掉
    #  (实测读出「好友藕提升」「好友提升」)，所以不能整串比对。
    # ----------------------------------------------------------------------
    # 单次调用最多点 30 下：游戏里这个弹窗是**连锁**的 —— 点掉一个又弹一个，
    # 一次点赞跨多级羁绊时能连弹十几个，所以上限给到 30（用户 11-09 指定）。
    # 30 仍低于防卡死阈值没问题：每次点击后都 click_record_clear，
    # 记录队列始终只剩 1 条，永远攒不到 device.py:256 要求的 10 条。
    FRIENDSHIP_POPUP_TRIES = 30
    FRIENDSHIP_POPUP_SETTLE = 0.6
    FRIENDSHIP_POPUP_KEYWORD = '提升'
    FRIENDSHIP_POPUP_KEYWORD_MAX = 8
    # OCR 兜底的廉价门控：弹窗带全屏遮罩，好友列表区(x160-520,y170-560)的平均亮度
    # 会被压到 ~63，正常好友页 ~169（11-09 实测 9 帧）。亮度正常就不花那 0.44 秒跑 OCR，
    # 只是省时间，判不出来时行为等于「只有素材判据」，不会更差。
    FRIENDSHIP_POPUP_DIM_ROI = (160, 170, 520, 560)
    FRIENDSHIP_POPUP_DIM_MEAN = 110.0
    # 弹窗 X 素材的标定左上角，只用来判断「素材有没有漂移」（漂了仍按安全落点点）
    FRIENDSHIP_POPUP_X = (1147, 80)
    FRIENDSHIP_POPUP_DRIFT = 6

    def _friendship_popup_visible(self, use_ocr: bool = True) -> bool:
        """当前帧是否有「好友羁绊提升」遮罩弹窗（素材为主、OCR 兜底）。

        use_ocr=False 只跑 27x28 的模板匹配（约 16ms），用于扫描循环的每轮检查；
        其余位置带 OCR 兜底，但先过一道「画面是否被压暗」的廉价门控（约 1ms），
        只有像有遮罩时才花 0.44 秒跑 OCR，正常好友页不会白等。
        """
        self.screenshot()
        if self.appear(self.I_FRIENDSHIP_UP):
            return True
        if not use_ocr:
            return False
        if not self._screen_dimmed():
            return False
        # 只能用 detect_and_ocr 取文字：RuleOcr.ocr() 在 Full 模式下返回的是
        # **合并后的坐标元组**（module/ocr/sub_ocr.py:25-63），不是文字，
        # 拿它比对关键词永远命中不了 —— 20:45 实测踩过一次。
        for item in self.O_FRIENDSHIP_UP_TITLE.detect_and_ocr(self.device.image):
            text = self._normalize_friend_name(getattr(item, 'ocr_text', ''))
            if (
                text
                and self.FRIENDSHIP_POPUP_KEYWORD in text
                and len(text) <= self.FRIENDSHIP_POPUP_KEYWORD_MAX
            ):
                logger.info('羁绊提升弹窗 OCR 兜底命中：%s', text)
                return True
        return False

    def _screen_dimmed(self) -> bool:
        """好友列表区是否被遮罩压暗（弹窗/全屏黑幕的廉价判据）。"""
        image = self.device.image
        if image is None:
            return False
        x1, y1, x2, y2 = self.FRIENDSHIP_POPUP_DIM_ROI
        h, w = image.shape[:2]
        patch = image[min(y1, h):min(y2, h), min(x1, w):min(x2, w)]
        if patch.size == 0:
            return False
        mean = float(patch.mean())
        if mean < self.FRIENDSHIP_POPUP_DIM_MEAN:
            logger.info('好友列表区偏暗（平均亮度 %.1f），可能有遮罩，启用 OCR 兜底', mean)
            return True
        return False

    def _dismiss_friendship_popup(self, reason: str, use_ocr: bool = True) -> bool:
        """有「好友羁绊提升」弹窗就点它的右上角 X；返回 True = 确认当前没有弹窗。

        判据 = I_FRIENDSHIP_UP（27x28 的粉紫 X，来自 DailyTrifles，全局唯一一份，
        不再复制第二份 png）；落点 = C_FRIENDSHIP_POPUP_CLOSE，**单独**锁在
        (1150,84,10,9) 这块「弹窗 X 中偏左上」的小框里（约 10x9 像素）。
        为什么要单独限制落点：这两个按钮在屏幕上是**物理重叠**的 ——
        弹窗 X 自身是 1147~1173 / 80~107 的一个小圆盘（中心 1160,94，半径约 14），
        好友页右上角红 X 的可点圆盘实测在 (1155,97,43,42)（中心 ≈1176,117，
        半径 21~29，11-09 实机量取），弹窗 X 的右下大半个身子都压在它上面。
        本框任一点距弹窗中心 ≤14.1（必在图标内，点得中）、距红 X 圆心 ≥30.2
        （在最保守的 29 半径之外，踩不到）—— 是唯一两全的那一小块。
        连锁弹窗点着点着弹窗没了，多点的这一下原本会落在红 X 上把好友页关掉
        （11-09 实测真的关过一次），现在只会点在两个按钮之间的底板上。
        素材没命中而 OCR 命中时(标题换位置/素材过期)，同样用这个安全落点。
        另外 `_ensure_friends_tab_ready` / `_search_friend_group` 里都加了「是否还在好友页」
        的复查：万一真被点关了，会重新进入而不是继续对着庭院 OCR。

        这个弹窗是连锁的（点掉一个再弹一个），所以是「点到看不见为止」，
        上限 FRIENDSHIP_POPUP_TRIES 只是防死循环，不是为了省点击。
        循环结束后再复查一帧：第 N 下恰好点成功时不该误报「连点 N 次仍在」。
        """
        for attempt in range(1, self.FRIENDSHIP_POPUP_TRIES + 1):
            if not self._friendship_popup_visible(use_ocr=use_ocr):
                if attempt > 1:
                    logger.info(
                        '羁绊提升弹窗已关闭（点了 %d 次），当前阶段：%s', attempt - 1, reason
                    )
                return True
            if attempt == 11:
                logger.warning(
                    '羁绊提升弹窗已连点 10 次仍在（上限 %d），疑似连锁弹窗过多，%s',
                    self.FRIENDSHIP_POPUP_TRIES, reason,
                )
            box = list(self.I_FRIENDSHIP_UP.roi_front)
            drift = max(
                abs(box[0] - self.FRIENDSHIP_POPUP_X[0]),
                abs(box[1] - self.FRIENDSHIP_POPUP_X[1]),
            )
            if drift > self.FRIENDSHIP_POPUP_DRIFT:
                logger.warning(
                    '羁绊提升弹窗的 X 落点漂移到 %s，与标定 %s 差 %d 像素；'
                    '落点仍按安全框走，若连点多次关不掉请看这里',
                    box, self.FRIENDSHIP_POPUP_X, drift,
                )
            x, y = self.C_FRIENDSHIP_POPUP_CLOSE.coord()
            logger.info(
                '检测到「好友羁绊提升」弹窗（%s），点右上角 X (%d,%d)：第 %d/%d 次',
                reason, x, y, attempt, self.FRIENDSHIP_POPUP_TRIES,
            )
            self.device.click(x, y, control_name='friendship_popup_close')
            self.device.click_record_clear()
            sleep(self.FRIENDSHIP_POPUP_SETTLE)
        # 第 N 下点完之后没再判过，这里补一帧，避免「刚好点成功」被记成失败
        if not self._friendship_popup_visible(use_ocr=use_ocr):
            logger.info(
                '羁绊提升弹窗在最后一次点击后关闭（共点 %d 次），当前阶段：%s',
                self.FRIENDSHIP_POPUP_TRIES, reason,
            )
            return True
        logger.warning(
            '羁绊提升弹窗连点 %d 次仍在，%s 的识别可能失败',
            self.FRIENDSHIP_POPUP_TRIES, reason,
        )
        return False

    def _in_friends_window(self) -> bool:
        """是否已经站在好友界面（「好友」或「最近」标签都算）。

        page_friends 的判定素材 I_CHECK_FRIENDS 只在「好友」标签命中(11-09 实测)；
        停在「最近」时 get_current_page() 认不出，goto_page(page_friends) 会再点一次
        庭院入口（此时无效）直到超时，整块点赞被跳过。所以用列表头文字兜底认「最近」。
        """
        self.screenshot()
        if self.appear(GameUiAssets.I_CHECK_FRIENDS):
            return True
        results = self.O_FRIEND_LIST.detect_and_ocr(self.device.image)
        for item in results:
            text = self._normalize_friend_name(getattr(item, 'ocr_text', ''))
            if '最近联系人' in text or '最近组队' in text:
                logger.info('识别到「最近」标签页（%s），判定已在好友界面', text)
                return True
        return False

    # 「好友」页签自愈：最多 2 轮。不要再往上加 —— 每轮至少一次入口点击 +
    # 一次页签点击，轮数多了会自己把点击历史撞到防连点阈值上。
    FRIENDS_TAB_READY_TRIES = 2
    # 点庭院好友入口后等窗口渲染的时间；11-18 实测列表 0.6~1.0s 内出来。
    FRIENDS_WINDOW_OPEN_WAIT = 1.0

    def _ensure_friends_tab_ready(self) -> bool:
        """把好友窗口摆到「好友」页签，保证紧接着的 goto_page 能被导航认出来。

        为什么必须自己动手（11-18 实机 + 截图实测）：
        `page_friends` 的判定素材 I_CHECK_FRIENDS 是左下角的**两个小纸人**，只在
        「好友」页签出现；好友窗口停在「最近」页签时那块是空木纹 —— 拿 18:41 的
        卡死截图实测只有 **0.0379**（阈值 0.8），全图搜索最高 0.57，说明整屏都没有
        这个图标，不是 ROI 偏几个像素能救的。
        一旦页签被带到「最近」，`goto_page(page_friends)` 就退化成
        「点庭院好友入口 → 认不出 → 点红X返回 → 再点入口」的死循环，6 轮后被设备层
        防连点拦成 GameTooManyClickError，整个任务重启。11-18 config 4 因此卡死 9 次
        （含 18:52 关闭点赞之后的 19:05 / 19:13 / 19:16 / 19:49 四次），首发失败全部
        紧跟「打完觉醒协战回庭院」或「切到停在最近页签的账号」。

        「最近」页签下点好友只在右侧打开聊天记录（11-09 实测 16416）：没有「完成任务」
        卡片、也没有能点的点赞按钮，点顶部点赞图标两次仍是白态 —— 所以不是「认出来就
        能用」，必须切回「好友」才继续。

        不改上游 page_friends（那要动 tasks/GameUi），这里自己走一遍：
        回庭院 → 点好友入口 → 认出「最近」→ 点 C_FRIENDS_TAB 切回 → I_CHECK_FRIENDS 复核。

        Returns:
            True 表示现在站在「好友」页签，紧接着 goto_page(page_friends) 能过；
            False 表示两轮都没摆正，调用方**必须放弃进好友页**，不能硬 goto。
        """
        for attempt in range(1, self.FRIENDS_TAB_READY_TRIES + 1):
            self.screenshot()
            if self.appear(GameUiAssets.I_CHECK_FRIENDS):
                logger.info(
                    '好友页签已就位（第 %d/%d 轮判定）',
                    attempt, self.FRIENDS_TAB_READY_TRIES,
                )
                return True
            # 站在庭院就一定不在好友窗口，省掉一次约 1 秒的列表全量 OCR。
            # 不在庭院时才去认「最近」，避免每号每轮白花 2 秒。
            in_window = (
                False if self.appear(GameUiAssets.I_CHECK_MAIN)
                else self._in_friends_window()
            )
            if not in_window:
                # 不在好友窗口：在庭院点入口。若屏幕还在探索/协战等页，底部导航没有
                # 好友入口，素材不会命中，必须先回庭院。
                if not self.appear(GameUiAssets.I_CHECK_MAIN):
                    logger.info('既不在庭院也不在好友窗口，先回主界面再点好友入口')
                    try:
                        self.goto_page(page_main)
                    except RequestHumanTakeover:
                        raise
                    except (GamePageUnknownError, GameStuckError, GameTooManyClickError) as error:
                        logger.warning('回主界面失败，无法自愈好友页签: %r', error)
                        self.device.click_record_clear()
                        return False
                    self.screenshot()
                if not self.appear_then_click(GameUiAssets.I_MAIN_GOTO_FRIENDS):
                    logger.warning(
                        '庭院底部没找到好友入口（第 %d/%d 轮），自愈失败',
                        attempt, self.FRIENDS_TAB_READY_TRIES,
                    )
                    return False
                sleep(self.FRIENDS_WINDOW_OPEN_WAIT)
                self._dismiss_friendship_popup('自愈打开好友页时')
                self.screenshot()
                if self.appear(GameUiAssets.I_CHECK_FRIENDS):
                    logger.info('点好友入口后直接落在「好友」页签')
                    return True
                if not self._in_friends_window():
                    logger.warning('点了好友入口但仍认不出好友界面（第 %d 轮）', attempt)
                    continue
            # 人到好友窗口了，但不在「好友」页签（游戏记住了上次的「最近」）
            x, y = self.C_FRIENDS_TAB.coord()
            logger.info(
                '好友窗口停在非「好友」页签（「最近」下判定素材仅 0.04），'
                '点 (%d,%d) 切回：第 %d/%d 轮',
                x, y, attempt, self.FRIENDS_TAB_READY_TRIES,
            )
            self.device.click(x, y, control_name=self.C_FRIENDS_TAB.name)
            self.device.click_record_clear()
            sleep(self.FRIEND_LIKE_GROUP_SWITCH_SETTLE)
        # 最后一轮点完没再判过，补一帧，避免「刚好点成功」被记成失败
        self.screenshot()
        if self.appear(GameUiAssets.I_CHECK_FRIENDS):
            logger.info('最后一轮点击后好友页签已就位')
            return True
        logger.warning(
            '「好友」页签自愈失败（%d 轮），放弃进好友页 —— 继续 goto 会踩死循环',
            self.FRIENDS_TAB_READY_TRIES,
        )
        return False

    def _friend_like_state(self) -> str:
        """重新截图后判点赞图标状态：on / off / unknown。

        白橙两态互认虽只有 0.075，切页动画帧仍可能让橙态漏检、白态误命中。
        两态同时命中或都不命中一律算「认不清」，绝不在状态不明时点 (617,140)
        —— 当天点赞不可逆，重复点的后果未实测过。
        """
        self.screenshot()
        on = self.appear(self.I_FRIEND_LIKE_ON)
        off = self.appear(self.I_FRIEND_LIKE_OFF)
        if on and not off:
            return 'on'
        if off and not on:
            return 'off'
        logger.warning(
            '点赞图标状态不明（白态=%s，橙态=%s），不点击，放弃本次点赞', off, on
        )
        return 'unknown'

    def _like_current_friend(self) -> str:
        """返回 liked / already / failed。点完必须看到橙黄态才算成功。

        已点赞（on）直接跳过，绝不因为「点过赞」反复重试；
        每次判定都重新截图，点击后立即清点击记录，避免同一坐标累计触发过密异常。
        点赞会让羁绊值达标而立刻弹出「好友羁绊提升」遮罩，把点赞图标整个盖住，
        所以每次判状态前都先关弹窗，否则成功也会被读成 unknown -> failed。
        """
        self._dismiss_friendship_popup('点赞前')
        state = self._friend_like_state()
        if state == 'on':
            return 'already'
        if state == 'unknown':
            return 'failed'
        for attempt in range(1, 3):
            clicked = self.appear_then_click(self.I_FRIEND_LIKE_OFF)
            self.device.click_record_clear()
            if not clicked:
                logger.warning('第 %d 次点赞时白态图标已不匹配，放弃', attempt)
                return 'failed'
            sleep(0.8)
            self._dismiss_friendship_popup('点赞后')
            state = self._friend_like_state()
            if state == 'on':
                return 'liked'
            if state == 'unknown':
                return 'failed'
            if attempt < 2:
                logger.info('第 %d 次点赞后图标仍是白态，再试一次', attempt)
        logger.warning('连续 2 次点赞后图标仍是白态，放弃，避免重复消耗当天点赞')
        return 'failed'

    FRIEND_LIKE_GROUP_SWITCH_SETTLE = 1.0
    # 跨区标题行 y：跨区展开时紧跟同区标题(列表顶部 y≈268、滚动后 y≈215)，
    # 同区展开时被挤到列表底(y≈587~598)。
    FRIEND_LIKE_CROSS_TITLE_Y = 300
    # 分组标题三角的搜索带。**不能用固定 45x45 小窗**：好友标题行是吸顶的，
    # 但列表滚到最顶部时上面会多出一行「Q搜索」，标题被压低约 50px
    # （16416 实测 11-09：滚动状态 y≈174，回顶后 y≈223），
    # 原来只覆盖 174 那一档的小窗在回顶后就两态全 False —— 不是素材老化，
    # 是落点漂移（同一帧素材匹配分数 0.987~1.000，两张 png 完全没问题）。
    FRIEND_GROUP_TRIANGLE_BAND = (456, 150, 34, 160)   # 「好友」标题所在带
    FRIEND_GROUP_CROSS_BAND = (456, 560, 34, 110)      # 「跨区」标题所在带（仅取落点用）
    FRIEND_GROUP_TRIANGLE_THRESHOLD = 0.9

    def _locate_group_triangle(self, band: tuple, refresh: bool = True):
        """在 band 里找**最靠上**的分组标题三角。

        返回 (shape, x, y, w, h, score)，shape 为 'expanded'(▼) / 'folded'(▶)；
        带内一个都没命中返回 None。

        为什么取「最靠上」而不是固定坐标：同区/跨区两组互斥展开，跨区标题要么
        紧贴在好友标题下面(y≈215~268)，要么被挤到列表底(y≈587~598)，而好友标题
        永远在它上面（滚走时吸顶）。所以带内最上面那个三角一定是「好友」标题行的。
        """
        if refresh:
            self.screenshot()
        best = None
        for shape, rule in (
            ('expanded', self.I_FRIEND_GROUP_EXPANDED),
            ('folded', self.I_FRIEND_GROUP_FOLDED),
        ):
            # 这里临时把素材的搜索区改成这条带，用完必须还原：
            # roi_back 是素材共享的标定，被改脏后别的调用点会跟着错
            saved = rule.roi_back
            try:
                rule.roi_back = band
                hits = rule.match_all(
                    self.device.image, threshold=self.FRIEND_GROUP_TRIANGLE_THRESHOLD
                )
            finally:
                rule.roi_back = saved
            if not hits:
                continue
            hit = min(hits, key=lambda item: item[2])
            if best is None or hit[2] < best[1][2]:
                best = (shape, hit)
        if best is None:
            return None
        shape, (score, x, y, w, h) = best
        return shape, x, y, w, h, score

    def _friend_group_state(self) -> str:
        """当前展开的分组：'same' 同区 / 'cross' 跨区 / 'unknown' 认不出。

        两组互斥展开，只看**好友标题行**的三角形态：▼ = 同区展开，▶ = 跨区展开。
        **不依赖跨区标题行的固定坐标** —— 那个 y 会在 215 与 587 之间跳。
        16416 实测(11-09 实机)：滚动态 ▼(463,170)=1.000 / ▶(466,167)=0.987，
        回顶态 ▼(463,223) 命中、▶ 0.000（旧的 45x45 小窗就是在这里判不出来）。
        """
        found = self._locate_group_triangle(self.FRIEND_GROUP_TRIANGLE_BAND)
        if found is None:
            logger.warning(
                '好友分组状态：认不出好友标题行的三角（带 %s 内 ▼/▶ 都没命中 0.9）',
                self.FRIEND_GROUP_TRIANGLE_BAND,
            )
            return 'unknown'
        shape, _x, y, _w, _h, score = found
        state = 'same' if shape == 'expanded' else 'cross'
        logger.info(
            '好友分组状态：%s（好友标题三角%s y=%d 分数 %.3f）',
            state, '▼' if shape == 'expanded' else '▶', y, score,
        )
        return state

    def _friend_group_state_by_ocr(self) -> str:
        """三角认不出时的兜底：用 OCR 读「跨区好友」标题行的 y 判布局。"""
        results = self.O_FRIEND_LIST.detect_and_ocr(self.device.image)
        for item in results:
            text = self._normalize_friend_name(getattr(item, 'ocr_text', ''))
            if '跨区' not in text:
                continue
            y = int(item.box[0, 1] + self.O_FRIEND_LIST.roi[1])
            state = 'cross' if y < self.FRIEND_LIKE_CROSS_TITLE_Y else 'same'
            logger.info('好友分组状态（OCR 兜底）：%s，跨区标题行 y=%d', state, y)
            return state
        logger.warning('好友分组状态 OCR 兜底也没读到「跨区好友」标题行')
        return 'unknown'

    def _friend_group_state_checked(self) -> str:
        state = self._friend_group_state()
        return state if state != 'unknown' else self._friend_group_state_by_ocr()

    def _switch_friend_group(self, target: str, label: str) -> bool:
        """确保当前展开的是 target 分组；已经是就不点，点完必须确认状态真的翻转。

        点击落点从「认出来的那个三角」算，不再用 click.json 里的固定坐标：
        固定坐标 (465,168) 在回顶态正好落在「Q搜索」搜索框上，一点就弹输入法。
        """
        state = self._friend_group_state_checked()
        if state == target:
            logger.info('好友分组（%s组）本来就是展开态，无需点击', label)
            return True
        if state == 'unknown':
            logger.warning(
                '好友分组状态认不出，不点击三角（避免误点好友），跳过%s组', label
            )
            return False
        band = (
            self.FRIEND_GROUP_TRIANGLE_BAND if target == 'same'
            else self.FRIEND_GROUP_CROSS_BAND
        )
        found = self._locate_group_triangle(band, refresh=False)
        if found is None:
            logger.warning(
                '切到%s组失败：%s 带内没找到分组标题三角（可能被挤到列表外），跳过本组',
                label, band,
            )
            return False
        _shape, bx, by, bw, bh, score = found
        x, y = bx + bw // 2, by + bh // 2
        logger.info(
            '切换到%s组：点击分组标题三角 (%d,%d)，三角框 (%d,%d,%d,%d) 分数 %.3f',
            label, x, y, bx, by, bw, bh, score,
        )
        self.device.click(x, y, control_name=f'friend_group_switch_{target}')
        self.device.click_record_clear()
        sleep(self.FRIEND_LIKE_GROUP_SWITCH_SETTLE)
        after = self._friend_group_state_checked()
        if after != target:
            logger.warning(
                '切到%s组失败：点击后状态=%s，本组可能扫不到', label, after
            )
            return False
        logger.info('已切到%s组（状态 %s → %s）', label, state, after)
        return True

    def _log_friend_scan_timeout(self, label: str, timer: Timer, rounds: int, pending: set) -> None:
        """扫描被超时掐断时留痕：只看日志就能分清「扫完了」和「没扫完」"""
        logger.warning(
            '好友点赞（%s组）扫描被超时掐断，未扫完：已用 %.1fs/%ss，'
            '滚动 %d/%d 次，剩余 %d 人未查：%s',
            label, timer.current(), self.FRIEND_LIKE_TIMEOUT,
            rounds, self.FRIEND_LIKE_MAX_SCROLL,
            len(pending), '、'.join(sorted(pending)),
        )

    # 同一个名字跨轮次点开核对失败几次后放弃（移出 pending）。
    # 为什么需要：一次失败只会 `continue`，名字仍留在 pending 里，下轮扫描还会再匹配到；
    # 若这个名字本身 OCR 定位不到（或游戏里重名/改名），就会每轮重试一次。
    # 11-22 实测一个「Gym」连续 15 轮失败，白烧 4 分钟（占整次 25 人任务的 1/3）。
    # 3 次是折中：正常的「第一下点击被列表滚动吞掉」在 FRIEND_LIKE_PICK_RETRY 里已重试过，
    # 跨轮再给 3 次机会足够，再多只说明这个目标真的匹配不上。
    FRIEND_LIKE_NAME_GIVEUP = 3

    def _search_friend_group(self, pending: set, label: str, fails: dict) -> None:
        """在当前展开的分组里滚动找人；找到就核对名字、点赞、移出 pending。

        每组自带独立计时器，同区滚满也不会把跨区的时间吃光；
        画面不再变化就提前收手，不做无用的空滑。
        每个出口都留一条日志，标明是「扫完了」还是「被上限/超时掐断」。

        Args:
            fails: 跨组共享的失败计数 {名字: 次数}。同区失败 3 次的在跨区不再试。
        """
        timer = Timer(self.FRIEND_LIKE_TIMEOUT).start()
        for scroll_count in range(1, self.FRIEND_LIKE_MAX_SCROLL + 1):
            if not pending:
                logger.info('好友点赞（%s组）名单已全部处理完，扫描结束', label)
                return
            if timer.reached():
                self._log_friend_scan_timeout(label, timer, scroll_count - 1, pending)
                return
            # 弹窗盖着时 OCR 读到的是弹窗正文，先关掉再找好友。
            # 每轮只跑模板匹配（16ms），不开 OCR —— detect_and_ocr 实测稳态 0.44s，
            # 20 轮就是 9 秒，会吃掉 60s 的扫描预算；遮罩在别的调用点也会被处理
            self._dismiss_friendship_popup(f'扫描{label}组第 {scroll_count} 轮', use_ocr=False)
            self.screenshot()
            # 廉价复查（素材 16ms）：万一连点误触右上角红X把好友页关了，
            # 继续往下就是对着庭院找好友、白滑到上限。只有这一格不过才去跑 OCR。
            if not self.appear(GameUiAssets.I_CHECK_FRIENDS) and not self._in_friends_window():
                logger.warning(
                    '扫描%s组第 %d 轮时已离开好友界面（弹窗连点可能误触右上角红X），本组中止',
                    label, scroll_count,
                )
                return
            for name, x, y in self._friend_list_hits(pending):
                if name not in pending:
                    continue
                if not self._pick_friend(name, x, y):
                    fails[name] = fails.get(name, 0) + 1
                    logger.warning(
                        '点赞跳过：点开后名字对不上（本次已试 %d 次），目标=%s，'
                        '累计失败 %d/%d 次',
                        self.FRIEND_LIKE_PICK_RETRY, name,
                        fails[name], self.FRIEND_LIKE_NAME_GIVEUP,
                    )
                    if fails[name] >= self.FRIEND_LIKE_NAME_GIVEUP:
                        logger.warning(
                            '好友 [%s] 累计 %d 次点开核对失败，放弃并移出名单'
                            '（可能是 OCR 定位不准、游戏里已改名或重名）',
                            name, fails[name],
                        )
                        pending.discard(name)
                    continue
                fails.pop(name, None)
                result = self._like_current_friend()
                if result == 'liked':
                    logger.info('已给好友点赞 [%s]（%s组）', name, label)
                elif result == 'already':
                    logger.info('好友 [%s] 已点过赞，跳过', name)
                else:
                    logger.warning('好友 [%s] 点赞后没变成黄色，跳过', name)
                pending.discard(name)
                if not pending:
                    logger.info('好友点赞（%s组）名单已全部处理完，扫描结束', label)
                    return
            if timer.reached():
                self._log_friend_scan_timeout(label, timer, scroll_count - 1, pending)
                return
            if self._friend_list_at_edge(self.S_FRIEND_LIST_DOWN):
                logger.info(
                    '好友列表（%s组）已经到底，提前结束滚动：第 %d/%d 次滑动，'
                    '剩余 %d 人未找到',
                    label, scroll_count, self.FRIEND_LIKE_MAX_SCROLL, len(pending),
                )
                return
        logger.warning(
            '好友点赞（%s组）滚动次数用尽 %d/%d，列表未必到底，'
            '剩余 %d 人未查：%s',
            label, self.FRIEND_LIKE_MAX_SCROLL, self.FRIEND_LIKE_MAX_SCROLL,
            len(pending), '、'.join(sorted(pending)),
        )

    def run_friend_like(self) -> bool:
        """给名单里的好友点赞：同区滚到底 -> 切跨区回顶再滚到底 -> 都没有就放弃。

        局部失效返回 False、正常跑完返回 True（哪怕一个都没找到），
        绝不抛异常打断协战主流程。
        """
        names = list(self.friend_like_names_v)
        if not names:
            logger.warning('「给指定好友点赞」已开启，但好友名列表是空的')
            return False
        if len(names) > self.FRIEND_LIKE_MAX_NAMES:
            logger.warning(
                '好友点赞名单 %d 人超过上限 %d，只处理前 %d 人',
                len(names), self.FRIEND_LIKE_MAX_NAMES, self.FRIEND_LIKE_MAX_NAMES,
            )
            names = names[:self.FRIEND_LIKE_MAX_NAMES]
        # 进好友页 + 摆正「好友」页签合成一次自愈。以前是「先 _in_friends_window 判，
        # 不行就 goto_page(page_friends)」—— 只要页签停在「最近」，goto 本身就会死循环，
        # except 拦不住（异常在被吞之前已经把点击记录打满），所以必须自己点入口。
        if not self._ensure_friends_tab_ready():
            logger.warning('好友页签没能摆正，本号跳过点赞')
            return False
        # 进好友页时游戏可能先弹「好友羁绊提升」遮罩（离线期间好友给你点赞/送礼），
        # 不关掉的话接下来的列表 OCR 全读到弹窗文字
        self._dismiss_friendship_popup('进入好友页时')

        pending = set(names)
        # 跨组共享的失败计数：同区已经失败 3 次的名字，跨区不再浪费轮次重试
        fails = {}
        # 进好友页时展开的可能是任意一组（16416 实测跨区展开），且各组各自保存滚动位置。
        # 两组**无条件各走一遍**：不假设初始状态，也不因第一组切换失败就放弃第二组。
        for label, target in (('同区', 'same'), ('跨区', 'cross')):
            if not pending:
                logger.info('好友点赞名单已全部处理完，跳过%s组', label)
                break
            if not self._switch_friend_group(target, label):
                logger.warning(
                    '%s组切换失败，仍扫描当前列表（可能重复扫到上一组）', label
                )
            # 切号/切组都会保留上次的滚动位置，先拉回顶部再扫，避免漏掉顶部好友
            self._friend_list_to_top()
            self._search_friend_group(pending, label, fails)
        if pending:
            logger.warning('以下好友没找到，放弃点赞：%s', '、'.join(sorted(pending)))
        logger.info('好友点赞完成：%d/%d', len(names) - len(pending), len(names))
        return True
