from module.atom.click import RuleClick
from module.atom.image import RuleImage
from module.atom.ocr import RuleOcr
from module.atom.swipe import RuleSwipe

# 注意：本文件不是 ./dev_tools/assets_extract.py 的生成物，是手写的（所以没有
# "automatically generated" 标记）。原因：这里的素材 png 分散在 tasks/AssistBattle/ab/
# 与 tasks/DailyTrifles/love/ 下，生成器只扫同目录的 png 目录，生成不出来；
# 而复制一份 png 会违反「同一语义只留一份资源」。手改本文件不会被生成器覆盖。


class FriendLikeAssets:
	"""好友界面「给指定好友点赞」用到的素材。

	png 文件沿用 tasks/AssistBattle/ab/ 与 tasks/DailyTrifles/love/ 下的**现有文件**，
	这里只重新声明一份 RuleImage/RuleOcr/RuleSwipe/RuleClick，让 FriendLike 组件不依赖
	AssistBattle 的素材类（百鬼夜行不继承 AssistBattle，拿不到 AssistBattleAssets）。

	png 不复制第二份：`friendship_up.png` 的注释明确要求全局唯一（见 DailyTrifles/assets.py），
	重复复制图片会让以后换素材时漏改一处。
	"""

	# ---- Click ----
	# 好友页顶部「好友」标签(切回好友列表;「最近」页点好友只出聊天记录,点赞无效)
	C_FRIENDS_TAB = RuleClick(roi_front=(186, 109, 12, 12), roi_back=(186, 109, 12, 12), name="friends_tab")
	# 「好友羁绊提升」弹窗右上角粉紫X的中偏左上落点区。
	# 弹窗X标定框 1147,80,27,28(中心1160,94,半径约14)，好友页红X的可点圆盘实测
	# 1155,97,43x42(中心1176,117,半径21~29)；两者在屏幕上物理重叠，只有弹窗X的左上角
	# 离圆盘够远。本框任一点距弹窗X中心<=14.1(必在图标内)、距红X圆心>=30.2(在最保守的
	# 半径29之外)，既点得中弹窗又踩不到红X。
	C_FRIENDSHIP_POPUP_CLOSE = RuleClick(roi_front=(1150, 84, 10, 9), roi_back=(1150, 84, 10, 9), name="friendship_popup_close")

	# ---- Image ----
	# 好友界面点赞图标-未点赞(白色)
	I_FRIEND_LIKE_OFF = RuleImage(roi_front=(617, 140, 28, 31), roi_back=(605, 130, 60, 80), threshold=0.8, method="Template matching", file="./tasks/AssistBattle/ab/ab_like_off.png")
	# 好友界面点赞图标-已点赞(橙黄色)
	I_FRIEND_LIKE_ON = RuleImage(roi_front=(617, 140, 28, 31), roi_back=(605, 130, 60, 80), threshold=0.8, method="Template matching", file="./tasks/AssistBattle/ab/ab_like_on.png")
	# 好友分组标题行-展开态(倒三角)，表示当前展开的是同区好友
	I_FRIEND_GROUP_EXPANDED = RuleImage(roi_front=(464, 171, 17, 10), roi_back=(450, 155, 45, 45), threshold=0.8, method="Template matching", file="./tasks/AssistBattle/ab/ab_group_expanded.png")
	# 好友分组标题行-折叠态(右三角)，表示当前展开的是跨区好友
	I_FRIEND_GROUP_FOLDED = RuleImage(roi_front=(466, 167, 11, 18), roi_back=(450, 155, 45, 45), threshold=0.8, method="Template matching", file="./tasks/AssistBattle/ab/ab_group_folded.png")
	# 「好友羁绊提升」弹窗右上角粉紫 X(27x28)。png 全局唯一一份，在 DailyTrifles 下；
	# 这里只重新声明定义，不复制图片文件。
	I_FRIENDSHIP_UP = RuleImage(roi_front=(1147, 80, 27, 28), roi_back=(1147, 80, 27, 28), threshold=0.8, method="Template matching", file="./tasks/DailyTrifles/love/friendship_up.png")

	# ---- Ocr ----
	# 好友界面左侧列表(分组标题+好友名)
	O_FRIEND_LIST = RuleOcr(roi=(140, 150, 360, 450), area=(140, 150, 360, 450), mode="Full", method="Default", keyword="", name="friend_list")
	# 好友界面右侧聊天区顶部对象名(点人后二次确认)
	O_FRIEND_CHAT_NAME = RuleOcr(roi=(600, 100, 180, 35), area=(600, 100, 180, 35), mode="Single", method="Default", keyword="", name="friend_chat_name")
	# 「好友羁绊提升」弹窗标题(OCR兜底判据;中文OCR会把「羁绊」读成「藕」或漏字,按「提升」模糊匹配)
	O_FRIENDSHIP_UP_TITLE = RuleOcr(roi=(440, 85, 380, 90), area=(440, 85, 380, 90), mode="Full", method="Default", keyword="", name="friendship_up_title")

	# ---- Swipe ----
	# 好友列表向下滚动(上滑)
	# mode 必须是 drag 不能是 default：default 是「快速甩一下」，180px 的轨迹 120ms 走完，
	# 松手后列表靠惯性再滑 3~4 屏，两次截图之间跳过约 11 行好友，那些好友永远匹配不到。
	# 11-22 实测(16384，同区 199 人)：default 12 次就到底但去重只读到 74 条，
	# drag 61 次到底、相邻两屏重叠平均 3.2 条、去重 152 条。drag 走
	# press_and_drag 通道（tasks/base_task.py:537），按住慢拖 + 松手前停顿，压惯性。
	S_FRIEND_LIST_DOWN = RuleSwipe(roi_front=(300, 480, 20, 20), roi_back=(300, 300, 20, 20), mode="drag", name="friend_list_down")
	# 好友列表向上滚动(下滑)，切组后用于把列表拉回顶部
	S_FRIEND_LIST_UP = RuleSwipe(roi_front=(300, 300, 20, 20), roi_back=(300, 480, 20, 20), mode="drag", name="friend_list_up")
	# 好友列表向上滚动-快速版(下滑)：**只用于回顶**。
	# 回顶不要求逐行覆盖，default 甩一次约 16 行，从 199 人列表底部回顶约 13 次(20 秒)；
	# 若回顶也用 drag 逐步滑，得 61 次、约 150 秒。到顶后列表不再动，
	# _friend_list_at_top 的「Q搜索」判据会立刻收手，甩过头没有副作用。
	S_FRIEND_LIST_UP_FAST = RuleSwipe(roi_front=(300, 300, 20, 20), roi_back=(300, 480, 20, 20), mode="default", name="friend_list_up_fast")
