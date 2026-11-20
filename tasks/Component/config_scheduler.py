# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from pydantic import Field

from tasks.Component.config_base import ConfigBase, TimeDelta, DateTime, Time

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
    weekly_jump_weekday: int = Field(
        default=2,
        title='目标周几（1=周一，7=周日）',
        description='1=周一, 2=周二, 3=周三, 4=周四, 5=周五, 6=周六, 7=周日。\n'
                    '只在上面开关勾选后生效。',
    )


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



