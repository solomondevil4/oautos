import numpy as np
from datetime import date, datetime, time, timedelta
from difflib import SequenceMatcher
from time import sleep

from module.base.timer import Timer
from module.logger import logger
from module.exception import (
    GamePageUnknownError, GameStuckError, GameTooManyClickError,
    RequestHumanTakeover, TaskEnd,
)
from tasks.Component.SwitchAccount.switch_account import SwitchAccount
from tasks.EvoZone.config import Layer, KirinType
from tasks.EvoZone.script_task import ScriptTask as EvoZoneScriptTask
from tasks.RealmRaid.script_task import ScriptTask as RealmRaidScriptTask
from tasks.DailyTrifles.script_task import ScriptTask as DailyTriflesScriptTask
from tasks.KekkaiUtilize.script_task import ScriptTask as KekkaiUtilizeScriptTask
from tasks.KekkaiUtilize.page import page_guild_realm
from tasks.MysteryShop.script_task import ScriptTask as MysteryShopScriptTask
from tasks.GameUi.page import page_main, page_assist_battle, page_mall, page_friends
from tasks.GameUi.assets import GameUiAssets
from tasks.WantedQuests.assets import WantedQuestsAssets
from tasks.WantedQuests.config import CooperationSelectMaskDescription, CooperationType
from tasks.WantedQuests.script_task import ScriptTask as WantedQuestsScriptTask
from tasks.AssistBattle.assets import AssistBattleAssets
from tasks.AssistBattle.config import AssistBattle, weekday_matches
from tasks.RichMan.mall.consignment import Consignment


class ScriptTask(
    EvoZoneScriptTask,
    RealmRaidScriptTask,
    DailyTriflesScriptTask,
    KekkaiUtilizeScriptTask,
    Consignment,
    AssistBattleAssets,
):

    conf: AssistBattle
    FIND_JADE_NONE = "无勾"
    FIND_JADE_NORMAL = "普勾"
    FIND_JADE_SPECIAL = "现勾"
    FIND_JADE_UNKNOWN = "未知"
    EVOZONE_TOTAL = 15
    REALMRAID_TOTAL = 3
    # 日常类功能的时间窗口, 开关关闭时不限
    DAILY_START = time(18, 0)
    DAILY_END = time(23, 0)
    # 本号是否打觉醒, 由 run_one_account 在切号前设置; True 表示跟随全局开关
    current_evozone_enable = True
    # 当前正在执行的账号对象, 由 run_one_account 在切号后设置, 跑完清空。
    # run_current_account 没有 account 参数, 判断「本号要不要跑神秘商店」要靠它。
    _current_account = None

    def run(self):
        self.conf = self.config.model.assist_battle
        # 整轮固定: 跑到一半跨过 23:00 也不会变成有的号做日常、有的号不做
        self.daily_window = self.in_daily_window()
        results = self.run_switch_account()
        if results:
            self.run_push_result(results)
        self.set_next_run(task='AssistBattle', success=True, finish=True)
        raise TaskEnd('AssistBattle')

    def evozone_enabled(self, account=None) -> bool:
        """本号是否要打觉醒: 全局总开关 AND 该号自己的勾选

        传 account 时按账号现算(切号前、推送、跳过判定都用这个);
        不传时用 run_one_account 为本号设好的值, 供 run_current_account 内部使用。
        """
        if not self.conf.assist_battle_config.evozone_enable:
            return False
        if account is None:
            return bool(self.current_evozone_enable)
        return bool(getattr(account, 'evozone_enable', True))

    def daily_gate_open(self, start_evozone: int) -> bool:
        """日常任务的放行条件

        上游逻辑是「本号觉醒协战剩余 0 次(今天已打完)就不再跑日常」, 用来防止重复执行。
        本号不打觉醒时这个前提不成立, 直接放行, 保证关掉觉醒不影响其它任务。
        """
        if not self.evozone_enabled():
            return True
        return start_evozone > 0

    def in_daily_window(self) -> bool:
        """日常类功能(邮件/寄售屋/庭院/商店签到)只在晚上执行, 白天那一轮只跑勾协"""
        if not self.conf.optimize_config.daily_time_window_enable:
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
        """协战次数是否正常: 觉醒打满 15 次、结界突破打满 3 次

        觉醒次数按该号自己的勾选判断, 没勾觉醒的号不参与这条检查。
        """
        cfg = self.conf.assist_battle_config
        if (
            result.get('evozone_enable', True)
            and result['evozone_final'] < self.EVOZONE_TOTAL
        ):
            return False
        if cfg.realmraid_enable and result['realmraid_final'] < self.REALMRAID_TOTAL:
            return False
        return True

    def format_assist_count(self, result) -> str:
        """拼接今日累计的协战次数"""
        message = ''
        if result.get('evozone_enable', False):
            message += f"-觉醒{result['evozone_final']}次"
        if self.conf.assist_battle_config.realmraid_enable:
            message += f"-个突{result['realmraid_final']}次"
        return message

    def format_executed_suffix(self, result) -> str:
        """本次执行明细的后缀: 协战次数 → 勾协 → 寄售券余额, 每一项都由各自的开关控制"""
        message = ''
        if self.conf.push_config.executed_count_enable:
            if result.get('evozone_enable', False):
                message += f"-觉醒{result['evozone_done']}次"
            if self.conf.assist_battle_config.realmraid_enable:
                message += f"-个突{result['realmraid_done']}次"
        if self.conf.daily_config.find_jade_enable:
            message += f"-{result['jade_flag']}"
        # 寄售券余额跟在勾协后面; 没买或没读到券数就不显示
        ticket = result.get('consignment_ticket')
        if self.conf.push_config.consignment_ticket_enable and ticket is not None:
            message += f"-寄售券{ticket}"
        return message

    def format_executed_results(self, executed):
        """拼接"本次执行"的逐账号明细, 没内容可显示时返回空列表

        协战次数、勾协、寄售券余额共用一个判定: 三者任意一个启用并真的产出了内容,
        这一段才有推送的价值, 否则只剩账号名, 不推送。
        """
        if not executed:
            return []
        lines = []
        has_content = False
        for result in executed:
            # 买了寄售券的账号必须完整推送, 不受勾协状态与"仅存在勾协推送"影响
            bought_consignment = result.get('consignment_ticket') is not None
            # 开启"仅存在勾协推送"时, 没有勾协的账号整行都不列
            if (
                not bought_consignment
                and self.conf.push_config.find_jade_push_clear_enable
                and result['jade_flag'] == self.FIND_JADE_NONE
            ):
                continue
            suffix = self.format_executed_suffix(result)
            if suffix:
                has_content = True
            # 账号取前4位，服务器取后4位，角色名取后2位；开了完整显示就原样输出
            lines.append(self.format_account_name(result, '**') + suffix)
        if not has_content:
            return []
        return lines

    def format_resumed_jade_results(self, results):
        """断点续跑时, 把之前已记录、本次被跳过的账号的勾协结果一并列出

        只跑勾协时没有"今日协战任务"那一段, 跳过的账号本来完全不进推送,
        中断恢复后就只剩本次执行的那几个号, 前面查到的勾协结果丢了。
        只列已确认过的状态, "未知"表示上轮没查到, 推出来没有意义。
        """
        confirmed = (self.FIND_JADE_NONE, self.FIND_JADE_NORMAL, self.FIND_JADE_SPECIAL)
        lines = []
        for result in results:
            if not result['skipped']:
                continue
            flag = result['jade_flag']
            if flag not in confirmed:
                continue
            if (
                self.conf.push_config.find_jade_push_clear_enable
                and flag == self.FIND_JADE_NONE
            ):
                continue
            lines.append(self.format_account_name(result, '…') + f"-{flag}")
        return lines

    def run_push_result(self, results):
        """输出协战结果: 今日总进度只列次数不正常的账号"""
        logger.hr('AssistBattle Result', 2)
        push_content = []
        # 切换失败的账号单独列出, 避免用户去日志里翻
        failed = getattr(self, 'switch_failed_accounts', [])
        if failed:
            push_content.append(f"切换失败（{len(failed)}个账号）：")
            for account in failed:
                message = self.format_account_name({
                    'account': account.account,
                    'character': account.character,
                    'svr': account.svr,
                    'apple_or_android': account.apple_or_android,
                }, '**')
                logger.info(message)
                push_content.append(message)
        # 当天已完成的账号不算"本次执行", 只在下面的总进度里体现
        executed = [result for result in results if not result['skipped']]
        executed_lines = self.format_executed_results(executed)
        if executed_lines:
            push_content.append(f"本次执行任务（{len(executed)}个账号）：")
            for message in executed_lines:
                logger.info(message)
                push_content.append(message)
        # 断点续跑: 补上此前记录过的勾协结果
        if self.conf.daily_config.find_jade_enable:
            resumed_lines = self.format_resumed_jade_results(results)
            if resumed_lines:
                push_content.append(f"此前已记录勾协（{len(resumed_lines)}个账号）：")
                for message in resumed_lines:
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
                push_content.append(f"今日协战任务（{len(results)}个账号）：")
            else:
                push_content.append(
                    f"今日协战任务（{len(results)}个账号）：次数全部正常"
                )
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
        # 最终切换失败的账号(重试后仍失败, 或未开启重试时首轮失败), 供推送使用
        self.switch_failed_accounts = []
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
                and self.conf.optimize_config.retry_failed_account_enable
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
                self.switch_failed_accounts = list(failed_accounts)
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
        # 本号是否打觉醒: 只改内存里的运行期标记, 不动配置也不落盘, 跑完恢复
        target_evozone = self.evozone_enabled(account)
        self.current_evozone_enable = target_evozone
        self._current_account = account
        try:
            (
                evozone_done,
                realmraid_done,
                evozone_final,
                realmraid_final,
                jade_flag,
                jade_checked,
                consignment_ticket,
            ) = self.run_current_account()
        finally:
            self.current_evozone_enable = True
            self._current_account = None
        self.mark_account_done(
            account, evozone_final, realmraid_final, jade_checked, jade_flag
        )
        results.append(
            {
                'account': account.account,
                'character': account.character,
                'svr': account.svr,
                'apple_or_android': account.apple_or_android,
                'evozone_enable': target_evozone,
                'evozone_done': evozone_done,
                'realmraid_done': realmraid_done,
                'evozone_final': evozone_final,
                'realmraid_final': realmraid_final,
                'jade_flag': jade_flag,
                'consignment_ticket': consignment_ticket,
                'skipped': False,
            }
        )
        return True

    def append_skipped_result(self, account, results) -> None:
        """当天已完成的账号不登录确认, 按当前开启的协战类型推算结果计入总进度"""
        cfg = self.conf.assist_battle_config
        evozone_on = self.evozone_enabled(account)
        results.append(
            {
                'account': account.account,
                'character': account.character,
                'svr': account.svr,
                'apple_or_android': account.apple_or_android,
                'evozone_enable': evozone_on,
                'evozone_done': 0,
                'realmraid_done': 0,
                'evozone_final': self.EVOZONE_TOTAL if evozone_on else 0,
                'realmraid_final': self.REALMRAID_TOTAL if cfg.realmraid_enable else 0,
                # 跳过的账号读上次记录的勾协状态(中断恢复也靠它)
                'jade_flag': self._skipped_jade_flag(account),
                'consignment_ticket': None,
                'skipped': True,
            }
        )

    def _skipped_jade_flag(self, account) -> str:
        """跳过账号显示的勾协状态: 确认过的原样显示, 没确认过显示未知"""
        valid = (
            self.FIND_JADE_NONE,
            self.FIND_JADE_NORMAL,
            self.FIND_JADE_SPECIAL,
        )
        if account.jade_status in valid:
            return account.jade_status
        # 本周期还没确认过(初始 none 或中断), 显示未知以区别于真正的无勾
        return self.FIND_JADE_UNKNOWN

    def is_account_done_today(self, account) -> bool:
        """联合判定: 协战按 0 点、勾协按 5/18 点各看各的时间戳, 都完成才跳过

        还有一项独立的待办：神秘商店。它只有周三/周六开放、且只算「本号本开放日跑过没有」，
        跟协战/勾协的时间戳没关系。不加这一条就会出现最典型的一种漏跑 ——
        上午那一轮把协战打满了，晚上 18 点这一轮所有号都被判成「今天已完成」整号跳过，
        商店一个号都进不去。
        """
        if not self.conf.optimize_config.skip_completed_account_enable:
            return False
        battle_enabled = (
            self.evozone_enabled(account)
            or self.conf.assist_battle_config.realmraid_enable
        )
        jade_enabled = self.conf.daily_config.find_jade_enable
        if not (battle_enabled or jade_enabled):
            return False
        now = datetime.now()
        if battle_enabled and account.last_complete_time.date() != now.date():
            return False
        if jade_enabled and account.jade_complete_time <= self._jade_refresh_point(now):
            return False
        # 协战、勾协都做完了，但商店还没跑 → 这个号仍然要切过去
        if self.mystery_shop_pending(account):
            return False
        return True

    @staticmethod
    def _jade_refresh_point(now: datetime) -> datetime:
        """勾协最近一次刷新时刻(每天 5 点/18 点), 完成时间晚于它才算本周期。"""
        points = [datetime.combine(now.date(), time(h, 0)) for h in (5, 18)]
        passed = [p for p in points if p <= now]
        if passed:
            return max(passed)
        return datetime.combine(now.date() - timedelta(days=1), time(18, 0))

    def _current_model_account(self, account):
        """从当前 config.model 找回账号对象（防中途 reload 导致写旧 model、save 落新 model）"""
        current_conf = self.config.model.assist_battle
        for candidate in current_conf.account_list or []:
            if (
                candidate.character == account.character
                and candidate.svr == account.svr
                and candidate.account == account.account
            ):
                return candidate
        return None

    def mark_account_done(
        self,
        account,
        evozone_final: int,
        realmraid_final: int,
        jade_checked: bool = False,
        jade_flag: str = FIND_JADE_NONE,
    ) -> None:
        """协战、勾协各自记各自的时间戳, 写入前重取当前 model 的账号对象"""
        battle = self.conf.assist_battle_config
        jade_enable = self.conf.daily_config.find_jade_enable
        if not self.conf.optimize_config.skip_completed_account_enable:
            return
        # 觉醒按本号自己的勾选算, 没勾觉醒的号不能因为觉醒次数不满而判成"没完成"
        evozone_on = self.evozone_enabled(account)
        battle_all_done = (
            (evozone_on or battle.realmraid_enable)
            and (evozone_final >= self.EVOZONE_TOTAL or not evozone_on)
            and (realmraid_final >= self.REALMRAID_TOTAL or not battle.realmraid_enable)
        )
        jade_done = jade_enable and jade_checked
        if not (battle_all_done or jade_done):
            return
        now = datetime.now()
        # 从当前 model 重取对象（防中途 reload 错位），写不进就跳过
        current = self._current_model_account(account)
        if current is None:
            logger.warning(
                'mark_account_done: %s-%s not found in current config model, skip writing',
                account.character,
                account.svr,
            )
            return
        # 各写各的: 协战时间只在打满时写, 勾协时间只在确认过时写
        if battle_all_done:
            current.last_complete_time = now
        if jade_checked:
            current.jade_complete_time = now
            current.jade_status = jade_flag
        self.config.save()
        if battle_all_done:
            logger.info(
                'Assist battle of %s-%s is done today, it will be skipped until next day',
                account.character,
                account.svr,
            )
        if jade_done:
            logger.info(
                'Jade quests of %s-%s checked this cycle, it will be skipped until next refresh',
                account.character,
                account.svr,
            )

    def run_current_account(self):
        # 执行任务前先获取本账号协战剩余次数以检查是否执行过前置任务，觉醒协战已做完将不再执行日常任务
        evozone_done, realmraid_done = 0, 0
        evozone_final, realmraid_final = 0, 0
        jade_flag = self.FIND_JADE_NONE
        jade_checked = False
        consignment_ticket = None
        # 不执行协战任务时，可以用来小号挂日常
        start_evozone = 1
        start_realmraid = 1

        if self.evozone_enabled() or self.conf.assist_battle_config.realmraid_enable:
            start_evozone, start_realmraid = self.get_assist_battle_count()

        # 邮件领取
        if (
            self.conf.daily_config.email_enable
            and weekday_matches(self.conf.daily_config.email_weekday, date.today())
            and self.daily_window
        ):
            self.run_pickup_email()
            self.goto_page(page_main)

        # 寄售屋（买寄售券 + 买指定联动碎片，两个开关共用一次进寄售屋）
        daily = self.conf.daily_config
        buy_ticket = daily.consignment_enable and weekday_matches(
            daily.consignment_weekday, date.today()
        )
        buy_shikigami = daily.consignment_shikigami_enable and weekday_matches(
            daily.consignment_shikigami_day, date.today()
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
                    rm_con.shikigami_name = daily.consignment_shikigami_name
                self.goto_page(page_mall, confirm_wait=2.5)
                self.execute_consignment()
                # 买完停在寄售屋弹窗里，此时立即读券数；不要先 goto_page，
                # 那样会把弹窗关掉，ROI 落到商城页读到别的内容。
                if buy_ticket:
                    ticket_count = self.get_consignment_ticket_count()
                    if ticket_count is not None:
                        consignment_ticket = ticket_count
                self.goto_page(page_main)
            finally:
                (
                    rm_con.enable,
                    rm_con.buy_sale_ticket,
                    rm_con.buy_shikigami,
                    rm_con.shikigami_name,
                ) = backup

        # 结界寄养
        if self.conf.daily_config.kekkaiutilize_enable and self.daily_gate_open(
            start_evozone
        ):
            # 进入寮结界
            self.goto_page(page_guild_realm)
            self.check_utilize_add()
            self.goto_page(page_main)

        # 庭院事务
        if (
            self.conf.daily_config.courtyard_affairs_enable
            and self.daily_gate_open(start_evozone)
            and self.daily_window
        ):
            self.run_courtyard_affairs()
            self.goto_page(page_main)

        # 商店签到
        if (
            self.conf.daily_config.store_sign_enable
            and self.daily_gate_open(start_evozone)
            and self.daily_window
        ):
            # 重置done状态
            self.config.daily_trifles.done_record.store_sign_dt = datetime(2023, 1, 1)
            self.run_store_sign()
            self.goto_page(page_main)

        # 神秘商店：开关 + 晚间窗口 + 开放日 + 本号本开放日还没跑，四样齐了才进店。
        # 刻意不看 daily_gate_open —— 那个条件是「觉醒协战还有剩余次数」，而商店最常见的
        # 执行时机恰恰是协战已经打完之后那一轮，套上去就会永远跑不到。
        if self.mystery_shop_pending(self._current_account):
            if self.run_mystery_shop():
                self.mark_mystery_shop_done()
            self.goto_page(page_main)

        # 给指定好友点赞（默认关闭；找不到/失败只打 warning，不影响其它日常）
        # try/finally 保证无论中途停在哪个子页面，都先回主界面再往下走
        if self.conf.daily_config.friend_like_enable:
            try:
                self.run_friend_like()
            except RequestHumanTakeover:
                raise
            except (GamePageUnknownError, GameStuckError, GameTooManyClickError) as e:
                # 设备层异常（点太密 / 卡住 / 认不出页面）不能打断本号后面的找勾协和下一个号；
                # finally 仍会回主界面，这里只负责吞掉异常 + 清掉点击历史
                logger.warning('好友点赞执行异常，本号跳过点赞并清理点击记录: %r', e)
                self.device.click_record_clear()
            finally:
                # 回主界面失败也不能把异常冒出去，否则后面的找勾协、下一个号全不跑
                try:
                    self.goto_page(page_main)
                except RequestHumanTakeover:
                    raise
                except (GamePageUnknownError, GameStuckError, GameTooManyClickError) as e:
                    logger.warning('好友点赞后回主界面失败，交给后续任务自行导航: %r', e)
                    self.device.click_record_clear()

        # 寻找勾协
        if self.conf.daily_config.find_jade_enable:
            jade_flag = self.find_jade()
            if (
                jade_flag == self.FIND_JADE_NORMAL
                and self.conf.daily_config.find_jade_finish_enable
            ):
                self.run_own_wanted_quests()
            self.ui_click_until_disappear(self.I_UI_BACK_RED, interval=1)
            self.goto_page(page_main)
            # 页面打开过才算确认(未知 = 没打开, 不记, 下轮重查)
            jade_checked = jade_flag != self.FIND_JADE_UNKNOWN

        # 执行觉醒副本任务
        if self.evozone_enabled() and start_evozone > 0:
            self.run_evozone(start_evozone)
            self.goto_page(page_main)

        # 执行结界突破任务
        if self.conf.assist_battle_config.realmraid_enable and start_realmraid > 0:
            self.run_realmraid(start_realmraid)
            self.goto_page(page_main)

        # 统计本次执行完成的协战任务
        if self.evozone_enabled() or self.conf.assist_battle_config.realmraid_enable:
            end_evozone, end_realmraid = self.get_assist_battle_count()
            evozone_done = start_evozone - end_evozone
            realmraid_done = start_realmraid - end_realmraid
            # 今天已经完成的任务
            evozone_final = self.EVOZONE_TOTAL - end_evozone
            realmraid_final = self.REALMRAID_TOTAL - end_realmraid
            if self.evozone_enabled():
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

        return (
            evozone_done,
            realmraid_done,
            evozone_final,
            realmraid_final,
            jade_flag,
            jade_checked,
            consignment_ticket,
        )

    #  ---------------------------------------------------------------------------------------------------------------
    #  神秘商店：只做「什么时候该跑、跑完记一笔」，怎么买全部由 MysteryShop 自己决定
    #  ---------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _mystery_shop_today() -> str:
        return datetime.now().strftime('%Y-%m-%d')

    def mystery_shop_pending(self, account) -> bool:
        """本号这一轮还要不要进神秘商店。

        三个条件全部满足才算待办：
          ① 开关打开；
          ② 今天是商店开放日（周三 / 周六，取自 MysteryShop.OPEN_WEEKDAYS，不在这里重写一遍）；
          ③ 本号本开放日还没跑过（mystery_shop_date != 今天）。

        **刻意不看 daily_window**（用户 11-04 明确）：商店在开放日 00:00 刷新、当天 24:00
        结束，整天都能买；而协战这一轮的启动时刻由 scheduler.server_update 决定 —— 它不等于
        09:00 时，module/config/config.py:360 会无视 success_interval，直接排到「明天同一时刻」
        （实测：今天 11:28 跑完，next_run 被写成次日 06:05，而不是 23:28）。也就是协战永远在
        早上 06:05 启动，落不进 18~23 点窗口，套上 daily_window 等于一次都进不了店。

        「当天只运行一次」不靠时间窗，靠 ③ 的日期戳：同一开放日不管协战跑几轮，
        第二轮起 mystery_shop_date 已是今天，自动跳过。将来若把 server_update 调成 09:00
        让 12 小时间隔真正生效（一天两轮），这条照样只让第一轮进店。

        不看 account.last_complete_time：那个是协战的时间戳，协战打完不代表商店跑过。
        """
        if account is None:
            return False
        if not self.conf.daily_config.mystery_shop_enable:
            return False
        if date.today().weekday() not in MysteryShopScriptTask.OPEN_WEEKDAYS:
            return False
        return getattr(account, 'mystery_shop_date', '') != self._mystery_shop_today()

    def run_mystery_shop(self) -> bool:
        """跑一趟神秘商店本体，跑成了返回 True。

        只调 run_shop_body()，不调 run() —— 后者内部靠 raise TaskEnd 收尾，
        而且会改 MysteryShop 自己的 next_run。用 body 版就等于「顺手替它跑一趟」，
        它的日程表、第二趟判定全都不受影响。

        TaskEnd 必须在这里吞掉：万一 MysteryShop 内部哪天又抛了 TaskEnd，
        冒到 run_switch_account 会让整个一键协战提前结束，后面的号全都不跑。
        """
        logger.hr('Run MysteryShop for current account', 3)
        try:
            task = MysteryShopScriptTask(self.config, self.device)
            done = task.run_shop_body()
        except RequestHumanTakeover:
            # 需要人工介入的一律往上抛，和 run_own_wanted_quests 的处理保持一致
            raise
        except TaskEnd as e:
            logger.warning(f'MysteryShop 提前结束（TaskEnd），本号商店按已完成处理: {e}')
            done = True
        except Exception as e:
            # 导航超时（GamePageUnknownError）/ 点击过密（GameTooManyClickError）都落这里：
            # 商店这一趟放弃，但这个号后面的协战、以及下一个号都要继续跑。
            # 清一次点击记录，否则「同一按钮 ≥10 次」的历史会带进下一个号，一进去就炸。
            logger.error(f'MysteryShop 执行异常，本号商店跳过: {e!r}')
            try:
                self.device.click_record_clear()
            except Exception as cleanup_error:
                logger.warning(f'清理点击记录失败，不影响继续: {cleanup_error!r}')
            done = False
        # run_shop_body 里可能 reload 过配置（第一趟会写 first_pass_date），
        # self.conf 还指着旧 model，重新取一次，后面写账号字段才不会丢
        self.conf = self.config.model.assist_battle
        return done

    def mark_mystery_shop_done(self) -> None:
        """把「本号今天商店跑完了」写进配置。

        写失败只影响效率（下一轮会再进一次店），不影响正确性，所以不抛异常。
        """
        account = self._current_account
        if account is None:
            return
        current = self._current_model_account(account)
        if current is None:
            logger.warning('mark_mystery_shop_done: 账号不在当前配置里，跳过记录')
            return
        current.mystery_shop_date = self._mystery_shop_today()
        self.config.save()
        logger.info(
            'MysteryShop of %s-%s done for %s, it will not be entered again today',
            account.character,
            account.svr,
            current.mystery_shop_date,
        )

    # ----------------------------------------------------------------------
    #  给指定好友点赞
    #  界面实证(16512, 11-05)：好友组与跨区组互斥展开，标题行固定不随列表滚动；
    #  点赞图标位置固定 (617,140)，白/橙两态模板互认仅 0.075。
    #  中文 OCR 有误字(「过山时雨」读成「过山时而」)，故模糊匹配 + 点开后二次核对。
    # ----------------------------------------------------------------------
    FRIEND_LIKE_MAX_SCROLL = 20
    FRIEND_LIKE_TIMEOUT = 60
    # 回顶专用上限，与扫描上限分开。从庭院进好友页时游戏会恢复上次滚动位置，
    # 11-05 实测最坏停在列表底部（190 个同区好友约 40 屏），20 次短滑回不到顶，
    # 顶部好友整屏漏掉。回顶不怕滑过头：到顶后 Q搜索 会一直稳定可见，
    # 所以放宽上限只影响耗时，不影响「是否漏扫」。扫描上限仍保持 20。
    FRIEND_LIKE_MAX_TO_TOP = 60
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
            diff = self._swipe_friend_list(self.S_FRIEND_LIST_UP)
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

    def _friend_list_hits(self, pending: set) -> list:
        """当前帧左侧列表里匹配到的 (目标名, 点击x, 点击y)"""
        results = self.O_FRIEND_LIST.detect_and_ocr(self.device.image)
        if not results:
            return []
        hits = []
        for result in results:
            box = result.box
            x = int(
                box[0, 0] + self.O_FRIEND_LIST.roi[0]
                + (box[1, 0] - box[0, 0]) / 2
            )
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
        """
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
            state = self._friend_like_state()
            if state == 'on':
                return 'liked'
            if state == 'unknown':
                return 'failed'
            if attempt < 2:
                logger.info('第 %d 次点赞后图标仍是白态，再试一次', attempt)
        logger.warning('连续 2 次点赞后图标仍是白态，放弃，避免重复消耗当天点赞')
        return 'failed'

    def _ensure_friend_group_expanded(self) -> bool:
        """确保当前展开的是同区好友组（判「到了」不判「点了」）"""
        self.screenshot()
        if self.appear(self.I_FRIEND_GROUP_EXPANDED):
            return True
        if self.appear(self.I_FRIEND_GROUP_FOLDED):
            self.appear_then_click(
                self.I_FRIEND_GROUP_FOLDED, action=self.C_FRIEND_GROUP_SWITCH
            )
            sleep(1.0)
            self.screenshot()
            return bool(self.appear(self.I_FRIEND_GROUP_EXPANDED))
        logger.warning('好友分组状态认不出来，跳过点赞')
        return False

    def _switch_to_cross_group(self) -> bool:
        """同区找完还没齐 -> 展开跨区好友组"""
        self.screenshot()
        if not self.appear(self.I_FRIEND_CROSS_FOLDED):
            return bool(self.appear(self.I_FRIEND_GROUP_FOLDED))
        self.appear_then_click(
            self.I_FRIEND_CROSS_FOLDED, action=self.C_CROSS_GROUP_SWITCH
        )
        sleep(1.0)
        self.screenshot()
        return bool(self.appear(self.I_FRIEND_GROUP_FOLDED))

    def _log_friend_scan_timeout(self, label: str, timer: Timer, rounds: int, pending: set) -> None:
        """扫描被超时掐断时留痕：只看日志就能分清「扫完了」和「没扫完」"""
        logger.warning(
            '好友点赞（%s组）扫描被超时掐断，未扫完：已用 %.1fs/%ss，'
            '滚动 %d/%d 次，剩余 %d 人未查：%s',
            label, timer.current(), self.FRIEND_LIKE_TIMEOUT,
            rounds, self.FRIEND_LIKE_MAX_SCROLL,
            len(pending), '、'.join(sorted(pending)),
        )

    def _search_friend_group(self, pending: set, label: str) -> None:
        """在当前展开的分组里滚动找人；找到就核对名字、点赞、移出 pending。

        每组自带独立计时器，同区滚满也不会把跨区的时间吃光；
        画面不再变化就提前收手，不做无用的空滑。
        每个出口都留一条日志，标明是「扫完了」还是「被上限/超时掐断」。
        """
        timer = Timer(self.FRIEND_LIKE_TIMEOUT).start()
        for scroll_count in range(1, self.FRIEND_LIKE_MAX_SCROLL + 1):
            if not pending:
                logger.info('好友点赞（%s组）名单已全部处理完，扫描结束', label)
                return
            if timer.reached():
                self._log_friend_scan_timeout(label, timer, scroll_count - 1, pending)
                return
            self.screenshot()
            for name, x, y in self._friend_list_hits(pending):
                if name not in pending:
                    continue
                self.device.click(x=x, y=y, control_name='friend_pick')
                self.device.click_record_clear()
                sleep(0.6)
                self.screenshot()
                if not self._verify_friend_chat_name(name):
                    logger.warning('点赞跳过：点开后名字对不上，目标=%s', name)
                    continue
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
        names = self.conf.daily_config.friend_like_names_v
        if not names:
            logger.warning('「给指定好友点赞」已开启，但好友名列表是空的')
            return False
        try:
            if self.get_current_page() != page_friends:
                self.goto_page(page_friends)
        except RequestHumanTakeover:
            # 需要人工介入的一律往上抛，别吞
            raise
        except (GamePageUnknownError, GameStuckError, GameTooManyClickError) as error:
            # 上游范本 KekkaiUtilize:655；导航失败只是本号不点赞，不能打断协战
            logger.warning('进入好友界面失败，跳过点赞: %r', error)
            self.device.click_record_clear()
            return False
        self.screenshot()
        if not self.appear(GameUiAssets.I_CHECK_FRIENDS):
            logger.warning('没能确认进入好友界面，跳过点赞')
            return False

        pending = set(names)
        if not self._ensure_friend_group_expanded():
            return False
        # 进好友页时列表常停在上次滚动的位置（切号/切组都会保留），先拉回顶部再扫
        self._friend_list_to_top()
        self._search_friend_group(pending, '同区')
        if pending:
            if self._switch_to_cross_group():
                self._friend_list_to_top()
                self._search_friend_group(pending, '跨区')
            else:
                logger.warning('切不到跨区好友分组，放弃剩余好友')
        if pending:
            logger.warning('以下好友没找到，放弃点赞：%s', '、'.join(sorted(pending)))
        logger.info('好友点赞完成：%d/%d', len(names) - len(pending), len(names))
        return True

    def find_jade(self):
        """寻找勾协并标记，返回 无勾/普勾/现勾/未知"""
        if self.get_current_page() != page_main:
            self.goto_page(page_main)
        # 打开悬赏封印 界面
        open_timer = Timer(8)
        open_timer.start()
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
            if open_timer.reached():
                self.ui_click_until_disappear(self.I_UI_BACK_RED)
                # 页面没打开, 无法确认有没有勾, 不能当"无勾"记
                logger.warning('悬赏封印页面 15 秒未打开, 勾协状态记为未知')
                return self.FIND_JADE_UNKNOWN

        # 页面刚打开时槽位图片可能还没渲染完（页面框架先出、任务图后加载），
        # 必须连续两帧判定一致才采信
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

        if not invited:
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

    def get_consignment_ticket_count(self):
        """读取寄售屋弹窗里的寄售券数量，读不到返回 None。

        照搬日常任务 read_title_value 的做法：裁掉 ROI 左右空白让数字居中，
        再直调底层模型并校验置信度，不足则回退普通 ocr()。
        """
        rule = self.O_CONSIGNMENT_TICKTS
        image = self.device.image
        result = None
        try:
            x, y, w, h = rule.roi
            crop = rule.pre_process(image[y : y + h, x : x + w])
            crop = self._trim_ocr_margin(crop)
            raw, score = rule.model.ocr_single_line(crop)
            if raw and score >= rule.score:
                result = rule.after_process(raw)
                logger.info('寄售券数量(裁边识别): %r score=%.2f', raw, score)
        except Exception as e:
            logger.warning('寄售券裁边识别异常，回退普通流程: %s', e)

        if result is None:
            result = rule.ocr(image)

        count = self._parse_ticket_ocr(result)
        if count is None:
            logger.warning('寄售券数量读取失败: %r', result)
            return None
        logger.info('寄售券数量: %s（原始 %r）', count, result)
        return count

    @classmethod
    def _trim_ocr_margin(cls, image, threshold: int = 130, pad: int = 2):
        """裁掉左右纯背景空白列，各保留 pad 像素（同 DailyTrifles）。"""
        column_max = image.max(axis=2).max(axis=0)
        ink = np.where(column_max > threshold)[0]
        if not len(ink):
            return image
        x0 = max(0, int(ink[0]) - pad)
        x1 = min(image.shape[1], int(ink[-1]) + 1 + pad)
        return image[:, x0:x1]

    @staticmethod
    def _parse_ticket_ocr(raw):
        """把 OCR 结果统一成整数；无法解析返回 None。"""
        if isinstance(raw, bool):
            return None
        if isinstance(raw, int):
            return raw
        if isinstance(raw, str):
            digits = ''.join(ch for ch in raw if ch.isdigit())
            return int(digits) if digits else None
        return None

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
