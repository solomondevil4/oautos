from typing import Any, Dict

from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    model_serializer,
    model_validator,
)

from tasks.Component.config_scheduler import Scheduler
from tasks.Component.config_base import ConfigBase
from tasks.Component.SwitchAccount.switch_account_config import AccountInfo
from tasks.GuildBanquet.config import Weekday
from tasks.RichMan.config import ConsignShikigami
from tasks.RichMan.mall.shikigami_map import SHIKIGAMI_NAMES


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


class AssistBattleConfig(BaseModel):
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
    email_enable: bool = Field(
        default=True,
        description='仅周日运行',
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
        description='仅周日运行',
    )
    # 购买指定联动碎片（复用大富翁-购买指定式神碎片的流程）
    consignment_shikigami_enable: bool = Field(
        default=False,
        description='与「启用寄售屋购买」共用一次进寄售屋，下面可选星期和式神',
    )
    consignment_shikigami_day: Weekday = Field(
        default=Weekday.Sunday,
        description='购买指定联动碎片在周几运行',
    )
    consignment_shikigami_name: ConsignShikigami = Field(
        default=ConsignShikigami(SHIKIGAMI_NAMES[0]),
        description='要购买的联动式神碎片',
    )
    retry_failed_account_enable: bool = Field(
        default=True,
        description='切号失败的账号会最后再尝试一次',
    )
    skip_completed_account_enable: bool = Field(
        default=True,
        description='协战已完成的账号当天不再重复登录',
    )
    daily_time_window_enable: bool = Field(
        default=True,
        description='邮件领取/寄售屋/庭院琐事/商店签到仅在 18:00-23:00 执行',
    )


class AssistBattle(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    assist_battle_config: AssistBattleConfig = Field(default_factory=AssistBattleConfig)
    push_config: AssistBattlePushConfig = Field(default_factory=AssistBattlePushConfig)
    account_list: list[AccountInfo] = None

    @model_validator(mode='before')
    @classmethod
    def validator_account_list(cls, value: dict) -> Any:
        account_count = value.get('assist_battle_config', {}).get('account_count', 1)
        account_list = value.setdefault('account_list', [])

        remove_keys = []
        for key, item_value in value.items():
            if key == 'account_list' or 'account_list' not in key:
                continue
            try:
                account = AccountInfo(**item_value)
                if account.is_valid():
                    account_list.append(account)
                remove_keys.append(key)
            except (TypeError, ValidationError):
                continue

        for key in remove_keys:
            del value[key]

        if len(account_list) < account_count:
            account_list.extend(
                AccountInfo() for _ in range(account_count - len(account_list))
            )
        return value

    # 账号列表展开成 account_list_1 / account_list_2 / ... 这样的键
    # last_complete_time 会被写入, 用于记录当天协战已完成的账号, 不再覆盖成 0xABCDEF
    @model_serializer()
    def serializer_model(self) -> Dict[str, Any]:
        data = {}

        for key, value in self.__dict__.items():
            if isinstance(value, list):
                for index, item in enumerate(value):
                    data[f'{key}_{index + 1}'] = item.model_dump()
            else:
                data[key] = (
                    value.model_dump() if isinstance(value, BaseModel) else value
                )

        return data
