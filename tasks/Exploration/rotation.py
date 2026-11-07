# This Python file uses the following encoding: utf-8
"""小号轮换（1 对多）的配置读取。

拓扑：一个队长配置依次带多个小号，只有小号位会切号。

- 队长（user_status=LEADER）：跑完自己设的场数/时间后，把
  exploration.rotation 的 current_index / current_member 推进到下一个小号，
  并改写 invite_config.friend_list，让下一轮开局时邀请新人。
- 小号（user_status=MEMBER）：用 leader_config 指认队长配置，轮询队长配置里的
  current_member，和自己上次接下的 assigned_member 比对，变了就切号。

状态只有队长写、小号只读，单写者，不需要文件锁。
"""
import json
import os

from module.config.utils import filepath_config
from module.logger import logger


def parse_member_list(text) -> list[str]:
    """把多行名单拆成角色名列表（去空行、去首尾空格）"""
    if not text:
        return []
    return [line.strip() for line in str(text).split('\n') if line.strip()]


def normalize_index(index, total: int) -> int:
    """指针越界归零，非法值也归零"""
    try:
        index = int(index)
    except (TypeError, ValueError):
        return 0
    if index < 0 or index >= total:
        return 0
    return index


def read_config_json(config_name: str, retry: int = 3) -> dict | None:
    """读配置原始 json。

    不构造 Config 对象，省掉整份 pydantic 校验；队长刚好在写文件时可能读到半截，
    所以失败重试几次。读不到返回 None，调用方按“没有新指令”处理。
    """
    config_name = (config_name or '').strip()
    if not config_name:
        return None
    path = filepath_config(config_name)
    if not os.path.exists(path):
        logger.warning(f'Rotation: 找不到配置 {path}')
        return None
    for _ in range(max(1, retry)):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
    logger.warning(f'Rotation: 读取配置 {config_name} 失败（可能正好被写）')
    return None


def read_leader_state(config_name: str) -> dict:
    """读队长配置里的轮换状态。

    :return: {'member': 当前该上场的小号, 'index': 指针, 'members': 名单}
             读不到时返回空 dict
    """
    data = read_config_json(config_name)
    if not data:
        return {}
    rotation = ((data.get('exploration') or {}).get('rotation')) or {}
    members = parse_member_list(rotation.get('member_list'))
    member = (rotation.get('current_member') or '').strip()
    index = rotation.get('current_index', 0)
    if not member and members:
        # 队长还没来得及写 member（刚开轮换），按指针兜底推一个
        index = normalize_index(index, len(members))
        member = members[index]
    return {'member': member, 'index': index, 'members': members}
