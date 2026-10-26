# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time

import numpy as np

from module.logger import logger

from tasks.Component.Buy.buy import Buy
# from tasks.RichMan.assets import RichManAssets
from tasks.RichMan.mall.navbar import MallNavbar
from tasks.RichMan.mall.shikigami_map import normalize_shikigami
from tasks.RichMan.config import Consignment as ConsignmentStore

# 素材规则由 json + dev_tools/assets_extract.py 生成，落在 tasks/RichMan/assets.py，
# 经 MallNavbar -> RichManAssets 继承获得：
#   I_CONSIGN_TICKET         寄售券图标（详情页有寄售时才出现）
#   I_CONSIGN_NO_TICKET      「寄售券不足」提示
#   I_CONSIGN_BUY_SELECTED   「购买」标签选中态
#   I_CONSIGN_BUY_SUCCESS    购买成功弹窗
#   I_CONSIGN_TAB_SHOP       顶部标签「饰品」（购买页/寄售页有，兑换页无）
#   I_CONSIGN_TAB_SHIKIGAMI  顶部子标签「式神碎片」选中态
#   I_CONSIGN_LIST_TICKET    碎片列表卡片价格前缀的寄售券图标
#   O_CONSIGN_SHIKIGAMI_LIST 列表区域 RuleOcr(mode=Full)
OCR_LIST_ROI = (100, 180, 1060, 140)
# 右上竖排灯「购买」/ 顶部子标签「式神碎片」（取其素材 roi_front 的中心）
CONSIGN_BUY_TAB_POS = (1208, 135)
CONSIGN_TAB_SHIKIGAMI_POS = (471, 109)
# 「寄售券不足，是否前往兑换」确认弹窗的「取消」按钮
CONSIGN_NO_TICKET_CANCEL_POS = (523, 446)
# 碎片数量购买窗：点弹窗外侧空白处关闭
CONSIGN_DIALOG_CLOSE_POS = (120, 660)
# 碎片列表一屏能看到 5 个式神名；「兑换」页 0 个、「推荐」页 1 个
CONSIGN_LIST_MIN_NAMES = 2


class Consignment(Buy, MallNavbar):

    def execute_consignment(self, con: ConsignmentStore=None):
        """
        必须要求这个是第一个执行的对于 商店来说
        :param con:
        :return:
        """
        if not con:
            con = self.config.rich_man.consignment
        if not con.enable:
            logger.info('Consignment is not enable')
            return
        # 买售货券 / 买式神碎片 是两个独立开关，任一开启都要进寄售屋
        if not con.buy_sale_ticket and not con.buy_shikigami:
            logger.info('Consignment: buy_sale_ticket and buy_shikigami both disabled')
            return
        self._enter_consignment()
        if con.buy_sale_ticket:
            self._execute_buy_sale_ticket(con)
        if con.buy_shikigami:
            self._execute_buy_shikigami(con)

    def _execute_buy_sale_ticket(self, con: ConsignmentStore):
        """
        购买售货券（原有逻辑，原样抽出成方法）

        买券必须点右侧「兑换」竖灯，所以买完（以及钱不够/数量为 0 提前退出）都会
        停在「兑换」页，「购买」灯是未选中态。大富翁买完券要去秘卷屋、一键协战买完券
        要接着买碎片，都是在「购买」页上才好找按钮，所以结尾统一切回「购买」页
        （点亮「购买」灯），不留人在兑换页上盲找。
        """
        try:
            self.ui_click(self.I_CON_ENTER, self.I_CON_ENTER_CHECK)
            time.sleep(0.5)
            self.screenshot()
            if not self.mall_check_money(3, 100):
                logger.warning('Consignment money is not enough')
                return
            remain_number = self.O_CON_NUMBER.ocr(self.device.image)
            if remain_number == 0:
                logger.warning('Consignment number is 0')
                return
            if remain_number == 10:
                self.buy_more(self.I_CON_TICKET)
            else:
                self.buy_one(self.I_CON_TICKET, remain_number)
            logger.info('Consignment buy_sale_ticket is success')
        finally:
            # 无论买没买到，都从「兑换」页切回「购买」页
            self._back_to_buy_tab()

    def _execute_buy_shikigami(self, con: ConsignmentStore):
        """
        寄售屋 · 购买指定式神碎片（OCR 方案，不用图片匹配）

        流程：
          1. 切到「购买」→「式神碎片」，用「饰品」标签 + 列表紫票 + OCR 式神名
             三重判据确认真的到了碎片列表
          2. 滑动 + OCR 找到目标式神的名字
          3. 点卡片进详情页
          4. 详情页看寄售券图标：没有 = 暂无寄售，退回列表跳过
          5. 有寄售：点第一行碎片 → 弹数量窗 → 拉满 → 购买
          6. 若提示「寄售券不足」→ 取消 → 退回列表
        """
        cn_name = con.shikigami_name.value
        logger.hr(f'Buy shikigami: {cn_name}', 2)

        # 1. 切到「购买」→「式神碎片」
        if not self._goto_buy_shikigami_list():
            logger.warning('Consignment: can not open shikigami list, skip this round')
            return

        # 2. 先滑回列表最左，再 OCR 查找
        #    清一次点击记录：回最左最多 8 次 + 查找最多 10 次滑动，累计会撞上
        #    框架的「同一按钮 ≥10 次」保护而报 GameTooManyClickError
        self.device.click_record_clear()
        self._back_to_list_head()
        pos = self._find_shikigami_by_ocr(cn_name, max_swipe=10)
        if not pos:
            logger.warning(f'{cn_name} not found in shikigami list, skip')
            return
        logger.info(f'Found {cn_name} at {pos}')

        # 3. 点卡片进详情
        #    注意：右侧 x≈1163~1232 是「购买/寄售/兑换」竖排标签，点那里会切标签而不是进详情。
        #    点名字条所在卡片左侧 60px 处最安全。
        self.device.click(pos[0] + 60, 350, control_name=f'CONSIGN_CARD_{cn_name}')
        time.sleep(2)

        # 4. 详情页：没有寄售券图标 = 暂无寄售
        self.screenshot()
        if not self.appear(self.I_CONSIGN_TICKET):
            logger.warning(f'{cn_name} 暂无寄售，跳过')
            self._exit_consignment_detail()
            return

        # 5. 点第一行碎片（恒点第一行）
        self.device.click(1000, 340, control_name='CONSIGN_ROW_1')
        time.sleep(1.5)

        # 6. 拉满 + 购买
        bought = self._consign_buy_first_row(cn_name)

        # 7. 退回列表
        self._exit_consignment_detail()

        if bought:
            logger.info(f'Buy {cn_name} fragment success')
        else:
            logger.warning(f'Buy {cn_name} fragment failed')

    # ---------------- OCR 查找 ----------------

    def _ocr_shikigami_names(self) -> list:
        """
        读当前帧列表区的式神名（已归一化，认不出的丢弃）

        这是判断「到底在不在碎片列表页」的内容级依据，比标签高亮图可靠得多。
        """
        self.screenshot()
        results = self.O_CONSIGN_SHIKIGAMI_LIST.detect_and_ocr(
            self.device.image, logDisplay=False)
        names = []
        for r in results:
            name = normalize_shikigami(str(r.ocr_text))
            if name:
                names.append(name)
        return names

    def _back_to_buy_tab(self, retry: int = 3) -> bool:
        """
        从「兑换」/「寄售」页切回「购买」页（把「购买」灯点亮）

        判据用 I_CONSIGN_TAB_SHOP（顶部「饰品」标签）：
        购买页/寄售页都是 1.00，兑换页只有 0.035。
        它分不开购买页和寄售页，但这里的目的只是「确认已经离开兑换页」，
        已经在购买页时再点一次「购买」灯是无副作用的，可以放心每轮都点。
        """
        for i in range(retry):
            self.device.click(*CONSIGN_BUY_TAB_POS, control_name='CONSIGN_TAB_BUY')
            time.sleep(1)
            self.screenshot()
            if self.appear(self.I_CONSIGN_TAB_SHOP):
                logger.info('Consignment back to buy tab')
                return True
            logger.warning(f'Consignment still on exchange tab ({i + 1}/{retry})')
        return False

    def _goto_buy_shikigami_list(self, retry: int = 3) -> bool:
        """
        切到 寄售屋 →「购买」→「式神碎片」，并确认真的到了碎片列表

        为什么不用 I_CONSIGN_BUY_SELECTED 判断：
        买完寄售券后界面停在「兑换」页，此时「购买」是**未选中**的，
        但该模板在未选中态实测仍有 0.8785~0.9015 分（阈值 0.8）→ 误判成已在
        购买页；接着点「式神碎片」子标签是空点、列表滑动全部无效，最后 18 次
        滑动触发 GameTooManyClickError 把任务打断。而且高亮底色会在橙/蓝之间
        流动，模板匹配本身就不稳。

        现在的三重判据（实测分数，购买-碎片页 / 寄售页 / 兑换页）：
          I_CONSIGN_TAB_SHOP      「饰品」   1.00 / 1.00 / 0.035
              → 证明顶部标签栏渲染出来了（= 已离开兑换页）。购买页和寄售页
                分不开，所以只用来判断要不要重试点「购买」。
          I_CONSIGN_TAB_SHIKIGAMI 「式神碎片」1.00 / 1.00 / 0.050
              → 同上，两页共享这一行标签；主要用来提供点击坐标 (471,109)，
                并挡一下"界面还没刷新完就白跑 OCR"。
          I_CONSIGN_LIST_TICKET   列表紫票 1.00 / 0.39 / 0.38
              → 真正能锁定「购买页的碎片列表」的判据。唯一盲区是整页
                没有任何在售碎片时不出现，所以再叠加 OCR 式神名数量兜底。
        """
        for i in range(retry):
            # 第一步就是切到「购买」页：_execute_buy_sale_ticket 结尾已经切过一次，
            # 重复点无副作用；这里保留是为了单跑本方法时也能自愈
            if not self._back_to_buy_tab(retry=1):
                continue

            # 无条件点「式神碎片」：已经在碎片页时这一下无副作用
            self.device.click(*CONSIGN_TAB_SHIKIGAMI_POS, control_name='CONSIGN_TAB_SHIKIGAMI')
            time.sleep(1.2)
            self.screenshot()
            if not self.appear(self.I_CONSIGN_TAB_SHIKIGAMI):
                logger.warning(f'Consignment sub tab row not ready ({i + 1}/{retry})')
                continue

            names = self._ocr_shikigami_names()  # 内部会重新 screenshot
            has_ticket = self.appear(self.I_CONSIGN_LIST_TICKET)
            if len(names) >= CONSIGN_LIST_MIN_NAMES or has_ticket:
                logger.info(f'Consignment shikigami list ready: names={names[:5]} '
                            f'ticket={has_ticket}')
                return True
            logger.warning(f'Consignment shikigami list not ready ({i + 1}/{retry}), '
                           f'names={names} ticket={has_ticket}')
        return False


    def _find_shikigami_by_ocr(self, cn_name: str, max_swipe: int = 10):
        """
        用 OCR 在列表里找式神

        不走 list_find：它的 ocr 模式是精确 keyword 匹配（base_ocr.py 的
        ocr_appear 还有"单字退化匹配"的 TODO 坑），容不得形近字。
        这里用 detect_and_ocr 拿全部文本，再经 normalize_shikigami 归一化。

        :return: 名字条中心坐标 (x, y)；找不到返回 None
        """
        for _ in range(max_swipe):
            self.screenshot()
            results = self.O_CONSIGN_SHIKIGAMI_LIST.detect_and_ocr(
                self.device.image, logDisplay=False)
            for r in results:
                if normalize_shikigami(str(r.ocr_text)) == cn_name:
                    return self._box_center(r)
            # 没找到就往前滑一张（起点取卡片中部，落在间隙会滑不动）
            self.device.swipe(p1=(846, 310), p2=(378, 310))
            time.sleep(1.2)
        return None

    @staticmethod
    def _box_center(result) -> tuple:
        """
        BoxedResult.box 转成绝对坐标中心点

        box 相对 OCR roi（因为 detect_and_ocr 内部先按 roi 裁剪），
        所以要把 roi 左上角加回来。box 可能是 [x,y,w,h] 或 4 个点，两种都兼容。
        """
        arr = np.asarray(result.box, dtype=float).reshape(-1)
        if arr.size == 4:
            x, y, w, h = arr
            cx, cy = x + w / 2, y + h / 2
        else:
            pts = arr.reshape(-1, 2)
            cx, cy = pts[:, 0].mean(), pts[:, 1].mean()
        return int(cx + OCR_LIST_ROI[0]), int(cy + OCR_LIST_ROI[1])

    def _back_to_list_head(self):
        """
        把式神列表滑回最左端

        OCR 查找是单向向前滑的，如果上一次任务停在列表中间，靠前的式神就搜不到了。
        这里用第一个式神（初音未来）的名字做标志。
        """
        for _ in range(8):
            self.screenshot()
            results = self.O_CONSIGN_SHIKIGAMI_LIST.detect_and_ocr(
                self.device.image, logDisplay=False)
            if any(normalize_shikigami(str(r.ocr_text)) == '初音未来' for r in results):
                return True
            # 向右滑（回到前面的式神），起点取卡片中部避免落在间隙
            self.device.swipe(p1=(400, 310), p2=(950, 310))
            time.sleep(1)
        logger.warning('Can not scroll shikigami list back to head')
        return False

    # ---------------- 购买 ----------------

    # ---------------- 弹窗处理 ----------------

    def _close_no_ticket_dialog(self, retry: int = 4) -> bool:
        """
        关掉「寄售券不足，是否前往兑换」确认弹窗（点「取消」，不点「确定」）

        按钮定位优先用全局通用素材 `I_UI_CANCEL`（实测在这个弹窗上 0.9975 命中，
        roi_front=(438,405,177,62) 正好是「取消」按钮），素材没匹配上才退回
        固定坐标 CONSIGN_NO_TICKET_CANCEL_POS。
        注意别用 `I_UI_CONFIRM`——那是「确定」（0.9976 命中），点了会跳去兑换页。

        必须「点一次 + 校验一次」地循环，不能只点一次：
        实测这个按钮偶尔第一下点不动（弹窗刚弹出、按钮还没响应点击），
        点一次就往下走的话弹窗会一直盖在最上层，后面所有点击全被它吃掉——
        2026-10-25 17:52 douji4 实况就是这样崩的：
          取消没关掉 → 详情页红 X 连点 4 次全无效（Failed to leave consignment detail page）
          → back_mall() 的黄箭头继续点到 10 次 → GameTooManyClickError 打断整个任务
        """
        for _ in range(retry):
            self.screenshot()
            if not self.appear(self.I_CONSIGN_NO_TICKET):
                return True
            if not self.appear_then_click(self.I_UI_CANCEL, interval=1):
                # 素材没匹配到（换皮/布局变化）→ 退回固定坐标
                self.device.click(*CONSIGN_NO_TICKET_CANCEL_POS,
                                  control_name='CONSIGN_NO_TICKET_CANCEL')
            time.sleep(1)
        self.screenshot()
        if self.appear(self.I_CONSIGN_NO_TICKET):
            logger.warning('Consignment: no-ticket dialog still on screen')
            return False
        return True

    def _close_purchase_dialog(self, retry: int = 3) -> bool:
        """
        关掉碎片数量购买窗（点弹窗外侧空白处），同样是点一次校验一次

        数量窗也是模态的，留在屏幕上会把红 X 一起挡住。
        """
        for _ in range(retry):
            self.screenshot()
            if not self.appear(self.I_BUY_PLUS):
                return True
            self.device.click(*CONSIGN_DIALOG_CLOSE_POS,
                              control_name='CONSIGN_CLOSE_DIALOG')
            time.sleep(1)
        self.screenshot()
        return not self.appear(self.I_BUY_PLUS)

    def close_local_modal(self) -> bool:
        """
        寄售屋自己的模态弹窗：「寄售券不足」确认框 + 碎片数量购买窗

        供 MallNavbar.back_mall() 兜底时调用。这两个窗盖在最上层时，
        黄箭头和红 X 全点不到，导航层的通用关闭动作（只会试红 X / 黄箭头 / 蓝箭头）
        也救不了，必须由知道它们长什么样的这里来收。
        """
        closed = False
        self.screenshot()
        if self.appear(self.I_CONSIGN_NO_TICKET):
            logger.warning('Consignment: close no-ticket dialog for back_mall')
            closed = self._close_no_ticket_dialog() or closed
        self.screenshot()
        if self.appear(self.I_BUY_PLUS):
            logger.warning('Consignment: close purchase dialog for back_mall')
            closed = self._close_purchase_dialog() or closed
        return closed

    def _consign_buy_first_row(self, cn_name: str) -> bool:
        """
        购买窗内操作：拉满 → 购买 → 处理「寄售券不足」
        :return: True 购买成功 / False 失败或货币不足
        """
        # 等购买窗出现
        appear = False
        for _ in range(6):
            self.screenshot()
            if self.appear(self.I_BUY_PLUS):
                appear = True
                break
            time.sleep(0.5)
        if not appear:
            logger.warning('Purchase dialog not found')
            return False

        # 拉满（连点两次，与 buy_more 的 number=None 分支一致）
        self.appear_then_click(self.I_BUY_PLUS, interval=0.4)
        time.sleep(0.8)
        self.appear_then_click(self.I_BUY_PLUS, interval=0.4)
        time.sleep(0.6)

        # 点购买
        self.click(self.C_BUY_MORE, interval=1)
        time.sleep(1.5)

        # 货币不足：关掉提示并取消
        self.screenshot()
        if self.appear(self.I_CONSIGN_NO_TICKET):
            logger.warning(f'{cn_name}: 寄售券不足，取消购买')
            self._close_no_ticket_dialog()
            # 提示关掉后仍停在数量窗，再点弹窗外侧关掉它
            self._close_purchase_dialog()
            return False

        # 购买成功
        self.screenshot()
        if self.appear(self.I_CONSIGN_BUY_SUCCESS):
            logger.info('Purchase success')
            # 直接点「购买成功」标题本身关闭弹窗。
            # 早期版本点屏幕中心 (640,360)，那里是式神立绘、不在弹窗上，白点了 6 次。
            for _ in range(4):
                self.screenshot()
                if not self.appear(self.I_CONSIGN_BUY_SUCCESS):
                    break
                self.appear_then_click(self.I_CONSIGN_BUY_SUCCESS, interval=1)
            return True
        # 兼容可能是通用奖励弹窗的情况
        if self.ui_reward_appear_click():
            for _ in range(10):
                self.screenshot()
                if not self.appear(self.I_UI_REWARD, threshold=0.6):
                    break
                if self.ui_reward_appear_click():
                    continue
            return True
        logger.warning('Purchase result unknown')
        return False

    def _exit_consignment_detail(self):
        """
        从详情页退回列表

        判据用「购买」标签而不是寄售券：详情页会盖住右侧标签（实测 0.65），
        列表页标签完整可见（1.000）。寄售券不能用——没寄售时详情页也没有券，
        会和列表页混淆，导致点不开红 X 而卡死。

        每轮点红 X 之前先检查有没有模态弹窗挡着（寄售券不足 / 数量窗）。
        模态窗在的时候红 X 是点不到的，硬点 4 次只会白费，
        接着 back_mall() 的黄箭头会一路点到触发 GameTooManyClickError。
        """
        for _ in range(6):
            self.screenshot()
            if self.appear(self.I_CONSIGN_BUY_SELECTED, threshold=0.9):
                logger.info('Back to shikigami list')
                break
            if self.appear(self.I_CONSIGN_NO_TICKET):
                logger.warning('Consignment: dialog on screen, close it before back')
                self._close_no_ticket_dialog()
                continue
            if self.appear(self.I_BUY_PLUS):
                logger.warning('Consignment: purchase dialog on screen, close it before back')
                self._close_purchase_dialog()
                continue
            self.appear_then_click(self.I_UI_BACK_RED, interval=1)
            time.sleep(1.5)
        else:
            logger.warning('Failed to leave consignment detail page')
        self.device.click_record_clear()


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = Consignment(c, d)

    t.execute_consignment()
