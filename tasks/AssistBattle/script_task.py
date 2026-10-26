from datetime import date, datetime, time, timedelta
from time import sleep

from module.base.timer import Timer
from module.logger import logger
from module.exception import RequestHumanTakeover, TaskEnd
from tasks.Component.SwitchAccount.switch_account import SwitchAccount
from tasks.EvoZone.config import Layer, KirinType
from tasks.EvoZone.script_task import ScriptTask as EvoZoneScriptTask
from tasks.RealmRaid.script_task import ScriptTask as RealmRaidScriptTask
from tasks.DailyTrifles.script_task import ScriptTask as DailyTriflesScriptTask
from tasks.KekkaiUtilize.script_task import ScriptTask as KekkaiUtilizeScriptTask
from tasks.KekkaiUtilize.page import page_guild_realm
from tasks.GameUi.page import page_main, page_assist_battle, page_mall
from tasks.WantedQuests.assets import WantedQuestsAssets
from tasks.WantedQuests.config import CooperationSelectMaskDescription, CooperationType
from tasks.WantedQuests.script_task import ScriptTask as WantedQuestsScriptTask
from tasks.AssistBattle.assets import AssistBattleAssets
from tasks.AssistBattle.config import AssistBattleConfig
from tasks.RichMan.mall.consignment import Consignment


class ScriptTask(
    EvoZoneScriptTask,
    RealmRaidScriptTask,
    DailyTriflesScriptTask,
    KekkaiUtilizeScriptTask,
    Consignment,
    AssistBattleAssets,
):

    conf: AssistBattleConfig
    FIND_JADE_NONE = "无勾"
    FIND_JADE_NORMAL = "普勾"
    FIND_JADE_SPECIAL = "现勾"
    EVOZONE_TOTAL = 15
    REALMRAID_TOTAL = 3
    # 日常类功能的时间窗口, 开关关闭时不限
    DAILY_START = time(18, 0)
    DAILY_END = time(23, 0)

    def run(self):
        self.conf = self.config.model.assist_battle
        # 整轮固定: 跑到一半跨过 23:00 也不会变成有的号做日常、有的号不做
        self.daily_window = self.in_daily_window()
        results = self.run_switch_account()
        if results:
            self.run_push_result(results)
        self.set_next_run(task='AssistBattle', success=True, finish=True)
        raise TaskEnd('AssistBattle')

    def in_daily_window(self) -> bool:
        """日常类功能(邮件/寄售屋/庭院/商店签到)只在晚上执行, 白天那一轮只跑勾协"""
        if not self.conf.assist_battle_config.daily_time_window_enable:
            return True
        return self.DAILY_START <= datetime.now().time() < self.DAILY_END

    def format_account_name(self, result, mask: str) -> str:
        """拼接推送用的账号信息, full_name_enable 打开时账号/服务器/角色名都原样显示"""
        account = str(result['account'])
        svr = str(result['svr'])
        character = result['character']
        if not self.conf.push_config.full_name_enable:
            account = account[:4] + (mask if len(account) > 4 else '')
            svr = svr[-4:]
            character = mask + character[-2:]
        platform = '安卓' if result['apple_or_android'] else '苹果'
        return f'{account}-{platform}-{svr:　>4}-{character}'

    def is_assist_count_normal(self, result) -> bool:
        """协战次数是否正常: 觉醒打满 15 次、结界突破打满 3 次"""
        cfg = self.conf.assist_battle_config
        if cfg.evozone_enable and result['evozone_final'] < self.EVOZONE_TOTAL:
            return False
        if cfg.realmraid_enable and result['realmraid_final'] < self.REALMRAID_TOTAL:
            return False
        return True

    def format_assist_count(self, result) -> str:
        """拼接今日累计的协战次数"""
        message = ''
        if self.conf.assist_battle_config.evozone_enable:
            message += f"-觉醒{result['evozone_final']}次"
        if self.conf.assist_battle_config.realmraid_enable:
            message += f"-个突{result['realmraid_final']}次"
        return message

    def run_push_result(self, results):
        """输出协战结果: 今日总进度只列次数不正常的账号"""
        logger.hr('AssistBattle Result', 2)
        push_content = []
        # 当天已完成的账号不算"本次执行", 只在下面的总进度里体现
        executed = [result for result in results if not result['skipped']]
        # 既不显示协战次数又没找勾协时, 这部分只剩账号名, 没有推送的价值
        if executed and (
            self.conf.push_config.executed_count_enable
            or self.conf.assist_battle_config.find_jade_enable
        ):
            push_content.append(f"本次执行任务共{len(executed)}个账号：")
            for result in executed:
                if (
                    result['jade_flag'] != self.FIND_JADE_NONE
                    or not self.conf.push_config.find_jade_push_clear_enable
                ):
                    # 账号取前4位，服务器取后4位，角色名取后2位；开了完整显示就原样输出
                    message = self.format_account_name(result, '**')
                    if self.conf.push_config.executed_count_enable:
                        if self.conf.assist_battle_config.evozone_enable:
                            message += f"-觉醒{result['evozone_done']}次"
                        if self.conf.assist_battle_config.realmraid_enable:
                            message += f"-个突{result['realmraid_done']}次"
                    if self.conf.assist_battle_config.find_jade_enable:
                        message += f"-{result['jade_flag']}"
                    logger.info(message)
                    push_content.append(message)
        # 未开启协战则不推送总进度
        if (
            self.conf.assist_battle_config.evozone_enable
            or self.conf.assist_battle_config.realmraid_enable
        ):
            # 次数正常的账号不再逐个列出, 只推送次数不正常的账号和明细
            abnormal = [r for r in results if not self.is_assist_count_normal(r)]
            if abnormal:
                push_content.append(f"今日协战任务共{len(results)}个账号：")
            else:
                push_content.append(f"今日协战任务共{len(results)}个账号，次数全部正常")
            for result in abnormal:
                message = self.format_account_name(result, '…')
                message += self.format_assist_count(result)
                logger.info(message)
                push_content.append(message)
        # 推送协战完成结果
        if self.conf.push_config.result_push_enable:
            self.config.notifier.push(
                title='多号任务完成',
                content='{}\n{}'.format(
                    self.config.config_name,
                    '\n'.join(push_content),
                ),
            )

    def run_switch_account(self):
        """切换账号并执行协战任务"""
        accounts = [account for account in self.conf.account_list if account.is_valid()]
        results = []
        if not accounts:
            logger.info('No AssistBattle account configured, exit')
        else:
            failed_accounts = []
            for account in accounts:
                if not self.run_one_account(account, results):
                    failed_accounts.append(account)
            # 切号失败的账号在最后再尝试一次, 切换角色时滑动幅度过大个别账号会被跳过
            if (
                failed_accounts
                and self.conf.assist_battle_config.retry_failed_account_enable
            ):
                logger.hr(
                    'Retry %s account(s) which failed to switch' % len(failed_accounts),
                    2,
                )
                still_failed = []
                for account in failed_accounts:
                    if not self.run_one_account(account, results):
                        still_failed.append(account)
                failed_accounts = still_failed
            if failed_accounts:
                logger.warning(
                    'Still failed to switch %s account(s): %s',
                    len(failed_accounts),
                    ', '.join(
                        '%s-%s' % (account.character, account.svr)
                        for account in failed_accounts
                    ),
                )
        return results

    def run_one_account(self, account, results) -> bool:
        """切换账号并执行协战任务, 切换成功返回 True"""
        if self.is_account_done_today(account):
            logger.info(
                'Skip %s-%s, assist battle is already done today at %s',
                account.character,
                account.svr,
                account.last_complete_time,
            )
            self.append_skipped_result(account, results)
            return True
        logger.hr('Run AssistBattle for %s-%s' % (account.character, account.svr), 2)
        if not SwitchAccount(self.config, self.device, account).switchAccount():
            logger.warning(
                'Switch to %s-%s failed; skip it',
                account.character,
                account.svr,
            )
            return False
        (
            evozone_done,
            realmraid_done,
            evozone_final,
            realmraid_final,
            jade_flag,
        ) = self.run_current_account()
        self.mark_account_done(account, evozone_final, realmraid_final)
        results.append(
            {
                'account': account.account,
                'character': account.character,
                'svr': account.svr,
                'apple_or_android': account.apple_or_android,
                'evozone_done': evozone_done,
                'realmraid_done': realmraid_done,
                'evozone_final': evozone_final,
                'realmraid_final': realmraid_final,
                'jade_flag': jade_flag,
                'skipped': False,
            }
        )
        return True

    def append_skipped_result(self, account, results) -> None:
        """当天已完成的账号不登录确认, 按当前开启的协战类型推算结果计入总进度"""
        cfg = self.conf.assist_battle_config
        results.append(
            {
                'account': account.account,
                'character': account.character,
                'svr': account.svr,
                'apple_or_android': account.apple_or_android,
                'evozone_done': 0,
                'realmraid_done': 0,
                'evozone_final': self.EVOZONE_TOTAL if cfg.evozone_enable else 0,
                'realmraid_final': self.REALMRAID_TOTAL if cfg.realmraid_enable else 0,
                'jade_flag': self.FIND_JADE_NONE,
                'skipped': True,
            }
        )

    def is_account_done_today(self, account) -> bool:
        """协战每天 0 点刷新, 当天已经打完的账号不必再登录确认一次"""
        if not self.conf.assist_battle_config.skip_completed_account_enable:
            return False
        return account.last_complete_time.date() == datetime.today().date()

    def mark_account_done(
        self, account, evozone_final: int, realmraid_final: int
    ) -> None:
        """记录当天协战已打完的账号, 重启后不会再登录它"""
        cfg = self.conf.assist_battle_config
        if not cfg.skip_completed_account_enable:
            return
        # 未开启协战时不记录, 避免把只挂日常的账号也标记掉
        if not (cfg.evozone_enable or cfg.realmraid_enable):
            return
        if cfg.evozone_enable and evozone_final < self.EVOZONE_TOTAL:
            return
        if cfg.realmraid_enable and realmraid_final < self.REALMRAID_TOTAL:
            return
        account.last_complete_time = datetime.now()
        self.config.save()
        logger.info(
            'Assist battle of %s-%s is done today, it will be skipped until next day',
            account.character,
            account.svr,
        )

    def run_current_account(self):
        SUNDAY = 6
        # 执行任务前先获取本账号协战剩余次数以检查是否执行过前置任务，觉醒协战已做完将不再执行日常任务
        evozone_done, realmraid_done = 0, 0
        evozone_final, realmraid_final = 0, 0
        jade_flag = self.FIND_JADE_NONE
        # 不执行协战任务时，可以用来小号挂日常
        start_evozone = 1

        if (
            self.conf.assist_battle_config.evozone_enable
            or self.conf.assist_battle_config.realmraid_enable
        ):
            start_evozone, start_realmraid = self.get_assist_battle_count()

        # 邮件领取
        if (
            self.conf.assist_battle_config.email_enable
            and date.today().weekday() == SUNDAY
            and self.daily_window
        ):
            self.run_pickup_email()
            self.goto_page(page_main)

        # 寄售屋（买寄售券 + 买指定联动碎片，两个开关共用一次进寄售屋）
        cfg = self.conf.assist_battle_config
        buy_ticket = (
            cfg.consignment_enable
            and date.today().weekday() == SUNDAY
        )
        buy_shikigami = (
            cfg.consignment_shikigami_enable
            and date.today().weekday() == cfg.consignment_shikigami_day.to_index()
        )
        if (buy_ticket or buy_shikigami) and self.daily_window:
            rm_con = self.config.rich_man.consignment
            # 暂改大富翁的寄售屋配置来复用其购买流程，跑完恢复，避免污染大富翁自己的设置
            backup = (
                rm_con.enable,
                rm_con.buy_sale_ticket,
                rm_con.buy_shikigami,
                rm_con.shikigami_name,
            )
            try:
                rm_con.enable = True
                rm_con.buy_sale_ticket = buy_ticket
                rm_con.buy_shikigami = buy_shikigami
                if buy_shikigami:
                    rm_con.shikigami_name = cfg.consignment_shikigami_name
                self.goto_page(page_mall, confirm_wait=2.5)
                self.execute_consignment()
                self.goto_page(page_main)
            finally:
                (
                    rm_con.enable,
                    rm_con.buy_sale_ticket,
                    rm_con.buy_shikigami,
                    rm_con.shikigami_name,
                ) = backup

        # 结界寄养
        if self.conf.assist_battle_config.kekkaiutilize_enable and start_evozone > 0:
            # 进入寮结界
            self.goto_page(page_guild_realm)
            self.check_utilize_add()
            self.goto_page(page_main)

        # 庭院事务
        if (
            self.conf.assist_battle_config.courtyard_affairs_enable
            and start_evozone > 0
            and self.daily_window
        ):
            self.run_courtyard_affairs()
            self.goto_page(page_main)

        # 商店签到
        if (
            self.conf.assist_battle_config.store_sign_enable
            and start_evozone > 0
            and self.daily_window
        ):
            # 重置done状态
            self.config.daily_trifles.done_record.store_sign_dt = datetime(2023, 1, 1)
            self.run_store_sign()
            self.goto_page(page_main)

        # 寻找勾协
        if self.conf.assist_battle_config.find_jade_enable:
            jade_flag = self.find_jade()
            if (
                jade_flag == self.FIND_JADE_NORMAL
                and self.conf.assist_battle_config.find_jade_finish_enable
            ):
                self.run_own_wanted_quests()
            self.ui_click_until_disappear(self.I_UI_BACK_RED, interval=1)
            self.goto_page(page_main)

        # 执行觉醒副本任务
        if self.conf.assist_battle_config.evozone_enable and start_evozone > 0:
            self.run_evozone(start_evozone)
            self.goto_page(page_main)

        # 执行结界突破任务
        if self.conf.assist_battle_config.realmraid_enable and start_realmraid > 0:
            self.run_realmraid(start_realmraid)
            self.goto_page(page_main)

        # 统计本次执行完成的协战任务
        if (
            self.conf.assist_battle_config.evozone_enable
            or self.conf.assist_battle_config.realmraid_enable
        ):
            end_evozone, end_realmraid = self.get_assist_battle_count()
            evozone_done = start_evozone - end_evozone
            realmraid_done = start_realmraid - end_realmraid
            # 今天已经完成的任务
            evozone_final = self.EVOZONE_TOTAL - end_evozone
            realmraid_final = self.REALMRAID_TOTAL - end_realmraid
            if self.conf.assist_battle_config.evozone_enable:
                logger.info(
                    "本次协战完成：觉醒 %s 次，结界突破 %s 次",
                    evozone_done,
                    realmraid_done,
                )
            if self.conf.assist_battle_config.realmraid_enable:
                logger.info(
                    "最终协战完成：觉醒 %s 次，结界突破 %s 次",
                    evozone_final,
                    realmraid_final,
                )

        return evozone_done, realmraid_done, evozone_final, realmraid_final, jade_flag

    def find_jade(self):
        """寻找勾协并标记"""
        if self.get_current_page() != page_main:
            self.goto_page(page_main)
        # 打开悬赏封印 界面
        done_timer = Timer(5)
        while 1:
            self.screenshot()
            if self.appear(WantedQuestsAssets.I_TRACE_ENABLE) or self.appear(
                WantedQuestsAssets.I_TRACE_DISABLE
            ):
                break
            if self.appear_then_click(WantedQuestsAssets.I_WQ_SEAL, interval=1):
                continue
            if self.appear_then_click(WantedQuestsAssets.I_WQ_DONE, interval=1):
                continue
            if self.appear(self.I_UI_BACK_RED):
                if not done_timer.started():
                    done_timer.start()
            if done_timer.started() and done_timer.reached():
                self.ui_click_until_disappear(self.I_UI_BACK_RED)
                return self.FIND_JADE_NONE

        if not (
            self.appear(WantedQuestsAssets.I_WQ_INVITE_1)
            or self.appear(WantedQuestsAssets.I_WQ_INVITE_2)
            or self.appear(WantedQuestsAssets.I_WQ_INVITE_3)
        ):
            logger.info("there is no cooperation quest")
            return self.FIND_JADE_NONE
        # 存在勾协即返回对应类型
        self.screenshot()
        if self.appear(WantedQuestsAssets.I_WQ_INVITE_1):
            if self.appear(WantedQuestsAssets.I_WQ_COOPERATION_TYPE_JADE_1):
                if self.appear(self.I_PRESENT_WORLD_JADE_ASSIST_1):
                    return self.FIND_JADE_SPECIAL
                return self.FIND_JADE_NORMAL
        if self.appear(WantedQuestsAssets.I_WQ_INVITE_2):
            if self.appear(WantedQuestsAssets.I_WQ_COOPERATION_TYPE_JADE_2):
                if self.appear(self.I_PRESENT_WORLD_JADE_ASSIST_2):
                    return self.FIND_JADE_SPECIAL
                return self.FIND_JADE_NORMAL
        if self.appear(WantedQuestsAssets.I_WQ_INVITE_3):
            if self.appear(WantedQuestsAssets.I_WQ_COOPERATION_TYPE_JADE_3):
                if self.appear(self.I_PRESENT_WORLD_JADE_ASSIST_3):
                    return self.FIND_JADE_SPECIAL
                return self.FIND_JADE_NORMAL
        return self.FIND_JADE_NONE

    def run_own_wanted_quests(self):
        """发现普通勾协: 先触发勾协, 再让悬赏任务自己把协作任务做完"""
        wq_conf = self.config.wanted_quests.wanted_quests_config
        cooperation_only_backup = wq_conf.cooperation_only
        cooperation_type_backup = wq_conf.cooperation_type
        # 只完成协作任务, 且不自动邀请好友(邀请由用户自己来)
        wq_conf.cooperation_only = True
        wq_conf.cooperation_type = CooperationSelectMaskDescription.NoInvite
        try:
            wq = WantedQuestsScriptTask(self.config, self.device)
            # 只追踪勾协(邀请交给用户自己)
            wq.trace_cooperation(CooperationType.Jade)
            wq.run()
        except TaskEnd as e:
            logger.info(f"WantedQuests finished: {e}")
        except RequestHumanTakeover:
            raise
        except Exception as e:
            logger.error(e)
        finally:
            wq_conf.cooperation_only = cooperation_only_backup
            wq_conf.cooperation_type = cooperation_type_backup

    def get_assist_battle_count(self):
        """获取当前账号剩余的协战次数。"""
        self.goto_page(page_assist_battle)
        # 切换界面可能卡顿等个动画
        sleep(0.5)
        self.screenshot()
        _, evozone_res, _ = self.O_NORMAL_ASSIST_COUNT.ocr(self.device.image)
        _, realmraid_res, _ = self.O_REALMRAID_ASSIT_COUNT.ocr(self.device.image)

        return evozone_res, realmraid_res

    def run_evozone(self, count: int):
        """运行觉醒协战"""
        logger.hr('Run Evozone AssistBattle', 3)
        self.config.evo_zone.evo_zone_config.layer = Layer.FIVE
        self.config.evo_zone.evo_zone_config.kirin_type = KirinType.LIGHTNINGKIRIN
        self.config.evo_zone.general_battle_config.lock_team_enable = True
        self.current_count = 0
        self.limit_count = count
        self.limit_time = timedelta(hours=10)
        self.run_alone()

    def run_realmraid(self, count: int):
        """运行结界突破"""
        logger.hr('Run RealmRaid AssistBattle', 3)

        con = self.config.realm_raid

        con.raid_config.number_attack = count
        if self.conf.assist_battle_config.realmraid_easy_enable:
            con.raid_config.exit_four = False
            con.raid_config.order_attack = '0 > 1 > 2 > 3 > 4 > 5'
        con.general_battle_config.lock_team_enable = True
        con.switch_soul_config.enable = False
        if self.conf.assist_battle_config.switch_soul_enable:
            con.switch_soul_config.enable = True
        self.run_realmraid_core(con)


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.screenshot()

    t.run()
