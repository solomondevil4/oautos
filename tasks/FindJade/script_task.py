import importlib
from datetime import datetime, timedelta
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

    def run(self):
        self.fade_conf = self.config.find_jade

        found = []    # 本次查出有勾协的号 (账号, 类型), 只有普勾/现勾, 无勾的不记

        for accountInfo in self.fade_conf.sup_account_list:
            logger.info("start %s-%s ", accountInfo.character, accountInfo.svr)
            if not self.is_need_login(accountInfo):
                logger.warning("%s Skipped last Login Time:%s", accountInfo.character, accountInfo.last_complete_time)
                continue
            suc = SwitchAccount(self.config, self.device, accountInfo).switchAccount()
            if not suc:
                logger.warning("switch to %s-%s Failed", accountInfo.character, accountInfo.svr)
                continue
            # 必须早于 WQ: WQ 会把协作任务做完, 做完之后就再也看不出原本有没有勾
            flag = self.detect_jade()
            if flag in (self.FIND_JADE_NORMAL, self.FIND_JADE_SPECIAL, self.FIND_JADE_BOTH):
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
        self.push_result(found)
        self.next_run("FindJade", success=True)
        raise TaskEnd("FindJade")
        pass

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

    def push_result(self, found) -> None:
        """只推有勾协的号: 逐个带 普勾/现勾/普勾+现勾, 无勾的号一概不列

        没有勾协就整封邮件都不发 —— 用户明确: 不需要通知完没完成,
        没有勾协的也不用通知。格式照搬一键协战「多号任务完成」那封。
        """
        if not self.fade_conf.find_jade_config.push_result_enable:
            return
        if not found:
            logger.info("本次没有账号有勾协, 不推送")
            return
        logger.hr('FindJade Result', 2)
        lines = [f"本次执行（{len(found)}个账号）："]
        for accountInfo, flag in found:
            message = '-'.join([self.format_account(accountInfo), flag])
            logger.info(message)
            lines.append(message)
        self.config.notifier.push(
            title='多号任务完成',
            content='{}\n{}'.format(self.config.config_name, '\n'.join(lines)),
        )


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

        WQEX = type("WQEX", (module.ScriptTask,), {
            "need_invite_vip": WantedQuestsEx.need_invite_vip,
            "get_invite_vip_name": WantedQuestsEx.get_invite_vip_name,
            "next_run": WantedQuestsEx.next_run,
            "invite_success_callback": WantedQuestsEx.invite_success_callback,
            "get_config": WantedQuestsEx.get_config
        })
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
