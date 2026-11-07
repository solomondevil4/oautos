# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from enum import Enum
from datetime import datetime, time, timedelta
from pydantic import BaseModel, ValidationError, validator, Field

from tasks.Component.config_base import Time, MultiLine


class FindMode(str, Enum):
    AUTO_FIND = 'auto_find'
    RECENT_FRIEND = 'recent_friend'


class InviteConfig(BaseModel):
    friend_list: MultiLine = Field(default='', description='invite_friend_list_help')
    find_mode: FindMode = Field(default=FindMode.AUTO_FIND, description='find_mode_help')
    wait_time: Time = Field(default=Time(minute=2), description='wait_time_help')
    default_invite: bool = Field(default=True, description='default_invite_help')
    # 默认关闭: 只有显式打开的任务(如探索)才会下滑搜索, 其他任务行为不变
    scroll_find_enable: bool = Field(default=False, title='下滑搜索好友', description='scroll_find_enable_help')
    notify_missing_enable: bool = Field(default=False, title='找不到好友时通知', description='notify_missing_enable_help')

    @property
    def friend_list_v(self) -> list[str]:
        return [line.strip() for line in self.friend_list.split('\n') if line.strip()]

    @property
    def wait_time_v(self) -> timedelta:
        if isinstance(self.wait_time, time):
            return timedelta(hours=self.wait_time.hour, minutes=self.wait_time.minute, seconds=self.wait_time.second)
        return self.wait_time


if __name__ == "__main__":
    i = InviteConfig()
    print(isinstance(i.wait_time, time))
    i.wait_time = "00:05:00"
    print(i.wait_time)
    print(isinstance(i.wait_time, time))

