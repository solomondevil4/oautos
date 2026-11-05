from datetime import datetime, timedelta
from typing import Any, Dict

from pydantic import Field, BaseModel, ConfigDict, model_validator, model_serializer, ValidationError

from tasks.Component.SwitchAccount.switch_account_config import AccountInfo
from tasks.Component.config_base import ConfigBase, DateTime, Time, dynamic_hide
from tasks.Component.config_scheduler import Scheduler
from tasks.WantedQuests.config import CooperationSelectMaskDescription, CooperationSelectMask, CooperationType


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

    def need_invite(self, ctype: CooperationType):
        if not ctype & CooperationSelectMask[self.default_invite_type.value]:
            return False
        last_time = self.__getattribute__("invite_history_" + str(ctype))

        now = datetime.now()
        if now - last_time > timedelta(hours=13):
            return True
        if (last_time.hour >= 18 or last_time.hour < 5) and (18 > now.hour >= 5):
            return True
        if (5 <= last_time.hour < 18) and now.hour >= 18:
            return True
        return False

    def update_invite_history(self, ctype: CooperationType, name=None):
        if name != self.name:
            return
        self.__setattr__("invite_history_" + str(ctype), datetime.now())

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
    hide_fields = dynamic_hide('jade_push_state')


class FindJade(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    find_jade_config: FindJadeConfig = Field(default_factory=FindJadeConfig)
    # 邀请人员信息
    invite_info_list: list[InviteInfo] = None
    # 小号信息
    sup_account_list: list[FindJadeAccountInfo] = None

    def get_invite_name(self, ctype: CooperationType):
        for info in self.invite_info_list:
            if info.need_invite(ctype):
                return info.name
        return ""

    def update_invite_history(self, ctype: CooperationType, name: str):
        # 更新邀请信息
        for info in self.invite_info_list:
            if info.name != name:
                continue
            info.update_invite_history(ctype, name)

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
