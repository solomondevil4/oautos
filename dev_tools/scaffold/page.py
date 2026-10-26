"""
本玩法的页面定义。

⚠️ 光定义不生效：`tasks/GameUi/registry.py:82` 有个已知 bug（`for module_name in ("page")`
少个逗号），`load_all_pages()` 扫不到任何 page.py。所以 **必须被 `script_task.py` 显式 import**
才会注册 —— 全仓 21 处都是这么做的，例如 `tasks/AbyssShadows/script_task.py:19`：

    from tasks.<Name>.page import page_<snake>

`script_task.py` 里已经 `import page_<snake>`，所以本文件现在是**注册状态**。
`run()` 仍用 `goto_page(page_main)` 起步、靠 `enter()` 点 I_ENTRY，两条路并行不冲突：
导航器知道"从主界面点 I_ENTRY 能到这里"，别处就能直接 `goto_page(page_<snake>)` 跳过来。

变量名就是 page key，**不需要改 tasks/GameUi/page.py**。
每个 page 至少要有一条"从别处连进来"的边，和一条"回得去"的边，否则导航器走不到它。
"""
from tasks.@@TASK_NAME@@.assets import @@TASK_NAME@@Assets
from tasks.GameUi.page import Page, page_main
from tasks.GlobalGame.assets import GlobalGameAssets

page_@@SNAKE_NAME@@ = Page(@@TASK_NAME@@Assets.I_CHECK)

page_main.connect(
    page_@@SNAKE_NAME@@,
    @@TASK_NAME@@Assets.I_ENTRY,
    key="page_main->page_@@SNAKE_NAME@@",
)
page_@@SNAKE_NAME@@.connect(
    page_main,
    GlobalGameAssets.I_UI_BACK_YELLOW,
    key="page_@@SNAKE_NAME@@->page_main",
)
