# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from datetime import timedelta, datetime, time
from pydantic import BaseModel, ConfigDict, Field
from tasks.Component.GeneralInvite.config_invite import InviteConfig

from tasks.Component.config_scheduler import Scheduler
from tasks.Component.config_base import ConfigBase, Time, dynamic_hide


class ShopConfig(BaseModel):
    # GUI 的时间输入框给的是字符串（"20:00:00"），不开赋值校验的话会原样存成 str，
    # 而 Time 的序列化器要求 time 对象（v.strftime），一保存整个配置就崩。
    # 开了之后 pydantic 会用 time_validator 自动把字符串转成 time，格式不对则报错返回。
    model_config = ConfigDict(validate_assignment=True)

    time_of_mystery: Time = Field(
        default=Time(hour=0, minute=0, second=0),
        title='第一次运行时间',
        description='开放日（周三 / 周六）当天第一趟的时刻，格式 HH:MM:SS。\n'
                    '00:00:00 = 不指定时刻，交给调度器自己排（默认，和以前一样）。\n'
                    '只设置此时间，会在第一次运行时分享。',
    )
    # 同一天的第二趟。开放日（周三/周六）跑完第一趟后：本日第二趟还没到点就排到那个时刻；
    # 已经过点（第一趟跑得比它晚、或下午才手动运行）就改成过 5 分钟补跑，绝不跳过；
    # 跑完第二趟才排到下一个开放日。00:00:00 = 不启用，行为与原来完全一致。
    time_of_mystery_2: Time = Field(
        default=Time(hour=0, minute=0, second=0),
        title='第二次运行时间',
        description='开放日（周三/周六）当天再跑一趟的时刻，格式 HH:MM:SS，例如 20:00:00。\n'
                    '00:00:00 = 不启用（默认，和以前一样每天只跑一趟）。\n'
                    '第一趟跑完时若已经过了这个时刻（第一趟耗时较长，或你下午才手动运行），'
                    '不会跳过，而是改成过 5 分钟补跑第二趟。',
    )

    # ---------- 运行状态（不给用户配，GUI 里隐藏） ----------
    # 本开放日「第一趟」跑完的日期。_which_pass 靠它判本次是第几趟：
    #   == 今天  => 第二趟（只比价，不刷新、不分享）
    #   否则     => 第一趟
    # 以前是按钟点猜（当前时刻 / 计划时刻离 first 还是离 second 近），第一趟一旦被拖后
    # （排队、卡顿重启、手动点运行），就会被猜成第二趟 —— 那天等于整天不刷新、不分享。
    # 记日期不看钟点，拖到多晚都不会判错。写法与 Dokan 的 attack_date 一致。
    first_pass_date: str = Field(
        default='',
        title='第一趟已完成日期',
        description='脚本自动维护：记录本开放日第一趟跑完的日期（格式 YYYY-MM-DD）。\n'
                    '留空或不是今天 = 今天还没跑第一趟。正常使用不要手改。',
    )

    # GUI 不显示上面这行状态（同 Dokan 的 hide_fields 写法）
    hide_fields = dynamic_hide('first_pass_date')

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

    # ---------- 达摩推送 ----------
    # 与「买不买」解耦：识别到就推，不看价格达不达标、也不管勾没勾选购买。
    # 碎片几乎每个店都有，所以不提供「有碎片就推」这种粗粒度开关，
    # 只按片数拆成 2 片 / 3 片两项（1 片最常见，推出来就是噪声）；
    # 整只一周未必刷出一次，单独一项。
    black_daruma_scrap_push_2: bool = Field(
        default=False,
        title='识别到 2 片御行达摩碎片时推送',
        description='只推「2 片装」的黑碎，1 片、3 片不推。\n'
                    '与买不买无关：没勾「御行达摩碎片」购买、或单价高于上限，识别到照样推。\n'
                    '推送正文只有账号、道具名和价格，不带截图。',
    )
    black_daruma_scrap_push_3: bool = Field(
        default=False,
        title='识别到 3 片御行达摩碎片时推送',
        description='只推「3 片装」的黑碎，1 片、2 片不推。\n'
                    '与买不买无关：没勾「御行达摩碎片」购买、或单价高于上限，识别到照样推。\n'
                    '推送正文只有账号、道具名和价格，不带截图。',
    )
    black_daruma_push_enable: bool = Field(
        default=False,
        title='识别到整只御行达摩时推送',
        description='推整只「御行达摩」，碎片看上面那两项。\n'
                    '识别到就推，不看价格达不达标、也不管勾没勾选购买；'
                    '推送正文会写明这一趟会不会自动买、不买的原因，并附上当屏截图。\n'
                    '需要先在「设置 - 推送」里配好渠道（邮箱等）；没配好时只写一行日志，'
                    '不会影响正常购买。',
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
