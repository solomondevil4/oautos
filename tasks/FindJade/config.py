from datetime import datetime, time, timedelta
from typing import Any, Dict

from pydantic import Field, BaseModel, ConfigDict, model_validator, model_serializer, ValidationError

from tasks.Component.SwitchAccount.switch_account_config import AccountInfo
from tasks.Component.config_base import ConfigBase, DateTime, Time, dynamic_hide
from tasks.Component.config_scheduler import Scheduler
from tasks.WantedQuests.config import CooperationSelectMaskDescription, CooperationSelectMask, CooperationType

# 悬赏封印的游戏刷新点, 与脚本实际运行时刻(run_time_1/2)无关
JADE_REFRESH_HOURS = (5, 18)


class InviteHistoryItem(BaseModel):
    cooperation_type: CooperationType
    time: DateTime


class InviteInfo(BaseModel):
    # 被邀请人员昵称
    name: str = Field(default="", description='name_help')
    default_invite_type: CooperationSelectMaskDescription = Field(default=CooperationSelectMaskDescription.JadeAndFood,
                                                                  description='default_invite_type_help')

    # 协作任务类型   上次邀请时间
    invite_history_1: DateTime = Field(default=DateTime.fromisoformat("2023-01-01 00:00:00"),
                                       description='invite_history_1_help')
    invite_history_2: DateTime = Field(default=DateTime.fromisoformat("2023-01-01 00:00:00"),
                                       description='invite_history_2_help')
    invite_history_4: DateTime = Field(default=DateTime.fromisoformat("2023-01-01 00:00:00"),
                                       description='invite_history_4_help')
    invite_history_8: DateTime = Field(default=DateTime.fromisoformat("2023-01-01 00:00:00"),
                                       description='invite_history_8_help')
    # 现世勾协单独记一份: 游戏里同一个好友每个周期可以各接一次普勾和现勾,
    # 两者都是勾玉奖励(CooperationType.Jade), 共用 invite_history_2 会让第二次被
    # 误判成「刚接过」而跳过, 少发一份额度。
    invite_history_jade_special: DateTime = Field(
        default=DateTime.fromisoformat("2023-01-01 00:00:00"),
        description='上次邀请现世勾协的时间（脚本自动维护，请勿手改）',
    )

    def _history_field(self, ctype: CooperationType, present_world: bool = False) -> str:
        """返回该协作类型该读写的「上次邀请时间」字段名

        现世勾协(Jade + 现世标记)走独立字段; 其余一律沿用上游的 invite_history_<类型值>。
        """
        if present_world and ctype & CooperationType.Jade:
            return 'invite_history_jade_special'
        return "invite_history_" + str(ctype)

    def need_invite(self, ctype: CooperationType, present_world: bool = False):
        if not ctype & CooperationSelectMask[self.default_invite_type.value]:
            return False
        last_time = self.__getattribute__(self._history_field(ctype, present_world))

        now = datetime.now()
        if now - last_time > timedelta(hours=13):
            return True
        if (last_time.hour >= 18 or last_time.hour < 5) and (18 > now.hour >= 5):
            return True
        if (5 <= last_time.hour < 18) and now.hour >= 18:
            return True
        return False

    def update_invite_history(self, ctype: CooperationType, name=None, present_world: bool = False):
        if name != self.name:
            return
        self.__setattr__(self._history_field(ctype, present_world), datetime.now())

    def is_valid(self):
        return self.name != "" and self.name is not None


class FindJadeAccountInfo(AccountInfo):
    """寻找勾协的账号: 额外记下本时段的检测结果, 供中断重启后合并推送

    照搬一键协战 AssistBattleAccountInfo 的做法: 每个号查完立刻把状态写回配置文件,
    进程被杀 / 人工接管 / 重启都还在; 下一轮被跳过的号再把状态读回来一起推送。
    """

    # 本时段确认过的勾协状态: none=没查过, 其余为 无勾/普勾/现勾/普勾+现勾
    jade_status: str = Field(default='none', description='脚本自动维护的勾协状态')
    # 本时段的检测时间, 与 WQ 完成用的 last_complete_time 分开记
    jade_complete_time: DateTime = Field(
        default=DateTime.fromisoformat("2023-01-01 00:00:00"),
        description='脚本自动维护的勾协检测时间',
    )
    hide_fields = dynamic_hide('jade_status', 'jade_complete_time')


class FindJadeConfig(ConfigBase):
    # GUI / Web 回写的都是字符串，不开赋值校验的话 Time 字段会原样存成 str，
    # 而 Time 的序列化器要求 time 对象（v.strftime），一保存整个配置就崩。
    # 开了之后 pydantic 会用 time_validator 自动把 "05:05:00" 转成 time。
    model_config = ConfigDict(extra='allow', validate_assignment=True)

    # 被邀请人数
    invite_info_count: int = Field(default=1, ge=1, description='invite_info_count_help')
    # 小号数
    sup_account_count: int = Field(default=1, ge=1, description='sup_account_count_help')
    # 每天两个固定运行时刻, 不受调度器 server_update 覆盖
    run_time_1: Time = Field(default=Time(hour=5, minute=5, second=0))
    run_time_2: Time = Field(default=Time(hour=18, minute=5, second=0))
    # 跑完把发现勾协的号推到邮箱
    push_result_enable: bool = Field(default=True)
    # 本时段(5点/18点刷新)已推送过的结果快照, 用来保证「一个时间段算一趟」:
    # 同一时段重启多次, 内容没变就不重发; 查出新勾协才补发一份完整清单。
    jade_push_state: str = Field(default='', description='脚本自动维护的推送快照')
    # ---------- 普通勾协邀请上限 ----------
    # 大号能挂的协作数量有限, 名单里的大号本时段都接过普勾之后, 后面的小号再邀请
    # 只会撞上「名单里所有好友本时段都已接过」, 每个格子白重试 5 次 × 5 秒。
    # 累计邀请成功的「普通勾协」达到这个数就直接结束本次任务。
    # 只数普勾: 现世勾协(同样是 Jade 类型, 靠现世标记区分)和粮协都不计数。
    normal_jade_limit: int = Field(
        default=-1,
        ge=-1,
        title='普通勾协邀请到 N 个就停止本次任务',
        description='只数「普通勾协」，现世勾协和粮协都不计数。\n'
                    '-1 = 自动：等于邀请名单里的大号数量（每个大号各 1 个）\n'
                    '0 = 不限制（老行为，一直切完所有小号）\n'
                    '正整数 = 指定的总数量\n'
                    '按游戏 5:00 / 18:00 刷新时段累计，同一时段重启不会重复计数。',
    )
    # 本时段已邀请成功的普通勾协数 + 计数所属时段(如 2026-11-12-PM), 跨时段自动归零
    normal_jade_invited: int = Field(default=0, description='脚本自动维护的普通勾协计数')
    normal_jade_cycle: str = Field(default='', description='脚本自动维护的计数时段')
    hide_fields = dynamic_hide('jade_push_state', 'normal_jade_invited', 'normal_jade_cycle')


class FindJade(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    find_jade_config: FindJadeConfig = Field(default_factory=FindJadeConfig)
    # 邀请人员信息
    invite_info_list: list[InviteInfo] = None
    # 小号信息
    sup_account_list: list[FindJadeAccountInfo] = None

    # ---------- 普通勾协邀请额度 ----------
    # 时段判定与 script_task 的推送快照共用一套, 只在这里实现一份。

    @staticmethod
    def jade_refresh_point(now: datetime) -> datetime:
        """悬赏封印最近一次刷新时刻(每天 5 点 / 18 点)"""
        points = [datetime.combine(now.date(), time(h, 0)) for h in JADE_REFRESH_HOURS]
        passed = [p for p in points if p <= now]
        if passed:
            return max(passed)
        return datetime.combine(now.date() - timedelta(days=1), time(18, 0))

    @classmethod
    def jade_cycle_key(cls, now: datetime) -> str:
        """一个时间段算一趟: 05:00-18:00 记 AM, 18:00-次日05:00 记 PM"""
        refresh = cls.jade_refresh_point(now)
        return f'{refresh:%Y-%m-%d}-{"AM" if refresh.hour == 5 else "PM"}'

    def normal_jade_limit_effective(self) -> int:
        """实际生效的普勾上限: -1 = 名单里有效的大号数量, 0 = 不限制"""
        conf = self.find_jade_config
        if conf.normal_jade_limit > 0:
            return conf.normal_jade_limit
        if conf.normal_jade_limit == 0:
            return 0
        return len([i for i in (self.invite_info_list or []) if i.is_valid()])

    def normal_jade_remain(self, now: datetime = None) -> int:
        """本时段还能邀请几个普通勾协; -1 = 不限制, 0 = 已达上限"""
        limit = self.normal_jade_limit_effective()
        if limit <= 0:
            return -1
        conf = self.find_jade_config
        if conf.normal_jade_cycle != self.jade_cycle_key(now or datetime.now()):
            # 换时段了, 老计数作废
            return limit
        return max(0, limit - conf.normal_jade_invited)

    def note_normal_jade_invited(self, now: datetime = None) -> int:
        """记一次「普通勾协邀请成功」, 返回记完后的剩余额度(-1 = 不限制)"""
        conf = self.find_jade_config
        now = now or datetime.now()
        cycle = self.jade_cycle_key(now)
        if conf.normal_jade_cycle != cycle:
            conf.normal_jade_cycle = cycle
            conf.normal_jade_invited = 0
        conf.normal_jade_invited += 1
        return self.normal_jade_remain(now)

    def get_invite_name(self, ctype: CooperationType, present_world: bool = False):
        for info in self.invite_info_list:
            if info.need_invite(ctype, present_world):
                return info.name
        return ""

    def update_invite_history(self, ctype: CooperationType, name: str, present_world: bool = False):
        # 更新邀请信息
        for info in self.invite_info_list:
            if info.name != name:
                continue
            info.update_invite_history(ctype, name, present_world)

    def update_account_login_history(self, account: AccountInfo):
        accountInfoList = self.sup_account_list
        for info in accountInfoList:
            if info.character != account.character or info.svr != account.svr:
                continue
            info.last_complete_time = datetime.now()
            break

    def get_cooperation_type_mask(self) -> CooperationSelectMaskDescription:
        """
            根据配置中各个邀请人 的邀请类型
            综合得出该值
        """
        result = CooperationSelectMask.NoInvite
        for info in self.invite_info_list:
            result |= CooperationSelectMask[info.default_invite_type]
        name = CooperationSelectMask(result).name
        return CooperationSelectMaskDescription(name)

    @model_validator(mode='before')
    @classmethod
    def validator_all(cls, v: dict) -> Any:
        invite_info_count = v.get('find_jade_config', {}).get('invite_info_count', 1)
        sup_account_count = v.get('find_jade_config', {}).get('sup_account_count', 1)

        def validator_list(list_name, data, item_type=None, list_size=1):
            if list_name not in data:
                data[list_name] = []

            remove_keys = []
            for key, value in data.items():
                if list_name == key or list_name not in key:
                    continue
                try:
                    item = item_type(**value)
                    if item.is_valid():
                        data[list_name].append(item)
                    remove_keys.append(key)
                except ValidationError as e:
                    pass
                except TypeError as e:
                    pass

            for key in remove_keys:
                del data[key]

            if item_type is not None:
                if len(data[list_name]) < list_size:
                    for i in range(list_size - len(data[list_name])):
                        data[list_name].append(item_type())

        validator_list('invite_info_list', v, InviteInfo, invite_info_count)
        validator_list('sup_account_list', v, FindJadeAccountInfo, sup_account_count)

        return v

    @model_serializer()
    def serializer_model(self, info) -> Dict[str, Any]:
        # 必须把 context 转发给子模型, 否则账号/配置上的 dynamic_hide 只对落盘生效、
        # 给 GUI 时隐藏字段会漏出来(一键协战 config.py:248-265 是同一写法)。
        context = info.context if info is not None else None
        data = {}

        def v_dump(v):
            return v.model_dump(context=context) if isinstance(v, BaseModel) else v

        for key, value in self.__dict__.items():
            if isinstance(value, list):
                for index, v in enumerate(value):
                    data[f'{key}_{index + 1}'] = v_dump(v)
            else:
                data[key] = v_dump(value)
        return data
