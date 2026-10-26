# 新任务骨架（scaffold）

拷贝这一套到 `tasks/<新任务名>/`，把 `@@...@@` 全部替换掉，跑一遍素材生成器，就是一个符合上游规范的任务。

## 怎么用（四步，第 3 步别漏）

```bash
# 1) 复制
cp -r dev_tools/scaffold tasks/NewTask

# 2) 替换下表 4 个占位符（只改 @@...@@，别动结构）

# 3) 生成 assets.py —— 必须做，否则任务一启动就 ModuleNotFoundError
#    它由 res/image.json 生成，不能手写；只生成这一个任务，不动其它 90 个
export PYTHONPATH=.        # Windows: set PYTHONPATH=.
python -c "from dev_tools.assets_extract import AllAssetsExtractor as A; A.work('tasks/NewTask')"

# 4) 自检
python dev_tools/oas_lint.py tasks/NewTask      # 必须 errors=0
```

第 3 步的作用：`script_task.py` 和 `page.py` 都 `from tasks.NewTask.assets import NewTaskAssets`，
但 `assets.py` 是**生成物**、不在骨架里。漏跑生成器时目录看着齐全，
旧版 lint 也全都不报错，直到启动才炸。现在会由 `T10` 直接拦下。

> 也可以直接 `python dev_tools/assets_extract.py`，但那是**全量**生成器：会重写 `tasks/` 下
> 全部 91 个 `assets.py`，还会凭空造出 `tasks/Component/QuickLoadout/assets.py`。
> 批量跑完得按 `.workbuddy/memory/MEMORY.md` 第四章的「四步还原」处理无关文件，麻烦。
> 两种写法都需要项目依赖（`numpy`、`rich`）已装好。

第 3 步跑完后仍会报 `A01`（引用的图片不存在），这是**正常的**：骨架里只有
`res/image.json`，没有真截图。把 `res_entry.png`、`res_check.png`、`res_claim.png`、
`res_finished.png` 四张图截好放进 `tasks/NewTask/res/`，A01 就消了。

## 占位符对照

| 占位符 | 替换成 | 例子 |
|---|---|---|
| `@@TASK_NAME@@` | 任务名，大驼峰，必须等于目录名 | `NewTask` |
| `@@SNAKE_NAME@@` | 上面的蛇形（小写下划线） | `new_task` |
| `@@AUTHOR@@` | 你的名字 | `runhey` |
| `@@TASK_DESC@@` | 一句话说明这个任务做什么 | `每天领日常奖励` |

`res/image.json` 里的 `imageName` 前缀是子目录名 `res_`，所以图片文件名形如
`res_entry.png`，对应代码里的素材常量 `I_ENTRY`。素材命名规则由生成器决定：
`res_entry.png` -> `I_ENTRY`，**不要手写 assets.py**（它由
`dev_tools/assets_extract.py` 生成，手改会被覆盖）。

## 唯一禁令

**只替换 `@@...@@`，不要增加或删除类和方法。**
骨架里的类名、字段名、方法结构都是被 `oas_lint` 校验过的；自己加结构容易漏掉
`raise TaskEnd`、`scheduler` 字段、config 注册这些"漏了不报错但会静默失效"的地方。

**`script_task.py` 顶部那行 `from tasks.NewTask.page import page_new_task` 也不要删。**
`tasks/GameUi/registry.py:82` 少个逗号，`load_all_pages()` 扫不到任何 `page.py`，
页面注册只能靠显式 import（全仓 29 个 `page.py` 都是这样）。删掉这行，`page.py` 里的
`connect()` 一次都不会执行，导航器永远不知道这个玩法存在。`T11` 会提醒，
但一开始就别动更省事。确实不需要自定义页面时，把 `page.py` 和这行 import 一起删掉。

## 走完四步后，lint 还剩这几条（都是要手补的真事）

| 报错 | 原因 | 怎么补 |
|---|---|---|
| `A01` ×N | 骨架只有 `image.json`，没有真截图 | 把 4 张 PNG 截好放进 `res/` |
| `M01` `M02` | 没在 `config_model.py` 登记 | 见下 |
| `M03` | 没在 `config_menu.py` 登记 | 见下 |

1. `module/config/config_model.py`：
   ```python
   from tasks.NewTask.config import NewTask
   ...
   new_task: NewTask = Field(default_factory=NewTask)
   ```
2. `module/config/config_menu.py`：任务列表里加 `'NewTask',`

改完 `config_menu.py` 需要重启 GUI 才能在界面上看到新任务。
全部补完再跑一次 `python dev_tools/oas_lint.py tasks/NewTask`，`errors=0` 才算收工。
