# This Python file uses the following encoding: utf-8
# @author Hurry2
from pydantic import BaseModel, Field

from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class AddFriendConfig(BaseModel):
    # 添加好友次数
    add_friend_count: int = Field(default=20, ge=1, description="add_friend_count_help")


class AddFriend(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    add_friend_config: AddFriendConfig = Field(default_factory=AddFriendConfig)

