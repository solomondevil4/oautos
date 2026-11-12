from time import sleep

from module.logger import logger
from tasks.WantedQuests.config import CooperationSelectMask, CooperationType, WantedQuestsConfig

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
    # 只给「普通勾协」记额度: 现世勾协同为 Jade 类型(靠现世标记区分), 粮协是 Food 类型,
    # 两者都不计数 —— 用户要的是「大号收到的普勾」满 N 个就收手。
    if int(ctype) & int(CooperationType.Jade) and not present_world:
        remain = self.fade_conf.note_normal_jade_invited()
        conf = self.fade_conf.find_jade_config
        if remain < 0:
            logger.info("本时段普通勾协已邀请 %s 个（未设上限）", conf.normal_jade_invited)
        else:
            logger.info(
                "本时段普通勾协已邀请 %s/%s 个，还剩 %s 个额度",
                conf.normal_jade_invited,
                self.fade_conf.normal_jade_limit_effective(),
                remain,
            )
    self.config.save()


def normal_jade_full(self) -> bool:
    """本时段普勾额度是否已用完; 配置没接上(独立跑 WQ)时一律当没上限"""
    conf = getattr(self, 'fade_conf', None)
    if not conf:
        return False
    return conf.normal_jade_remain() == 0


def all_cooperation_invite(self, name_all: str = None):
    """上游 WantedQuests.all_cooperation_invite 的副本, 只多一句额度检查

    上游这个函数没有「邀请到第 N 个就收手」的钩子, 循环里插不进回调, 只能整段照搬
    tasks/WantedQuests/script_task.py:572-632, 在每格邀请前加一次检查。
    **上游改这个函数时必须同步这里**, 差异只有标了 QUOTA 的那几行。
    """
    self.screenshot()
    if not self.appear(self.I_WQ_INVITE_1):
        return False

    ret = self.get_cooperation_info()
    if len(ret) == 0:
        logger.info("no Cooperation found")
        return False
    typeMask = CooperationSelectMask[(self.get_config()).cooperation_type.value]
    for item in ret:
        # 该任务是需要邀请的任务类型
        if not (item['type'] & typeMask):
            # BUG 存在多个协作任务时,邀请完第一个协作任务对方接受后,未邀请的任务位置无法确定(缺少信息)
            # 例如 按顺序存在 abc 3个协作任务,邀请完a,好友接受后,这三个任务在界面上的顺序变化,abc 还是bca
            # 如果顺序不变 则应该没有问题
            logger.info(
                "cooperationType %s But needed Type %s ,Skipped",
                item['type'],
                typeMask,
            )
            break
        # QUOTA: 额度已满就连同后面的格子一起停, 不再一个个白等 5×5 秒
        if normal_jade_full(self):
            logger.info("普通勾协额度已满，停止邀请剩余协作格子")
            break
        '''
           尝试5次 如果邀请失败 等待5s 重新尝试
           阴阳师BUG: 好友明明在线 但邀请界面找不到该好友(好友未接受任何协作任务的情况下)
       '''
        index = 0
        item['inviteResult'] = False
        if name_all is None:
            name = self.get_invite_vip_name(item['type'])
        else:
            name = name_all
        logger.warning(
            "find cooperationType %s ,start invite %s", item['type'], name
        )
        while index < 5:
            if self.cooperation_invite(item['inviteBtn'], name):
                item['inviteResult'] = True
                index = 5
                continue
            logger.info(
                "%s not found,Wait 5s,%d invitations left", name, 5 - index - 1
            )
            index += 1
            sleep(5) if index < 5 else sleep(0)
            # NOTE 等待过程如果出现协作邀请 将会卡住 为了防止卡住
            self.screenshot()
        # 邀请追踪一起吧,只有邀请成功才追踪
        if item['inviteResult']:
            self.invite_success_callback(item['type'], name)
            if (self.get_config()).cooperation_only:
                logger.info("start trace_one")
                self.trace_one(item['inviteBtn'])
    return ret


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
