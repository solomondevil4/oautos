from module.logger import logger
from tasks.WantedQuests.config import CooperationType, WantedQuestsConfig

# FindJade 用 CreatObjectFromModule 动态生成 WantedQuests 子类, 这里存下上游那个原始类,
# 供 get_cooperation_info 调用上游实现 —— 只补一列标记, 不复制上游的识别逻辑,
# 免得上游以后调类型判断时我们这份悄悄跑偏。
_WQ_BASE = None


def set_wq_base(base_cls):
    global _WQ_BASE
    _WQ_BASE = base_cls


def _invite_slot_index(self, btn) -> int:
    """把协作格子的邀请按钮对回第几格(1/2/3)

    不能直接用结果列表下标: 上游遇到「某格不是任何已知类型」时会跳过该格, 但格子号继续加,
    结果列表就短了一截, 下标和格子号会错位。这里按按钮横向位置就近匹配, 不依赖命名。
    """
    try:
        btn_x = float(btn.roi_front[0])
    except Exception:
        return 0
    best_index, best_dx = 0, None
    for index in (1, 2, 3):
        slot = getattr(self, "I_WQ_INVITE_%d" % index, None)
        if slot is None:
            continue
        dx = abs(float(slot.roi_front[0]) - btn_x)
        if best_dx is None or dx < best_dx:
            best_index, best_dx = index, dx
    return best_index


def _pop_present_world(self, ctype: CooperationType) -> bool:
    """从本帧的格子队列里取出该协作类型的「是不是现世勾协」

    上游 all_cooperation_invite 只把类型传给 get_invite_vip_name, 拿不到格子号,
    所以识别时先按顺序把标记排成队列, 这里按类型取走一个。
    """
    queue = getattr(self, '_coop_present_queue', None) or []
    for index, (item_type, present_world) in enumerate(queue):
        if int(item_type) == int(ctype):
            queue.pop(index)
            return present_world
    logger.warning("现世勾协标记缺失(类型=%s), 按普勾记账", ctype)
    return False


def need_invite_vip(self):
    return True


def get_cooperation_info(self):
    """上游识别结果 + 每格「是不是现世勾协」

    普勾和现世勾协的奖励图标都是勾玉, 上游只认 CooperationType.Jade, 完全没看现世标记
    素材 I_PRESENT_WORLD_JADE_ASSIST_N, 于是两者共用 invite_history_2 ——
    同一个好友接过普勾后, 13 小时内再发现世勾协会被误判成「刚接过」而跳过。
    """
    ret = _WQ_BASE.get_cooperation_info(self) if _WQ_BASE is not None else []
    # 每帧重来: 队列和「上一格是不是现勾」都清掉, 防止串到下一轮邀请
    self._coop_present_queue = []
    self._invite_present_world = False
    if not ret:
        return ret

    for item in ret:
        item['present_world'] = False
        if int(item['type']) & int(CooperationType.Jade):
            slot = _invite_slot_index(self, item['inviteBtn'])
            rule = getattr(self, "I_PRESENT_WORLD_JADE_ASSIST_%d" % slot, None) if slot else None
            if rule is None:
                logger.warning("第 %s 格: 拿不到现世勾协素材, 按普勾处理", slot or '未知')
            else:
                item['present_world'] = bool(self.appear(rule))
            logger.info(
                "协作格子 %s: 类型=%s, %s",
                slot or '未知', item['type'].name,
                '现世勾协' if item['present_world'] else '普通勾协',
            )
        self._coop_present_queue.append((item['type'], item['present_world']))
    return ret


def get_invite_vip_name(self, ctype: CooperationType):
    if not self.fade_conf:
        return ""
    present_world = _pop_present_world(self, ctype)
    self._invite_present_world = present_world
    name = self.fade_conf.get_invite_name(ctype, present_world)
    if name:
        logger.info(
            "%s → 邀请好友 [%s]",
            '现世勾协' if present_world else '普通勾协', name,
        )
    else:
        logger.warning(
            "%s → 名单里所有好友本时段都已接过, 名字为空", 
            '现世勾协' if present_world else '普通勾协',
        )
    return name


def next_run(self):
    pass


def invite_success_callback(self, ctype: CooperationType, name: str):
    if not self.fade_conf:
        return
    present_world = bool(getattr(self, '_invite_present_world', False))
    self.fade_conf.update_invite_history(ctype, name, present_world)
    logger.info(
        "记录邀请历史: [%s] 类型=%s, %s",
        name, ctype, '现世勾协' if present_world else '普通勾协',
    )
    self.config.save()


def get_config(self):
    # FindJade 复用 WantedQuests 的执行逻辑，保留用户在 WQ 中配置的
    # battle_priority 等任务参数，仅覆盖 FindJade 专用的协作设置。
    wq_config = self.config.wanted_quests.wanted_quests_config
    # 通过序列化重新构造，避免复制 cached_property 中可能过期的排序结果。
    config = WantedQuestsConfig.model_validate(wq_config.model_dump())
    config.invite_friend_name="default"
    config.cooperation_only=True
    config.cooperation_type=self.fade_conf.get_cooperation_type_mask()
    return config
