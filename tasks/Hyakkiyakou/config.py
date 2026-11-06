from pydantic import BaseModel, Field, field_validator
from enum import Enum
from tasks.Component.SwitchOnmyoji.config import Onmyoji

from tasks.Component.config_scheduler import Scheduler
from tasks.Component.config_base import ConfigBase, Time, MultiLine, dynamic_hide


class ModelPrecision(str, Enum):
    FP32 = 'FP32'
    INT8 = 'INT8'

class InferenceEngine(str, Enum):
    ONNXRUNTIME = 'Onnxruntime'
    TENSORRT = 'TensorRT'

class ScreenshotMethod(str, Enum):
    WINDOW_BACKGROUND = 'window_background'
    NEMU_IPC = 'nemu_ipc'

class ControlMethod(str, Enum):
    MINITOUCH = 'minitouch'
    WINDOW_MESSAGE = 'window_message'


class HyakkiyakouConfig(ConfigBase):
    hya_limit_time: Time = Field(default=Time(minute=20), description='hya_limit_time_help')
    hya_limit_count: int = Field(default=10, description='hya_limit_count_help')
    hya_invite_friend: bool = Field(default=False, description='hya_invite_friend_help')
    hya_onmyoji: Onmyoji = Field(default=Onmyoji.KAGURA, description='切换阴阳师')
    # 自动调整豆子数量
    hya_auto_bean: bool = Field(default=False, description='hya_auto_bean_help')
    hya_bean_threshold_low: int = Field(default=10, description='hya_bean_threshold_low_help')
    hya_priorities: str = Field(default='', description='hya_priorities_help')
    hya_sp: float = Field(default=1., description='hya_sp_help')
    hya_ssr: float = Field(default=1., description='hya_ssr_help')
    hya_sr: float = Field(default=0.7, description='hya_sr_help')
    hya_r: float = Field(default=0.3, description='hya_r_help')
    hya_n: float = Field(default=0.0, description='hya_n_help')
    # 呱太和N卡不是一个东西
    hya_g: float = Field(default=0.0, description='hya_g_help')
    # BUFF omega 权重
    hya_buff_prob_up: float = Field(default=2.2)
    hya_buff_speed_up: float = Field(default=2.0)
    hya_buff_add_beans: float = Field(default=2.0)
    hya_buff_slow_down: float = Field(default=2.0)
    hya_buff_freeze: float = Field(default=2.0)

    # ---------------- 帮指定好友砸（肝帝专属） ----------------
    # 开启后跳过「自己开局」的整套流程，只按下面的名单逐个邀请好友同行。
    hya_help_enable: bool = Field(
        default=False,
        title='仅帮指定好友砸',
        description='开启后不再用自己的号刷百鬼夜行，只按下面两份名单逐个邀请好友同行：\n'
        '点开邀请好友弹窗 → 点名单里的好友 → 弹窗关闭即邀请成功 → 点「进」开局砸完 → 回来继续下一个。\n'
        '好友当天已被别人邀请满 3 次的，点了弹窗不会关闭，程序会记一条日志并跳过该好友。\n'
        '不受上面「执行次数上限」限制，改用「最多帮砸局数」兜底；'
        '进界面时若发现百鬼券为 0，会直接跳过本次任务并打日志。',
    )
    hya_help_friend_names: MultiLine = Field(
        default='',
        title='同区好友名单',
        description='每行一个好友名，空行自动忽略，最多 200 人（超出的会被忽略并记日志）。'
        '在弹窗的「好友」页签里按名字查找。',
    )
    hya_help_remote_names: MultiLine = Field(
        default='',
        title='跨区好友名单',
        description='每行一个好友名，空行自动忽略，最多 200 人（超出的会被忽略并记日志）。'
        '在弹窗的「跨区」页签里按名字查找。',
    )
    hya_help_max_round: int = Field(
        default=100,
        title='最多帮砸局数',
        description='防止死循环的兜底上限，不是业务数量限制。'
        '按「好友数 × 每人每天 3 次」估算即可，名单跑完会自动结束。',
    )

    @property
    def hya_help_friend_names_v(self) -> list[str]:
        """多行好友名 -> 列表（与 AssistBattle 点赞名单同款写法）"""
        return [
            line.strip()
            for line in self.hya_help_friend_names.split('\n')
            if line.strip()
        ]

    @property
    def hya_help_remote_names_v(self) -> list[str]:
        return [
            line.strip()
            for line in self.hya_help_remote_names.split('\n')
            if line.strip()
        ]


class HyakkiyakouModels(ConfigBase):
    # 置信度阈值
    conf_threshold: float = Field(default=0.6, description='conf_threshold_help')
    # nms阈值
    iou_threshold: float = Field(default=0.7, description='iou_threshold_help')
    # 模型的推理精度 目前全部是fp32
    model_precision: ModelPrecision = Field(default=ModelPrecision.FP32, description='model_precision_help')
    # 推理引擎 目前全部是onnxruntime
    inference_engine: InferenceEngine = Field(default=InferenceEngine.ONNXRUNTIME, description='inference_engine_help')


class DebugConfig(ConfigBase):
    # 运行期间现实每一张的跟踪结果
    hya_show: bool = Field(default=False, description='hya_show_help')
    # 输出更多调试信息， 给使用者显示的
    hya_info: bool = Field(default=False, description='hya_info_help')
    # 保存图片，拿去回喂给模型
    continuous_learning: bool = Field(default=False, description='continuous_learning_help')
    # 保存每一张票的结果
    hya_save_result: bool = Field(default=False, description='hya_save_result_help')
    # 单独的设定截屏间隔, 单位ms
    hya_interval: float = Field(default=300, description='hya_interval_help')
    # 单独的截屏设置
    hya_screenshot_method: ScreenshotMethod = Field(default=ScreenshotMethod.WINDOW_BACKGROUND,
                                                    description='hya_screenshot')
    # 单独的点击
    hya_control_method: ControlMethod = Field(default=ControlMethod.WINDOW_MESSAGE,
                                              description='hya_control_method')

    hide_fields = dynamic_hide('continuous_learning')


    @field_validator('continuous_learning', mode='after')
    @classmethod
    def false_continuous_learning(cls, v):
        if v:
            return False
        return False


class Hyakkiyakou(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    hyakkiyakou_config: HyakkiyakouConfig = Field(default_factory=HyakkiyakouConfig)
    hyakkiyakou_models: HyakkiyakouModels = Field(default_factory=HyakkiyakouModels)
    debug_config: DebugConfig = Field(default_factory=DebugConfig)

