# This Python file uses the following encoding: utf-8
# @author @@AUTHOR@@
from pydantic import BaseModel, Field

from tasks.Component.config_scheduler import Scheduler
from tasks.Component.config_base import ConfigBase


class @@TASK_NAME@@Config(BaseModel):
    """本玩法的开关与参数。每个 Field 的 description 就是 GUI 上显示的提示文案。"""

    enable: bool = Field(default=True, description='@@TASK_NAME@@_enable_help')


class @@TASK_NAME@@(ConfigBase):
    """
    任务配置根类。类名必须与 tasks/<目录名> 完全一致，字段名必须是目录名的蛇形。
    config.py 用 convert_to_underscore(任务名) + getattr 取这个对象，
    类名或字段名写错不会报错，只会表现为"排期不更新、配置读不到"。
    """

    scheduler: Scheduler = Field(default_factory=Scheduler)
    @@SNAKE_NAME@@_config: @@TASK_NAME@@Config = Field(
        default_factory=@@TASK_NAME@@Config, description='@@TASK_NAME@@_config_help'
    )
