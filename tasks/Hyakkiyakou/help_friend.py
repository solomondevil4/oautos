# This Python file uses the following encoding: utf-8
# 帮指定好友砸百鬼夜行（邀请好友同行）
#
# 只给 tasks/Hyakkiyakou/script_task.py 用，ScriptTask 通过多继承拿到这些方法。
# 素材全部复用 HyakkiyakouAssets 里现成的，一张图都没有新采集：
#   I_HINVITE            打开「邀请好友」弹窗
#   I_CHECK_INVITATION   弹窗是否开着（关闭判据 = 它消失）
#   I_FRIEND_SAME_1/2    「好友」页签 未选中 / 选中
#   I_FRIEND_REMOTE_1/2  「跨区」页签 未选中 / 选中
#   O_HELP_TICKET        右下角百鬼券数量      （本次新增）
#   O_HELP_LIST          好友列表 OCR 区       （本次新增）
#   S_HELP_LIST_SCROLL   列表上滑 / BACK 回顶  （本次新增，只是坐标，无图片）
#
# 和手动操作的对照：
#   主界面 → 点「邀请好友」 → 切「好友」/「跨区」页签 → 按名单点好友
#   → 弹窗关闭 = 邀请成功 → 点「进」开局砸完 → 回主界面继续下一个
# 好友当天已被别人邀请满 3 次时，点它弹窗不会关闭，记日志并跳过该好友。
#
# 列表布局与滑动（16416 实测，1280x720）：
#   弹窗列表 y 196~616 共 5 行、每行 2 个好友、行高 84px、首行文字中心 y=231。
#   一次只滑 2 行（手指 130px / 600ms），保证相邻两帧有 3 行重叠，不会跳过任何一行。
#   旧配置 280px + 默认时长 250ms 一次能滚 5~6 行 —— 整屏被换掉，中间的好友
#   从未出现在任何一帧里，这就是「名单上有人却识别不到」的根因。
import random
import re
import time

from module.exception import RequestHumanTakeover, TaskEnd
from module.logger import logger
from tasks.GameUi.page import page_hyakkiyakou, page_main


class HelpFriendTask:
    """帮好友砸百鬼夜行。

    宿主类需要提供 screenshot / appear / appear_then_click / click / swipe /
    goto_page / device / _config 这些基础能力，以及 ScriptTask 的 _enter_and_play()。
    """

    # 每个好友每天最多被邀请 3 次（游戏机制），到量就不再点它
    HELP_PER_FRIEND = 3
    # 点好友后等「弹窗关闭」的最长时间（秒），超时即认为对方今天已被邀请满
    HELP_PICK_WAIT = 3.0
    # 点好友后两帧之间的间隔
    HELP_PICK_INTERVAL = 0.3

    # ---- 列表滑动的实测参数（16416，1280x720，2026-11-06 逐档量过） --------------
    # 列表可视区：y 196~616，5 行卡片；每行 2 个好友；行高 84px（首行文字中心 y=231）。
    HELP_LINE_HEIGHT = 84
    HELP_LINES_PER_SCROLL = 2
    # 手指位移 -> 列表实际滚动的放大倍数：慢滑(600ms) 1.30，快滑(250ms) 1.69。
    # 实测：80px/600ms 滚 104px(1.24 行)；80px/250ms 滚 135px(1.61 行)；
    # 160px/600ms 滚 203px(2.42 行)；**280px/250ms 直接滚 5~6 行，整屏被换掉**。
    # 旧配置就是 280px + 默认时长，于是中间 4~5 行的好友从未出现在任何一帧里 → 漏人。
    HELP_SCROLL_GAIN = 1.30
    # 往下扫时每次的手指位移：目标滚 2 行 = 168px，除以放大倍数得 129px。
    # 这只是初值，实际会按 _help_adapt_step 的实测滚幅在线收敛。
    HELP_STEP_PX = 130
    HELP_STEP_PX_MIN = 60
    HELP_STEP_PX_MAX = 220
    # 实测滚幅超过这个行数就认为「一次滑太远，再大就会整屏跳过」，步长收一档
    HELP_STEP_SAFE_LINES = 2.5
    # 实测滚幅不到这个行数就认为「滑得太慢，白费时间」，步长放一档
    HELP_STEP_SLOW_LINES = 1.0
    # 滑动时长必须自己指定：base_task.swipe() 用默认 (0.1,0.2)，adb 再 ×2.5 只有
    # 0.25~0.5 秒，惯性大、滑得远。拉长到 0.6 秒后放大倍数从 1.69 降到 1.30。
    HELP_SWIPE_DURATION = (0.55, 0.65)
    # 滑完必须等惯性滚完再截图，否则 OCR 读到的是滚动中途帧（名字会抖、会漏）
    HELP_SWIPE_SETTLE = 0.5
    # 相邻两帧名字数为 0 重叠时，只有两边都识别到这么多名字才判定「真的滑过头」，
    # 否则可能只是 OCR 抖动，误判会白滑一次
    HELP_OVERLAP_GUARD = 3

    # 连续多少次滑动后列表内容不变，就判定已到底 / 已回顶
    # 不能要求「完全一致」：OCR 会把「最近受邀」读成「最近爱邀」、「弦月孤舟」读成「弦月孤盘」，
    # 还会凭空多出 60% / 50 / 60）这类数字，实测 19 帧里 18 次相邻比较全都不相等。
    # 所以改成「名字集合重合度」判稳，见 _help_same()
    HELP_SCROLL_STILL = 2
    # 两帧名字集合重合度达到这个比例就算「列表没动」
    HELP_NAME_OVERLAP = 0.75
    # 两帧名字集合相差不超过这么多条（一边多、一边少各算一条）也算「没动」
    HELP_NAME_ALLOW_DIFF = 2
    # 回顶最多真滑几次。回顶用大位移 280px（一次 5~6 行），200 个好友约 100 行 ≈ 20 次
    HELP_TO_TOP_MAX = 30
    # 往下扫最多真滑几次。每次 2 行，同区/跨区各 200 人（100 行）≈ 50 次，
    # 留 3 倍余量给「滑过头反滑重扫」和 OCR 抖动
    HELP_SCROLL_MAX = 160
    # 单份名单最多 200 人（用户 11-06 定的上限），超出截断并告警
    HELP_MAX_NAMES = 200
    # 列表 OCR 里这些不是好友名，不参与名单匹配
    HELP_IGNORE_TOKENS = ('好友', '寮友', '跨区', '搜索', '请输入好友昵称或备注搜索', '请输人好发昵称或备注搜索')
    # 纯数字 / 百分号 / 括号 / 度这类是「最近受邀」旁边的人数标记，不是名字
    HELP_NOISE_PATTERN = re.compile(r'^[\d\s.,%％°()（）]+$')
    # 邀请弹窗外的蒙层。实测弹窗亮区是 x340-955 / y130-560，四个角外都是
    # 亮度 20 左右的纯蒙层，左下角这一块没有主界面按钮，点它关弹窗最安全。
    HELP_BLANK_POINTS = ((100, 400), (640, 60), (1180, 400))

    # ------------------------------------------------------------------ 基础读写

    def _help_ticket(self) -> int:
        """读右下角百鬼券数量。读不出来返回 -1，不阻塞流程。"""
        self.screenshot()
        try:
            raw = self.O_HELP_TICKET.ocr(self.device.image, log=False)
        except Exception as e:
            logger.warning(f'帮砸：百鬼券数量 OCR 异常 {e}')
            return -1
        if raw is None:
            logger.warning('帮砸：百鬼券数量没读出来')
            return -1
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            logger.warning(f'帮砸：百鬼券数量识别结果异常 [{raw}]')
            return -1

    def _help_list_ocr(self) -> list:
        """OCR 好友列表，返回 [(文字, 全屏x, 全屏y)]，已过滤人数标记和固定文案。"""
        self.screenshot()
        roi = self.O_HELP_LIST.roi
        ox, oy = roi[0], roi[1]
        items = []
        try:
            results = self.O_HELP_LIST.detect_and_ocr(self.device.image)
        except Exception as e:
            logger.warning(f'帮砸：好友列表 OCR 异常 {e}')
            return items
        for res in results:
            text = (res.ocr_text or '').strip()
            if not text or self._help_is_noise(text):
                continue
            box = res.box
            cx = ox + int((box[0][0] + box[2][0]) / 2)
            cy = oy + int((box[0][1] + box[2][1]) / 2)
            items.append((text, cx, cy))
        return items

    @classmethod
    def _help_is_noise(cls, text: str) -> bool:
        """判断 OCR 文本是不是噪音（人数标记、页签名、搜索框提示）。"""
        if text in cls.HELP_IGNORE_TOKENS:
            return True
        # 60 / 60% / 60） / 50 这类是「最近受邀」旁边的人数标记
        if cls.HELP_NOISE_PATTERN.match(text):
            return True
        # 「最近受邀」「最近爱邀」「最近邀请」OCR 三种写法都有，按特征一刀切
        if text.startswith('最近') and '邀' in text:
            return True
        return False

    @staticmethod
    def _help_names(items: list) -> set:
        """取这一帧里所有名字的集合，用于判断滑动后列表有没有变。"""
        return {t for t, _, _ in items}

    @classmethod
    def _help_overlap(cls, a: set, b: set) -> float:
        """两帧名字集合的重合度，1.0 = 完全一样，0.0 = 毫不相干。"""
        if not a and not b:
            return 1.0
        if not a or not b:
            return 0.0
        return len(a & b) / max(len(a), len(b))

    @classmethod
    def _help_same(cls, a: set, b: set) -> bool:
        """两帧是否算「列表没动」。容忍 OCR 抖动，见 HELP_NAME_OVERLAP 的注释。"""
        if not a and not b:
            # 两帧一个名字都没读到：可能是正在滚动/被遮挡，不能当成「已经到底」，
            # 否则会漏掉下面的好友。多滑几次有 HELP_TO_TOP_MAX / HELP_SCROLL_MAX 兜底。
            logger.warning('帮砸：连续两帧都没读到好友名，继续滑')
            return False
        if not a or not b:
            return False
        # OCR 抖动通常只影响 1 条：一个名字被读错（退出一个、进入一个，共差 2 条）。
        # 名字少的时候重合度会被 1 条差异压到阈值以下，所以先按「差几条」兜底放行。
        if len(a ^ b) <= cls.HELP_NAME_ALLOW_DIFF:
            return True
        return cls._help_overlap(a, b) >= cls.HELP_NAME_OVERLAP

    def _help_step(self) -> int:
        """当前生效的手指位移（px）。会被 _help_adapt_step 在线自校准。"""
        return getattr(self, '_help_step_px', self.HELP_STEP_PX)

    def _help_scroll(self, to_bottom: bool) -> None:
        """滑一次列表。to_bottom=True 往下看更多好友，False 回顶部。

        不走 base_task.swipe()：往下滑的位移要能自适应调整，必须自己算坐标。
        回顶不需要连续覆盖，固定用 S_HELP_LIST_SCROLL_BACK 的大位移（一次 5~6 行）。
        """
        if not to_bottom:
            rule = self.S_HELP_LIST_SCROLL_BACK
            x1, y1, x2, y2 = rule.coord()
            self.device.swipe(
                p1=(x1, y1),
                p2=(x2, y2),
                duration=self.HELP_SWIPE_DURATION,
                control_name=rule.name,
            )
        else:
            # 起止点带随机抖动，避免每次都从同一像素开始（长时间同轨迹容易被判定为脚本）
            x = random.randint(600, 680)
            y1 = random.randint(485, 515)
            y2 = y1 - self._help_step()
            self.device.swipe(
                p1=(x, y1),
                p2=(x, y2),
                duration=self.HELP_SWIPE_DURATION,
                control_name=self.S_HELP_LIST_SCROLL.name,
            )
        # 框架把同名动作累加成「重复点击」，同一个素材滑满 10 次就抛
        # GameTooManyClickError（module/device/device.py 的 click_record_check）。
        # 长列表本来就要滑几十次，所以每次滑完立刻清记录；
        # 死循环风险由 HELP_TO_TOP_MAX / HELP_SCROLL_MAX 自己兜底。
        self.device.click_record_clear()
        # 等惯性滚完再截图，否则读到的是滚动中途帧
        time.sleep(self.HELP_SWIPE_SETTLE)

    @staticmethod
    def _help_measure_shift(prev_items: list, cur_items: list) -> float:
        """用同名好友的 y 位移量出这一帧列表实际滚了多少像素；量不出来返回 -1。

        取中位数而不是平均值：OCR 的 box 高度有抖动，个别条目会脱群。
        """
        pa = {t: y for t, _, y in prev_items}
        cb = {t: y for t, _, y in cur_items}
        common = set(pa) & set(cb)
        if not common:
            return -1.0
        deltas = sorted(pa[t] - cb[t] for t in common)
        return float(deltas[len(deltas) // 2])

    def _help_adapt_step(self, shift: float, label: str) -> None:
        """按实测滚幅自校准下次的滑动步长。

        minitouch 的滑动时长由距离决定（15px/10ms），改不动，所以不能靠「慢一点」
        压惯性，只能靠「滑短一点」。滑太远就把步长收一档，滑太少就放一档。
        """
        if shift <= 0:
            return
        lines = shift / self.HELP_LINE_HEIGHT
        old = self._help_step()
        if lines >= self.HELP_STEP_SAFE_LINES:
            new = max(self.HELP_STEP_PX_MIN, int(old * 0.6))
            if new != old:
                self._help_step_px = new
                logger.warning(
                    f'帮砸：「{label}」上次实际滚了 {shift:.0f}px（{lines:.1f} 行，'
                    f'安全线 {self.HELP_STEP_SAFE_LINES} 行），步长 {old} -> {new}px'
                )
        elif lines <= self.HELP_STEP_SLOW_LINES:
            new = min(self.HELP_STEP_PX_MAX, int(old * 1.4))
            if new != old:
                self._help_step_px = new
                logger.info(
                    f'帮砸：「{label}」上次只滚了 {shift:.0f}px（{lines:.1f} 行），'
                    f'步长 {old} -> {new}px'
                )

    def _help_back_step(self) -> None:
        """反着滑一小步（约 1 行），用于纠正「一次滑过头」。

        只在相邻两帧名字零重叠时调用，属于异常补救，不是常规路径。
        """
        px = max(40, int(self._help_step() * 0.6))
        x = 640
        y1 = 460
        y2 = y1 + px
        logger.info(
            f'帮砸：反滑一小步 {px}px'
            f'（约 {px * self.HELP_SCROLL_GAIN / self.HELP_LINE_HEIGHT:.1f} 行）'
        )
        self.device.swipe(
            p1=(x, y1),
            p2=(x, y2),
            duration=self.HELP_SWIPE_DURATION,
            control_name='hya_help_back_step',
        )
        self.device.click_record_clear()
        time.sleep(self.HELP_SWIPE_SETTLE)

    def _help_scroll_until_still(self, label: str, to_bottom: bool, limit: int, action: str) -> None:
        """一直滑到列表不再变化为止。limit 是真正滑出去的次数上限，纯防死循环。"""
        prev = self._help_names(self._help_list_ocr())
        still = 0
        done = 0
        while done < limit:
            self._help_scroll(to_bottom)
            done += 1
            cur = self._help_names(self._help_list_ocr())
            overlap = self._help_overlap(prev, cur)
            logger.info(f'帮砸：「{label}」{action}第 {done}/{limit} 次，可见 {len(cur)} 个名字，与上一帧重合 {overlap:.2f}')
            if self._help_same(prev, cur):
                still += 1
                if still >= self.HELP_SCROLL_STILL:
                    logger.info(f'帮砸：「{label}」{action}结束，列表不再变化（滑了 {done} 次）')
                    return
            else:
                still = 0
            prev = cur
        logger.warning(f'帮砸：「{label}」{action}达到上限 {limit} 次，按当前位置继续')

    def _help_find(self, items: list, pending: list):
        """在 OCR 结果里找待处理好友。先精确匹配，再退一步做包含匹配（容忍 OCR 少字/多字）。"""
        for text, cx, cy in items:
            if text in pending:
                return text, cx, cy
        for text, cx, cy in items:
            if len(text) < 2:
                continue
            for target in pending:
                if len(target) >= 2 and (target in text or text in target):
                    return target, cx, cy
        return None

    # ------------------------------------------------------------------ 弹窗与页签

    def _help_popup_open(self) -> bool:
        """打开「邀请好友」弹窗；已经开着就直接返回。"""
        if self.appear(self.I_CHECK_INVITATION):
            return True
        self.ui_click(self.I_HINVITE, self.I_CHECK_INVITATION, interval=2)
        self.screenshot()
        if self.appear(self.I_CHECK_INVITATION):
            logger.info('帮砸：已打开邀请好友弹窗')
            return True
        logger.warning('帮砸：点「邀请好友」后没看到弹窗')
        return False

    def _help_popup_close(self) -> bool:
        """点弹窗外的蒙层把弹窗关掉。"""
        if not self.appear(self.I_CHECK_INVITATION):
            return True
        for x, y in self.HELP_BLANK_POINTS:
            logger.info(f'帮砸：点弹窗外蒙层 ({x},{y}) 关闭弹窗')
            self.device.click(x=x, y=y, control_name='hya_help_blank')
            time.sleep(1)
            self.screenshot()
            if not self.appear(self.I_CHECK_INVITATION):
                logger.info('帮砸：邀请弹窗已关闭')
                return True
        logger.warning('帮砸：弹窗没能关掉，继续后续收尾')
        return False

    def _help_tab(self, rule, label: str) -> None:
        """点一次页签。点已选中的页签没有副作用，所以不需要先判断当前在哪一页。"""
        x, y, w, h = rule.roi_front
        logger.info(f'帮砸：切到「{label}」页签 @ ({x + w // 2},{y + h // 2})')
        self.device.click(x=x + w // 2, y=y + h // 2, control_name=f'hya_help_tab_{label}')
        time.sleep(0.8)

    # ------------------------------------------------------------------ 点好友

    def _help_pick(self, name: str, x: int, y: int) -> bool:
        """点好友。返回 True 表示弹窗关了（邀请成功），False 表示对方今天已满 3 次。"""
        logger.info(f'帮砸：点击好友 [{name}] @ ({x},{y})')
        self.device.click(x=x, y=y, control_name='hya_help_pick')
        deadline = time.time() + self.HELP_PICK_WAIT
        while time.time() < deadline:
            time.sleep(self.HELP_PICK_INTERVAL)
            self.screenshot()
            if not self.appear(self.I_CHECK_INVITATION):
                logger.info(f'帮砸：弹窗已关闭，[{name}] 邀请成功')
                return True
        logger.info(f'帮砸：点了 [{name}] 弹窗没有关闭，判定它今天已被邀请满 {self.HELP_PER_FRIEND} 次，本次跳过')
        return False

    def _help_scan_tab(self, label: str, pending: list, skipped: set, tab_rule):
        """在某个页签里从顶滑到底找待处理好友，找到一个就点掉并返回名字。

        :return: 邀请成功的好友名；None 表示这个页签没有可处理的对象了
        """
        if not pending:
            return None
        if not self._help_popup_open():
            logger.warning(f'帮砸：「{label}」页签打不开邀请弹窗，跳过')
            return None
        self._help_tab(tab_rule, label)
        self._help_scroll_until_still(label, to_bottom=False, limit=self.HELP_TO_TOP_MAX, action='回顶')

        prev = self._help_names(self._help_list_ocr())
        still = 0
        done = 0
        while done < self.HELP_SCROLL_MAX:
            items = self._help_list_ocr()
            hit = self._help_find(items, pending)
            if hit is not None:
                name, x, y = hit
                if self._help_pick(name, x, y):
                    return name
                skipped.add(name)
                pending.remove(name)
                logger.info(f'帮砸：[{name}] 已加入跳过名单，本次剩余待邀请 {len(pending)} 人')
                # 弹窗还开着、位置也没变，接着用当前这一屏找下一个
                prev = self._help_names(items)
                still = 0
                continue

            prev_items = items
            self._help_scroll(to_bottom=True)
            done += 1
            cur_items = self._help_list_ocr()
            cur = self._help_names(cur_items)

            # 用同名好友的 y 位移量出这一帧实际滚了多远，并据此收敛步长
            shift = self._help_measure_shift(prev_items, cur_items)
            if shift > 0:
                logger.info(
                    f'帮砸：「{label}」实测滚动 {shift:.0f}px'
                    f'（{shift / self.HELP_LINE_HEIGHT:.1f} 行，步长 {self._help_step()}px）'
                )
                self._help_adapt_step(shift, label)

            # 滑过头兜底：正常一次只滑 2 行，前后必然有 3 行重叠；
            # 一旦零重叠说明惯性把整屏带走了，中间那几行从没进过任何一帧，
            # 反滑一步补看。宁可多绕一次，也不能漏好友。
            if (
                len(prev) >= self.HELP_OVERLAP_GUARD
                and len(cur) >= self.HELP_OVERLAP_GUARD
                and not (prev & cur)
            ):
                logger.warning(
                    f'帮砸：「{label}」第 {done} 次滑动后与上一帧零重叠'
                    f'（{len(prev)} vs {len(cur)} 个名字），判定滑过头，反滑一步补看'
                )
                self._help_back_step()
                cur = self._help_names(self._help_list_ocr())
                logger.info(
                    f'帮砸：「{label}」反滑后可见 {len(cur)} 个名字，'
                    f'与上一帧重合 {self._help_overlap(prev, cur):.2f}'
                )

            overlap = self._help_overlap(prev, cur)
            logger.info(f'帮砸：「{label}」往下翻第 {done}/{self.HELP_SCROLL_MAX} 次，可见 {len(cur)} 个名字，与上一帧重合 {overlap:.2f}')
            if self._help_same(prev, cur):
                still += 1
                logger.info(f'帮砸：「{label}」列表不再变化（{still}/{self.HELP_SCROLL_STILL}）')
                if still >= self.HELP_SCROLL_STILL:
                    logger.info(f'帮砸：「{label}」已滑到底，本页签没有待邀请的好友了')
                    break
            else:
                still = 0
            prev = cur
        return None

    # ------------------------------------------------------------------ 主流程

    def _help_next_round(self, groups, done: dict, skipped: set):
        """按「好友 → 跨区」的顺序找一个还能邀请的好友。

        :return: (好友名, 页签名)；None 表示两份名单都处理完了
        """
        for label, names, tab_rule in groups:
            pending = [
                n for n in names
                if n not in skipped and done.get(n, 0) < self.HELP_PER_FRIEND
            ]
            if not pending:
                continue
            name = self._help_scan_tab(label, pending, skipped, tab_rule)
            if name is not None:
                return name, label
        return None

    def _help_finish(self) -> None:
        """收尾：关弹窗 → 关百鬼夜行主界面 → 回庭院。"""
        self.screenshot()
        if self.appear(self.I_CHECK_INVITATION):
            self._help_popup_close()
        for _ in range(5):
            self.screenshot()
            if not self.appear(self.I_HCLOSE_RED):
                break
            if self.appear_then_click(self.I_HCLOSE_RED, interval=1):
                continue
        if self.appear(self.I_HACCESS):
            logger.warning('帮砸：百鬼夜行界面还开着，按右上角红叉再关一次')
            self.appear_then_click(self.I_HCLOSE_RED, interval=1)
        try:
            self.goto_page(page_main)
            logger.info('帮砸：已回到庭院')
        except Exception as e:
            logger.warning(f'帮砸：回庭院失败（{e}），本次不再重试')

    @classmethod
    def _help_trim_names(cls, names: list, label: str) -> list:
        """单份名单最多 HELP_MAX_NAMES 人，超出截断并告警（不静默丢弃）。"""
        if len(names) <= cls.HELP_MAX_NAMES:
            return names
        dropped = names[cls.HELP_MAX_NAMES:]
        logger.warning(
            f'帮砸：{label}名单填了 {len(names)} 人，超过上限 {cls.HELP_MAX_NAMES}，'
            f'只处理前 {cls.HELP_MAX_NAMES} 人；被忽略的 {len(dropped)} 人：{dropped}'
        )
        return names[:cls.HELP_MAX_NAMES]

    def help_friend_run(self) -> None:
        """帮好友砸百鬼夜行，入口。"""
        conf = self._config.hyakkiyakou_config
        friend_names = self._help_trim_names(conf.hya_help_friend_names_v, '同区')
        remote_names = self._help_trim_names(conf.hya_help_remote_names_v, '跨区')
        max_round = max(1, int(conf.hya_help_max_round))

        logger.hr('帮好友砸百鬼夜行', 1)
        logger.info(f'帮砸：同区名单 {len(friend_names)} 人 {friend_names}')
        logger.info(f'帮砸：跨区名单 {len(remote_names)} 人 {remote_names}')
        logger.info(f'帮砸：最多 {max_round} 局，每个好友最多邀请 {self.HELP_PER_FRIEND} 次')
        logger.info(
            f'帮砸：列表按每行 2 人、行高 {self.HELP_LINE_HEIGHT}px 滑动，'
            f'每次滑 {self.HELP_STEP_PX}px（约 {self.HELP_LINES_PER_SCROLL} 行），'
            f'单页签上限 {self.HELP_SCROLL_MAX} 次'
        )

        if not friend_names and not remote_names:
            logger.warning('帮砸：同区和跨区名单都是空的，不做任何操作直接结束')
            self.set_next_run(task='Hyakkiyakou', success=True, finish=True)
            raise TaskEnd

        self.goto_page(page_hyakkiyakou)
        self.screenshot()
        if not self.appear(self.I_HACCESS):
            logger.error('帮砸：没能进入百鬼夜行主界面，放弃本次任务')
            raise RequestHumanTakeover('Failed to navigate to Hyakkiyakou page')

        ticket = self._help_ticket()
        if ticket == 0:
            logger.warning('帮砸：百鬼券数量为 0，本次任务一个都不砸，直接结束')
            self._help_finish()
            self.set_next_run(task='Hyakkiyakou', success=True, finish=True)
            raise TaskEnd
        if ticket >= 0:
            logger.info(f'帮砸：开始前百鬼券数量 = {ticket}')
        else:
            logger.info('帮砸：开始前百鬼券数量没读出来，继续跑')

        done: dict = {}
        skipped: set = set()
        groups = (
            ('好友', friend_names, self.I_FRIEND_SAME_1),
            ('跨区', remote_names, self.I_FRIEND_REMOTE_1),
        )

        rounds = 0
        while rounds < max_round:
            target = self._help_next_round(groups, done, skipped)
            if target is None:
                logger.info('帮砸：两份名单都处理完了')
                break
            name, label = target
            rounds += 1
            logger.info(f'帮砸：第 {rounds}/{max_round} 局，[{name}]（{label}）邀请成功，开始开局')
            self._enter_and_play()
            done[name] = done.get(name, 0) + 1
            logger.info(f'帮砸：第 {rounds} 局结束，[{name}] 本次累计 {done[name]} 次')

            remain_ticket = self._help_ticket()
            if remain_ticket == 0:
                logger.warning('帮砸：百鬼券已经用完（0），不再继续')
                break
            logger.info(f'帮砸：剩余百鬼券 {remain_ticket}')

        if rounds >= max_round:
            logger.warning(f'帮砸：达到局数上限 {max_round}，提前收手')

        logger.info(
            f'帮砸汇总：成功 {sum(done.values())} 局 / {len(done)} 个好友 '
            f'{[f"{k}×{v}" for k, v in done.items()]}；跳过 {len(skipped)} 个 {sorted(skipped)}'
        )
        self._help_finish()
        self.set_next_run(task='Hyakkiyakou', success=True, finish=True)
        raise TaskEnd
