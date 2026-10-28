# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from datetime import timedelta, datetime, time
from pydantic import BaseModel, ConfigDict, Field
from tasks.Component.GeneralInvite.config_invite import InviteConfig

from tasks.Component.config_scheduler import Scheduler
from tasks.Component.config_base import ConfigBase, Time


class ShopConfig(BaseModel):
    # GUI 的时间输入框给的是字符串（"20:00:00"），不开赋值校验的话会原样存成 str，
    # 而 Time 的序列化器要求 time 对象（v.strftime），一保存整个配置就崩。
    # 开了之后 pydantic 会用 time_validator 自动把字符串转成 time，格式不对则报错返回。
    model_config = ConfigDict(validate_assignment=True)

    time_of_mystery: Time = Field(default=Time(hour=0, minute=0, second=0), description='time_of_mystery_help')
    # 同一天的第二趟。开放日（周三/周六）跑完第一趟后，若本日第二趟还没到点就排到它，
    # 到点才跑；跑完第二趟才排到下一个开放日。00:00:00 = 不启用，行为与原来完全一致。
    time_of_mystery_2: Time = Field(
        default=Time(hour=0, minute=0, second=0),
        title='第二次运行时间',
        description='开放日（周三/周六）当天再跑一趟的时刻，格式 HH:MM:SS，例如 20:00:00。\n'
                    '00:00:00 = 不启用（默认，和以前一样每天只跑一趟）。\n'
                    '必须晚于上面第一趟的时间，否则不会触发。',
    )
    mystery_amulet: bool = Field(default=False)
    black_daruma_scrap: bool = Field(default=False)
    shop_kaiko_3: bool = Field(default=False)
    shop_kaiko_4: bool = Field(default=False)
    buy_coin_item: bool = Field(default=False, description='购买金币道具（用于提升神秘商人等级，需开启本项）')
    coin_max_level: int = Field(default=5, description='神秘商人等级达到该值时停止购买金币道具')


class MysteryShop(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    shop_config: ShopConfig = Field(default_factory=ShopConfig)
    invite_config: InviteConfig = Field(default_factory=InviteConfig)

