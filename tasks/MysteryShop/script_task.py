# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import re
from time import sleep
from datetime import timedelta, datetime, time
from cached_property import cached_property

import numpy as np
import cv2

from module.exception import TaskEnd
from module.logger import logger
from module.base.timer import Timer
from module.atom.click import RuleClick

from tasks.GameUi.page import page_main, page_mall
from tasks.GameUi.game_ui import GameUi
from tasks.RichMan.mall.friendship_points import FriendshipPoints
from tasks.MysteryShop.config import MysteryShop, ShopConfig
from tasks.MysteryShop.assets import MysteryShopAssets
from tasks.Component.GeneralInvite.general_invite import GeneralInvite
from tasks.Component.GeneralInvite.config_invite import InviteConfig

class ScriptTask(FriendshipPoints, MysteryShopAssets, GeneralInvite):

    # 神秘商店只在周三(2)、周六(5)开放，游戏机制如此，任务也只在这两天跑。
    OPEN_WEEKDAYS = (2, 5)
    # 判断「滑到底了」的画面平均像素差阈值。
    # 实测（16416）：有效滑动 21.6，到底后再滑 0.3~0.6，差 40 倍，5.0 两边都安全。
    SCROLL_MIN_DIFF = 5.0

    # 判断「商品列表是否已经在最顶部」的采样带：标题栏正下方 y=54~80 这一窄条。
    # 标题栏是固定遮罩，商品一旦滚动，每格卡片上方的「剩余购买次数」标签就会被它
    # 裁掉上半截，只剩一点笔画露在这条窄带里。
    # 实测 9 张截图（16416 自己商店、16512 自己商店、好友商店、刷新前后）：
    #   已在顶部：亮像素占比 0.24%（约 54 个像素，纯噪声）
    #   滚动过  ：亮像素占比 5.07% ~ 5.36%（1134 ~ 1190 个像素）
    # 差 22 倍；且这条窄带只受「是否滚动」影响，与商品内容、谁分享的商店都无关
    # （两个账号的顶部读数完全相同），取 2% 两边余量都足够。
    TOP_PROBE_BOX = (140, 54, 860, 26)
    TOP_PROBE_LEVEL = 80
    TOP_PROBE_MAX = 0.02
    # 回顶部最多上滑几次（有界）。实测一次上滑就够（整屏位移约 253 像素）。
    SCROLL_TOP_MAX = 2
    # 往下最多滑几次（有界）。整店最多 3 行 11 件，第一屏已显示 2 行，
    # 而 ms_scroll_down 的 y 轴跨度是 390~470 像素（约 2 行，且小于一屏可视高度 604，
    # 滑动前后窗口必有重叠、不会漏行），所以再滑 2 次一定到底。
    SCROLL_DOWN_MAX = 2

    # ---------- 「剩余购买次数」标签行 ----------
    # 商品一滚动，格子坐标就整体位移（顶部两行 y=79/340，滚一次 -27，滚到底 -212），
    # 所以 8 个固定 OCR 区域不可用，改成用 ms_leftcount 模板全屏定位这行字。
    # 跨 11 张截图实测最低分 0.924，阈值 0.8 留足余量；售罄格文字变暗到约 40%
    # 亮度仍能匹配到 0.994（归一化相关不敏感于整体明暗）。
    LEFTCOUNT_MIN_SCORE = 0.8
    # 标签行与商品的横向关联容差：列宽 225，取半列宽略大一点，
    # 让 117 宽的蓝票模板和 40 宽的黑碎模板都能稳定归到正确的那一列。
    LEFTCOUNT_COL_TOL = 115

    # ---------- 「同种商品只买一次」 ----------
    # 历史展示名 -> 比价池里的种类名。buy_daruma_screen 现在直接传全名
    # （御行达摩碎片/御行达摩），这张表留着兜住旧入口（_buy_heisui_at 的默认参数、
    # 外部直接调 _mark_bought），免得两种写法在比价池里被当成两个不同的种类。
    KIND_ALIAS = {'黑碎': '御行达摩碎片', '黑蛋': '御行达摩'}
    # 本趟运行里已经买走的商品，(种类, 片数)。
    # 刷新前那趟是当场买的，买走一件之后全店（含刷新后、所有好友店）的同款都会变黑，
    # 所以刷新前买过的东西不再进比价池 —— 否则回程会对着一格卖空的货反复点。
    # 1 片 / 2 片 / 3 片是三个独立格子、各有各的库存，所以键里必须带片数。
    # 这里先给个类级默认值：万一某个入口没走 run()，读它也不会 AttributeError。
    bought_kinds = frozenset()

    # 本趟运行是否已经撞到「勾玉不足」。一旦弹出过那个弹窗就置位，之后不再买任何
    # 勾玉道具（神秘咒符 / 御行达摩碎片 / 御行达摩 / 太鼓），金币道具不受影响。
    # 类级默认值同样是给「没走 run() 的入口」兜底，读它不会 AttributeError。
    jade_short = False

    # ---------- 「剩余购买次数」的跨屏记忆 ----------
    # 商品列表下滑之后，画面最顶那一行的「剩余购买次数」会被标题栏遮住上半截
    # （标签本来在 y=79，滑到底后落到 y≈52），只剩半截笔画 —— 实测完整模板的
    # 匹配分数从 0.99 掉到 0.5 以下，既定位不到也读不出数字。
    # 但**滑动只是视图位移，库存数字不会变**：那一行在顶部那一屏一定被完整读过。
    # 所以把每一格读到的次数按 (列, 种类) 记住，滑下去之后遇到「读不到标签」的格子
    # 就去查这张表，把上一屏的同格读数拿来用。
    #
    # 表的寿命是**一家店**：换店（含 5 勾玉刷新）时清空（_reset_times_memo）。
    # 值 None 表示「这一格这一屏买过，现在还剩几次不知道了」—— 点过「最大」按钮
    # 可能一次买光也可能没买光，所以保守按不可买处理，等下一屏标签露出来再读真值。
    times_memo = None
    # 「剩余购买次数」标签的左边界四档，标签宽 140 => 四列中心线 x = 235/460/686/911，
    # 间隔 225。用它把商品命中框归入某一列，好和下方的次数标签对上。
    LEFTCOUNT_COL_X0, LEFTCOUNT_COL_STEP = 235, 225

    # ---------- 一天两趟的分工 ----------
    # 配置里有两个时刻（time_of_mystery / time_of_mystery_2），两趟干的活完全不同：
    #   PASS_SINGLE  只配了一趟（第二次留 00:00:00）：自己的店刷新前后都买、都分享，
    #                不逛好友店 —— 没有第二趟来接刷新后那批货，只能当场消化。
    #   PASS_FIRST   两趟里的第一趟：买「刷新前」那批（刷新后它就永久消失），
    #                刷新后那批只识别 + 分享、不买，留给第二趟比价挑便宜的；
    #                全程不点右箭头（好友店留给第二趟统一比价）。
    #   PASS_SECOND  第二趟：不分享，把每家店都只记录一遍，挑出最便宜的那家回程买。
    PASS_SINGLE, PASS_FIRST, PASS_SECOND = 0, 1, 2
    # 用「本次计划运行时刻」判趟次时允许的偏差（秒）。调度器把第一趟排在 first、
    # 第二趟排在 second，所以计划时刻和哪一头对得上就是那一趟；但手动点运行时
    # next_run 可能还挂着一个老时刻，偏差超过这个值就不信它、改用当前时间兜底。
    PASS_PLAN_TOLERANCE = 7200

    # 分享次数上限：一天最多两次（刷新前一次、刷新后一次），每次对应一个店面。
    # 刷新前后在游戏里是两个独立商店，好友点开看到的是各自那批货，所以各分享一次。
    SHARE_MAX = 2
    # 本趟已经分享了几次、当前这个店面有没有「价格达标」的勾玉道具。
    # share_worthy 里放种类名，只影响「要不要分享」，与买不买无关 ——
    # 用户要求即使没勾选购买，只要价格低于配置上限也要分享给好友。
    share_done = 0
    share_worthy = frozenset()

    # 点刷新后等确认框的时间。实测确认框 0.5~1 秒就出来；等不到就说明本日已刷新过。
    REFRESH_CONFIRM_TIMEOUT = 5

    # ---------- 黑碎（御行达摩碎片）比价 ----------
    # 【锚点约定】所有商品素材统一以「模板命中框的左下角」为原点：
    #     ax, ay = match[1], match[2] + match[4]      # 左上角 + 模板高度
    # 不再是左上角。换成左下角是为了让黑碎(40×40)和太鼓(79×50)这类高矮不一的模板
    # 共用同一套写法 —— 原点统一在「卡片图标底边」，价格牌就在它正下方，
    # 偏移是正的、直观；用左上角时每个模板都要先记住自己多高才能推价格位置。
    #
    # 价格 / 数量 ROI 相对这个左下角原点的偏移。尺寸直接用资产里的
    # O_MS_PRICE(44×30) / O_MS_QTY(36×34)，这里只写偏移，改尺寸不用动两处。
    # 默认锚点 (200,478) 时：价格 256,523；数量 268,446。
    # 与旧左上角基准的换算（模板高 40）：价格 85 = 40+45；数量 8 = 40-32。
    #
    # 偏移是实测标定的，别凭截图目测改：价格牌左侧勾玉图标的右边缘在锚点 +51 附近，
    # 牌尾花纹在 +117 起，ROI 只要压到任一边，OCR 就会多读出一位数字
    # （实测 54 → 154、96 → 16、96 → 963）。当前 +56 起、宽 44 刚好卡在空白里，
    # 锚点在 ±2 像素内漂移实测 77/77 读数不变。
    PRICE_DX, PRICE_DY = 56, 45
    QTY_DX, QTY_DY = 68, -32
    # 模板高度：match 元组里本来就带着（第 5 项），这里留常量是为了不依赖画面内容
    # 也能把左下角换算回左上角（点图标要用左上角那套坐标）。
    HEISUI_H = 40
    # 点开这一格时点的框，相对模板左上角的偏移与尺寸
    HEISUI_CLICK = (8, 8, 24, 24)
    # 同一片黑碎会被 match_all 返回好几个高度重叠的命中，两命中相距小于该值视为同一片。
    HEISUI_MERGE_DIST = 25
    # 读数置信度下限，价格和数量共用。
    # 实测 7 个样本：无角标时达摩脸被读成 "3"，分数 0.127 / 0.250 / 0.311；
    # 真角标（含整体压暗到最大通道值仅 101 的那张）读 "2"，分数 0.980~0.989。
    # 中间是空的，0.6 两边都安全。价格 0.86~0.99，同样够用。
    OCR_MIN_SCORE = 0.6
    # 单片数量上限，超过说明读错了，一律按 1 片算
    QTY_MAX = 9
    # 价格上限，超过说明读到的是金币标价（五位数），按读不到处理
    PRICE_MAX = 999
    # 整只御行达摩的价格牌是四位数，O_MS_PRICE 的 44 宽装不下（见 _daruma_full_price）
    PRICE_WIDE_DX = 44
    PRICE_WIDE_W = 64
    # 整只御行达摩的**读取**上限，和配置里的购买上限是两件事：
    # 实测标价 1920，而 GUI 默认上限是 1600。如果拿购买上限当读取上限，1920 会被
    # _price_in_box 判成「读不到或超上限」而直接跳过 —— 日志里看不到这只整只，
    # 用户会以为识别漏了，比价池里也没有它。所以读取放到 3000（远高于任何真实标价，
    # 又能挡住五位数的金币标价），买不买仍由各自的 cap 判定。
    DARUMA_PRICE_READ_MAX = 3000
    # 同一片黑碎最多连买几次（受「剩余购买次数」限制，买不动时第 1 次就会失败退出）
    HEISUI_BUY_MAX = 3
    # 点完图标等购买弹窗、点完购买等获得奖励、等奖励动画结束，三段各自的时间上限（秒）
    HEISUI_POPUP_TIMEOUT = 10
    HEISUI_BUY_TIMEOUT = 12
    HEISUI_REWARD_TIMEOUT = 8
    # 「购买」是固定坐标盲点（C_BUY_ONE，弹窗里不做模板匹配），所以光靠 Timer 不够：
    # 这里再给一个总次数上限，两条线取先到的那条。上限值不是随便定的 ——
    # module/device/device.py:265 规定同一按钮 10 次、或两个按钮各 6 次就连记录一起抛
    # GameTooManyClickError，把整条任务炸掉。累计 6 次（购买 + 确认框合计）在两条规则下
    # 都留足余量。
    HEISUI_BUY_TRIES = 6
    # 出奖励之后最多再补点几次。出奖励那一下（第二段的 ui_reward_appear_click）已经点过了，
    # 正常情况下面板自己就散了，这里留 2 次兜底；点满就只等不点。
    # 不给更多是因为 C_UI_REWARD 也是固定坐标，每 0.4 秒一记连点 8 秒 = 20 记，
    # 同样会撞 device.py:265 的 10 次红线（上游 task/Component/Buy/buy.py:69-76 就无限点）。
    HEISUI_REWARD_CLICKS = 2

    # 神秘商人等级上限。不再做成 GUI 配置项：这一项的唯一目的就是把商人顶到 5 级
    # （5 级才刷得出高价值道具），用户要求固定 5，省掉一行输入框。
    COIN_MAX_LEVEL = 5

    # ---------- 太鼓（结界卡）比价 ----------
    # 2026-10-31 换素材：原来用整卡（三星 140×104 / 四星 90×88），卡片边框和「特惠」角标
    # 会随格子变，同一件道具换了格子就对不上。改成只截 66×57 的**图标本体**，
    # 和蓝票（I_MS_BLUE_ICON 62×59）同一个思路。
    #
    # 价格 ROI 相对「图标命中框左下角」的偏移。为什么是左下角：图标在不同格子里
    # y 能差 27px（实测三星出现在 395 / 422 两处），卡片高度也不恒定，只有
    # 「图标底边 → 下面那条价格牌」这段距离是稳定的，和蓝票 / 黑碎同一套锚法。
    #
    # 标定方法：以底边为原点扫 dx 38~74 / dy 24~54（步长 2），统计哪些偏移能把
    # 价格读对。三星 3 个不同 y、3 张图都读对的是 dx 38~62 / dy 26~52（147 个偏移点），
    # 四星读对的是 dx 38~64 / dy 30~52（119 个）。取两者交集的中间值 (52,40)：
    #   三星 0.998~0.999、四星 0.957 —— 不是最优单点，但四周各留 10px 余量。
    # 尺寸沿用 O_MS_PRICE 的 44×30。太鼓是单价商品，没有数量调节，只读价格。
    TAIKO_3_PRICE_DX, TAIKO_3_PRICE_DY = 52, 40
    TAIKO_4_PRICE_DX, TAIKO_4_PRICE_DY = 52, 40
    # 点开这一格的框，相对模板左上角。66×57 图标居中取一块 40×40，
    # 中心落在 (33,28)，即图标正中；避开右下角的 999 角标。
    TAIKO_3_CLICK = (13, 8, 40, 40)
    TAIKO_4_CLICK = (13, 8, 40, 40)
    # 两个模板外观很接近（都是叠起来的饼状物，只有底色/光效不同），必须用比
    # 资产阈值更严的门槛，否则四星会被当三星买。实测（显式传 threshold 时稳定）：
    #   三星：5 张正样本 0.9805~0.9830 / 四星格 0.7294 以下 → 0.90（余量 0.08 / 0.17）
    #   四星：正样本 0.9782 / 三星格误命中 0.7294~0.7309 → 0.85（余量 0.13 / 0.12）
    TAIKO_3_MIN_SCORE = 0.90
    TAIKO_4_MIN_SCORE = 0.85
    # ---------- 神秘咒符（蓝票）----------
    # 用 I_MS_BLUE_ICON（62×59，只截商品格里的图标本体）定位货架，不再用 ms_blue 那张
    # 117×98 的整卡：整卡带紫边框和「特惠」角标，没特惠的蓝票卡对不上，而且满屏假命中
    # （18 张截图 match_all 一次出 11~16 个 ≥0.7 的点）。换图标后有蓝票的 19 张最低
    # 0.8506（被「勾玉不足」弹窗遮住半张）、没蓝票的 50 张最高 0.5590，0.8 两头都够。
    BLUE_MIN_SCORE = 0.8
    # 蓝票卡面价格的读法：**以图标命中框的左下角为原点**（不是左上角），
    # 价格 = 原点 + (52,44)，尺寸沿用 O_MS_PRICE 的 44×30。
    # 为什么必须用左下角：蓝票图标在不同格子里 y 能差 260px（实测 155 ~ 418），
    # 卡片高度又随布局变，钉死绝对坐标或按顶部算都会跟着漂；底边是图标和价格牌
    # 之间唯一稳定的锚。
    # 为什么是 (52,44) 而不是之前的 (56,42)：以底边为原点扫了 dx 44~72 / dy 28~60
    # （步长 4），5 个未被遮挡的真机蓝票格（x=425/650/875，三种格子）全部读到「80」
    # 的只有中间那一带 —— dx 44~60、dy 32~56；(56,42) 只有 4 档 dy 能过，dx 再往右
    # 到 64 就一个都读不到（框右边缘切到数字）。取 52,44 后左右各留 8px、上下各留 8px。
    BLUE_PRICE_DX, BLUE_PRICE_DY = 52, 44
    # 点开这一格点哪：62×59 图标的正中一块
    BLUE_CLICK = (14, 14, 34, 31)

    # ---------- 购买确认框里的「数量 / 总价」 ----------
    # 位置都是相对**勾玉确认键命中框左上角**的偏移，因为确认框有两套布局
    # （带「- 1 +」数量调节 / 不带），勾玉键上下差 44 像素，只有相对它才不跑偏。
    #   总价：+46,+8（70×34）—— 两套布局实测都准：「35」0.9990、「80」0.9990、
    #         「90」0.9950、「160」0.9980、「450」0.9990。
    #         宽度从 50 加宽到 70 是给「点最大」留的：一次买 N 份时总价是 N 倍，
    #         三位变四位（比如三星太鼓 450 × 3 = 1350），50 宽会把首位切掉、
    #         读成 350，单价就被算便宜了。
    #   数量：−10,−82（36×34）—— 2026-10-31 用真机重新标定：旧值 (−11,−92) 在
    #         「数量 2」上读到的是空白（分数 0.534），会退回默认值 1，导致把
    #         160 算成单价 160；新偏移 5 张图全对（数量 1 / 2 都是 0.990~0.995），
    #         不带调节器的确认框这块是说明文字，分数只有 0.19 < OCR_MIN_SCORE，按 1 算。
    DLG_PRICE_DX, DLG_PRICE_DY = 46, 8
    DLG_PRICE_W = 70
    DLG_QTY_DX, DLG_QTY_DY = -10, -82
    # 点过「最大」之后，确认框里的总价是「N 份的钱」。如果读出来的单价
    # 低于货架单价的这个倍数，只可能是位数被价格框截断了（截断只会让数字变小），
    # 宁可这一格不买，也不能按读错的钱扣勾玉。
    DLG_UNIT_MIN_RATIO = 0.8
    # ---------- 御行达摩碎片 / 整只（黑蛋）的身份判定 ----------
    # 两者的达摩脸是同一张画，I_MS_HEISUI 那块 40×40 对整只也命中 0.87，
    # 光看脸分不开身份。真正的区别在商品卡边框形状：
    #   碎片卡 = 六边形，左下角和右上角各斜切一刀；
    #   整只卡 = 完整正方形，四个角都是直角。
    # 缺角本来就在 I_MS_HEISUI 那块 40×40 里，但 40×40 约 5/6 是共用的达摩脸，
    # 斜切只占一小块、被摊薄，碎片 0.99 对整只 0.87 只差 0.12 —— 阈值夹在中间太险。
    # 所以运行时从 I_MS_HEISUI 里再切一块 30×30 的左下角（不单独存图，见
    # _corner_template），拿锚点（命中框左下角）推出卡片左下角做二次判定：
    # 命中 = 碎片，不命中 = 整只。
    #
    # 实测（9 张实机截图、13 个达摩格、两家店）：
    #   严格对齐 碎片 0.9986~1.0000、整只 0.5866~0.5911
    #   带 ±2 像素搜索窗口 碎片 ≥0.9975、整只 ≤0.8229
    # 阈值 0.9 两头余量约 0.09 / 0.08，比整块 40×40 的 0.06 宽一倍多。
    # 售罄变暗不影响：已售罄的碎片格实测仍 1.0000（归一化相关对整体明暗不敏感）。
    CORNER_DX, CORNER_DY = 5, -30   # corner 左上角 = 锚点(命中框左下角) + (5, -30)
    CORNER_PAD = 2                  # 锚点 ±2 像素的漂移容差
    CORNER_SIZE = 30
    CORNER_MIN_SCORE = 0.90
    # 角块模板缓存（_corner_template 首次调用时从 I_MS_HEISUI 切出来填充）
    _corner_tmpl = None

    # 黑蛋（整只御行达摩）单价下限。身份现在由 CORNER 角判定，这里只当**兜底**：
    # 判成整只却读到低于 500 的单价，说明角判定八成看错了（碎片单价实测 48~72、
    # 整只 1600~1920，中间空了很大一段），此时按碎片处理 —— 宁可少买一只整只，
    # 也不把碎片当整只多花勾玉。
    BLACK_DARUMA_MIN_PRICE = 500

    # ---------- 跨店比价 ----------
    # 最多记录多少家店。好友商店一屏一家、切一次 3~5 秒，30 家已经远超实际数量，
    # 纯粹是防「next_one 一直返回 True」的死循环。
    COMPARE_MAX_SHOPS = 30
    # 切店成功的判据：画面平均像素差。和 _scroll_down_one_page 用同一把尺
    # （实测 16416 切店前后差 20 以上，点不动时差 0.x）。
    SHOP_SWITCH_DIFF = 5.0
    SHOP_SWITCH_TRIES = 3
    SHOP_SWITCH_TIMEOUT = 20

    def run(self):
        if self.start_time.weekday() not in self.OPEN_WEEKDAYS:
            logger.warning('Today is not MysteryShop day')
            self.next_time(False)
        self.goto_page(page_mall)
        self.ui_click(self.I_ME_ENTER, self.I_MS_SHARE)
        logger.info('Enter MysteryShop')
        con = self.config.mystery_shop
        # 神秘商人等级只在这里读一次。此刻站在自己的商店页（上面等的是 I_MS_SHARE），
        # 显示的才是自己的真实等级；好友页显示的是分享者的等级，一律不读也不用。
        # 这个值当整趟任务的开关：到上限就全程不买金币道具，否则全程照买。
        self.coin_level = self._shop_level() if con.shop_config.buy_coin_item else 0
        # 本趟任务是否已经尝试过刷新商店（每天只能花 5 勾玉刷 1 次，绝不重试）
        self.refresh_done = False
        # 本趟买了哪些种类（种类, 片数）：刷新前买过的，后面全店不再比价也不再买。
        # 每趟从干净集合开始，不跨运行残留。
        self.bought_kinds = set()
        # 勾玉不足标记也每趟重置：上一趟撞到过，不代表这一趟钱不够。
        self.jade_short = False
        # 分享计数与「本店面有没有达标道具」也每趟重置。share_worthy 不是每趟一个，
        # 是每**个店面**一个：进新店面 / 刷新后都要清空，否则刷新前那批货会把
        # 刷新后那家店的分享判据带过去，变成「没货也分享」。
        self.share_done = 0
        self.share_worthy = set()
        # 弹窗卡死标记。run_shop 里靠它判断「这一屏还能不能继续点」，每趟必须清，
        # 否则上一趟卡过一次会让这一趟整趟不买金币道具。
        self.popup_stuck = False
        if con.shop_config.buy_coin_item:
            if self.coin_level > 0:
                logger.info(f'MysteryShop: 神秘商人 Lv{self.coin_level}（目标 {self.COIN_MAX_LEVEL} 级）')
            else:
                logger.warning('MysteryShop: 进店读不到神秘商人等级，本任务不买金币道具')

        # 判本次是第几趟。两趟分工完全不同的原因见 PASS_SINGLE / PASS_FIRST / PASS_SECOND
        # 三个常量的注释：第一趟把「刷新前」的货吃掉并把刷新后的货分享出去，
        # 第二趟才逐店比价挑最便宜的 —— 这样 5 勾玉刷出来的那批货不会在第一趟被高价买光。
        self.pass_index = self._which_pass(con.shop_config)
        if self.pass_index == self.PASS_SECOND:
            logger.info('MysteryShop: 本趟是今天第二趟 —— 只比价不分享')
            self._run_pass2(con)
        else:
            if self.pass_index == self.PASS_FIRST:
                logger.info('MysteryShop: 本趟是今天第一趟 —— 买刷新前那批；'
                            '刷新后只识别+分享；不逛好友店')
            else:
                logger.info('MysteryShop: 只配了一趟 —— 刷新前后都买；不逛好友店')
            self._run_pass1(con, buy_after_refresh=self.pass_index == self.PASS_SINGLE)

        self.shop_reward()
        logger.info('Exit MysteryShop')
        self.back_mall()

        self.next_time(True)

    def _which_pass(self, cfg) -> int:
        """
        本次是当天第几趟：PASS_SINGLE / PASS_FIRST / PASS_SECOND。

        判据优先级：
        ① 没配第二次时刻（00:00:00）=> 只有一趟，直接 PASS_SINGLE；
        ② 本次的**计划时刻**（scheduler.next_run）离 first 近就是第一趟、离 second 近
           就是第二趟。这是最准的 —— next_time() 排班时第一趟排 first、第二趟排 second；
        ③ 计划时刻不可信（日期不是今天，或当前时间离它超过 PASS_PLAN_TOLERANCE）时，
           按当前时刻离 first / second 哪个近算。手动点「运行」走的都是这条。
        """
        zero = time(hour=0, minute=0, second=0)
        first, second = cfg.time_of_mystery, cfg.time_of_mystery_2
        if second == zero:
            return self.PASS_SINGLE

        now = datetime.now()

        def seconds(t) -> int:
            return t.hour * 3600 + t.minute * 60 + t.second

        ref = now.time()
        try:
            planned = self.config.mystery_shop.scheduler.next_run
        except Exception:
            planned = None
        if isinstance(planned, datetime) and planned.date() == now.date():
            drift = abs(seconds(planned.time()) - seconds(ref))
            if drift <= self.PASS_PLAN_TOLERANCE:
                ref = planned.time()
                logger.info(f'MysteryShop: 本次计划时刻 {ref.strftime("%H:%M:%S")}')
            else:
                logger.info(f'MysteryShop: 计划时刻 {planned.strftime("%H:%M:%S")} 与实际'
                            f'相差 {drift // 60} 分钟，改按当前时间判趟次')

        if abs(seconds(ref) - seconds(second)) < abs(seconds(ref) - seconds(first)):
            return self.PASS_SECOND
        return self.PASS_FIRST

    def _run_pass1(self, con, buy_after_refresh: bool) -> None:
        """
        第一趟（或只配一趟时的唯一一趟）：只处理自己的店，**全程不点右箭头**。

        顺序固定为「先买刷新前那批 -> 分享 -> 刷新 -> 处理刷新后那批 -> 再分享」：
        - 刷新前那批必须先买：花 5 勾玉刷新后它就永久消失，等不到比价结果；
        - 两趟运行时刷新后那批**只识别不买**（buy_after_refresh=False），留给第二趟
          逐店比价；只配一趟时没有第二趟来接，所以当场买掉（buy_after_refresh=True）；
        - 两次分享各自对应一个店面，合起来正好 SHARE_MAX=2 次。

        好友店一次都不逛 —— 用户要求第一趟只碰自己的店（不点右侧黄箭头）。
        好友店的货统一由第二趟的比价来处理。
        """
        cfg = con.shop_config

        # ① 刷新前：按勾选买一遍。run_shop 内部会先打一条识别日志，顺便把
        #    「价格达标的勾玉道具」记进 share_worthy。
        self.share_worthy = set()
        self.run_shop(cfg)

        # ② 分享第 1 次（刷新前的店面）
        self._share_if_worthy(con, '刷新前')

        # ③ 花 5 勾玉刷新。回不到顶部 / 没刷新选项 / 勾玉不足时 refresh_shop 自己会放弃。
        if not (self.shop_ok and self.refresh_shop(cfg)):
            logger.info('MysteryShop: 本趟没有刷新成功，到此结束（不逛好友店）')
            return

        # ④ 刷新后：两趟运行时只识别，只配一趟时连买
        self.share_worthy = set()
        if buy_after_refresh:
            self.run_shop(cfg)
        else:
            self._scan_shop(cfg)

        # ⑤ 分享第 2 次（刷新后的店面）
        self._share_if_worthy(con, '刷新后')

    def _share_if_worthy(self, con, where: str = '本店') -> bool:
        """
        满足条件才分享：本店面**识别到价格达标**的勾玉道具，且今天分享次数还没用完。

        和「买不买」解耦：用户要求即使没勾选购买，只要价格低于配置里那行上限，
        也要分享给好友（好友能买走）。所以判据用的是各 *_max_price，
        不看 mystery_amulet / black_daruma_scrap 这些购买开关。

        上限设 0 表示「连分享也不要」，与「不买」同义。
        """
        if self.share_done >= self.SHARE_MAX:
            logger.info(f'MysteryShop: 本趟已分享 {self.share_done} 次（上限 {self.SHARE_MAX}），不再分享')
            return False
        if not con.invite_config.friend_list_v:
            logger.info('MysteryShop: 没有配置分享好友，跳过分享')
            return False
        if not self.share_worthy:
            logger.info(f'MysteryShop: {where}的店面没有价格达标的勾玉道具，不分享')
            return False
        logger.info(f'MysteryShop: {where}的店面有价格达标的勾玉道具（'
                    f'{"、".join(sorted(self.share_worthy))}），分享给好友')
        self.share(con.invite_config)
        self.share_done += 1
        return True

    def next_one(self):
        """
        切换下一个好友的商店
        :return:
        """
        self.screenshot()
        if not self.appear(self.I_MS_NEXT):
            sleep(0.5)
            self.screenshot()
            if self.appear(self.I_MS_NEXT):
                pass
            else:
                logger.info('No next friend')
                return False

        own_page = self.appear(self.I_MS_SHARE)
        if own_page:
            timer = Timer(30).start()
            while 1:
                self.screenshot()
                if not self.appear(self.I_MS_SHARE):
                    break
                if timer.reached():
                    logger.warning('Next button is gone, stop switching friend')
                    return False
                if self.appear_then_click(self.I_MS_NEXT, interval=1):
                    timer.reset()
                    continue
            logger.info('Switch to next friend')
            return True

        present_friend = self.O_MS_FRIEND.ocr(self.device.image)
        timer = Timer(30).start()
        while 1:
            self.screenshot()
            next_friend = self.O_MS_FRIEND.ocr(self.device.image)
            if present_friend != next_friend:
                break
            if timer.reached():
                logger.warning('Next button is gone, stop switching friend')
                return False
            if self.appear_then_click(self.I_MS_NEXT, interval=2.5):
                timer.reset()
                continue

        logger.info('Switch to next friend')
        return True



    def run_shop(self, shop_config: ShopConfig = None):
        """
        购买当前商店：**先滑到顶部买一遍，再一路滑到底买一遍**（每进一个新页面都这么干）。

        实测（16416，Lv4，商店数量 6/6）整店有 11 个商品，一屏只显示 8 格：
        第一屏 樱饼/破碎的符咒/花笺/八岐大蛇鳞片/二星神乐铃/大蛇的逆鳞/吉运达摩/插画券，
        下滑后才露出第 3 行（六星式神转换符/被遗忘的绘本/御行达摩碎片）。
        所以一屏不够，必须往下扫；滑动幅度见 SCROLL_DOWN_MAX 的注释（y 轴大跨步，两次到底）。

        **进店必须先回顶**：滚动位置会被沿用下来 —— 花 5 勾玉刷新后位置不变（仍在
        第二屏），切到下一个好友的商店也是照着上一家店的位置显示（实测 23:5x 的好友页
        截图里第一行卡片被裁掉、标签只剩半截）。不从顶部开始就会漏掉第一行商品。
        回顶本身有风险（在顶部继续下拉 = 下拉关闭），所以由 _scroll_to_top() 先用
        图像判据确认「真的滚过」才滑。

        副作用：正常扫到底后把 self.shop_ok 置 True，run() 用它决定能不能去刷新。
        回顶失败 / 弹窗卡死 / 下滑后离店这三种情况保持 False —— 画面已不可信，
        此时不能点刷新按钮，否则 5 勾玉会花在错页面上。
        返回整店买到的总件数，只用于日志；**买 0 件不影响刷新**。
        """
        self.shop_ok = False
        # 每进一家店清空「剩余次数」记忆表：上一家店的库存次数不能套到这一家。
        self._reset_times_memo()
        if not self._scroll_to_top():
            logger.error('MysteryShop: 回不到商品顶部，本商店放弃')
            return 0
        bought = self._buy_screen(shop_config)              # 顶部这一屏
        # 一次下滑 = 一大步（见 ms_scroll_down 的 y 轴跨度），最多滑 SCROLL_DOWN_MAX 次。
        # 整店最多 3 行（11 件），第一屏已含 2 行，再滑 2 次必到底；
        # 画面不再变化（diff < SCROLL_MIN_DIFF）就说明到底了，提前收手，不做无用的空滑。
        for i in range(self.SCROLL_DOWN_MAX):
            if getattr(self, 'popup_stuck', False):
                # 弹窗盖在商品上时滑动与点击坐标全不可信，剩下的屏直接放弃
                logger.error('MysteryShop: 弹窗关不掉，本商店放弃（不再往下扫）')
                return bought
            if not self._scroll_down_one_page():
                break
            if not self._in_shop():
                # 滑动前还在这页，滑完不在了 => 下拉关闭之类的异常，绝不能在错页面上点道具
                logger.error('MysteryShop: 下滑后离开了商店页，本商店放弃')
                return bought
            bought += self._buy_screen(shop_config)
        self.shop_ok = True
        if bought:
            logger.info(f'MysteryShop: 本商店共买到 {bought} 件')
        else:
            logger.info('MysteryShop: 本商店没有买到配置里的道具')
        return bought

    def _can_buy_something(self, shop_config: ShopConfig) -> bool:
        """配置里是否勾了任何一项。一项都没勾时买不到东西，但**不代表不用刷新** ——
        见 refresh_shop：只要配了分享好友，刷新后那批货还能分享出去。"""
        return any((shop_config.mystery_amulet, shop_config.black_daruma_scrap,
                    shop_config.black_daruma,
                    shop_config.shop_kaiko_3, shop_config.shop_kaiko_4,
                    shop_config.buy_coin_item))

    def _worth_refresh(self, shop_config: ShopConfig) -> bool:
        """
        花这 5 勾玉值不值：要么有东西可买，要么有好友可分享。

        用户要求「两种道具都没勾选时也要处理刷新后的页面」—— 那边的「处理」就是
        识别 + 分享，所以只要好友列表非空，刷新就有意义（好友能来买刷新后的货）。
        两样都没有时刷新纯属浪费，跳过。
        """
        if self._can_buy_something(shop_config):
            return True
        try:
            return bool(self.config.mystery_shop.invite_config.friend_list_v)
        except Exception:
            return False

    def refresh_shop(self, shop_config: ShopConfig) -> bool:
        """
        自己商店按设置扫完之后，**无条件**花 5 勾玉刷新一次（买没买到都一样）。
        返回 True 表示确实刷新了。

        只在这几种情况下不刷：
        - 本趟已经刷过一次（每天只允许刷 1 次）；
        - 配置里一项道具都没勾（刷了也没有能买的东西）；
        - 回不到商品顶部（新商品看不见，5 勾玉白花）；
        - 画面上的「5(仅1次)」刷新选项不存在（当日已刷 / 该店不可刷）。

        **刷新选项只在自己商店的第一屏**：下滑后的第二屏没有，好友页从来没有
        （用户 10-29 实测）。调用时人正站在第二屏（run_shop 刚扫到底），所以
        点刷新之前必须先用 _scroll_to_top() 回到第一屏；回不去就放弃刷新 ——
        刷新后位置不变，回不了顶新商品也看不见。

        判据全部来自真机实测（16512，23:52）：
        - 还能刷新时，点右下角刷新按钮会弹出带「确定」的确认框 —— 就是全局的
          I_UI_CONFIRM（roi 666,406,175,64），点它才真的刷新（扣 5 勾玉，商品全换）；
        - 本日已经刷新过时，点刷新按钮**完全没反应**，而「5(仅1次)」这个标签仍然挂着，
          所以标签不能当判据；「有没有弹确认框」才是唯一可靠信号。
        因此：没等到确认框 => 已刷新过 => 本趟任务不再尝试（每天只允许刷 1 次）。
        """
        if getattr(self, 'refresh_done', False):
            return False
        if self.jade_short:
            logger.info('MysteryShop: 勾玉不足，刷新要 5 勾玉，不刷新商店')
            return False
        if not self._worth_refresh(shop_config):
            logger.info('MysteryShop: 既没有勾选要买的道具、也没有配分享好友，不刷新商店')
            return False
        # 刷新按钮只在第一屏，先回顶再去找它；已在顶部则一次都不滑
        if not self._scroll_to_top():
            logger.warning('MysteryShop: 回不到商品顶部，跳过刷新')
            return False

        self.screenshot()
        if not self.appear(self.I_MS_REFRESH_5):
            logger.info('MysteryShop: 没有「5(仅1次)」刷新选项，不刷新商店')
            return False

        # 到这里才真正「尝试」过，一次任务最多试一次
        self.refresh_done = True
        logger.hr('Refresh shop', 3)
        self.click(self.C_MS_REFRESH)

        timer = Timer(self.REFRESH_CONFIRM_TIMEOUT).start()
        while 1:
            self.screenshot()
            # 必须先判「勾玉不足」再判 I_UI_CONFIRM：勾玉不足弹窗的确定/确认按钮
            # 和刷新确认框的确定是**同一套模板**，顺序反了就会把「勾玉不足」当刷新
            # 确认点下去，直接跳去商城。文字判据能分开，按钮不能。
            if self._jade_short_cancel():
                logger.warning('MysteryShop: 刷新也要 5 勾玉，勾玉不足，放弃刷新')
                return False
            if self.appear(self.I_UI_CONFIRM):
                break
            if timer.reached():
                logger.info('MysteryShop: 点刷新没弹确认框，说明本日已经刷新过，之后不再尝试')
                return False
            sleep(0.3)

        logger.info('MysteryShop: 花 5 勾玉刷新商店')
        self.click(self.I_UI_CONFIRM)

        # 等确认框消失（点确定后立刻关），再给商品重新加载留点时间
        timer = Timer(10).start()
        while 1:
            self.screenshot()
            if self._jade_short_cancel():
                logger.warning('MysteryShop: 刷新时勾玉不足，放弃刷新后的商店')
                return False
            if not self.appear(self.I_UI_CONFIRM) and not self.appear(self.I_UI_CANCEL):
                break
            if timer.reached():
                logger.warning('MysteryShop: 刷新确认框没关掉，放弃刷新后的商店')
                return False
            sleep(0.3)
        sleep(1.5)

        self.screenshot()
        if not self._in_shop():
            logger.error('MysteryShop: 刷新后不在商店页，放弃刷新后的商店')
            return False
        logger.info('MysteryShop: 商店已刷新')
        return True

    def _in_shop(self) -> bool:
        """
        当前是否还在神秘商店页。I_MS_NEXT（切下一个好友）在所有商店页都有，
        I_MS_SHARE 只有自己的商店有，任一命中即算在店内。
        """
        self.screenshot()
        return self.appear(self.I_MS_NEXT) or self.appear(self.I_MS_SHARE)

    def _buy_screen(self, shop_config: ShopConfig) -> int:
        """把当前这一屏能买的都买一遍，返回买到的件数。"""
        bought = 0
        # 买之前先把这一屏识别到的道具和价格打出来 —— 用户要能在日志里直接看到
        # 「当前刷出了什么、多少钱」，而不是只看到买/不买的零散分支日志。
        # _scan_screen 只读不点（截图 + 模板 + OCR），多跑一次的代价可以接受。
        self._log_found(self._scan_screen(shop_config), '本屏')
        if self.jade_short:
            logger.info('MysteryShop: 勾玉不足中，本屏不再买勾玉道具')
        if shop_config.mystery_amulet and not self.jade_short:
            bought += self.buy_amulet_screen(shop_config)
        if (shop_config.black_daruma_scrap or shop_config.black_daruma) and not self.jade_short:
            bought += self.buy_daruma_screen(shop_config)
        if shop_config.shop_kaiko_3 and not self.jade_short:
            bought += self._price_capped_buy(self.I_MS_TAIKO_3, self.I_MS_CHECK_TAIKO_3,
                                             shop_config.kaiko_3_max_price,
                                             self.TAIKO_3_PRICE_DX, self.TAIKO_3_PRICE_DY,
                                             self.TAIKO_3_MIN_SCORE, self.TAIKO_3_CLICK, '三星太鼓')
        if shop_config.shop_kaiko_4 and not self.jade_short:
            bought += self._price_capped_buy(self.I_MS_TAIKO_4, self.I_MS_CHECK_TAIKO_4,
                                             shop_config.kaiko_4_max_price,
                                             self.TAIKO_4_PRICE_DX, self.TAIKO_4_PRICE_DY,
                                             self.TAIKO_4_MIN_SCORE, self.TAIKO_4_CLICK, '四星太鼓')
        # 金币道具（提升神秘商人等级），放在勾玉道具之后，避免挤占上面几项的点击次数
        if shop_config.buy_coin_item:
            bought += self.buy_coin_items(shop_config)
        return bought

    def _price_capped_buy(self, rule, check, cap: int, price_dx: int, price_dy: int,
                          min_score: float, click_box: tuple, label: str,
                          read_dialog: bool = False) -> int:
        """
        通用「按标价买」：在这一屏找出所有 label，读价格，不高于 cap 才买。

        为什么不沿用 buy_mall_one：那条路是「模板命中就买」，完全不看价格，
        上限只能靠代码里写死的 buy_money 当「余额够不够」，起不到比价作用。

        min_score 比资产阈值更严，是因为三星/四星两个模板会互相误命中
        （见 TAIKO_3_MIN_SCORE 的注释），用资产阈值会把四星当三星买。

        买之前先查「剩余购买次数」（_cell_buyable），0 或读不到就跳过这一格：
        售罄的格子点开也不会弹确认框，只会白等一次 10 秒超时。

        read_dialog 透传给 _buy_at：勾了就是「点开确认框后再按数量/总价复核一次」，
        给有「- 1 +」数量调节的商品（神秘咒符）用。
        """
        self.popup_stuck = False
        if self.jade_short:
            # 勾玉不足已经置位：连扫都不用扫，直接跳过整类道具
            logger.info(f'MysteryShop: 勾玉不足中，跳过{label}')
            return 0
        self.screenshot()
        image = self.device.image
        # 这一屏的「剩余购买次数」标签行，用来剔掉已售罄的格子。
        # 没有这一步的后果（10-31 真机复现）：售罄的蓝票照样被点开，弹不出确认框，
        # 一直空等到 HEISUI_POPUP_TIMEOUT（10 秒）才跳过 —— 不会买错（不花钱），
        # 但每格白等 10 秒，日志还会先报一句「90 勾玉」让人以为能买。
        lines = self._count_lines(image)
        hits = []
        # threshold 必须显式传：实测不传（走规则自带的 0.8）时同一张图连跑两次，
        # 返回的分数会变（0.9235 / 0.8976），坐标也会差 1 像素；显式传之后
        # 连跑 5 次结果完全一致。比价不能建立在不稳定的分数上。
        # 先按分数降序再去重：match_all 的返回顺序里低分命中可能排在前面，
        # 直接取第一个会把 0.92 当成这一格的最好成绩（实际是 0.99）。
        raw = sorted(rule.match_all(image, threshold=min_score), key=lambda t: -float(t[0]))
        for m in raw:
            x, y, w, h = int(m[1]), int(m[2]), int(m[3]), int(m[4])
            if all(abs(x - px) > 25 or abs(y - py) > 25 for px, py, _, _ in hits):
                hits.append((x, y, w, h))
        if not hits:
            logger.info(f'MysteryShop: 当前这一屏没有{label}')
            return 0
        hits.sort(key=lambda t: (t[1] // 100, t[0]))
        # 先把售罄的格子剔掉再报数，日志里「有 N 个」才是真能买的个数。
        # 顺手给每一格算记忆键 =(列, 种类)：只有这一屏里该键**唯一**才启用记忆，
        # 否则同一列上下两格同种道具会互相套用对方的剩余次数。
        keys = [(self._col_key(x + w / 2.0), label) for x, y, w, h in hits]
        usable = [k for k in keys if keys.count(k) == 1]
        hits = [(hit, keys[i] if keys[i] in usable else None)
                for i, hit in enumerate(hits)]
        hits = [(hit, key) for hit, key in hits
                if self._cell_buyable(image, lines, *hit, key=key)]
        if not hits:
            logger.info(f'MysteryShop: 当前这一屏没有可买的{label}')
            return 0
        logger.info(f'MysteryShop: 当前这一屏有 {len(hits)} 个可买的{label}')

        bought = 0
        for (x, y, w, h), key in hits:
            ay = y + h
            for _ in range(self.HEISUI_BUY_MAX):
                roi = [x + price_dx, ay + price_dy] + list(self.O_MS_PRICE.roi[2:])
                price = self._price_in_box(image, roi)
                if price <= 0:
                    logger.warning(f'MysteryShop: {label}底边({x},{ay}) 价格读不到，跳过')
                    break
                if price > cap:
                    logger.info(f'MysteryShop: {label}底边({x},{ay}) 价格 {price} 高于上限 {cap}，不买')
                    break
                logger.info(f'MysteryShop: {label}底边({x},{ay}) 价格 {price} 不高于上限 {cap}，买')
                if not self._buy_at(x, ay, h, click_box, check, label, 1, price, float(price),
                                    read_dialog=read_dialog, cap=cap):
                    break
                bought += 1
                # 买到之后这一格还剩几次就不知道了（有「最大」按钮时可能一次买光），
                # 记忆值作废：下一屏如果它的标签又被顶栏遮住，宁可跳过也不猜，
                # 免得对着一格已经卖空的货再点一次、白等 10 秒。
                if key is not None and isinstance(self.times_memo, dict):
                    self.times_memo[key] = None
                # 同一格连买时点击名相同，不清记录会撞 GameTooManyClickError（≥10 次）
                self.device.click_record_clear()
                if self.popup_stuck:
                    logger.warning(f'MysteryShop: 弹窗关不掉，本商店剩下的{label}不再买')
                    return bought
                # 买过之后画面会变（售罄遮罩 / 奖励残留），重新取一帧再读下一个
                self.screenshot()
                image = self.device.image
                # 买走一件后这一格（或整排）的「剩余购买次数」会变，标签行也重新定位，
                # 否则下一个格子会套用买之前的那份剩余次数。
                lines = self._count_lines(image)
        if bought:
            logger.info(f'MysteryShop: 本屏{label}购买 {bought} 次')
        return bought

    def buy_amulet_screen(self, shop_config: ShopConfig) -> int:
        """
        按标价买「神秘咒符」（蓝票），返回买到的张数。

        为什么不沿用上游 buy_mall_one：那条路是「模板一命中就买」，完全不看标价 ——
        GUI 里那个「数值小于等于 85」根本没生效（代码里写死的 85 只是「勾玉余额够不够」），
        点开确认框后又是点固定坐标 C_BUY_ONE，连勾玉不足都不认。换成和太鼓同一条
        按价买的通道之后，上限才真的起作用。

        蓝票一次可以买多张（剩余购买次数 2~3），所以同一格允许连买 HEISUI_BUY_MAX 次；
        买完格子变黑、价格读不到，循环自然退出。

        read_dialog=True：蓝票的确认框带「- 1 +」数量调节器，价格显示的是这一单的
        总价，必须按 单价 = 总价 / 数量 复核（见 _dialog_qty_price）。

        **判据用 I_MS_JADE_BUY（勾玉确认键）而不是 I_MS_CHECK_BLUE**：实测蓝票的
        确认框里那块道具大图在 (595,200)，而 I_MS_CHECK_BLUE 标定在 (592,241)，
        它的搜索框 roiBack(455,235,261,146) 根本盖不到 200 那一行，match_all 一张都不出；
        把搜索框拉高到 y185 能命中，但分数只有 0.804~0.818（截图是 JPEG，压缩把分数
        压下去了），做判据太薄。勾玉确认键在两套布局下都有 0.972~0.979，而且是
        「这个框是花勾玉的购买确认框」最直接的证据，所以拿它当判据。
        点错格子也不会买错：确认框里的总价会按上限复核，太鼓 450 这种当场就被拦下。
        """
        return self._price_capped_buy(
            self.I_MS_BLUE_ICON, self.I_MS_JADE_BUY,
            shop_config.mystery_amulet_max_price,
            self.BLUE_PRICE_DX, self.BLUE_PRICE_DY,
            self.BLUE_MIN_SCORE, self.BLUE_CLICK, '神秘咒符', read_dialog=True)

    def buy_daruma_screen(self, shop_config: ShopConfig) -> int:
        """
        在当前这一屏上按价格买「达摩」类道具（黑碎 + 黑蛋），返回买到的次数。

        为什么不用 buy_mall_one：那条路是「模板命中就买」，命中位置由 appear() 自己
        回写，屏幕上同时有两片黑碎时永远只会盯着第一片，而且完全不看价格。
        这里先用 match_all 拿到每一格的坐标，再逐格读价格和数量。

        **碎片和整只靠卡片左下角分身份**（见 _cell_is_scrap）：40×40 的达摩脸对整只
        「契约书·御行达摩」同样命中 0.87，看脸分不开；但碎片卡左下角是斜切的、
        整只卡四角是直角，实测碎片 ≥0.9975 对整只 ≤0.8229（带 ±2 像素搜索窗口），
        阈值 0.9 两边都有余量。
          - 命中角块 → 御行达摩碎片，按单片价比 scrap_cap
          - 没命中   → 整只，按整只价对 daruma_cap
          - 判成整只但单价低于 BLACK_DARUMA_MIN_PRICE → 角判定八成错了，按碎片处理

        价格读不到就跳过（绝不把「读不到」当成 0 勾玉去买）；数量读不到按 1 片算。
        """
        self.popup_stuck = False
        if self.jade_short:
            logger.info('MysteryShop: 勾玉不足中，跳过达摩')
            return 0
        self.screenshot()
        image = self.device.image
        positions = self._heisui_positions(image)
        if not positions:
            logger.info('MysteryShop: 当前这一屏没有达摩')
            return 0
        logger.info(f'MysteryShop: 当前这一屏有 {len(positions)} 个达摩格')

        do_scrap = shop_config.black_daruma_scrap
        do_daruma = shop_config.black_daruma
        scrap_cap = shop_config.black_daruma_scrap_max_price
        daruma_cap = shop_config.black_daruma_max_price
        price_max = max(scrap_cap, daruma_cap, self.DARUMA_PRICE_READ_MAX)

        bought = 0
        for ax, ay in positions:
            for _ in range(self.HEISUI_BUY_MAX):
                qty = self._heisui_qty(image, ax, ay)
                price = self._price_in_box(
                    image, [ax + self.PRICE_DX, ay + self.PRICE_DY] + list(self.O_MS_PRICE.roi[2:]),
                    max_price=price_max)
                if price <= 0:
                    logger.warning(f'MysteryShop: 达摩({ax},{ay}) 价格读不到或超出上限，跳过')
                    break
                unit = price / qty

                # 身份以卡片左下角为准，价格只判上限（见 _cell_is_scrap 的说明）
                is_scrap = self._cell_is_scrap(image, ax, ay)

                if not is_scrap and unit < self.BLACK_DARUMA_MIN_PRICE:
                    logger.warning(f'MysteryShop: 达摩({ax},{ay}) 角判定为整只，但单价 {unit:.0f} '
                                   f'低于整只下限 {self.BLACK_DARUMA_MIN_PRICE}，按碎片处理')
                    is_scrap = True

                if is_scrap:
                    # 碎片：按单片价比上限。1/2/3 片是三个独立格子，各自比各自买。
                    if not do_scrap:
                        logger.info(f'MysteryShop: 达摩({ax},{ay}) {price} 勾玉 / {qty} 片 ≈ '
                                    f'单价 {unit:.0f}，是碎片但没勾选，跳过')
                        break
                    if unit > scrap_cap:
                        logger.info(f'MysteryShop: 御行达摩碎片({ax},{ay}) {price} 勾玉 / {qty} 片 = '
                                    f'单价 {unit:.1f}，高于上限 {scrap_cap}，不买')
                        break
                    logger.info(f'MysteryShop: 御行达摩碎片({ax},{ay}) {price} 勾玉 / {qty} 片 = '
                                f'单价 {unit:.1f}，不高于上限 {scrap_cap}，买')
                    label = '御行达摩碎片'
                else:
                    if not do_daruma:
                        logger.info(f'MysteryShop: 达摩({ax},{ay}) {price} 勾玉，是整只御行达摩但没勾选，跳过')
                        break
                    # 整只是四位数，44 宽的价格框会切掉首位（1920 读成 920），必须复核
                    full = self._daruma_full_price(image, ax, ay, price_max)
                    if full <= 0:
                        logger.warning(f'MysteryShop: 御行达摩({ax},{ay}) 窄窗口读到 {price}，'
                                       f'四位价格复核失败，当读不准跳过')
                        break
                    if full != price:
                        logger.info(f'MysteryShop: 御行达摩({ax},{ay}) 窄窗口 {price} -> 四位复核 {full}')
                    unit = float(full)
                    if unit > daruma_cap:
                        logger.info(f'MysteryShop: 御行达摩({ax},{ay}) 价格 {unit:.0f} 高于上限 '
                                    f'{daruma_cap}，不买')
                        break
                    logger.info(f'MysteryShop: 御行达摩({ax},{ay}) 价格 {unit:.0f} 不高于上限 '
                                f'{daruma_cap}，买')
                    label = '御行达摩'

                if not self._buy_heisui_at(ax, ay, qty, label, price, unit):
                    break
                bought += 1
                # 同一片连买时点击名相同，不清记录会撞 GameTooManyClickError（≥10 次）
                self.device.click_record_clear()
                if self.popup_stuck:
                    logger.warning('MysteryShop: 弹窗关不掉，本商店剩下的达摩不再买')
                    return bought
                # 买过之后画面会变（售罄遮罩 / 奖励残留），重新取一帧再读下一个
                self.screenshot()
                image = self.device.image
        if bought:
            logger.info(f'MysteryShop: 本屏达摩购买 {bought} 次')
        return bought

    def _heisui_positions(self, image) -> list:
        """
        在这一帧里找出所有黑碎，返回 [(ax, ay)]，即模板命中框的**左下角**绝对坐标。

        不用 match()：它只把一处的坐标回写进 roi_front，屏幕上第二片就丢了。
        match_all 会为同一片返回多个高度重叠的命中（相邻像素都过阈值），按 25 像素去重。
        match 元组是 (score, x, y, w, h)，左下角 = (x, y + h)。
        """
        positions = []
        for match in self.I_MS_HEISUI.match_all(image):
            x, y = int(match[1]), int(match[2])
            ax, ay = x, y + int(match[4])          # 第 5 项 = 模板高度
            if all(abs(ax - px) > self.HEISUI_MERGE_DIST
                   or abs(ay - py) > self.HEISUI_MERGE_DIST for px, py in positions):
                positions.append((ax, ay))
        # 从上到下、从左到右，买的时候顺序稳定，日志也好对
        positions.sort(key=lambda p: (p[1] // 100, p[0]))
        return positions

    def _corner_template(self):
        """
        从 I_MS_HEISUI 里切出卡片左下角 30×30，当身份判定的模板。

        不单独存一份 png：缺角本来就长在这块 40×40 里，再存一张只是同一块区域
        的第二份副本，改素材时容易忘了同步。锚点是命中框左下角，所以角块在模板
        内的坐标 = (CORNER_DX, 模板高 + CORNER_DY) = (5, 10)，切到底边为止。

        切一次缓存住：_cell_is_scrap 一屏要调好几次，没必要反复切。
        切不出来（素材被换小了）就返回 None，调用方按「判不了」处理。
        """
        if self._corner_tmpl is not None:
            return self._corner_tmpl
        rule = getattr(self, 'I_MS_HEISUI', None)
        base = None if rule is None else rule.image
        if base is None:
            logger.error('MysteryShop: 读不到 I_MS_HEISUI，无法判定达摩身份')
            return None
        size = self.CORNER_SIZE
        dx = self.CORNER_DX
        dy = base.shape[0] + self.CORNER_DY
        if dx < 0 or dy < 0 or base.shape[0] < dy + size or base.shape[1] < dx + size:
            logger.error(f'MysteryShop: I_MS_HEISUI 尺寸 {base.shape} 切不出 '
                         f'{size}×{size} 角块（dx={dx}, dy={dy}），无法判定达摩身份')
            return None
        self._corner_tmpl = np.ascontiguousarray(base[dy:dy + size, dx:dx + size])
        return self._corner_tmpl

    def _cell_is_scrap(self, image, ax: int, ay: int) -> bool:
        """
        锚点 (ax, ay) 这一格是「御行达摩碎片」还是整只「契约书·御行达摩」。

        为什么不再靠价格分（10-31 之前的做法）：价格区间只是当下的经验，出个打折
        整只或高价碎片就错了；而卡片边框形状是身份本身 —— 碎片卡左下角缺一块，
        整只卡四角齐全，跟价格、跟行情都无关。

        为什么不用整块 I_MS_HEISUI 判：缺角的斜切**本来就在它里面**（左下角那一刀，
        只是碎片的边框是亮橙色、整只是暗红直角），但 40×40 里约 5/6 面积是碎片和
        整只共用的那张达摩脸，斜切只占一小块，归一化相关被摊薄 —— 实测碎片 0.99、
        整只仍有 0.87，只差 0.12，阈值夹中间没余量。所以只取它左下角那块 30×30。

        搜索窗口给 ±CORNER_PAD：锚点实测在同格内稳定到 ±1 像素，但窗口放宽会让
        整只的直角也跟着蹭分（碎片仍 ≥0.9975、整只升到 ≤0.8229），所以 PAD 不能
        再加。阈值 0.90 两边各留约 0.09 / 0.08。
        """
        tmpl = self._corner_template()
        if tmpl is None:
            return False
        pad, size = self.CORNER_PAD, self.CORNER_SIZE
        x = ax + self.CORNER_DX - pad
        y = ay + self.CORNER_DY - pad
        win = size + pad * 2
        roi = image[max(y, 0):y + win, max(x, 0):x + win]
        if roi.shape[0] < size or roi.shape[1] < size:
            logger.warning(f'MysteryShop: 达摩({ax},{ay}) 角判定窗口越界，本格跳过')
            return False
        roi = np.ascontiguousarray(roi)
        score = float(cv2.matchTemplate(roi, tmpl, cv2.TM_CCOEFF_NORMED).max())
        return score >= self.CORNER_MIN_SCORE

    @staticmethod
    def _brief(kind: str, qty: int, price=None, unit=None) -> str:
        """
        日志里的一行道具描述：种类(片数) 价格 勾玉（单价 x）。

        碎片必须带片数和单价：1/2/3 片是三个独立格子、各自有独立上限，只写
        「御行达摩碎片 180」看不出买的是哪一种，也看不出到底贵不贵。
        整只和太鼓没有片数概念，价格就是单价，不重复写。

        一次买多件时（神秘咒符的确认框可以选数量）要写成「×3 共 270（单价 90.0）」：
        只写 270 会让人以为单价 270，只写 90 又对不上实际扣的钱。
        """
        name = f'{kind}({qty}片)' if kind == '御行达摩碎片' else kind
        if price is None:
            return name
        if unit is not None and qty and qty > 1:
            return f'{name} ×{qty} 共 {price} 勾玉（单价 {unit:.1f}）'
        if kind == '御行达摩碎片' and unit is not None:
            return f'{name} {price} 勾玉（单价 {unit:.1f}）'
        return f'{name} {price} 勾玉'

    def _log_found(self, items: list, where: str = '本屏') -> None:
        """
        把这一屏识别到的道具和价格打进日志。

        用户要能一眼看到「现在刷出了什么、多少钱」，不用去翻每一格的分支日志
        （那些日志只在决定买/不买时才出现，售罄和不勾选的格子根本不打印）。
        一件一行而不是挤成一行：日志窗口宽度有限，挤一行会被自动折行，
        价格和「单价」被拆到两行反而更难读。
        """
        if not items:
            logger.info(f'MysteryShop: [识别] {where}没有勾选的道具')
            return
        logger.info(f'MysteryShop: [识别] {where}共 {len(items)} 件：')
        for it in items:
            logger.info(f'MysteryShop: [识别]   · '
                        f'{self._brief(it["kind"], it["qty"], it["price"], it["unit"])}')

    def _heisui_unit_price(self, image, ax: int, ay: int, max_price: int = None) -> tuple:
        """
        算左下角锚点 (ax,ay) 这一片的单价，返回 (是否读成功, 价格, 片数, 单价)。

        价格读不到返回 False —— 由调用方跳过，绝不能拿 0 去比价、更不能拿 0 去平摊。
        max_price 要传 DARUMA_PRICE_READ_MAX 级别（而不是 PRICE_MAX=999）：整只御行达摩
        实测标价 1920，用 999 会被当成读不到，整只就永远进不了比价池、也不出现在日志里。
        """
        qty = self._heisui_qty(image, ax, ay)
        price = self._heisui_price(image, ax, ay, max_price)
        if price <= 0:
            return False, price, qty, 0.0
        return True, price, qty, price / qty

    def _heisui_qty(self, image, ax: int, ay: int) -> int:
        """
        读数量角标。只有 1 片时游戏根本不显示角标，所以「读不到」在这里就等于「1 片」。

        这里不用 ocr()：Digit 模式在 0.3~0.6 之间有一条「结果里含数字就放行」的挽救分支
        （module/ocr/base_ocr.py:201），无角标时达摩脸会被读成 "3" 放进来，直接
        54/3=18 当成便宜货买下去。改成自己取原始分数，低于 0.6 一律当没有角标。
        """
        roi = [ax + self.QTY_DX, ay + self.QTY_DY] + list(self.O_MS_QTY.roi[2:])
        self.O_MS_QTY.roi = roi
        text, score = self.O_MS_QTY.model.ocr_single_line(self.O_MS_QTY.crop(image, roi))
        if score < self.OCR_MIN_SCORE:
            return 1
        qty = self.O_MS_QTY.after_process(text)
        if not isinstance(qty, int) or qty < 1 or qty > self.QTY_MAX:
            return 1
        return qty

    def _price_in_box(self, image, roi, max_price: int = None, rule=None) -> int:
        """
        在给定 ROI 里读价格数字，读不到 / 置信度不够 / 超出合理范围都返回 0。

        不用 ocr()：Digit 模式在 0.3~0.6 有一条「含数字就放行」的挽救分支
        （module/ocr/base_ocr.py:201），价格牌边上的装饰很容易被读成数字放进来。
        这里自己取原始分数，低于 0.6 一律当读不到。

        max_price 默认 PRICE_MAX：超上限的读数基本就是金币标价（五位数），
        必须放弃，否则 180000 金币会被当成 180 勾玉，单价直接变地板价。
        黑蛋整只标价 1920 超过 PRICE_MAX，调用时要把 max_price 放宽。

        rule 默认 O_MS_PRICE（货架价签 44×30）。购买确认框里的总价宽一号、
        而且不能借用 O_MS_PRICE —— 它的 roi 会被这里原地改写，尺寸一旦变宽，
        后面 _heisui_price 按 .roi[2:] 取尺寸的地方就跟着错位了。
        """
        if max_price is None:
            max_price = self.PRICE_MAX
        if rule is None:
            rule = self.O_MS_PRICE
        rule.roi = list(roi)
        text, score = rule.model.ocr_single_line(rule.crop(image, roi))
        if score < self.OCR_MIN_SCORE:
            return 0
        price = rule.after_process(text)
        if not isinstance(price, int) or price <= 0 or price > max_price:
            return 0
        return price

    def _heisui_price(self, image, ax: int, ay: int, max_price: int = None) -> int:
        """
        读黑碎的价格数字，读不到或明显不合理返回 0，由调用方跳过这一片。

        黑碎在神秘商店里恒为勾玉标价，而价格牌左侧的勾玉图标在 ROI 左边之外
        （见 PRICE_DX 的注释），所以 ROI 里只剩数字。
        """
        roi = [ax + self.PRICE_DX, ay + self.PRICE_DY] + list(self.O_MS_PRICE.roi[2:])
        return self._price_in_box(image, roi, max_price)

    def _daruma_full_price(self, image, ax: int, ay: int, max_price: int) -> int:
        """
        整只御行达摩的价格复核，读不准返回 0（调用方据此跳过）。

        为什么不能直接用 44 宽的那次读数：整只标价是四位数（实测「契约书·御行达摩
        1920」），牌子上这串数字横向约 59 像素，塞不进 44 宽的 O_MS_PRICE。实测按
        +56 去读，左边那个「1」被切掉，读出来是 **920** —— 按 920 跟上限比，一台
        1920 的整只就被当成 920 买下来了，而且 920 还落在「像黑蛋」的区间里，
        分流也拦不住。改成 64 宽的窗口：左边界 44 仍然躲开价格牌左侧的勾玉图标
        （实测图标右缘在 +39），40 位以上的四位价放得下；读不到就说明这串数字超出
        了能可靠识别的宽度，宁可跳过也不拿一个残缺价格去比价。
        """
        roi = [ax + self.PRICE_WIDE_DX, ay + self.PRICE_DY, self.PRICE_WIDE_W, 30]
        return self._price_in_box(image, roi, max_price)

    def _dialog_qty_price(self, image, jx: int, jy: int, max_price: int = None):
        """
        购买确认框里的「数量」和「总价」，读不到就返回 (1, 0)。

        为什么必须读数量：确认框底部那个价格是**这一单的总价**，不是单价。数量 1 时
        两者相同；一旦游戏把数量记成 2、3（右边还有「最大」箭头），总价就是单价的整数倍
        —— 直接拿总价去比「数值小于等于 85」，3 张 90 的符咒会被读成 270 判成超上限不买，
        或者反过来把便宜的漏掉。所以统一按 单价 = 总价 / 数量 来判。

        数量读不到就当 1：不带「- 1 +」数量调节的确认框（四星奉为达摩那种）里，
        这个位置是道具说明文字，OCR 分数只有 0.19，低于 OCR_MIN_SCORE，自然落到 1。

        但反过来，**带调节器的确认框里数量必须读准**：2026-10-31 真机上点了「最大」
        之后数量是 2、总价 160，旧偏移读到的是空白（0.534）→ 退回 1 → 单价被算成 160。
        所以偏移按「数量 1 与数量 2 都要读对」重新标定过（见 DLG_QTY_DX/DY 的注释）。

        (jx, jy) 是勾玉确认键命中框的左上角：确认框有两套布局，勾玉键上下差 44 像素，
        只有相对它取偏移才不跑偏（两套布局实测总价都读得准）。
        """
        if max_price is None:
            max_price = self.DARUMA_PRICE_READ_MAX
        qty_roi = [jx + self.DLG_QTY_DX, jy + self.DLG_QTY_DY, 36, 34]
        self.O_MS_DLG_QTY.roi = qty_roi
        text, score = self.O_MS_DLG_QTY.model.ocr_single_line(
            self.O_MS_DLG_QTY.crop(image, qty_roi))
        qty = 1
        if score >= self.OCR_MIN_SCORE:
            value = self.O_MS_DLG_QTY.after_process(text)
            if isinstance(value, int) and 1 <= value <= self.QTY_MAX:
                qty = value
        price = self._price_in_box(
            image, [jx + self.DLG_PRICE_DX, jy + self.DLG_PRICE_DY, self.DLG_PRICE_W, 34],
            max_price, rule=self.O_MS_DLG_PRICE)
        return qty, price

    def _close_purchase_dialog(self, retry: int = 3) -> bool:
        """
        点左侧空白关掉购买确认框，并确认它真的关了。

        判据用勾玉确认键：它只在确认框里出现，货架上是没有的。不确认的话，下一格的
        模板匹配会跑在确认框画面上，点位全错。
        """
        for i in range(retry):
            self.click(self.C_MS_BLANK)
            sleep(0.6)
            self.screenshot()
            if not self.appear(self.I_MS_JADE_BUY):
                return True
            logger.warning(f'MysteryShop: 购买确认框还在，再点一次空白（第 {i + 1} 次）')
        return False

    def _buy_heisui_at(self, ax: int, ay: int, qty: int = 1, label: str = '御行达摩碎片',
                       price: int = None, unit: float = None) -> bool:
        """
        达摩格的薄包装：40×40 模板高、点图标框、I_MS_CHECK_BLACK 判据。

        label 只用于记账和日志。整只御行达摩和碎片是同一张达摩脸、同一个判据，
        但记账时必须分开：整只的 qty 恒为 1，要是也记成「黑碎 1 片」，
        后面就会把好友店真正的 1 片装碎片当成「已经买过」而跳过。
        price / unit 只用于日志（「买了什么、多少钱」），不参与点击。
        """
        return self._buy_at(ax, ay, self.HEISUI_H, self.HEISUI_CLICK,
                            self.I_MS_CHECK_BLACK, label, qty, price, unit)

    def _jade_short_cancel(self) -> bool:
        """
        屏幕上是「勾玉不足」弹窗吗？是就点取消、置位 jade_short、返回 True。

        **靠文字判、不靠按钮判**：这个弹窗的取消按钮（I_UI_CANCEL 438,405,177,62 /
        I_UI_CANCEL_SAMLL 472,389,128,62）和普通确认框（刷新商店那个）位置尺寸完全
        一样，只看按钮分不开 —— 一误判就会把正常的刷新确认框给点掉。所以先匹配
        弹窗里那句『勾玉不足』的文字，确认是它之后再点取消。

        两种弹窗是两个不同控件，字宽 24px vs 28px（差 1.17 倍），一份模板对另一份
        只有 0.36，所以 ms_jade_short / ms_jade_short_samll 两份都要测（阈值 0.8）：
          「勾玉不足，是否前往购买勾玉？」
          「勾玉不足，是否前往商城？」
        33 张正常商店截图 / 其它弹窗对两份模板最高 0.2393 / 0.2494，不会误报。

        roiBack 给的是 140,150,1000,420，覆盖整块弹窗区、不紧贴文字：两版面板本身的
        位置就差 30px 左右（实测外接框 403~872 对 433~846），而『勾玉不足』这四个字
        恰好两版都在 (457, 297) 附近。真按文字紧贴着裁窗口，纵向只剩 12px 余量，
        弹窗一浮动就漏；放大到整块后负样本最高只从 0.2343 涨到 0.2494，阈值 0.8
        仍有 0.55 余量。文字位置稳定、窗口放宽，等于把「弹窗挪了位置」这个变量消掉。

        只点取消：确定/确认会跳到商城或勾玉购买页，直接把任务带离神秘商店。
        appear 自带同一帧的结果缓存，多次调用不会重复截图、重复匹配。
        """
        if self.jade_short:
            return True
        text = None
        for rule in (self.I_MS_JADE_SHORT, self.I_MS_JADE_SHORT_SAMLL):
            if self.appear(rule):
                text = rule
                break
        if text is None:
            return False

        logger.warning(f'MysteryShop: 勾玉不足（匹配到 {text.name}），点取消')
        clicked = False
        for rule in (self.I_UI_CANCEL, self.I_UI_CANCEL_SAMLL):
            if self.appear(rule):
                self.click(rule)
                clicked = True
                break
        if not clicked:
            # 文字认出来了但取消按钮没匹配上：两版取消按钮的重叠区是 x472-600、y405-451，
            # 点重叠区中心兜底，绝不点右边那个确定。
            logger.warning('MysteryShop: 勾玉不足的取消按钮没匹配上，按重叠区兜底点击')
            self.click(RuleClick(roi_front=(516, 416, 40, 24),
                                 roi_back=(516, 416, 40, 24), name='ms_jade_cancel'))
        sleep(0.5)
        self.jade_short = True
        logger.warning('MysteryShop: 勾玉不足，本次运行跳过所有勾玉道具'
                       '（神秘咒符 / 御行达摩碎片 / 御行达摩 / 三星太鼓 / 四星太鼓）')
        return True

    def _buy_at(self, ax: int, ay: int, tmpl_h: int, click_box: tuple,
                check, label: str, qty: int = 1, price: int = None,
                unit: float = None, read_dialog: bool = False,
                cap: int = 0) -> bool:
        """
        点开左下角锚点 (ax,ay) 这一格并买 1 次，返回是否真的买到了。

        qty 只用于记账（_mark_bought），不参与点击：买到的商品属于「哪一片数」，
        后面全店比价时要靠它判断「这种早就买过了，不用再找」。

        **购买确认框**：点商品图标后先弹确认框，上半是道具大图、下半是勾玉确认键
        （591,498,49,45）。第一段等 check（道具大图）证明「框真开了、也没点错格子」，
        第二段优先点勾玉确认键 I_MS_JADE_BUY —— 认出来就只点它，绝不同时盲点
        C_BUY_ONE（两者位置重叠，会买两次）；认不出来才退回盲点。点完才出
        「获得奖励」或「勾玉不足」。

        **勾玉不足**：点开商品后要么出「获得奖励」（买到），要么弹「勾玉不足」。
        后者的确定/确认按钮和普通确认框是同一套模板，只能靠『勾玉不足』这几个字
        认（详见 _jade_short_cancel）。认出来就点取消、置位 jade_short 并返回 False，
        之后本趟任务不再买任何勾玉道具。

        锚点是左下角，点图标要换算回左上角：top = ay - tmpl_h。

        三段全部自带 Timer，任何一段超时都点空白关弹窗返回 False —— 这里绝不能
        照抄 Buy.buy_one 的 while 1，那个循环在弹窗不出现时会一直点固定坐标，
        最后撞 GameTooManyClickError 把整条任务炸掉。

        判据 check 是购买弹窗里那块道具大图（黑碎 I_MS_CHECK_BLACK、三星太鼓
        I_MS_CHECK_TAIKO_3 …），它出现才说明点的是这个道具、弹窗真的开了
        （也是唯一能确认「没点错格子」的信号）。

        read_dialog=True 时，在点勾玉确认键之前先读确认框里的「数量」和「总价」，
        按 单价 = 总价 / 数量 复核一遍（见 _dialog_qty_price）。传了 cap 且复核出的
        单价超上限，就点空白关掉确认框、不买 —— 货架上的价签可能读错，确认框里这行
        是即将真扣的那笔钱，以它为准。默认 False，达摩 / 太鼓那些没有数量调节器的
        商品不用多这一步。

        **「最大」按钮**：确认框的数量行最右侧有时会多一个双上箭头圆按钮（I_MS_MAX_BUY），
        点它数量直接跳到这次能买的上限。出现就点、没出现就按数量 1 点勾玉。点了之后
        总价变成 N 份的钱，所以即使 read_dialog=False 也会强制读一次数量/总价复核单价。
        """
        cx, cy, cw, ch = click_box
        top = ay - tmpl_h
        if self.jade_short:
            # 本趟已经确认勾玉不够，别再点 —— 点了也只会弹一次「勾玉不足」白等一圈
            logger.info(f'MysteryShop: 勾玉不足中，跳过 {label}({ax},{ay})')
            return False
        icon = RuleClick(roi_front=(ax + cx, top + cy, cw, ch),
                         roi_back=(ax + cx, top + cy, cw, ch),
                         name=f'ms_buy_{ax}_{ay}')
        self.click(icon)
        sleep(1)

        timer = Timer(self.HEISUI_POPUP_TIMEOUT).start()
        while 1:
            self.screenshot()
            if self._jade_short_cancel():
                return False
            if self.appear(check):
                break
            if self.appear(self.I_BUY_RMB):
                logger.warning(f'MysteryShop: {label}({ax},{ay}) 弹出人民币购买，取消')
                self.click(self.C_BUY_CANCEL)
                sleep(0.5)
                return False
            if timer.reached():
                logger.warning(f'MysteryShop: {label}({ax},{ay}) 购买弹窗没出现（多半已售罄），跳过')
                if not self.close_popup():
                    self.popup_stuck = True
                return False
            sleep(0.3)

        # 有些道具的确认框，数量行最右侧还多一个「最大」圆按钮（两个向上箭头）：
        # 点它会把数量直接拉到这次能买的上限（剩 3 次就一次买 3 份）。
        # 用户 10-31 定死：**出现就点它，没出现就按数量 1 直接点勾玉**。
        # 只有带「- 1 +」数量调节的确认框才渲染这个按钮（不带调节的确认框里
        # 那块位置是道具说明文字，模板最高只有 0.73，阈值 0.85 认不出来，自然跳过）。
        #
        # 点完之后底部总价变成 N 份的钱，所以下面**必须重读一次数量/总价**，
        # 仍按 单价 = 总价 / 数量 比上限 —— 拿 3 倍的总价去跟单份上限比，
        # 会把本来就便宜的货误判成超限。
        max_clicked = False
        order_qty = 1           # 这一单实际买几份：只有点过「最大」才会 > 1
        if self.appear(self.I_MS_MAX_BUY):
            if self.click(self.I_MS_MAX_BUY, interval=1):
                max_clicked = True
                # 数量和总价的数字是一格一格跳上去的，等它停稳再读，否则会读到中间值
                sleep(0.5)
                self.screenshot()
                logger.info(f'MysteryShop: {label}({ax},{ay}) 确认框有「最大」按钮，已点成一次买满')
            else:
                logger.warning(f'MysteryShop: {label}({ax},{ay}) 认出「最大」按钮但没点下去，按数量 1 买')

        # 确认框已经开了，先读「数量」和「总价」，按单价复核一遍再决定点不点 ——
        # 点下去就是真花钱，所以校验必须放在点击之前。读不到总价就沿用货架上读到的那份。
        # 点了「最大」之后一定要读：价格已经不是货架上那个单份价了。
        dialog_ok = (read_dialog or max_clicked) and self.appear(self.I_MS_JADE_BUY)
        if max_clicked and not dialog_ok:
            # 点过「最大」之后连勾玉键都认不出来，画面已经不可信：这时点下去
            # 可能是「以 N 份的钱」结账，而我们对这个 N 一无所知。
            logger.warning(f'MysteryShop: {label}({ax},{ay}) 点「最大」之后确认框认不出来了，放弃这一格')
            if not self.close_popup():
                self.popup_stuck = True
            return False
        if dialog_ok:
            jx, jy = self.I_MS_JADE_BUY.roi_front[0], self.I_MS_JADE_BUY.roi_front[1]
            dqty, dprice = self._dialog_qty_price(self.device.image, jx, jy)
            if dprice > 0:
                dunit = dprice / dqty
                logger.info(f'MysteryShop: {label}({ax},{ay}) 确认框读到 数量 {dqty} / 总价 '
                            f'{dprice} 勾玉（单价 {dunit:.1f}）')
                if cap and dunit > cap:
                    logger.info(f'MysteryShop: {label}({ax},{ay}) 确认框单价 {dunit:.1f} 高于上限 '
                                f'{cap}，取消不买')
                    if not self._close_purchase_dialog():
                        self.popup_stuck = True
                    return False
                # 点过「最大」之后总价是 N 份的钱，位数可能超出价格框被切掉首位，
                # 而截断只会让数字变小 —— 算出的单价明显低于货架单价就说明读的是
                # 残数。这时候宁可这一格不买，也不能按读错的钱扣勾玉。
                if max_clicked and unit and dunit < unit * self.DLG_UNIT_MIN_RATIO:
                    logger.warning(f'MysteryShop: {label}({ax},{ay}) 确认框总价 {dprice} 算出的单价 '
                                   f'{dunit:.1f} 明显低于货架单价 {unit:.1f}，多半是总价被截断，取消不买')
                    if not self._close_purchase_dialog():
                        self.popup_stuck = True
                    return False
                # price / unit 一律存**单份**：qty 是货架规格（碎片是 1/2/3 片那种），
                # 绝不能拿它装「这一单买几份」—— 1 片 × 3 份会被 _mark_bought 记成
                # 「3 片买过了」，真正的 3 片那格反而漏买。份数单独用 order_qty 记。
                order_qty = dqty
                price, unit = int(round(dunit)), dunit
            elif max_clicked:
                # 点了「最大」却读不到总价：这一单要花多少勾玉完全不知道，不能盲点。
                logger.warning(f'MysteryShop: {label}({ax},{ay}) 点了「最大」但读不到总价，'
                               f'不知道这一单花多少，取消不买')
                if not self._close_purchase_dialog():
                    self.popup_stuck = True
                return False

        timer = Timer(self.HEISUI_BUY_TIMEOUT).start()
        tries = 0
        while 1:
            self.screenshot()
            if self._jade_short_cancel():
                return False
            if self.ui_reward_appear_click():
                break
            if tries >= self.HEISUI_BUY_TRIES:
                logger.warning(f'MysteryShop: {label}({ax},{ay}) 点了 {tries} 次购买都没出获得奖励，'
                               f'关弹窗放弃')
                if not self.close_popup():
                    self.popup_stuck = True
                return False
            if self.appear(self.I_BUY_RMB):
                logger.warning(f'MysteryShop: {label}({ax},{ay}) 弹出人民币购买，取消')
                self.click(self.C_BUY_CANCEL)
                sleep(0.5)
                return False
            if self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=1):
                # 确认框弹出来是真状态变化，给它重置计时；同时占用一次尝试名额
                tries += 1
                timer.reset()
                continue
            if self.appear(self.I_MS_JADE_BUY):
                # 购买确认框下半那个勾玉图标（591,498,49,45）= 「花勾玉买」的确认键，
                # 点它才会出「获得奖励」或「勾玉不足」。认出来就只点它然后 continue，
                # **绝不落到下面盲点 C_BUY_ONE** —— 两者位置高度重叠（C_BUY_ONE 是
                # 551,506,174,36），同一次判断里连点两下就是买两次，白花勾玉。
                # 这里不 reset timer：道理和盲点一样，稳住 12 秒超时别被自己续命。
                if self.click(self.I_MS_JADE_BUY, interval=1):
                    tries += 1
                continue
            if self.click(self.C_BUY_ONE, interval=1):
                # 这里绝不能 timer.reset()：click() 的返回值是「interval 到了且真的点下去了」
                # （tasks/base_task.py:566），不是「点到了有效东西」。固定坐标盲点必然返回 True，
                # 一 reset 这个 12 秒超时就永远走不到，弹窗卡住时会每秒盲点到
                # GameTooManyClickError。所以盲点只计数、不重置。
                tries += 1
                continue
            if timer.reached():
                logger.warning(f'MysteryShop: {label}({ax},{ay}) 没买到（没出现获得奖励）')
                if not self.close_popup():
                    self.popup_stuck = True
                return False
            sleep(0.3)

        # 奖励动画没散就去找下一片，模板匹配会跑在动画上，坐标全错
        # 注意：出奖励那一下已经在上面点过一记了（ui_reward_appear_click 自带 interval=0.4），
        # 这里只在面板迟迟不散时补点，且总补点次数封顶 —— 不再像上游那样「一直点击」。
        timer = Timer(self.HEISUI_REWARD_TIMEOUT).start()
        clicks = 0
        while 1:
            self.screenshot()
            if not self.appear(self.I_UI_REWARD, threshold=0.6):
                break
            if timer.reached():
                logger.warning('MysteryShop: 获得奖励动画没散，继续往下走')
                break
            if clicks >= self.HEISUI_REWARD_CLICKS:
                sleep(0.2)          # 补点名额用完，只等不点
                continue
            if self.ui_reward_appear_click():
                clicks += 1
            sleep(0.2)
        if order_qty > 1:
            logger.info(f'MysteryShop: [买到] {self._brief(label, qty, price, unit)} '
                        f'×{order_qty} 份 共 {order_qty * price} 勾玉 格子 ({ax},{ay})')
        else:
            logger.info(f'MysteryShop: [买到] {self._brief(label, qty, price, unit)} 格子 ({ax},{ay})')
        self._mark_bought(label, qty)
        return True

    def _mark_bought(self, label: str, qty: int = 1):
        """
        记下「这种商品已经买走一件了」，后面全店不再比价。

        种类名统一成比价池的写法（黑碎 -> 御行达摩碎片），键是 (种类, 片数)。
        为什么带片数：1 片 / 2 片 / 3 片在商店里是三个独立格子、各有各的剩余次数，
        买走 1 片的那格不会让 2 片、3 片变黑，所以它们还得继续比价。
        """
        kind = self.KIND_ALIAS.get(label, label)
        self.bought_kinds = self.bought_kinds | {(kind, qty)}
        name = f'{kind} {qty} 片' if kind == '御行达摩碎片' else kind
        logger.info(f'MysteryShop: 已记下「{name}」买过，后续全店不再比价这一种')

    # ==================== 第二趟：跨店比价 ====================
    def _run_pass2(self, con):
        """
        第二趟：把每家店都只「记录」一遍，挑出每件道具最便宜的那家，再倒着走回去买。

        和第一趟的分工：
        - 第一趟买的是**刷新前**那批（刷新后永久消失，等不到比价），并把刷新后那批
          分享出去但**不买**；第一趟一个好友店都不逛。
        - 第二趟因此拿到的是一整片没被动过的货架：自己的店（第一趟刷出来那批）
          + 每个好友的店，全都还在，可以放心比价。

        为什么不边逛边买：同样的黑碎 A 店 48、B 店 60，边逛边买会先把 60 的买掉。
        所以先只记录、后回头买：

        ① 自己的店只记录（第一趟没买刷新后那批）；
        ② 逐个好友店只记录、不买；
        ③ 挑出每件道具最便宜的那一家，用左箭头**倒着走回去**买。

        「倒着走」是因为 next_one() 只能往前，而记录时已经走到最后一家；
        I_MS_BEFORE（左侧白色左箭头）可以直接退，不必从自己的店重切一遍。
        自己的店没有这个左箭头 —— 这既是「第一家」的判据，也是回程的终点标志。

        **全程不分享**（用户要求）。第二趟也不刷新：每天的 5 勾玉刷新是第一趟的事，
        这里要的是「直接比较每个商店的价格」，多刷一次只会把已经记过的价格作废。
        """
        cfg = con.shop_config
        catalogs = []

        # ① 自己的店（第一趟刷新出来的那批，第一趟只识别没买）
        items = self._scan_shop(cfg)
        catalogs.append((0, items))
        logger.info(f'MysteryShop: [比价] 自己的店记录 {len(items)} 件')

        # ② 好友店逐个只记录
        idx = 1
        while idx <= self.COMPARE_MAX_SHOPS:
            if not self.next_one():
                break
            items = self._scan_shop(cfg)
            catalogs.append((idx, items))
            logger.info(f'MysteryShop: [比价] 第 {idx} 家记录 {len(items)} 件')
            idx += 1

        if not catalogs:
            logger.warning('MysteryShop: [比价] 一家店都没记录到，直接收奖励')
            return

        plan = self._pick_cheapest(catalogs, cfg)
        if not plan:
            logger.info('MysteryShop: [比价] 没有符合价格上限的道具，不回程购买')
            return
        for shop_idx in sorted(plan.keys(), reverse=True):
            for it in plan[shop_idx]:
                logger.info(f'MysteryShop: [比价] {self._item_label(it)} 最低 {it["unit"]:.0f} '
                            f'在第 {shop_idx} 家，回程买')
        # 当前位置必须是**记过的那家店的序号**，不能拿 catalogs 的个数当序号：
        # 自己店没记录时 catalogs 从 1 开始，个数和序号差 1。
        bought = self._buy_plan(plan, catalogs[-1][0], cfg)
        logger.info(f'MysteryShop: [比价] 回程共买到 {bought} 件')

    def _scan_shop(self, cfg) -> list:
        """把当前这家店从顶部扫到底，只识别不买，返回整店的道具清单。"""
        # 每进一家店清空「剩余次数」记忆表：上一家店的库存次数不能套到这一家。
        self._reset_times_memo()
        if not self._scroll_to_top():
            logger.error('MysteryShop: [比价] 回不到顶部，本店记录为空')
            return []
        page = self._scan_screen(cfg)
        self._log_found(page, '第 1 屏')
        items = page
        for i in range(self.SCROLL_DOWN_MAX):
            if not self._scroll_down_one_page():
                break
            if not self._in_shop():
                logger.error('MysteryShop: [比价] 下滑后离开商店页，本店记录中断')
                break
            page = self._scan_screen(cfg)
            self._log_found(page, f'第 {i + 2} 屏')
            items += page
        merged = self._merge_items(items)
        self._log_found(merged, '本店合计')
        return merged

    def _merge_items(self, items: list) -> list:
        """两屏有重叠时同一格会被识别两次，按「同类 + 锚点相距 25 像素内」合并。"""
        merged = []
        for it in items:
            if any(o['kind'] == it['kind'] and abs(o['ax'] - it['ax']) <= 25
                   and abs(o['ay'] - it['ay']) <= 25 for o in merged):
                continue
            merged.append(it)
        return merged

    def _count_lines(self, image) -> list:
        """
        全屏找出所有「剩余购买次数」标签行，返回 [(x, y, w, h)]，不保证有序。

        **为什么不用 O_MS_LEFT_1..8 那 8 个固定 ROI**：商品列表一滚动，整排格子就整体
        位移（实测顶部两行在 y=79/340，滚一次变 y=313，也就是 -27；换一家店滚到底能到
        -212），固定的 OCR 区域会读到别的格子甚至一片空白。这行字是固定 UI 的一部分，
        文字内容恒定不变，把它做成模板 match_all 就能定位到任意滚动位置，与位移量无关。

        跨 11 张不同店铺/不同滚动位置的截图实测：命中 11/11，分数 0.924~1.000。
        **售罄格的这行字会整体变暗到约 40% 亮度，但 TM_CCOEFF_NORMED 是归一化相关，
        对整体明暗不敏感，实测仍能匹配到 0.994** —— 这正是不用像素亮度判售罄的原因。

        空位（那一格没有商品）根本没有这行字，自然不会命中，正好用来排除空位。
        """
        raw = self.I_MS_LEFTCOUNT.match_all(image, threshold=self.LEFTCOUNT_MIN_SCORE)
        lines = []
        for m in sorted(raw, key=lambda t: -float(t[0])):
            x, y, w, h = int(m[1]), int(m[2]), int(m[3]), int(m[4])
            # 同一个位置会返回一堆高度重叠的框，按「x 差 30 内且 y 差 12 内」并成一个
            if any(abs(x - lx) <= 30 and abs(y - ly) <= 12 for lx, ly, _, _ in lines):
                continue
            lines.append((x, y, w, h))
        return lines

    def _line_times(self, image, line) -> int:
        """
        读一条标签行上的购买次数。读不到返回 -1，与「读到 0（售罄）」区分开。

        用 Single 模式读整行再抓数字，和原来 32 格全对的路子一致；不用 Digit 模式，
        因为它在 0.3~0.6 有「含数字就放行」的挽救分支（module/ocr/base_ocr.py:201），
        容易把标签旁的装饰吃成数字。价格读数用 Digit 是另一回事，那边有 max_price 兜底。
        """
        x, y, w, h = line
        roi = [x - 2, y - 4, w + 40, h + 8]
        ruler = self.O_MS_LEFT_1
        text, score = ruler.model.ocr_single_line(ruler.crop(image, roi))
        if score < self.OCR_MIN_SCORE or not isinstance(text, str):
            return -1
        match = re.search(r'(\d+)', text)
        return int(match.group(1)) if match else -1

    def _col_key(self, cx: float) -> int:
        """商品命中框中心 x 落在哪一列（0~3），四列中心线 235/460/686/911。"""
        return round((cx - self.LEFTCOUNT_COL_X0) / self.LEFTCOUNT_COL_STEP)

    def _reset_times_memo(self) -> None:
        """
        换一家店（含 5 勾玉刷新）就把「剩余次数」记忆表清空。

        必须清：不同店的货品完全不同，跨店复用会把上一家店的库存次数套到这一家的
        格子上，直接把「售罄」判成「还有货」。
        """
        self.times_memo = {}

    def _memo_times(self, key):
        """查记忆：(列, 种类) -> 次数 / None(买过，未知) / 键不存在时返回 'miss' 标记。"""
        if not isinstance(self.times_memo, dict) or key not in self.times_memo:
            return None, False
        return self.times_memo[key], True

    def _cell_buyable(self, image, lines, x: int, y: int, w: int, h: int,
                      key=None) -> bool:
        """
        给定一件商品的命中框，判断它所在那一格还能不能买。

        判据是格子上方那行「剩余购买次数：N」，不是像素亮度（实测同一件黑碎，
        售罄和可买两张截图逐像素平均差只有 2.1，肉眼都看不出，达摩脸本来就暗）。

        **比价扫描和真正下单（_price_capped_buy）都走这里**：售罄的格子点开也
        不会弹确认框，只会白等一次 HEISUI_POPUP_TIMEOUT（10 秒）。

        关联方式：**按列 + 垂直距离最近**。标签行的左边界固定在 165/390/616/841 四档，
        商品的 x 中心落到哪一列就归哪一列；再在这一列里取「位于商品上方且最近」的
        那一条标签行。实测标签行底边到商品图标顶边的距离恒为 75 像素左右，
        相邻行间距 261，所以「最近」不会串到隔壁行去。

        key=(列, 种类) 时启用**跨屏记忆**（见 times_memo 的注释）：找上方标签行失败
        ——通常是这一行被下滑到标题栏底下、标签只剩半截——就查这张表，把这家店上一屏
        读到的同格次数拿来用。没有记录时仍按「空位」跳过，不做猜测。
        """
        cx = x + w / 2.0
        best, best_d = None, None
        for line in lines:
            lx, ly, lw, lh = line
            if abs(lx + lw / 2.0 - cx) > self.LEFTCOUNT_COL_TOL:
                continue                      # 不在同一列
            gap = y - (ly + lh)               # 标签行底边 -> 商品顶边
            if gap < 0 or gap > 200:
                continue                      # 不在商品上方，或离得太远（跨行）
            if best_d is None or gap < best_d:
                best, best_d = line, gap
        if best is None:
            # 上方没有标签行：要么是空位（没商品），要么是这一行滑到了标题栏底下、
            # 标签只露出半截导致模板匹配不上（完整行 0.99，半截 <0.5）。
            # 后者才有救 —— 用这家店上一屏记住的次数。
            times, known = self._memo_times(key)
            if not known:
                return False                  # 空位
            if times is None:
                logger.info(f'MysteryShop: 格子({x},{y}) 标签被顶栏遮住，这一格本店买过，'
                            f'剩余次数未知，跳过（等标签露出来再读真值）')
                return False
            logger.info(f'MysteryShop: 格子({x},{y}) 标签被顶栏遮住，用本店上一屏记住的'
                        f'剩余次数 {times}')
            return times > 0
        times = self._line_times(image, best)
        if times >= 0 and key is not None and isinstance(self.times_memo, dict):
            # 读到真值就刷新记忆。存 -1（读不到）没意义，下一屏再遇到还是查不出，
            # 所以只读到数字时覆盖。
            self.times_memo[key] = times
        if times <= 0:
            # 购买路径（_price_capped_buy）也会调这里，所以文案不带「比价」前缀
            logger.info(f'MysteryShop: 格子({x},{y}) 剩余购买次数 {times} '
                        f'{"(读不到，按售罄)" if times < 0 else "(已售罄)"}，跳过这一格')
            return False
        return True

    def _scan_screen(self, cfg) -> list:
        """
        识别当前这一屏的勾玉道具，一个都不点。

        **扫描不再看购买开关**：用户要求「哪怕不勾选买这个，只要价格低于配置里的上限，
        也要分享给好友」。分享判据和购买判据分开之后，识别必须始终覆盖全部五种道具，
        否则没勾选的那种连价格都读不到，就无从判断该不该分享。
        买不买、进不进比价池仍由各自开关控制（见 _buy_screen / _pick_cheapest）。

        售罄判断对**任意滚动位置**都有效：_count_lines 用模板定位「剩余购买次数」标签行，
        位置跟着画面走，不依赖 8 格固定坐标（早期版本只在顶部那一屏能判，因为用的是
        写死的 O_MS_LEFT_1..8；那 8 个区域在滚动后就对不上了）。
        """
        self.screenshot()
        image = self.device.image
        lines = self._count_lines(image)
        items = []
        # 整只御行达摩实测标价 1920，超过碎片读取上限 PRICE_MAX(999)。
        # 这里统一用 DARUMA_PRICE_READ_MAX 当读取上限，和 buy_daruma_screen
        # 是同一套算法；买不买仍分别由碎片/整只自己的上限决定。
        price_max = max(cfg.black_daruma_scrap_max_price,
                        cfg.black_daruma_max_price, self.DARUMA_PRICE_READ_MAX)
        positions = self._heisui_positions(image)
        # 记忆键只能笼统按「达摩」算（碎片还是整只要读完价才知道）。
        # 同一列出现两格达摩时这个键就不唯一了，谁都不许用记忆，避免套错次数。
        daruma_cols = [self._col_key(ax) for ax, _ in positions]
        daruma_key_ok = len(daruma_cols) == len(set(daruma_cols))
        for ax, ay in positions:
            box_h = self.HEISUI_H
            key = ('达摩', self._col_key(ax)) if daruma_key_ok else None
            if not self._cell_buyable(image, lines, ax, ay - box_h, box_h, box_h, key=key):
                continue
            ok, price, qty, unit = self._heisui_unit_price(image, ax, ay, price_max)
            if not ok:
                continue
            # 和 buy_daruma_screen 同一套分流：卡片左下角缺角 -> 碎片
            is_scrap = self._cell_is_scrap(image, ax, ay)
            if not is_scrap and unit < self.BLACK_DARUMA_MIN_PRICE:
                logger.warning(f'MysteryShop: [比价] 达摩({ax},{ay}) 角判定为整只，但单价 '
                               f'{unit:.0f} 低于整只下限，按碎片处理')
                is_scrap = True
            kind = '御行达摩碎片' if is_scrap else '御行达摩'
            if kind == '御行达摩':
                # 整只是四位数，44 宽的价格框会切掉首位（1920 读成 920），必须复核
                full = self._daruma_full_price(image, ax, ay, price_max)
                if full <= 0:
                    logger.warning(f'MysteryShop: [比价] 整只达摩({ax},{ay}) 窄窗口读到 '
                                   f'{price}，四位价格复核失败，当读不准跳过')
                    continue
                price = full
                unit = float(full)
            items.append({'kind': kind, 'price': price, 'qty': qty, 'unit': unit,
                          'ax': ax, 'ay': ay, 'tmpl_h': self.HEISUI_H,
                          'click': self.HEISUI_CLICK, 'check': self.I_MS_CHECK_BLACK})
            self._mark_worthy(kind, price, unit, cfg)
        for rule, dx, dy, score, click, check, label in (
            (self.I_MS_TAIKO_3, self.TAIKO_3_PRICE_DX, self.TAIKO_3_PRICE_DY,
             self.TAIKO_3_MIN_SCORE, self.TAIKO_3_CLICK, self.I_MS_CHECK_TAIKO_3, '三星太鼓'),
            (self.I_MS_TAIKO_4, self.TAIKO_4_PRICE_DX, self.TAIKO_4_PRICE_DY,
             self.TAIKO_4_MIN_SCORE, self.TAIKO_4_CLICK, self.I_MS_CHECK_TAIKO_4, '四星太鼓'),
        ):
            got = self._scan_image_items(image, lines, rule, dx, dy, score, click, check, label)
            items += got
            for it in got:
                self._mark_worthy(it['kind'], it['price'], it['unit'], cfg)
        # 神秘咒符（蓝票）不参与比价（价格固定、每个商店都是同一档），所以不进 items，
        # 只单独打一条识别日志 —— 不然用户看不出这一屏到底有没有刷出蓝票。
        # lines 复用上面那份，售罄的蓝票格在这里被剔掉，不会进分享判据。
        self._log_amulet(image, lines, cfg)
        return items

    def _mark_worthy(self, kind: str, price: int, unit: float, cfg) -> None:
        """
        记下「这个店面有价格达标的勾玉道具」，供 _share_if_worthy 判断要不要分享。

        与购买开关**无关**：用户要求即使没勾选购买，只要价格不高于配置里那行上限，
        也要分享给好友。上限设 0 当「这项我不要」处理，连分享也跳过。

        碎片按**单价**比（勾玉 ÷ 片数），和真正下单时的判据一致；
        其它种类按标价比。
        """
        caps = {
            '御行达摩碎片': cfg.black_daruma_scrap_max_price,
            '御行达摩': cfg.black_daruma_max_price,
            '三星太鼓': cfg.kaiko_3_max_price,
            '四星太鼓': cfg.kaiko_4_max_price,
            '神秘咒符': cfg.mystery_amulet_max_price,
        }
        cap = caps.get(kind, 0)
        if cap <= 0:
            return
        value = unit if kind == '御行达摩碎片' else float(price)
        if 0 < value <= cap:
            self.share_worthy = set(self.share_worthy) | {kind}

    def _log_amulet(self, image, lines=None, cfg=None) -> None:
        """
        把这一屏识别到的「神秘咒符」（蓝票）和标价打进日志，并记进分享判据。

        只报数、不参与跨店比价：蓝票价格每个商店都是同一档，没必要为了它多绕一家店。
        但买入仍按标价卡上限（见 buy_amulet_screen），所以这里读的价和真正买单时
        用的是同一个偏移。

        **售罄格要剔掉**：蓝票格「剩余购买次数」可以是 0，那时价格牌还在、模板照样
        命中，报一句「90 勾玉」会让人以为能买（10-31 真机就是这个现象）。
        已售罄的也不进 share_worthy —— 分享出去好友也买不了。

        lines 由调用方（_scan_screen）传进来复用，省一次全屏模板匹配；单独调用时
        会自动算。
        """
        if cfg is None:
            cfg = self.config.mystery_shop.shop_config
        if lines is None:
            lines = self._count_lines(image)
        # 先去重、再判售罄：match_all 在临界分附近会在同一格出 ±1px 的多个点
        # （售罄格变暗后分数更接近阈值，这种情况更明显）。若把售罄判断写进去重条件里，
        # 被 skip 的点不进列表、下一轮又会当成新格子，同一格会刷出 4 行日志。
        cells = []
        for m in sorted(self.I_MS_BLUE_ICON.match_all(image, threshold=self.BLUE_MIN_SCORE),
                        key=lambda t: -float(t[0])):
            x, y, w, h = int(m[1]), int(m[2]), int(m[3]), int(m[4])
            if all(abs(x - px) > 25 or abs(y - py) > 25 for px, py, _, _ in cells):
                cells.append((x, y, w, h))
        hits, sold_out = [], 0
        # 同一列出现两个蓝票格时，记不住谁的数是谁的，索性都不用记忆
        blue_cols = [self._col_key(x + w / 2.0) for x, y, w, h in cells]
        blue_key_ok = len(blue_cols) == len(set(blue_cols))
        for i, (x, y, w, h) in enumerate(cells):
            key = ('神秘咒符', blue_cols[i]) if blue_key_ok else None
            if not self._cell_buyable(image, lines, x, y, w, h, key=key):
                sold_out += 1
                continue
            ax, ay = x, y + h
            price = self._price_in_box(
                image, [ax + self.BLUE_PRICE_DX, ay + self.BLUE_PRICE_DY, 44, 30])
            hits.append((ax, ay, price))
        tail = f'（另有 {sold_out} 个已售罄，跳过）' if sold_out else ''
        if not hits:
            logger.info('MysteryShop: [识别] 本屏没有可买的神秘咒符' + tail)
            return
        parts = [f'{p} 勾玉' if p > 0 else '标价未读到' for _, _, p in hits]
        logger.info(f'MysteryShop: [识别] 本屏神秘咒符 {len(hits)} 个：' + '、'.join(parts) + tail)
        for _, _, price in hits:
            if price > 0:
                self._mark_worthy('神秘咒符', price, float(price), cfg)

    def _scan_image_items(self, image, lines, rule, price_dx: int, price_dy: int, min_score: float,
                          click, check, label: str) -> list:
        """
        在整屏里找出所有 label（三星/四星太鼓），逐格读价，只保留读得到价的。

        match_all 必须显式传 threshold，否则同一张图两次跑的分数会变
        （见 _price_capped_buy 里的实测），比价不能建立在不稳定的分数上。
        """
        raw = sorted(rule.match_all(image, threshold=min_score), key=lambda t: -float(t[0]))
        hits = []
        for m in raw:
            x, y, w, h = int(m[1]), int(m[2]), int(m[3]), int(m[4])
            if all(abs(x - px) > 25 or abs(y - py) > 25 for px, py, _, _ in hits):
                hits.append((x, y, w, h))
        # 同一列出现两格同 label 时记忆键不唯一，两边都不用记忆
        cols = [self._col_key(x + w / 2.0) for x, y, w, h in hits]
        key_ok = len(cols) == len(set(cols))
        items = []
        for i, (x, y, w, h) in enumerate(hits):
            key = (label, cols[i]) if key_ok else None
            if not self._cell_buyable(image, lines, x, y, w, h, key=key):
                continue
            ay = y + h
            price = self._price_in_box(
                image, [x + price_dx, ay + price_dy] + list(self.O_MS_PRICE.roi[2:]))
            if price <= 0:
                continue
            items.append({'kind': label, 'price': price, 'qty': 1, 'unit': float(price),
                          'ax': x, 'ay': ay, 'tmpl_h': h, 'click': click, 'check': check})
        return items

    @staticmethod
    def _item_label(it: dict) -> str:
        """
        日志里的道具名。碎片要带片数 —— 1 片 / 2 片 / 3 片是三个独立格子，
        只写「御行达摩碎片」时日志里三行一模一样，看不出到底买了哪一种。
        其它种类（整只达摩、三星/四星太鼓）没有片数概念，原样返回。
        """
        if it['kind'] == '御行达摩碎片':
            return f'御行达摩碎片({it["qty"]}片)'
        return it['kind']

    def _pick_cheapest(self, catalogs: list, cfg) -> dict:
        """
        每件道具挑一家最便宜的，返回 {店序号: [条目]}。

        **分组键是 (种类, 片数)，不是种类**：御行达摩碎片在商店里可以同时挂
        1 片装、2 片装、3 片装三个格子，每格有独立的剩余购买次数，各能买一次。
        只按种类分组的话这三个格子只会有一个进比价结果，另两个永远买不到。

        刷新前已经买走的 (种类, 片数) 直接跳过：第一趟是当场买的，买走之后全店
        同款都会变黑，再比价只会选出几格卖空的货来回点。
        （第二趟的 bought_kinds 是空的 —— 第一趟买过什么这一趟读不到；那种格子
        在扫店时已经被 _cell_buyable 按「剩余购买次数 0」剔掉了。）

        同价时取序号**大**的那家，也就是离回程起点**最近**的那家：
        回程从最后记录的那家店往回走（见 _buy_plan），序号大的先遇到。
        用户要求同价选最近的 —— 因为买走一件后其余店的同款会立刻变黑，
        多走几家纯属白跑。
        """
        caps = {
            '御行达摩碎片': cfg.black_daruma_scrap_max_price if cfg.black_daruma_scrap else -1,
            '御行达摩': cfg.black_daruma_max_price if cfg.black_daruma else -1,
            '三星太鼓': cfg.kaiko_3_max_price if cfg.shop_kaiko_3 else -1,
            '四星太鼓': cfg.kaiko_4_max_price if cfg.shop_kaiko_4 else -1,
        }
        best = {}
        # 用 getattr 兜底：这里只做「读」，万一实例没经过 run()（比如被当成工具类直接调用），
        # 不能因为少一个属性就整条比价炸掉。空集合的含义就是「还没有买过任何东西」。
        bought = getattr(self, 'bought_kinds', ())
        for shop_idx, items in catalogs:
            for it in items:
                cap = caps.get(it['kind'], -1)
                if cap < 0 or it['unit'] > cap:
                    continue
                key = (it['kind'], it['qty'])
                if key in bought:
                    continue
                cur = best.get(key)
                if cur is None or it['unit'] < cur['unit'] or (
                        it['unit'] == cur['unit'] and shop_idx > cur['shop']):
                    best[key] = dict(it, shop=shop_idx)
        plan = {}
        for it in best.values():
            plan.setdefault(it['shop'], []).append(it)
        return plan

    def _buy_plan(self, plan: dict, cur_idx: int, cfg) -> int:
        """从当前位置往回走，走到哪家买哪家。"""
        bought = 0
        for target in sorted(plan.keys(), reverse=True):
            while cur_idx > target:
                if not self._switch_shop(self.I_MS_BEFORE, '上一家'):
                    logger.error(f'MysteryShop: [比价] 退不到第 {target} 家，剩下的都不买了')
                    return bought
                cur_idx -= 1
            for it in plan[target]:
                bought += self._buy_recorded(it, cfg)
        return bought

    def _buy_recorded(self, it: dict, cfg) -> int:
        """
        在目标店里重新找到这一格再买。

        为什么不直接用在别家店记下的坐标：每家店的滚动位置和商品排布都不同，
        A 店第 3 格的坐标拿到 B 店就是另一件商品。所以按「同类 + 同片数 + 单价相同」
        重新匹配 —— 片数必须一起比：1 片装和 2 片装常常同单价（48 与 96），
        只比单价会把 2 片装的格子当成 1 片装买走，另一种片数就漏了。
        """
        label = it['kind']
        if not self._scroll_to_top():
            logger.warning(f'MysteryShop: [比价] {label} 回不到顶部，放弃购买')
            return 0
        for page in range(self.SCROLL_DOWN_MAX + 1):
            for cand in self._scan_screen(cfg):
                if cand['kind'] != label or cand['qty'] != it['qty']:
                    continue
                if abs(cand['unit'] - it['unit']) > 0.5:
                    continue
                if self._buy_at(cand['ax'], cand['ay'], cand['tmpl_h'],
                                cand['click'], cand['check'], label, cand['qty'],
                                cand['price'], cand['unit']):
                    self.device.click_record_clear()
                    return 1
            if page >= self.SCROLL_DOWN_MAX:
                break
            if not self._scroll_down_one_page():
                break
            if not self._in_shop():
                break
        logger.warning(f'MysteryShop: [比价] 目标店里没再找到 {label} 单价 {it["unit"]:.0f}')
        return 0

    def _switch_shop(self, rule, label: str) -> bool:
        """
        点一次切店箭头，确认画面真的换了才返回 True。

        判据用画面差而不是 OCR 好友名：自己的店根本没有好友名，
        而且 OCR 读歪一个字就会把「切成功」判成「没切」。
        """
        for attempt in range(self.SHOP_SWITCH_TRIES):
            self.screenshot()
            if not self.appear(rule):
                logger.warning(f'MysteryShop: [比价] 没找到{label}箭头（已在第一家？）')
                return False
            before = self.device.image.copy()
            if not self.appear_then_click(rule, interval=1.0):
                sleep(0.5)
                continue
            timer = Timer(self.SHOP_SWITCH_TIMEOUT).start()
            while 1:
                sleep(0.5)
                self.screenshot()
                diff = float(np.abs(before.astype(np.int16)
                                    - self.device.image.astype(np.int16)).mean())
                if diff >= self.SHOP_SWITCH_DIFF:
                    logger.info(f'MysteryShop: [比价] {label}切换成功（画面变化 {diff:.1f}）')
                    return True
                if timer.reached():
                    logger.warning(f'MysteryShop: [比价] {label}后画面没变，第 {attempt + 1} 次重试')
                    break
        return False

    def _at_top(self) -> bool:
        """
        商品列表是否已经在最顶部（一次都没往下滚过）。

        判据见 TOP_PROBE_BOX 的注释：看标题栏正下方那条窄带里有没有被裁切的
        「剩余购买次数」标签笔画。这个方法只是「读」，不做任何点击或滑动。
        """
        self.screenshot()
        image = self.device.image
        gray = image[:, :, 0] * 0.299 + image[:, :, 1] * 0.587 + image[:, :, 2] * 0.114
        x, y, w, h = self.TOP_PROBE_BOX
        ratio = float((gray[y:y + h, x:x + w] > self.TOP_PROBE_LEVEL).mean())
        at_top = ratio < self.TOP_PROBE_MAX
        logger.info(f'MysteryShop: 顶部检测亮像素 {ratio * 100:.2f}% -> '
                    f'{"已在顶部" if at_top else "滚动过，需要回顶"}')
        return at_top

    def _scroll_to_top(self) -> bool:
        """
        把商品列表拉回最顶部，返回是否确认已在顶部。

        **这里绝不能用「滑一次试试」的做法**：这个界面在已经到顶时继续下拉等于
        下拉关闭，会一级级退出（商店 -> 商城 -> 庭院，23:17 真机踩过）。
        所以每次上滑之前都先用 _at_top() 确认真的滚过，读数说在顶部就一次都不滑。
        """
        for i in range(self.SCROLL_TOP_MAX):
            if self._at_top():
                if i:
                    logger.info(f'MysteryShop: 上滑 {i} 次回到商品顶部')
                return True
            logger.info(f'MysteryShop: 商品列表不在顶部，上滑回顶（第 {i + 1} 次）')
            self._swipe_and_diff(self.S_MS_SCROLL_UP)
            if not self._in_shop():
                # 上滑把商店关掉过（顶部继续下拉），此时后面所有点击坐标都不可信
                logger.error('MysteryShop: 上滑后离开了商店页，停止本商店')
                return False
        if self._at_top():
            logger.info(f'MysteryShop: 上滑 {self.SCROLL_TOP_MAX} 次回到商品顶部')
            return True
        logger.warning(f'MysteryShop: 上滑 {self.SCROLL_TOP_MAX} 次仍没回到顶部')
        return False

    def _scroll_down_one_page(self) -> bool:
        """
        下滑一大步（见 ms_scroll_down 的 roi：y 轴跨度约 390~470 像素）。
        画面几乎没变化就说明已经到底（或本来只有一屏），返回 False。

        实测阈值标定（16416）：有效滑动画面平均像素差 21.6，到底后再滑只有 0.3~0.6，
        差 40 倍，取 5.0 两边都不会误判。
        """
        if self._swipe_and_diff(self.S_MS_SCROLL_DOWN) < self.SCROLL_MIN_DIFF:
            logger.info('MysteryShop: 已经到底，没有第二屏')
            return False
        return True

    def _swipe_and_diff(self, swipe) -> float:
        """执行一次滑动，返回滑动前后画面的平均像素差（0 表示画面没动）。"""
        self.screenshot()
        before = self.device.image.copy()
        self.swipe(swipe)
        sleep(1.5)
        self.screenshot()
        diff = float(np.abs(before.astype(np.int16) - self.device.image.astype(np.int16)).mean())
        logger.info(f'MysteryShop: {swipe.name} 后画面变化 {diff:.1f}')
        return diff

    def _shop_level(self, retry: int = 3) -> int:
        """
        读神秘商人等级。Digit 模式读不到时返回 0（见 module/ocr/sub_ocr.py:133），
        而等级最低是 1，所以 0 一律当「本次读取失败」，不能当成「等级 0」。
        """
        for _ in range(retry):
            self.screenshot()
            level = self.O_MS_LEVEL.ocr(self.device.image, log=False)
            if isinstance(level, int) and level > 0:
                return level
            sleep(0.5)
        return 0

    def _left_times(self, index: int, retry: int = 3):
        """
        读第 index 格的「剩余购买次数」。读不到返回 None —— 绝不返回 0，
        否则会和「已售罄」混为一谈（.codebuddy/rules/11-task-code-patterns.md 第 9 条）。
        """
        ocr = getattr(self, f'O_MS_LEFT_{index}')
        for _ in range(retry):
            self.screenshot()
            text = ocr.ocr(self.device.image, log=False)
            if isinstance(text, str):
                match = re.search(r'(\d+)', text)
                if match:
                    return int(match.group(1))
            sleep(0.5)
        return None

    def buy_coin_items(self, shop_config: ShopConfig = None):
        """
        买金币标价的道具，用来把神秘商人等级顶上去。

        等级开关由 run() 进店时读的那一次决定（自己的商店页），这里不再重读：
        好友商店里买金币道具同样能提升等级，所以这个值对每个商店都有效；
        而好友页显示的等级是分享者的，读了只会干扰判断。
        """
        max_level = self.COIN_MAX_LEVEL
        level = getattr(self, 'coin_level', 0)
        if level <= 0:
            logger.debug('MysteryShop: 没有可用的神秘商人等级，跳过金币道具')
            return 0
        if level >= max_level:
            logger.info(f'MysteryShop: 神秘商人 Lv{level} 已达目标 {max_level} 级，本任务不买金币道具')
            return 0

        logger.hr('Buy coin items', 3)
        # 只扫当前这一屏：两屏的遍历由 run_shop() 负责，这里不自己滑动
        coins = self._coin_slots()
        if not coins:
            logger.info('MysteryShop: 当前屏没有金币道具，跳过')
            return 0

        bought = 0
        self.popup_stuck = False
        self.coin_shortage = False
        for index in coins:
            left = self._left_times(index)
            if left is None:
                logger.warning(f'MysteryShop: 第{index}格剩余次数读不到，跳过')
                continue
            if left <= 0:
                logger.info(f'MysteryShop: 第{index}格已售罄，跳过')
                continue
            logger.info(f'MysteryShop: 第{index}格为金币道具，剩余 {left} 次')
            if self._buy_coin_one(index):
                bought += 1
            # 同一 control_name 连点会被判 GameTooManyClickError（≥10 次），每格结束清一次
            self.device.click_record_clear()
            if self.popup_stuck:
                # 弹窗关不掉时后面每一格的坐标都会落在弹窗上，及时收手
                logger.warning('MysteryShop: 弹窗关不掉，本商店剩下的格子不再买')
                break
            if self.coin_shortage:
                # 金币不足：这个号换不出更多金币，后面每一格都会卡同一个窗口，直接结束
                logger.warning('MysteryShop: 金币不足，本商店剩下的金币格不再尝试')
                break

        logger.info(f'MysteryShop: 本屏金币道具购买 {bought} 件（进店时 Lv{level}）')
        return bought

    def _coin_slots(self) -> list:
        """
        截一次图，把 8 格全判一遍，返回「是金币道具」的格子编号列表（可能为空）。
        比逐格判断省 7 次截图。
        """
        self.screenshot()
        image = self.device.image
        return [i for i in range(1, 9) if self._is_coin_in(image, i)]

    def _is_coin_in(self, image, index: int) -> bool:
        """
        在给定截图上判断第 index 格是不是金币道具，只看价格牌左侧的金币图标。

        模板分区分度足够：实测金币格 0.975~1.000，勾玉格 0.27~0.40（16416 Lv4 与
        16512 Lv1 两套商店都成立）。旧版还叠了一层「价格牌红色占比 < 5%」的判据，
        结果把 16512 第 1 格（勋章 18 万金币，价格牌偏橙、红色 7.0%）误杀成勾玉道具，
        导致金币格一个都识别不到 —— 已删除，别再叠颜色判据。
        """
        return getattr(self, f'I_MS_COIN_{index}').match(image)

    def _buy_coin_one(self, index: int) -> bool:
        """
        点开第 index 格并买下 1 个。成功判据是「出现了获得奖励」。
        没出现就关弹窗，绝不在同一格反复点：
        - 出现「购买金币」（金币不足）窗口 → 点右上角 X 关掉，并中止本商店剩余金币格；
        - 其他情况 → 点弹窗左侧空白关掉。
        """
        click = getattr(self, f'C_MS_ITEM_{index}')
        self.click(click)
        sleep(1)

        # 1) 等购买弹窗出现（金币购买按钮 / 限购确认框，任一出现即认为弹窗已开）
        timer = Timer(8).start()
        while 1:
            self.screenshot()
            if self.appear(self.I_MS_COIN_BUY) or self.appear(self.I_UI_CONFIRM_SAMLL):
                break
            if timer.reached():
                logger.warning(f'MysteryShop: 第{index}格购买弹窗未出现，跳过')
                return False
            sleep(0.5)

        # 2) 有确认框先点确认，否则点购买按钮，直到奖励出现
        # 正常点一次购买，1~2 秒就出获得奖励；8 秒够用，不必等满 12 秒白耗时间
        timer = Timer(8).start()
        got_reward = False
        while 1:
            self.screenshot()
            if self.ui_reward_appear_click():
                got_reward = True
                break
            # 金币不足会弹出「购买金币」窗口。这个窗口点左侧空白关不掉，必须点右上角 X；
            # 而且它的出现说明这个号的勾玉换不成金币，继续买剩下的格子毫无意义，直接收工。
            if self.appear(self.I_MS_NO_COIN):
                logger.warning(f'MysteryShop: 第{index}格金币不足，关闭「购买金币」窗口，本次金币购买结束')
                if not self.close_coin_popup():
                    # X 都点不掉 => 窗口还盖在商品上，后续任何点击都不可信
                    self.popup_stuck = True
                self.coin_shortage = True
                return False
            if self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=1):
                timer.reset()
                continue
            if self.appear_then_click(self.I_MS_COIN_BUY, interval=2):
                timer.reset()
                continue
            if timer.reached():
                break
            sleep(0.5)

        # 3) 没出奖励（金币不足 / 已达上限等）→ 点左侧空白关闭弹窗
        if not got_reward:
            logger.warning(f'MysteryShop: 第{index}格未买到（未出现获得奖励）')
            if not self.close_popup():
                self.popup_stuck = True
        return got_reward

    def close_coin_popup(self, retry: int = 3) -> bool:
        """
        关闭「购买金币」（金币不足）弹窗 —— 只能点右上角 X（C_MS_CLOSE，938,138,41,35）。

        实测（16512 Lv1 商店）：点弹窗左侧空白**关不掉**这个窗口，弹窗照旧盖在商品上，
        随后每一格的模板匹配和点击坐标全错位，所以这里必须点 X 并确认窗口真的消失。
        判据用 I_MS_NO_COIN（窗口标题「购买金币」）是否消失。
        """
        for i in range(retry):
            self.click(self.C_MS_CLOSE)
            sleep(1)
            self.screenshot()
            if not self.appear(self.I_MS_NO_COIN):
                logger.info('MysteryShop: 「购买金币」窗口已关闭')
                return True
            logger.warning(f'MysteryShop: 「购买金币」窗口还在，再点一次 X（第 {i + 1} 次）')
        logger.error('MysteryShop: 「购买金币」窗口关不掉')
        return False

    def close_popup(self, retry: int = 3) -> bool:
        """
        点弹窗左侧空白把它关掉，并确认真的关上了。

        判据：商店页本来就没有 I_MS_COIN_BUY / I_UI_CONFIRM_SAMLL，两个都消失即弹窗已关。
        不确认的话，下一格的道具模板匹配会跑在弹窗画面上，随后的点击坐标也全错位。
        """
        # 「购买金币」窗口点空白关不掉，只能点右上角 X，交给 close_coin_popup()
        self.screenshot()
        if self.appear(self.I_MS_NO_COIN):
            return self.close_coin_popup()
        for i in range(retry):
            self.click(self.C_MS_BLANK)
            sleep(1)
            self.screenshot()
            if not self.appear(self.I_MS_COIN_BUY) and not self.appear(self.I_UI_CONFIRM_SAMLL):
                return True
            logger.warning(f'MysteryShop: 弹窗还在，再点一次空白（第 {i + 1} 次）')
        logger.error('MysteryShop: 弹窗关不掉')
        return False

    def share(self, invite_config: InviteConfig = None):
        logger.hr('Share', 3)
        if len(invite_config.friend_list_v) == 0:
            logger.info('Share is disabled')
            return
        self.ui_click(self.I_MS_SHARE, self.I_INVITE_ENSURE)
        self.invite_friends(invite_config, False, self.I_INVITE_ENSURE)

    def shop_reward(self):
        logger.info('Shop reward')
        self.screenshot()
        number = self.O_MS_RECORDS.ocr(self.device.image)
        if not isinstance(number, int):
            logger.warning('No shop reward')
            return
        logger.info(f'Shop reward {number}')
        if number >= 3:
            self.ui_get_reward(self.I_MS_REWARD_3)
            sleep(0.5)
        if number >= 5:
            self.ui_get_reward(self.I_MS_REWARD_5)
            sleep(0.5)
        if number >= 10:
            self.ui_get_reward(self.I_MS_REWARD_10)
            sleep(0.5)
        logger.info('Shop reward done')

    def next_time(self, success: bool = True):
        """
        重写：开放日（周三/周六）支持当天跑两趟。

        两个时刻都来自配置：
          time_of_mystery    第一趟，默认 00:00:00（= 开放日零点跑，与上游的实际行为一致）
          time_of_mystery_2  同一开放日的第二趟，默认 00:00:00（= 不启用，只跑一趟）

        任务结束时的判定：今天这个开放日的第二趟还没到点 → 排到今天第二趟；
        否则排到下一个开放日的第一趟。跑完第二趟自然就落到后者。

        上游的 success 分支这里不用了：它把 target_time 从 time 覆盖成 timedelta
        再去和 time(0,0,0) 比较，那个「00:00:00 表示不使用」的判断恒为 False，
        实际一直是按星期排到下一个开放日，本实现保持同样的结果。
        """
        con = self.config.mystery_shop.shop_config
        first: time = con.time_of_mystery
        second: time = con.time_of_mystery_2
        zero = time(hour=0, minute=0, second=0)
        now = datetime.now()

        if second != zero and first != zero and second <= first:
            logger.warning('MysteryShop: 第二次运行时间不晚于第一次，第二趟永远不会触发')

        # ① 今天还有第二趟没到点 → 排到今天第二趟
        if second != zero and now.weekday() in self.OPEN_WEEKDAYS:
            today_second = datetime.combine(now.date(), second)
            if now < today_second:
                logger.info(f'MysteryShop: 今天还有第二趟 {second.strftime("%H:%M:%S")}')
                self.set_next_run(task='MysteryShop', target=today_second)
                raise TaskEnd('MysteryShop')

        # ② 排到下一个开放日的第一趟
        target = self._next_open_datetime(now, first)
        logger.info(f'MysteryShop: 下次运行 {target.strftime("%Y-%m-%d %H:%M:%S")}')
        self.set_next_run(task='MysteryShop', target=target)
        raise TaskEnd('MysteryShop')

    def _next_open_datetime(self, after: datetime, target_time: time) -> datetime:
        """严格晚于 after 的、最近的开放日（周三/周六）的 target_time。"""
        for delta in range(0, 8):
            day = (after + timedelta(days=delta)).date()
            if day.weekday() in self.OPEN_WEEKDAYS:
                candidate = datetime.combine(day, target_time)
                if candidate > after:
                    return candidate
        # range(0, 8) 覆盖完整一周，必含一个开放日，正常走不到这里
        return datetime.combine((after + timedelta(days=7)).date(), target_time)


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.screenshot()

    # t.run_shop(t.config.mystery_shop.shop_config)
    t.run()



