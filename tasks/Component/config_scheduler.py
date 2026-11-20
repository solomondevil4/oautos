# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from enum import Enum

from pydantic import Field, field_validator

from module.logger import logger
from tasks.Component.config_base import ConfigBase, TimeDelta, DateTime, Time


class WeeklyJumpWeekday(str, Enum):
    """运行后跳到的目标周几。

    值直接用中文, 界面上会渲染成下拉框, 选项文字本身就是中文, 不需要再查词典。
    枚举顺序即周一~周日, 与 datetime.weekday() 的 0~6 一一对应。
    """
    MONDAY = '周一'
    TUESDAY = '周二'
    WEDNESDAY = '周三'
    THURSDAY = '周四'
    FRIDAY = '周五'
    SATURDAY = '周六'
    SUNDAY = '周日'


# 周一~周日按顺序排列, 下标就是 datetime.weekday() 的返回值
_WEEKDAY_ORDER = list(WeeklyJumpWeekday)

# 1~7 数字 -> 周几(1=周一), 兼容旧配置和手改 json
_WEEKDAY_BY_NUMBER = {i + 1: day for i, day in enumerate(_WEEKDAY_ORDER)}

# 各种写法 -> 周几, 兼容手改成中文/英文/缩写
_WEEKDAY_ALIASES = {
    'monday': WeeklyJumpWeekday.MONDAY, 'mon': WeeklyJumpWeekday.MONDAY,
    'tuesday': WeeklyJumpWeekday.TUESDAY, 'tue': WeeklyJumpWeekday.TUESDAY,
    'wednesday': WeeklyJumpWeekday.WEDNESDAY, 'wed': WeeklyJumpWeekday.WEDNESDAY,
    'thursday': WeeklyJumpWeekday.THURSDAY, 'thu': WeeklyJumpWeekday.THURSDAY,
    'friday': WeeklyJumpWeekday.FRIDAY, 'fri': WeeklyJumpWeekday.FRIDAY,
    'saturday': WeeklyJumpWeekday.SATURDAY, 'sat': WeeklyJumpWeekday.SATURDAY,
    'sunday': WeeklyJumpWeekday.SUNDAY, 'sun': WeeklyJumpWeekday.SUNDAY,
}


class Scheduler(ConfigBase):
    enable: bool = Field(default=False, description='enable_help')
    next_run: DateTime = Field(default=DateTime.fromisoformat("2023-01-01 00:00:00"), description='next_run_help')
    priority: int = Field(default=5, description='priority_help')

    success_interval: TimeDelta = Field(default=TimeDelta(days=1), description='success_interval_help')
    failure_interval: TimeDelta = Field(default=TimeDelta(days=1), description='failure_interval_help')
    server_update: Time = Field(default=Time(hour=9, minute=0, second=0), description='server_update_help')
    # 第二个每日执行时刻: 与 server_update 组成一天两次(如 5:00/18:00); 09:00=不启用
    server_update_2: Time = Field(default=Time(hour=9, minute=0, second=0), description='server_update_2_help')
    delay_date: int = Field(default=1, description='delay_date_help', ge=1, le=31)
    float_time: Time = Field(default=Time(hour=0, minute=0, second=0), description='float_time_help')

    # ---------- 运行后跳到指定周几 ----------
    # 勾选后, 本次跑完的「下次运行」会被挪到下一个目标周几, 时刻(时分秒)保持原样。
    # 例: 目标是周二, 本周三 21:00 跑完 -> 下次 = 下周二 21:00。
    # 默认 False, 不勾选时行为与原来完全一致。
    # 只在调用方没有显式指定 target 时生效; 百鬼棋局跳下周一、道馆自算时间等
    # 自带 target 的路径会自动跳过, 不受影响。
    weekly_jump_enable: bool = Field(
        default=False,
        title='运行后跳到指定周几',
        description='勾选后，本次运行结束就把「下次运行」挪到下一个指定的星期，\n'
                    '具体的时分秒保持不变（例如原来 21:00 跑，跳完还是 21:00）。\n'
                    '不勾选时完全按原来的间隔排期，行为不变。',
    )
    weekly_jump_weekday: WeeklyJumpWeekday = Field(
        default=WeeklyJumpWeekday.TUESDAY,
        title='目标周几',
        description='下拉选择周一~周日，默认周二。\n'
                    '只在上面开关勾选后生效。',
    )

    @field_validator('weekly_jump_weekday', mode='before')
    @classmethod
    def _coerce_weekly_jump_weekday(cls, value):
        """把旧配置的 1~7 数字、手改的英文/中文写法统一转成枚举。

        不做这个兼容的话, 配置文件里留着旧值(如 2)会让整份配置加载失败。
        无法识别的值退回默认周二, 只影响这一个字段, 不影响配置读取。
        """
        if isinstance(value, WeeklyJumpWeekday):
            return value
        result = None
        # bool 是 int 的子类, 必须先挡掉, 否则 True 会被当成 1
        if isinstance(value, int) and not isinstance(value, bool):
            result = _WEEKDAY_BY_NUMBER.get(value)
        elif isinstance(value, str):
            text = value.strip()
            # 中文直接命中枚举值
            result = next((day for day in _WEEKDAY_ORDER if text == day.value), None)
            # 数字字符串 "1"~"7"
            if result is None and text.isdigit():
                result = _WEEKDAY_BY_NUMBER.get(int(text))
            # 英文写法 monday / MONDAY / mon
            if result is None:
                result = _WEEKDAY_ALIASES.get(text.lower())
        if result is None:
            logger.warning('周跳: 目标周几的值 %r 无法识别，已退回默认周二', value)
            return WeeklyJumpWeekday.TUESDAY
        return result

    @property
    def weekly_jump_weekday_index(self) -> int:
        """目标周几对应的 datetime.weekday() 值(0=周一, 6=周日); 非法值返回 -1。"""
        for index, day in enumerate(_WEEKDAY_ORDER):
            if day == self.weekly_jump_weekday:
                return index
        return -1


if __name__ == "__main__":
    dict_s = {
        "enable": False,
        "next_run": "2026-07-19T14:15:37",
        "priority": 5,
        "success_interval": "10 00:00:01",
        "failure_interval": "10 00:00:01",
        "server_update": "09:03:00",
        "float_time": "02:00:05"
    }
    s = Scheduler(**dict_s)
    print(s.model_dump())



