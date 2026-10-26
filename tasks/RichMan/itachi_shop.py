# This Python file uses the following encoding: utf-8
# @author XDeolx (testoyj) / Maratrain (mine)
# 移植来源：
#   https://github.com/XDeolx/OnmyojiAutoScript 分支 testoyj  tasks/RichMan/itachi_shop.py（2026-10-23，逻辑最全）
#   https://github.com/Maratrain/OnmyojiAutoScript 分支 mine  tasks/RichMan/itachi_shop.py（2026-10-26，入口 ROI 加宽）
#
# 功能：进「娱乐」页的鼬乐园商店，把最前排自动前移的鼬乐礼盒买掉（最多 3 个/次）。
# 说明：本模块是 RichMan（大富翁）任务里的一个可选步骤，默认关闭。
#
# 彻底移除的办法（三步，随时可做，不影响其他功能）：
#   1. 删掉本文件与 tasks/RichMan/itachi_shop/ 整个目录
#   2. 删掉 tasks/RichMan/config.py 里的 class ItachiCoinShop 与 RichMan.itachi_coin_shop 两处
#   3. 删掉 tasks/RichMan/page.py 里 page_itachi_shop 的三段，
#      以及 tasks/RichMan/script_task.py 里 import / 基类 / 调用 三处
#   只关功能不动代码：GUI 里把「鼬乐币商店 → 购买礼盒」关掉即可（本模块所有动作都在开关之后）。
import time

from module.exception import GamePageUnknownError, RequestHumanTakeover, TaskEnd
from module.logger import logger

from tasks.Chess.assets import ChessAssets
from tasks.Component.Buy.buy import Buy
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_entertainment
from tasks.RichMan.assets import RichManAssets
from tasks.RichMan.config import ItachiCoinShop as ItachiCoinShopConfig
from tasks.RichMan.page import page_itachi_shop


class ItachiCoinShop(Buy, GameUi, RichManAssets):
    """购买鼬乐园商店中自动前移的鼬乐礼盒。"""

    ENTERTAINMENT_OVERLAY_TIMEOUT = 8.0
    ENTERTAINMENT_STABLE_TIME = 1.0
    GIFT_DIALOG_TIMEOUT = 3.0
    PURCHASE_RESULT_TIMEOUT = 8.0
    PURCHASE_RETURN_GRACE = 3.0
    REWARD_DISMISS_TIMEOUT = 5.0
    MAX_GIFT_PURCHASES = 3
    GIFT_OPENED = 'opened'
    GIFT_PURCHASED = 'purchased'
    GIFT_STOP = 'stop'

    def execute_itachi_coin_shop(self, con: ItachiCoinShopConfig = None) -> None:
        """入口。任何异常都在这里兜住，绝不让它打断 RichMan 的后续步骤。"""
        if not con:
            con = self.config.rich_man.itachi_coin_shop
        if not con.itachi_coin_buy_jade:
            logger.info('鼬乐币商店购买未启用')
            return

        try:
            self._itachi_coin_shop_flow()
        except (TaskEnd, RequestHumanTakeover):
            # 这两类是全局控制流，必须原样往上抛
            raise
        except Exception as e:
            # 活动没开、入口点不到、OCR 服务异常……一律只记警告并跳过
            logger.warning(f'鼬乐币商店流程异常，本次跳过（不影响后续商店）：{e}')
        finally:
            self.device.click_record_clear()

    def _itachi_coin_shop_flow(self) -> None:
        logger.hr('鼬乐币商店', 1)
        self.goto_page(page_entertainment)
        if not self._wait_entertainment_overlay_closed():
            logger.warning('鼬乐园附属页未关闭，跳过本次购买')
            return

        self.goto_page(page_itachi_shop)
        purchased = 0
        for _ in range(self.MAX_GIFT_PURCHASES):
            open_result = self._open_front_gift_once()
            if open_result != self.GIFT_OPENED:
                # 已买过的礼盒不会再弹购买框，这里只点一次就收手，避免同一位置高频试探
                logger.info('鼬乐币礼盒已购买或不可购买，结束')
                break

            purchase_result = self._buy_opened_gift()
            if purchase_result != self.GIFT_PURCHASED:
                break
            purchased += 1
            self.device.click_record_clear()

        logger.info(f'鼬乐币礼盒购买完成: count={purchased}')
        self._leave_itachi_shop()

    def _wait_entertainment_overlay_closed(self) -> bool:
        """处理鼬乐园附属页：有「跳过」就点，画面稳定后才继续。"""
        deadline = time.monotonic() + self.ENTERTAINMENT_OVERLAY_TIMEOUT
        stable_since = None
        while time.monotonic() < deadline:
            self.screenshot()
            if self.appear(ChessAssets.I_SKIP):
                self.appear_then_click(ChessAssets.I_SKIP, interval=0.5)
                stable_since = None
                continue

            if stable_since is None:
                stable_since = time.monotonic()
                continue
            if time.monotonic() - stable_since >= self.ENTERTAINMENT_STABLE_TIME:
                return True
        return False

    def _open_front_gift_once(self) -> str:
        """点一次最前排礼盒，等购买框出现。"""
        self.screenshot()
        self.click(self.C_ITACHI_GIFT)
        deadline = time.monotonic() + self.GIFT_DIALOG_TIMEOUT
        while time.monotonic() < deadline:
            self.screenshot()
            if self.appear(self.I_BUY_PLUS):
                return self.GIFT_OPENED
        return self.GIFT_STOP

    def _read_itachi_coin(self) -> tuple[int, int] | None:
        result = self.O_ITACHI_COIN.ocr(self.device.image)
        if isinstance(result, tuple) and len(result) >= 3:
            current, _, total = result[:3]
            if total > 0:
                logger.info(f'鼬乐币: {current}/{total}')
                return int(current), int(total)
        logger.warning(f'鼬乐币 OCR 失败: result={result}')
        return None

    def _read_buy_cost(self) -> int | None:
        result = self.O_ITACHI_BUY_COST.ocr(self.device.image)
        if isinstance(result, int) and result > 0:
            logger.info(f'鼬乐礼盒价格: cost={result}')
            return result
        logger.warning(f'鼬乐礼盒价格 OCR 失败: result={result}')
        return None

    def _read_purchase_status(self) -> str | None:
        # Full 模式的关键词匹配会回退到单字命中，这里必须核对完整提示。
        results = self.O_ITACHI_COIN_INSUFFICIENT.detect_and_ocr(self.device.image)
        text = ''.join(''.join((result.ocr_text or '').split()) for result in (results or []))
        if '鼬乐币不足' in text:
            logger.warning(f'鼬乐币不足，停止礼盒购买: text={text}')
            return self.GIFT_STOP
        return None

    def _buy_opened_gift(self) -> str:
        """确认购买，以「奖励页出现并能关掉」作为成功依据。"""
        self.screenshot()
        before_coin = self._read_itachi_coin()
        cost = self._read_buy_cost()
        self.click(self.C_BUY_MORE)

        deadline = time.monotonic() + self.PURCHASE_RESULT_TIMEOUT
        dialog_absent_since = None
        while time.monotonic() < deadline:
            self.screenshot()
            # 与上游一致，以奖励领取完成作为成功依据；扣币 OCR 仅用于诊断。
            if self._purchase_reward_visible():
                if self._dismiss_purchase_reward():
                    self._verify_coin_cost(before_coin, cost)
                    return self.GIFT_PURCHASED
                return self.GIFT_STOP

            status = self._read_purchase_status()
            if status is not None:
                self._dismiss_confirm_dialog()
                return status

            if self.appear(self.I_UI_CONFIRM_SAMLL):
                self._dismiss_confirm_dialog()
                logger.warning('鼬乐礼盒出现其他购买提示，已确认并停止购买')
                return self.GIFT_STOP

            if self.appear(self.I_BUY_PLUS):
                dialog_absent_since = None
                continue

            if dialog_absent_since is None:
                dialog_absent_since = time.monotonic()
                continue
            if time.monotonic() - dialog_absent_since >= self.PURCHASE_RETURN_GRACE:
                logger.warning('鼬乐礼盒购买失败或鼬乐币不足')
                return self.GIFT_STOP

        logger.warning('鼬乐礼盒购买结果等待超时')
        return self.GIFT_STOP

    def _purchase_reward_visible(self) -> bool:
        # 本仓库没有 I_UI_REWARD_NEW（上游新版奖励页），统一用 I_UI_REWARD 低阈值识别。
        return self.appear(self.I_UI_REWARD, threshold=0.6)

    def _dismiss_confirm_dialog(self, retry: int = 4) -> bool:
        """
        点掉一个小确认框（例如「鼬乐币不足」）。

        刻意不用 ui_click_until_disappear：它内部是 while 1，弹窗关不掉时会一直点
        同一个按钮，点满 10 次就触发框架的「同一按钮 ≥10 次」保护 →
        GameTooManyClickError。这里改成有限次数，关不掉就收手。
        :param retry: 最多点几次
        :return: 是否已关掉
        """
        for _ in range(retry):
            self.screenshot()
            if not self.appear(self.I_UI_CONFIRM_SAMLL):
                return True
            self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=1)
            time.sleep(1)
        self.screenshot()
        return not self.appear(self.I_UI_CONFIRM_SAMLL)

    def _dismiss_purchase_reward(self) -> bool:
        deadline = time.monotonic() + self.REWARD_DISMISS_TIMEOUT
        while time.monotonic() < deadline:
            self.screenshot()
            if not self._purchase_reward_visible():
                return True
            self.ui_reward_appear_click()
        logger.warning('鼬乐礼盒奖励页未能关闭')
        return False

    def _verify_coin_cost(
        self,
        before_coin: tuple[int, int] | None,
        cost: int | None,
    ) -> bool:
        """扣币数 == 价格 → 购买确认；仅为诊断，不影响流程走向。"""
        self.screenshot()
        after_coin = self._read_itachi_coin()
        if before_coin is None or after_coin is None or cost is None:
            return True

        consumed = before_coin[0] - after_coin[0]
        if consumed == cost:
            logger.info(f'鼬乐礼盒购买确认: consumed={consumed}')
            return True
        logger.warning(
            '鼬乐币变化与价格不一致: '
            f'before={before_coin[0]}, after={after_coin[0]}, cost={cost}'
        )
        return False

    def _leave_itachi_shop(self) -> None:
        """离开鼬乐商店回娱乐页。失败只警告，交给后面的 goto_page(page_mall) 兜底。"""
        # 先把可能残留的奖励弹窗戳掉，否则红 X 点不到
        for _ in range(3):
            if not self._purchase_reward_visible():
                break
            self.ui_reward_appear_click()

        for _ in range(2):
            try:
                self.goto_page(page_entertainment, timeout=20)
                return
            except GamePageUnknownError:
                logger.warning('离开鼬乐商店失败，尝试通用兜底关闭')
            except Exception as e:
                logger.warning(f'离开鼬乐商店异常: {e}')
                return
            # 兜底：让导航层的通用关闭动作（红 X / 黄箭头 / 蓝箭头）再试一次
            for _ in range(2):
                if not self.close_unknown_pages(skip_first_screenshot=False):
                    break
        logger.warning('仍未离开鼬乐商店，交由后续商店导航兜底')


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = ItachiCoinShop(c, d)

    t.execute_itachi_coin_shop()
