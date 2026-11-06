import importlib
from datetime import datetime, time, timedelta
from time import sleep

from module.base.timer import Timer
from module.exception import TaskEnd, RequestHumanTakeover
from module.logger import logger
from tasks.AssistBattle.assets import AssistBattleAssets
from tasks.Component.SwitchAccount.switch_account import SwitchAccount
from tasks.FindJade import WantedQuestsEx
from tasks.FindJade.assets import FindJadeAssets
from tasks.FindJade.config import AccountInfo, FindJade
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main
from tasks.WantedQuests.assets import WantedQuestsAssets


class ScriptTask(GameUi, FindJadeAssets, AssistBattleAssets):
    fade_conf: FindJade= None
    FIND_JADE_NONE = "无勾"
    FIND_JADE_NORMAL = "普勾"
    FIND_JADE_SPECIAL = "现勾"
    FIND_JADE_BOTH = "普勾+现勾"   # 同一个号三格里同时有普勾和现勾
    FIND_JADE_UNKNOWN = "未知"
    # 只有这几种算「有勾」, 需要出现在推送里
    JADE_HAS = (FIND_JADE_NORMAL, FIND_JADE_SPECIAL, FIND_JADE_BOTH)
    # 悬赏封印的游戏刷新点, 与脚本实际运行时刻(run_time_1/2)无关
    JADE_REFRESH_HOURS = (5, 18)

    def run(self):
        self.fade_conf = self.config.model.find_jade

        found = []    # 本轮查出有勾协的号 (账号, 类型)
        resumed = []  # 本时段此前已记录、本轮被跳过的号

        try:
            for accountInfo in self.fade_conf.sup_account_list:
                logger.info("start %s-%s ", accountInfo.character, accountInfo.svr)
                if not self.is_need_login(accountInfo):
                    logger.warning("%s Skipped last Login Time:%s", accountInfo.character, accountInfo.last_complete_time)
                    # 跳过不等于丢弃: 从配置文件读回本时段确认过的勾协状态, 一起推送
                    flag = self.saved_jade_flag(accountInfo)
                    if flag in self.JADE_HAS:
                        resumed.append((accountInfo, flag))
                    continue
                suc = SwitchAccount(self.config, self.device, accountInfo).switchAccount()
                if not suc:
                    logger.warning("switch to %s-%s Failed", accountInfo.character, accountInfo.svr)
                    continue
                # 必须早于 WQ: WQ 会把协作任务做完, 做完之后就再也看不出原本有没有勾
                flag = self.detect_jade()
                # 逐号落盘: 后面 WQ 或进程出任何问题, 这个号的结果都还在配置文件里。
                # 返回值是合并后的最终状态(同一时段重查时不会被「无勾」冲掉)。
                flag = self.record_jade_result(accountInfo, flag)
                logger.info("%s-%s 勾协状态: %s", accountInfo.character, accountInfo.svr, flag)
                if flag in self.JADE_HAS:
                    found.append((accountInfo, flag))
                #
                wq = self.CreatObjectFromModule("WantedQuests", config=self.config, device=self.device)
                wq.fade_conf = self.fade_conf

                try:
                    wq.run()
                except TaskEnd as e:
                    logger.warning("%s-%s TaskEnd", accountInfo.character, accountInfo.svr)
                    # 更新配置文件中的时间
                    self.fade_conf.update_account_login_history(accountInfo)
                    self.save_config()
                    continue
                except RequestHumanTakeover as e:
                    raise
                except Exception as e:
                    logger.error(e)
                    self.next_run("FindJade", success=False)
        finally:
            # 中断(人工接管 / 异常)也把已查到的结果推走; 重启后快照变了会补发完整的一份。
            # 推送本身出错不能盖掉原始异常。
            try:
                self.push_result(found, resumed)
            except Exception as e:
                logger.error('push_result failed: %s', e)
        self.next_run("FindJade", success=True)
        raise TaskEnd("FindJade")

    def detect_jade(self) -> str:
        """切号后看悬赏封印有没有勾协, 返回 无勾/普勾/现勾/普勾+现勾/未知"""
        if self.get_current_page() != page_main:
            self.goto_page(page_main)
        open_timer = Timer(8)
        open_timer.start()
        opened = False
        clicked = False
        while 1:
            self.screenshot()
            if (
                self.appear(WantedQuestsAssets.I_TRACE_ENABLE)
                or self.appear(WantedQuestsAssets.I_TRACE_DISABLE)
            ):
                opened = True
                break
            # 悬赏全部封印完时界面上没有"一键追踪", 只剩红叉; 只认追踪按钮会白等 8 秒。
            # 但红叉必须"点过封字之后"才认 —— 庭院上偶发的公告弹窗也有红叉,
            # 提前认会让明明没进页面的号被判成无勾(漏号)。
            if clicked and self.appear(self.I_UI_BACK_RED):
                opened = True
                break
            if self.appear_then_click(WantedQuestsAssets.I_WQ_SEAL, interval=1):
                clicked = True
                continue
            if self.appear_then_click(WantedQuestsAssets.I_WQ_DONE, interval=1):
                clicked = True
                continue
            if open_timer.reached():
                break
        if not opened:
            self.ui_click_until_disappear(self.I_UI_BACK_RED)
            # 页面没打开就没法确认, 不能当成"无勾"记
            logger.warning('悬赏封印页面 8 秒未打开, 勾协状态记为未知')
            return self.FIND_JADE_UNKNOWN

        # 页面刚打开时槽位图可能还没渲染完, 连续两帧判定一致才采信
        prev = None
        invited = False
        for _ in range(10):
            invited = bool(
                self.appear(WantedQuestsAssets.I_WQ_INVITE_1)
                or self.appear(WantedQuestsAssets.I_WQ_INVITE_2)
                or self.appear(WantedQuestsAssets.I_WQ_INVITE_3)
            )
            if prev is not None and invited == prev:
                break
            prev = invited
            sleep(0.5)
            self.screenshot()

        flag = self._jade_flag(invited)
        # 退出去, 后面 WQ 还要自己再进来一次
        self.ui_click_until_disappear(self.I_UI_BACK_RED)
        return flag

    def _jade_flag(self, invited: bool) -> str:
        """有勾协时再看是普勾、现勾, 还是两种都有

        三个槽位逐个看, 不能在第一格不是勾协时就收工 —— 第一格是狗粮/金币、
        第二格才是勾协的情况很常见。三格之间有几种就报几种: 只有普勾报「普勾」,
        只有现勾报「现勾」, 同时存在报「普勾+现勾」。
        """
        if not invited:
            logger.info("there is no cooperation quest")
            return self.FIND_JADE_NONE
        self.screenshot()
        slots = (
            (WantedQuestsAssets.I_WQ_INVITE_1,
             WantedQuestsAssets.I_WQ_COOPERATION_TYPE_JADE_1,
             self.I_PRESENT_WORLD_JADE_ASSIST_1),
            (WantedQuestsAssets.I_WQ_INVITE_2,
             WantedQuestsAssets.I_WQ_COOPERATION_TYPE_JADE_2,
             self.I_PRESENT_WORLD_JADE_ASSIST_2),
            (WantedQuestsAssets.I_WQ_INVITE_3,
             WantedQuestsAssets.I_WQ_COOPERATION_TYPE_JADE_3,
             self.I_PRESENT_WORLD_JADE_ASSIST_3),
        )
        has_normal = False
        has_special = False
        for invite, jade_type, present_world in slots:
            if not self.appear(invite):
                continue
            if not self.appear(jade_type):
                continue
            if self.appear(present_world):
                has_special = True
            else:
                has_normal = True
            if has_normal and has_special:
                return self.FIND_JADE_BOTH
        if has_special:
            return self.FIND_JADE_SPECIAL
        if has_normal:
            return self.FIND_JADE_NORMAL
        return self.FIND_JADE_NONE

    @staticmethod
    def format_account(accountInfo) -> str:
        platform = '安卓' if accountInfo.apple_or_android else '苹果'
        return f'{accountInfo.account}-{platform}-{accountInfo.svr}-{accountInfo.character}'

    def current_conf(self):
        """从当前 config.model 重取配置, 防止中途 reload 后把结果写进旧对象"""
        return self.config.model.find_jade

    def current_account(self, accountInfo):
        """按 账号+服务器+角色名 找回当前 model 里的账号对象"""
        for candidate in self.current_conf().sup_account_list or []:
            if (
                candidate.character == accountInfo.character
                and candidate.svr == accountInfo.svr
                and candidate.account == accountInfo.account
            ):
                return candidate
        return None

    def record_jade_result(self, accountInfo, flag: str) -> str:
        """查完立刻逐号写回配置文件, 中断 / 重启 / 人工接管都不丢

        同一键协战 mark_account_done 的做法。「未知」表示悬赏页没打开,
        不能当成无勾记, 下一趟重查。返回最终采用的状态供推送使用。
        """
        if flag == self.FIND_JADE_UNKNOWN:
            return flag
        current = self.current_account(accountInfo)
        if current is None:
            logger.warning(
                'record_jade_result: %s-%s not found in current config model',
                accountInfo.character, accountInfo.svr,
            )
            return flag
        flag = self.merge_jade_flag(current, flag)
        current.jade_status = flag
        current.jade_complete_time = datetime.now()
        self.save_config()
        return flag

    def merge_jade_flag(self, current, flag: str) -> str:
        """同一时段重复检测时, 别让一次「页面没打开 / 协作已被 WQ 打掉」把结果冲掉

        中断常发生在 WQ 已邀请之后: 重启重查时勾协格子已经空了, 直接覆盖成「无勾」
        就会把之前确实查到的勾协弄丢。只合并本时段(jade_complete_time 晚于刷新点)的记录。
        """
        if current.jade_complete_time <= self.jade_refresh_point(datetime.now()):
            return flag
        old = current.jade_status
        if old not in self.JADE_HAS:
            return flag
        if flag in (self.FIND_JADE_NONE, 'none'):
            return old
        if old == self.FIND_JADE_BOTH or flag == self.FIND_JADE_BOTH:
            return self.FIND_JADE_BOTH
        if old != flag:
            return self.FIND_JADE_BOTH
        return old

    def saved_jade_flag(self, accountInfo) -> str:
        """被跳过的号, 从配置文件读回本时段确认过的勾协状态"""
        current = self.current_account(accountInfo)
        if current is None:
            return self.FIND_JADE_UNKNOWN
        if current.jade_complete_time <= self.jade_refresh_point(datetime.now()):
            return self.FIND_JADE_UNKNOWN
        return current.jade_status

    @staticmethod
    def jade_refresh_point(now: datetime) -> datetime:
        """悬赏封印最近一次刷新时刻(每天 5 点 / 18 点)"""
        points = [datetime.combine(now.date(), time(h, 0)) for h in ScriptTask.JADE_REFRESH_HOURS]
        passed = [p for p in points if p <= now]
        if passed:
            return max(passed)
        return datetime.combine(now.date() - timedelta(days=1), time(18, 0))

    @staticmethod
    def jade_cycle_key(now: datetime) -> str:
        """一个时间段算一趟: 05:00-18:00 记 AM, 18:00-次日05:00 记 PM"""
        refresh = ScriptTask.jade_refresh_point(now)
        return f'{refresh:%Y-%m-%d}-{"AM" if refresh.hour == 5 else "PM"}'

    @staticmethod
    def account_key(accountInfo) -> str:
        return f'{accountInfo.account}|{accountInfo.svr}|{accountInfo.character}'

    def push_result(self, found, resumed) -> None:
        """只推有勾协的号, 一封邮件两段: 本次执行 + 此前已记录

        没有勾协就整封邮件都不发。按游戏刷新时段算一趟: 同一时段重启多次,
        内容没变就不重发; 中途新查出勾协才补发一份完整清单。
        格式照搬一键协战「多号任务完成」那封。
        """
        conf = self.current_conf().find_jade_config
        if not conf.push_result_enable:
            return

        # 同一个号只列一次, 优先算「本次执行」
        seen = set()
        found_items = []
        for accountInfo, flag in found:
            key = self.account_key(accountInfo)
            if flag in self.JADE_HAS and key not in seen:
                seen.add(key)
                found_items.append((accountInfo, flag))
        resumed_items = []
        for accountInfo, flag in resumed:
            key = self.account_key(accountInfo)
            if flag in self.JADE_HAS and key not in seen:
                seen.add(key)
                resumed_items.append((accountInfo, flag))
        # 兜底: 本时段已落盘、却因切号失败等原因没进上面两段的号也算这一趟
        refresh = self.jade_refresh_point(datetime.now())
        for accountInfo in self.current_conf().sup_account_list or []:
            key = self.account_key(accountInfo)
            if key in seen or accountInfo.jade_complete_time <= refresh:
                continue
            if accountInfo.jade_status not in self.JADE_HAS:
                continue
            seen.add(key)
            resumed_items.append((accountInfo, accountInfo.jade_status))

        if not (found_items or resumed_items):
            logger.info("本时段没有账号有勾协, 不推送")
            return
        cycle = self.jade_cycle_key(datetime.now())
        snapshot = cycle + '|' + ';'.join(
            f'{self.account_key(accountInfo)}:{flag}'
            for accountInfo, flag in sorted(
                found_items + resumed_items, key=lambda item: self.account_key(item[0])
            )
        )
        if conf.jade_push_state == snapshot:
            logger.info("本时段结果已推送过, 不重复推送")
            return

        logger.hr('FindJade Result', 2)
        lines = []
        if found_items:
            lines.append(f"本次执行（{len(found_items)}个账号）：")
            for accountInfo, flag in found_items:
                message = '-'.join([self.format_account(accountInfo), flag])
                logger.info(message)
                lines.append(message)
        if resumed_items:
            lines.append(f"此前已记录勾协（{len(resumed_items)}个账号）：")
            for accountInfo, flag in resumed_items:
                message = '-'.join([self.format_account(accountInfo), flag])
                logger.info(message)
                lines.append(message)
        self.config.notifier.push(
            title='多号任务完成',
            content='{}\n{}'.format(self.config.config_name, '\n'.join(lines)),
        )
        # 推送成功后才记快照, 发信失败下次还能重推
        conf.jade_push_state = snapshot
        self.save_config()


    def is_need_login(self, item: AccountInfo):
        """
            根据上次登陆时间 判断是否需要登录查找
        @param item:
        @type item:
        """
        lastTime = item.last_complete_time
        now = datetime.now()
        if now - lastTime > timedelta(hours=13):
            return True
        if (lastTime.hour >= 18 or lastTime.hour < 5) and (18 > now.hour >= 5):
            return True
        if (5 <= lastTime.hour < 18) and now.hour >= 18:
            return True
        return False

    def CreatObjectFromModule(self, task_name: str, **kwargs):
        module_name = 'script_task'
        from pathlib import Path
        module_path = str(Path.cwd() / 'tasks' / task_name / (module_name + '.py'))

        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # 多继承 AssistBattleAssets 是为了现世勾协标记素材 I_PRESENT_WORLD_JADE_ASSIST_N,
        # WantedQuestsAssets 里没有这几张图(两份 assets 属性名无交集, 不会互相覆盖)。
        WQEX = type("WQEX", (module.ScriptTask, AssistBattleAssets), {
            "need_invite_vip": WantedQuestsEx.need_invite_vip,
            "get_cooperation_info": WantedQuestsEx.get_cooperation_info,
            "get_invite_vip_name": WantedQuestsEx.get_invite_vip_name,
            "next_run": WantedQuestsEx.next_run,
            "invite_success_callback": WantedQuestsEx.invite_success_callback,
            "get_config": WantedQuestsEx.get_config
        })
        # 让覆写的 get_cooperation_info 能调到上游原实现(只补标记, 不复制逻辑)
        WantedQuestsEx.set_wq_base(module.ScriptTask)
        wq = WQEX(**kwargs)
        return wq

    def save_config(self):
        self.config.save()

    def next_run(self, task: str, finish: bool = False,
                 success: bool = None, server: bool = True, target: datetime = None) -> None:
        now = datetime.now()
        if not success:
            # 失败沿用原逻辑: 10 分钟后再试
            self.set_next_run(task, target=now + timedelta(minutes=10), server=False)
            return
        # server=False: 不让调度器把时间改写成它的 server_update
        self.set_next_run(task, target=self.next_time_point(now), server=False)

    def next_time_point(self, now: datetime) -> datetime:
        """按配置的两个固定时刻, 取 now 之后最近的一个"""
        conf = self.fade_conf if self.fade_conf else self.config.find_jade
        points = sorted({conf.find_jade_config.run_time_1,
                         conf.find_jade_config.run_time_2})
        for days in (0, 1):
            day = (now + timedelta(days=days)).date()
            for point in points:
                candidate = datetime.combine(day, point)
                if candidate > now:
                    return candidate
        return datetime.combine((now + timedelta(days=2)).date(), points[0])


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    # from mypatch import SimplePatch

    # SimplePatch.patch()

    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)

    t.run()
