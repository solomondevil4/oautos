from typing import Any, Dict
from datetime import date
from enum import Enum

from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    model_serializer,
    model_validator,
)

from tasks.Component.config_scheduler import Scheduler
from tasks.Component.config_base import ConfigBase, DateTime, dynamic_hide
from tasks.Component.SwitchAccount.switch_account_config import AccountInfo
from tasks.RichMan.config import ConsignShikigami
from tasks.RichMan.mall.shikigami_map import SHIKIGAMI_NAMES


class AssistBattleAccountInfo(AccountInfo):
    """一键协战的账号: 额外的勾协字段, 供跳过判定与推送使用"""

    # 勾协检查结果: 从未执行是 none, 执行过是无勾/普勾/现勾; 内部状态, 不在 oasx 显示
    jade_status: str = Field(default='none', description='jade_status_help')
    # 协战过程中发现勾协的时间(5/18 点刷新), 与协战的 last_complete_time 分开记
    jade_complete_time: DateTime = Field(
        default=DateTime.fromisoformat('2023-01-01 00:00:00'),
        description='jade_complete_time_help',
    )
    hide_fields = dynamic_hide('jade_status')


class Weekday(str, Enum):
    """邮件/寄售屋等一次性事务的执行日。"""

    EVERY_DAY = '每天'
    MONDAY = '周一'
    TUESDAY = '周二'
    WEDNESDAY = '周三'
    THURSDAY = '周四'
    FRIDAY = '周五'
    SATURDAY = '周六'
    SUNDAY = '周日'


# 与 datetime.weekday() 对应：周一=0 … 周日=6
WEEKDAY_INDEX = {
    Weekday.MONDAY: 0,
    Weekday.TUESDAY: 1,
    Weekday.WEDNESDAY: 2,
    Weekday.THURSDAY: 3,
    Weekday.FRIDAY: 4,
    Weekday.SATURDAY: 5,
    Weekday.SUNDAY: 6,
}


def weekday_matches(setting: Weekday, today: date) -> bool:
    """今天是否该执行。'每天' 恒为 True。"""
    if setting == Weekday.EVERY_DAY:
        return True
    return today.weekday() == WEEKDAY_INDEX.get(setting, 6)


class AssistBattlePushConfig(BaseModel):
    result_push_enable: bool = Field(default=False, description='需在脚本设置中启用')
    executed_count_enable: bool = Field(
        default=False,
        description='本次执行结果显示觉醒和结界次数',
    )
    find_jade_push_clear_enable: bool = Field(
        default=False,
        description='只推送有勾协的账号',
    )
    full_name_enable: bool = Field(
        default=False,
        description='显示完整的账号、服务器和角色名',
    )
    consignment_ticket_enable: bool = Field(
        default=False,
        description='推送每个账号购买寄售券后的剩余数量（逐个账号列出）',
    )


class AssistBattleBattleConfig(BaseModel):
    """协战：打几次、怎么打。"""

    account_count: int = Field(
        default=1,
        ge=1,
        description='account_list_description',
    )
    evozone_enable: bool = Field(
        default=False,
        description='默认雷麒麟5层',
    )
    realmraid_enable: bool = Field(default=True, description='默认锁定阵容')
    realmraid_easy_enable: bool = Field(
        default=True,
    )
    switch_soul_enable: bool = Field(
        default=False,
        description='读个突配置',
    )


class AssistBattleDailyConfig(BaseModel):
    """日常：协战之外顺带完成的功能。"""

    email_enable: bool = Field(
        default=True,
        description='按「邮件领取日」执行',
    )
    email_weekday: Weekday = Field(
        default=Weekday.SUNDAY,
        description='邮件领取的执行日，选「每天」则每次运行都领',
    )
    courtyard_affairs_enable: bool = Field(default=True)
    store_sign_enable: bool = Field(default=True)
    find_jade_enable: bool = Field(
        default=False,
        description='标记有勾协的账号且可推送',
    )
    find_jade_finish_enable: bool = Field(
        default=True,
        description='直接完成普通勾协后续只需邀请',
    )
    kekkaiutilize_enable: bool = Field(
        default=False,
        description='按结界蹭卡设置执行',
    )
    consignment_enable: bool = Field(
        default=False,
        description='按「寄售屋执行日」执行',
    )
    consignment_weekday: Weekday = Field(
        default=Weekday.SUNDAY,
        description='寄售券购买的执行日，选「每天」则每次运行都买',
    )
    # 购买指定联动碎片（复用大富翁-购买指定式神碎片的流程）
    consignment_shikigami_enable: bool = Field(
        default=False,
        description='与「启用寄售屋购买」共用一次进寄售屋，下面可选星期和式神',
    )
    consignment_shikigami_day: Weekday = Field(
        default=Weekday.SUNDAY,
        description='购买指定联动碎片在周几运行',
    )
    consignment_shikigami_name: ConsignShikigami = Field(
        default=ConsignShikigami(SHIKIGAMI_NAMES[0]),
        description='要购买的联动式神碎片',
    )


class AssistBattleOptimizeConfig(BaseModel):
    """优化：何时做、跳过谁、失败怎么办。"""

    daily_time_window_enable: bool = Field(
        default=True,
        description='邮件领取/寄售屋/庭院琐事/商店签到仅在 18:00-23:00 执行',
    )
    skip_completed_account_enable: bool = Field(
        default=True,
        description='本轮已完成的账号不再重复执行',
    )
    retry_failed_account_enable: bool = Field(
        default=True,
        description='切号失败的账号会最后再尝试一次',
    )


class AssistBattle(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    battle_config: AssistBattleBattleConfig = Field(
        default_factory=AssistBattleBattleConfig
    )
    daily_config: AssistBattleDailyConfig = Field(
        default_factory=AssistBattleDailyConfig
    )
    optimize_config: AssistBattleOptimizeConfig = Field(
        default_factory=AssistBattleOptimizeConfig
    )
    push_config: AssistBattlePushConfig = Field(default_factory=AssistBattlePushConfig)
    account_list: list[AssistBattleAccountInfo] = None

    @model_validator(mode='before')
    @classmethod
    def validator_account_list(cls, value: dict) -> Any:
        account_count = value.get('battle_config', {}).get('account_count', 1)
        account_list = value.setdefault('account_list', [])

        remove_keys = []
        for key, item_value in value.items():
            if key == 'account_list' or 'account_list' not in key:
                continue
            try:
                account = AssistBattleAccountInfo(**item_value)
                if account.is_valid():
                    account_list.append(account)
                remove_keys.append(key)
            except (TypeError, ValidationError):
                continue

        for key in remove_keys:
            del value[key]

        if len(account_list) < account_count:
            account_list.extend(
                AssistBattleAccountInfo()
                for _ in range(account_count - len(account_list))
            )
        return value

    # 账号列表展开成 account_list_1 / ... 键; context 要手动转发,
    # AccountInfo 上的 dynamic_hide 才能区分"给 GUI"(隐藏)和"落盘"(保留)
    @model_serializer()
    def serializer_model(self, info) -> Dict[str, Any]:
        data = {}

        for key, value in self.__dict__.items():
            if isinstance(value, list):
                for index, item in enumerate(value):
                    data[f'{key}_{index + 1}'] = item.model_dump(context=info.context)
            else:
                data[key] = (
                    value.model_dump(context=info.context)
                    if isinstance(value, BaseModel)
                    else value
                )

        return data
