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

    time_of_mystery: Time = Field(
        default=Time(hour=0, minute=0, second=0),
        title='第一次运行时间',
        description='开放日（周三 / 周六）当天第一趟的时刻，格式 HH:MM:SS。\n'
                    '00:00:00 = 不指定时刻，交给调度器自己排（默认，和以前一样）。',
    )
    # 同一天的第二趟。开放日（周三/周六）跑完第一趟后，若本日第二趟还没到点就排到它，
    # 到点才跑；跑完第二趟才排到下一个开放日。00:00:00 = 不启用，行为与原来完全一致。
    time_of_mystery_2: Time = Field(
        default=Time(hour=0, minute=0, second=0),
        title='第二次运行时间',
        description='开放日（周三/周六）当天再跑一趟的时刻，格式 HH:MM:SS，例如 20:00:00。\n'
                    '00:00:00 = 不启用（默认，和以前一样每天只跑一趟）。\n'
                    '必须晚于上面第一趟的时间，否则不会触发。',
    )

    # ---------- 金币道具 ----------
    # 等级不再做成单独一行输入框：目标等级固定为 5，规则直接写进标题。
    # GUI（oasx / Flutter）是按 schema 一个字段一行渲染的，想只占一行就不能带数值字段。
    #
    # 原来那个「跨店比价」开关已经删掉：两趟分工写进流程了 ——
    # 第一趟只买刷新前那批、刷新后那批只识别+分享，第二趟自动逐店比价挑最便宜；
    # 只配一趟时没有第二趟来接刷新后的货，当场买掉、也不逛好友店。
    # 留个开关只会让人以为「勾了才比价」，反而是坑。
    buy_coin_item: bool = Field(
        default=False,
        title='购买金币道具（识别到商店五级 / 弹出金币不足就停止购买金币道具）',
        description='只用金币标价的道具把神秘商人顶到 5 级。进店先读自己的等级，已经 5 级就整趟不买；\n'
                    '购买过程中弹出「金币不足」也立刻停手。',
    )

    # ---------- 勾玉道具 ----------
    # 下面每项都是「复选框一行 + 数值一行」：勾上开关，才有下面那行上限生效。
    mystery_amulet: bool = Field(
        default=False,
        title='神秘咒符',
        description='勾上后买蓝票，标价不高于下面那行才买。',
    )
    mystery_amulet_max_price: int = Field(
        default=85,
        title='└ 数值小于等于才买（勾玉）',
        description='默认 85（蓝票常见标价 85 勾玉），填几就按几卡上限：'
                    '货架标价和确认框里的"总价÷数量"两处都会比，取更严的那个。'
                    '设 0 = 不买。',
    )

    black_daruma_scrap: bool = Field(
        default=False,
        title='御行达摩碎片',
        description='勾上后按「单价」比价：勾玉价格 ÷ 片数 不高于下面那行才买。',
    )
    black_daruma_scrap_max_price: int = Field(
        default=60,
        title='└ 数值小于等于才买（勾玉/片）',
        description='实测常见单价：48（96 两片）、54、55、60、72（144 两片）。\n'
                    '60 = 买 48/54/55/60，不买 72；想更省设 54。设 0 = 不买黑碎。\n'
                    '价格读不到时跳过该片，不会当成 0 勾玉。',
    )

    black_daruma: bool = Field(
        default=False,
        title='御行达摩',
        description='勾上后按「整只标价」比价：标价不高于下面那行才买。',
    )
    black_daruma_max_price: int = Field(
        default=1600,
        title='└ 数值小于等于才买（勾玉）',
        description='整只御行达摩常见标价 1920，默认 1600 表示 1920 的整只不买。\n'
                    '设 0 = 不买。\n'
                    '黑蛋和黑碎是同一张达摩脸，靠价格区分：超过黑碎上限、但不超过本上限的按整只买。',
    )

    shop_kaiko_3: bool = Field(
        default=False,
        title='三星太鼓',
        description='勾上后按标价买：标价不高于下面那行才买。',
    )
    kaiko_3_max_price: int = Field(
        default=45,
        title='└ 数值小于等于才买（勾玉）',
        description='实测常见标价 40、45。设 0 = 不买三星太鼓。',
    )

    shop_kaiko_4: bool = Field(
        default=False,
        title='四星太鼓',
        description='勾上后按标价买：标价不高于下面那行才买。',
    )
    kaiko_4_max_price: int = Field(
        default=80,
        title='└ 数值小于等于才买（勾玉）',
        description='实测常见标价 72、80。设 0 = 不买四星太鼓。',
    )


class MysteryShop(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    shop_config: ShopConfig = Field(default_factory=ShopConfig)
    invite_config: InviteConfig = Field(default_factory=InviteConfig)
