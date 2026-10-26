#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OAS 任务规范自检（oas_lint）

用途：写完或改完 tasks/ 下的任务后跑一遍，把「和上游 65 个任务不同构」的地方揪出来。
核心价值不是"又一份文档"，而是"AI 能自己跑、自己看结果、自己改"。

用法：
    python dev_tools/oas_lint.py tasks/NewTask      # 检查单个任务（也接受多个）
    python dev_tools/oas_lint.py --all              # 全仓体检，用来校准规则
    python dev_tools/oas_lint.py --self             # 检查 dev_tools/scaffold 模板自身
    python dev_tools/oas_lint.py --all --verbose    # 逐条列出 WARN（默认折叠成一行）
    python dev_tools/oas_lint.py --all --strict     # 额外开启 S02（while 1 无 Timer）

退出码：0=无 ERROR，1=有 ERROR，2=用法错误。WARN 不计入退出码
（上游有近 200 处 while 1，若 WARN 也算失败，这脚本永远是红的，没人会信它）。

单行豁免：在对应行或上一行写  # oas-lint: disable=S01

设计约束：
- 只用标准库。绝不 import 项目代码 —— 那会拉起 GameUi -> device -> frida，
  没有设备必炸，且慢。
- 规则全部可从 AST / 正则判定。写不出判定条件的口号不进来。
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
import warnings
from pathlib import Path

# ast.parse 会对被检查的源码里的非法转义报 SyntaxWarning，和我们无关，静音掉
warnings.filterwarnings("ignore", category=SyntaxWarning)

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "tasks"
CONFIG_MODEL = ROOT / "module" / "config" / "config_model.py"
CONFIG_MENU = ROOT / "module" / "config" / "config_menu.py"
SCAFFOLD_DIR = Path(__file__).resolve().parent / "scaffold"

# tasks/ 下不是"玩法任务"的目录，不套用四件套规则
NON_TASK_DIRS = {
    "Component",   # 公共组件
    "GameUi",      # 页面与导航
    "GlobalGame",  # 全局玩法
    "Utils",       # 工具
    "Script",      # 脚本集合
    "LBS",         # 定位相关
}

# 规则白名单：{(相对路径, 规则ID)}  —— 全仓体检出现误报时往这里加，不要放宽规则本身
# 每条都写了理由；新增任务永远落在白名单之外，所以规则对它仍然生效。
WHITELIST: set[tuple[str, str]] = {
    # GotoMain 是系统任务（回主界面），没有独立配置，用的是全局配置
    ("tasks/GotoMain", "C01"),
    ("tasks/GotoMain", "M01"),
    ("tasks/GotoMain", "M02"),
    ("tasks/GotoMain", "M03"),
    # OrochiMoans 只有 assets.py + config.py，没有任务入口（只提供素材与配置）
    ("tasks/OrochiMoans", "T02"),
    ("tasks/OrochiMoans", "M03"),
    # 这两个任务依赖子 act 抛 TaskEnd，本文件确实没有 raise TaskEnd（上游既有写法）
    ("tasks/ActivityShikigami/script_task.py", "T05"),
    ("tasks/Exploration/script_task.py", "T05"),
    # 上游遗留：image.json 引用的图确实不存在（游戏改版后素材没同步），不是本次任务的问题
    ("tasks/KekkaiActivation/assets.py", "A01"),
    ("tasks/MetaDemon/assets.py", "A01"),
    ("tasks/MysteryShop/assets.py", "A01"),
    ("tasks/WantedQuests/assets.py", "A01"),
}

# 判断 except 捕获的是不是框架自定义异常：是的话说明循环靠异常退出
BUILTIN_EXC = {
    "Exception", "BaseException", "ValueError", "TypeError", "KeyError", "IndexError",
    "AttributeError", "RuntimeError", "OSError", "IOError", "StopIteration", "StopAsyncIteration",
    "TimeoutError", "ZeroDivisionError", "FileNotFoundError", "NotImplementedError",
    "AssertionError", "ArithmeticError", "UnicodeDecodeError", "SyntaxError", "KeyboardInterrupt",
}

PLACEHOLDER_RE = re.compile(r"@@[A-Z0-9_]+@@")
DIR_NAME_RE = re.compile(r"^[A-Z][A-Za-z0-9]*$")
TEXT_SUFFIXES = {".py", ".json", ".md", ".txt", ".yaml", ".yml"}


# --------------------------------------------------------------------------- #
# 基础设施
# --------------------------------------------------------------------------- #
class Issue:
    __slots__ = ("level", "code", "file", "line", "msg", "key")

    def __init__(self, level: str, code: str, file: str, line: int, msg: str, key: str = ""):
        self.level = level
        self.code = code
        self.file = file
        self.line = line
        self.msg = msg
        self.key = key or file


class Report:
    def __init__(self) -> None:
        self.issues: list[Issue] = []
        self.files = 0

    def add(self, level: str, code: str, file: str, line: int, msg: str, key: str = "") -> None:
        # 白名单在这里统一过滤，避免每条规则各写一遍判断而漏掉。
        # key 是"作用域"：按任务目录登记（如 tasks/GotoMain）也能覆盖它下面的文件。
        k = key or file
        for pk, pc in WHITELIST:
            if pc == code and (k == pk or k.startswith(pk + "/")):
                return
        self.issues.append(Issue(level, code, file, line, msg, k))

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "ERROR"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "WARN"]


def convert_to_underscore(text: str) -> str:
    """与 module/config/utils.py:161 同算法；这里复制一份，避免 import 项目代码。"""
    if "_" in text:
        return text
    text = text.replace(" ", "")
    result = ""
    for i, char in enumerate(text):
        if char.isupper():
            if i > 0 and (text[i - 1].islower() or (i < len(text) - 1 and text[i + 1].islower())):
                result += "_"
            result += char.lower()
        elif char.isdigit():
            if i > 0 and (text[i - 1].isalpha() or (i < len(text) - 1 and text[i + 1].isalpha())):
                result += "_"
            result += char
        else:
            result += char
    return result


def parse_py(path: Path, text: str | None = None):
    """解析 python 源码，语法错误返回 None（由调用方报出来）。"""
    try:
        return ast.parse(text if text is not None else path.read_text(encoding="utf-8"))
    except SyntaxError:
        return None
    except UnicodeDecodeError:
        return None


def walk_body(node) -> list:
    """遍历 while/for 的 body，但不进入嵌套的函数/类定义（避免把被子函数的 return 当出口）。"""
    stack = list(getattr(node, "body", []))
    out = []
    while stack:
        n = stack.pop(0)
        out.append(n)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(n))
    return out


def top_level_classes(tree) -> dict:
    return {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}


def class_method(cls_node, name: str):
    for n in cls_node.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def base_names(cls_node) -> set[str]:
    out = set()
    for b in cls_node.bases:
        if isinstance(b, ast.Name):
            out.add(b.id)
        elif isinstance(b, ast.Attribute):
            out.add(b.attr)
    return out


def find_inherited_run(classes: dict, cls_node):
    """沿「同文件内定义的基类」往上找 run()。Chess 的 run 在 ChessStateLoopMixin 里。"""
    seen: set[str] = set()
    queue = list(base_names(cls_node))
    while queue:
        name = queue.pop(0)
        if name in seen:
            continue
        seen.add(name)
        parent = classes.get(name)
        if parent is None:
            continue
        run = class_method(parent, "run")
        if run is not None:
            return run
        queue.extend(base_names(parent))
    return None


def disabled_lines(lines: list[str]) -> set[int]:
    """收集  # oas-lint: disable=CODE  的豁免行（本行 + 下一行）。"""
    out: dict[int, set[str]] = {}
    for idx, line in enumerate(lines, start=1):
        m = re.search(r"oas-lint:\s*disable=([A-Z0-9,\s]+)", line)
        if not m:
            continue
        codes = {c.strip() for c in m.group(1).split(",") if c.strip()}
        out.setdefault(idx, set()).update(codes)
        out.setdefault(idx + 1, set()).update(codes)
    return out  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# 规则实现
# --------------------------------------------------------------------------- #
def lint_task_dir(task_dir: Path, rep: Report, rendered: dict[str, str] | None = None,
                  name_override: str | None = None) -> None:
    """
    检查一个任务目录。

    rendered：--self 模式传进来，键是文件名、值是"占位符替换后"的源码，
    此时跳过 T08 / M 系列 / A01（模板里没有真实图片、也没登记进 config_model）。
    name_override：--self 模式下用替换后的任务名（LintSelfCheck）做类名校验。
    """
    self_mode = rendered is not None
    name = name_override or task_dir.name
    rel_dir = f"tasks/{name}"

    # ---- 目录名 ----
    if not self_mode and not DIR_NAME_RE.match(name) and name not in NON_TASK_DIRS:
        rep.add("ERROR", "T01", rel_dir, 0, f"目录名必须大驼峰（首字母大写、只含字母数字），当前为 {name!r}")

    # 收集所有文本文件
    text_files: dict[str, str] = {}
    if self_mode:
        for fname, src in rendered.items():
            text_files[fname] = src
    else:
        if not task_dir.is_dir():
            rep.add("ERROR", "T02", rel_dir, 0, "目录不存在")
            return
        for p in sorted(task_dir.rglob("*")):
            if p.is_file() and p.suffix in TEXT_SUFFIXES and "__pycache__" not in p.parts:
                try:
                    text_files[str(p.relative_to(task_dir)).replace("\\", "/")] = p.read_text(
                        encoding="utf-8"
                    )
                except (UnicodeDecodeError, OSError):
                    continue

    rep.files += len(text_files)

    # ---- T08 模板占位符没替换 ----
    if not self_mode:
        for fname, src in text_files.items():
            m = PLACEHOLDER_RE.search(src)
            if m:
                line = src[: m.start()].count("\n") + 1
                rep.add("ERROR", "T08", f"{rel_dir}/{fname}", line,
                        f"残留模板占位符 {m.group(0)}，说明没替换干净")

    # ---- T10：import 了自己的 assets.py，但那个文件不存在 ----
    # 典型场景：照 dev_tools/scaffold 复制出四件套后直接启动 -> ModuleNotFoundError。
    # assets.py 是 dev_tools/assets_extract.py 由 image.json 生成的产物（不能手写），
    # 忘了跑生成器时目录看起来齐全、其它规则也全都沉默，但任务一启动就崩。
    # 只查自引用（tasks.<本任务>.assets）；跨任务引用（如 AssistBattle 用 WantedQuests 的素材）不查。
    if not self_mode and not (task_dir / "assets.py").exists():
        own_assets = re.compile(rf"^tasks\.{re.escape(name)}\.assets$")
        for fname, src in text_files.items():
            if not fname.endswith(".py"):
                continue
            tree = parse_py(task_dir / fname, src)
            if tree is None:
                continue
            for node in ast.walk(tree):
                mod = None
                if isinstance(node, ast.ImportFrom) and not node.level and node.module:
                    mod = node.module
                elif isinstance(node, ast.Import):
                    mod = next((a.name for a in node.names if own_assets.match(a.name)), None)
                if mod and own_assets.match(mod):
                    rep.add("ERROR", "T10", f"{rel_dir}/{fname}", node.lineno,
                            f"import 了 tasks.{name}.assets，但 {rel_dir}/assets.py 不存在；"
                            f"先跑 dev_tools/assets_extract.py 由 image.json 生成它，"
                            f"否则任务一启动就 ModuleNotFoundError")

    # ---- T11：page.py 存在，但同任务里没有任何文件 import 它 ----
    # registry.py:82 的 for module_name in ("page") 少个逗号，load_all_pages() 扫不到任何
    # page.py，所以页面注册**只能**靠显式 import。写了 page.py 却没人 import 时，
    # 里面的 connect() 一次都不会执行，导航器永远不知道这个页面存在。
    # 全仓 29 个 page.py 全部被同任务 import，所以这条不会误报。
    if "page.py" in text_files:
        mod = f"tasks.{name}.page"
        if not any(f.endswith(".py") and f != "page.py" and mod in s
                   for f, s in text_files.items()):
            rep.add("WARN", "T11", f"{rel_dir}/page.py", 1,
                    f"page.py 存在，但同任务里没有任何文件 import {mod}；"
                    f"registry 扫不到 page.py，不显式 import 的话里面 connect() 一次都不会执行")

    # ---- T02 / T03 / T04 / T05 / T06 / T07 ----
    st_src = text_files.get("script_task.py")
    if st_src is None:
        rep.add("ERROR", "T02", rel_dir, 0, "缺少 script_task.py（任务入口文件名是硬约定）")
    else:
        st_path = f"{rel_dir}/script_task.py"
        tree = parse_py(task_dir / "script_task.py", st_src)
        if tree is None:
            rep.add("ERROR", "T02", st_path, 0,
                    "script_task.py 解析失败（语法错误）；如果文件里还有 @@ 占位符，"
                    "先把 T08 那几条改掉它自然就好了")
        else:
            classes = top_level_classes(tree)
            cls = classes.get("ScriptTask")
            if cls is None:
                rep.add("ERROR", "T03", st_path, 0, "顶层没有 class ScriptTask（类名是硬约定）")
            else:
                run = class_method(cls, "run")
                if run is None:
                    # run 允许来自同文件内的 mixin 基类：Chess 就是
                    # class ScriptTask(ChessStateLoopMixin, ChessLegacyScriptTask)，run 在 mixin 里
                    run = find_inherited_run(classes, cls)
                if run is None:
                    rep.add("ERROR", "T04", st_path, cls.lineno, "class ScriptTask 里没有 run() 方法")
                else:
                    body_nodes = walk_body(run)

                    in_run = False
                    for n in body_nodes:
                        if not isinstance(n, ast.Raise) or n.exc is None:
                            continue
                        exc = n.exc.func if isinstance(n.exc, ast.Call) else n.exc
                        if isinstance(exc, ast.Name) and exc.id == "TaskEnd":
                            in_run = True
                            break

                    in_module = any(
                        isinstance(n, ast.Raise) and n.exc is not None
                        and isinstance((n.exc.func if isinstance(n.exc, ast.Call) else n.exc), ast.Name)
                        and (n.exc.func if isinstance(n.exc, ast.Call) else n.exc).id == "TaskEnd"
                        for n in ast.walk(tree)
                    )
                    if not in_module:
                        rep.add("ERROR", "T05", st_path, run.lineno,
                                "整个文件里没有 raise TaskEnd；正常结束必须抛它，"
                                "否则 script.py 会把本次当失败（见 script.py:625）")
                    elif not in_run:
                        rep.add("WARN", "T09", st_path, run.lineno,
                                "run() 里没有 raise TaskEnd，TaskEnd 写在别的函数里；"
                                "确认 run() 的每条路径都能走到那里")

                    for n in body_nodes:
                        if isinstance(n, ast.Return) and n.value is not None:
                            rep.add("WARN", "T06", st_path, n.lineno,
                                    "run() 里 return 了一个值；上游 run() 用 raise TaskEnd 收尾，"
                                    "带值 return 容易被当成异常返回")

                    if not any(
                        isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Attribute)
                        and n.func.attr == "set_next_run"
                        for n in walk_body(run) + list(ast.iter_child_nodes(run))
                    ):
                        rep.add("WARN", "T07", st_path, run.lineno,
                                "run() 里没有 set_next_run；不设置下次运行时间会按默认排期")

    # ---- C 系列：config.py ----
    cfg_src = text_files.get("config.py")
    if cfg_src is None:
        rep.add("ERROR", "C01", rel_dir, 0, "缺少 config.py（配置类必须和任务同目录）")
    else:
        cfg_path = f"{rel_dir}/config.py"
        tree = parse_py(task_dir / "config.py", cfg_src)
        if tree is None:
            rep.add("ERROR", "C01", cfg_path, 0,
                    "config.py 解析失败（语法错误）；如果文件里还有 @@ 占位符，先修 T08")
        else:
            classes = top_level_classes(tree)
            root_cls = classes.get(name)
            if root_cls is None:
                rep.add("ERROR", "C02", cfg_path, 0,
                        f"缺少 class {name}(ConfigBase)；类名必须与目录名同名，"
                        f"否则 config_model 注册和 GUI 都找不到")
            else:
                if "ConfigBase" not in base_names(root_cls):
                    rep.add("ERROR", "C02", cfg_path, root_cls.lineno,
                            f"class {name} 的基类里没有 ConfigBase（当前 {sorted(base_names(root_cls))}）")
                # C04：必须挂 scheduler，否则 set_next_run 静默不生效
                has_sched = False
                for n in root_cls.body:
                    if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) \
                            and n.target.id == "scheduler":
                        has_sched = True
                        break
                if not has_sched:
                    rep.add("ERROR", "C04", cfg_path, root_cls.lineno,
                            f"class {name} 里没有 scheduler 字段；"
                            f"config.py 用 getattr(task_obj,'scheduler') 取，取不到就直接 return，"
                            f"表现为「任务跑完了但排期没更新」")

            # C03：子配置类的命名只是多数派（上游 40/67 用 <目录名>Config，TalismanPass 用
            # TalismanConfig），故只能提示，不能算错误。
            if name + "Config" not in classes:
                rep.add("WARN", "C03", cfg_path, 0,
                        f"没有 class {name}Config；上游 40/67 个任务用这个命名（其余如 "
                        f"TalismanPass 用 TalismanConfig），跟随多数派更省事")

            # C05：Field 缺 description
            for n in ast.walk(tree):
                if not isinstance(n, ast.Call):
                    continue
                func = n.func
                fname = func.id if isinstance(func, ast.Name) else (
                    func.attr if isinstance(func, ast.Attribute) else None
                )
                if fname != "Field":
                    continue
                # scheduler / 子配置这类只用 default_factory 的结构字段，上游一律不写 description，
                # 只对带实际默认值的标量字段要求 description（那才是 GUI 上能看到的开关）
                if any(k.arg == "default_factory" for k in n.keywords):
                    continue
                if not any(k.arg == "description" for k in n.keywords):
                    rep.add("WARN", "C05", cfg_path, n.lineno,
                            "Field(...) 没写 description；它就是 GUI 上的提示文案（args.qml 直接显示）")

    # ---- M 系列：必须在 module/config 里登记 ----
    if not self_mode:
        snake = convert_to_underscore(name)
        model_src = CONFIG_MODEL.read_text(encoding="utf-8") if CONFIG_MODEL.exists() else ""
        menu_src = CONFIG_MENU.read_text(encoding="utf-8") if CONFIG_MENU.exists() else ""

        if re.search(rf"^from tasks\.{re.escape(name)}\.config import {re.escape(name)}\s*$",
                     model_src, re.M) is None:
            rep.add("ERROR", "M01", "module/config/config_model.py", 0,
                    f"缺少 `from tasks.{name}.config import {name}`", key=rel_dir)
        if re.search(rf"^\s*{re.escape(snake)}\s*:\s*{re.escape(name)}\s*=\s*Field\(",
                     model_src, re.M) is None:
            rep.add("ERROR", "M02", "module/config/config_model.py", 0,
                    f"缺少 `{snake}: {name} = Field(default_factory={name})`；"
                    f"字段名必须是目录名的蛇形（config.py 靠 getattr(model, 蛇形名) 取）", key=rel_dir)
        if f"'{name}'" not in menu_src and f'"{name}"' not in menu_src:
            rep.add("ERROR", "M03", "module/config/config_menu.py", 0,
                    f"config_menu.py 里没有 '{name}'，GUI 任务列表里不会出现它", key=rel_dir)

    # ---- A 系列：素材 ----
    assets_src = text_files.get("assets.py")
    if assets_src is not None:
        if "automatically generated" not in "\n".join(assets_src.splitlines()[:10]):
            rep.add("WARN", "A02", f"{rel_dir}/assets.py", 1,
                    "文件头没有 'automatically generated' 标记；assets.py 是生成物，手改会被覆盖")
        if not self_mode:
            for m in re.finditer(r'file="([^"]+)"', assets_src):
                rel_img = m.group(1).replace("\\", "/").lstrip("./")
                if not (ROOT / rel_img).exists():
                    line = assets_src[: m.start()].count("\n") + 1
                    rep.add("ERROR", "A01", f"{rel_dir}/assets.py", line,
                            f"引用的图片不存在：{rel_img}（跑 dev_tools/assets_extract.py 重新生成，"
                            f"不要手改 assets.py）")

    # ---- S 系列：防死循环与硬编码（新建、改现有都要跑）----
    for fname, src in text_files.items():
        if not fname.endswith(".py"):
            continue
        lint_safety(src, f"{rel_dir}/{fname}", rep, strict=STRICT, is_self=self_mode)


def lint_safety(src: str, path: str, rep: Report, strict: bool = False, is_self: bool = False) -> None:
    tree = parse_py(Path(path), src)
    if tree is None:
        return
    dis = disabled_lines(src.splitlines())

    def ok(line: int, code: str) -> bool:
        # 白名单匹配规则与 Report.add 保持一致：既能写完整文件路径，也能写任务目录前缀
        if code in dis.get(line, set()):
            return False
        return not any(
            pc == code and (path == pk or path.startswith(pk + "/"))
            for pk, pc in WHITELIST
        )

    # S01/S02：只查 while True / while 1 这种恒真循环。
    # while round_no is not None: 这类条件驱动循环靠条件自己退出，不在检查范围。
    def _is_custom_exc(t) -> bool:
        if t is None:
            return False
        if isinstance(t, ast.Tuple):
            return any(_is_custom_exc(e) for e in t.elts)
        if isinstance(t, ast.Name):
            return t.id not in BUILTIN_EXC
        if isinstance(t, ast.Attribute):
            return t.attr not in BUILTIN_EXC
        return True

    def check_while(node: ast.While, exc_exit: bool) -> None:
        children = walk_body(node)
        exits = any(isinstance(n, (ast.Break, ast.Return, ast.Raise)) for n in children)
        has_timer = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "reached"
            for n in children
        )
        if exits or has_timer:
            if strict and not has_timer and ok(node.lineno, "S02"):
                rep.add("WARN", "S02", path, node.lineno,
                        "while 有出口但没有 Timer 兜底（上游同款写法，只在 --strict 下提示）")
            return
        if not ok(node.lineno, "S01"):
            return
        if exc_exit:
            rep.add("WARN", "S01", path, node.lineno,
                    "while True 没有 break/return/raise/Timer，出口靠被调函数抛自定义异常"
                    "再由外层 except 接住；确认那个函数真的会抛，否则就是死循环")
        else:
            rep.add("ERROR", "S01", path, node.lineno,
                    "while True 里既没有 break/return/raise，也没有 Timer(...).reached()，"
                    "是真正的死循环；上游靠设备层连点保护兜底，但纯截图空转不受保护")

    def visit(node, exc_exit: bool = False) -> None:
        if isinstance(node, ast.Try):
            custom = any(_is_custom_exc(h.type) for h in node.handlers)
            for child in ast.iter_child_nodes(node):
                visit(child, exc_exit or custom)
            return
        if isinstance(node, ast.While) and isinstance(node.test, ast.Constant) \
                and bool(node.test.value):
            check_while(node, exc_exit)
        for child in ast.iter_child_nodes(node):
            visit(child, exc_exit)

    visit(tree)

    # S03：ui_click 家族被套进外层循环
    # 按行号去重：嵌套循环时外层和内层各遍历一遍，同一行会被报两次（DailyTrifles / Dokan 实测）
    s03: dict[int, str] = {}
    for loop in [n for n in ast.walk(tree) if isinstance(n, (ast.While, ast.For))]:
        for n in walk_body(loop):
            if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Attribute):
                continue
            attr = n.func.attr
            if attr.startswith("ui_click") and attr != "ui_click_until_appear_or_timeout":
                s03.setdefault(n.lineno, attr)
    for line, attr in sorted(s03.items()):
        if ok(line, "S03"):
            rep.add("WARN", "S03", path, line,
                    f"{attr}() 外层还套了循环；它内部就是 while 1，"
                    f"同一个按钮会被反复点到触发「10 次」保护 -> GameTooManyClickError")

    # S04：硬编码坐标
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Attribute):
            continue
        if n.func.attr != "click":
            continue
        owner = n.func.value
        if not (isinstance(owner, ast.Attribute) and owner.attr == "device"):
            continue
        nums = [a for a in n.args if isinstance(a, ast.Constant) and isinstance(a.value, (int, float))]
        if len(nums) >= 2 and len(nums) == len(n.args) and ok(n.lineno, "S04"):
            rep.add("WARN", "S04", path, n.lineno,
                    "device.click(数字, 数字) 硬编码坐标；应注册成 click.json 的 C_ 素材，"
                    "这样 coord() 会在 roi 内随机取点，且名字可读")

    # S05：异常吞掉
    for n in ast.walk(tree):
        if not isinstance(n, ast.ExceptHandler):
            continue
        if n.type is None:
            if ok(n.lineno, "S05"):
                rep.add("WARN", "S05", path, n.lineno, "裸 except: 会吞掉一切，包括 KeyboardInterrupt")
        elif isinstance(n.type, ast.Name) and n.type.id == "Exception" \
                and len(n.body) == 1 and isinstance(n.body[0], ast.Pass):
            if ok(n.lineno, "S05"):
                rep.add("WARN", "S05", path, n.lineno, "except Exception: pass 静默吞异常，出问题查不出来")


# --------------------------------------------------------------------------- #
# --self：把模板占位符替换掉再检查
# --------------------------------------------------------------------------- #
def render_scaffold() -> dict[str, str]:
    task, sub, anchor = "LintSelfCheck", "ls", "ENTRY"
    out: dict[str, str] = {}
    mapping = {
        "@@TASK_NAME@@": task,
        "@@SNAKE_NAME@@": convert_to_underscore(task),
        "@@SUB@@": sub,
        "@@ANCHOR@@": anchor,
    }
    for p in sorted(SCAFFOLD_DIR.rglob("*")):
        if not p.is_file() or p.suffix not in TEXT_SUFFIXES:
            continue
        text = p.read_text(encoding="utf-8")
        for k, v in mapping.items():
            text = text.replace(k, v)
        text = PLACEHOLDER_RE.sub("X", text)  # 其余占位符一律视作已填
        out[str(p.relative_to(SCAFFOLD_DIR)).replace("\\", "/")] = text
    return out


def find_task_dirs() -> list[Path]:
    if not TASKS_DIR.is_dir():
        return []
    return [
        p for p in sorted(TASKS_DIR.iterdir())
        if p.is_dir() and p.name not in NON_TASK_DIRS and not p.name.startswith((".", "_"))
    ]


def lint_shared_dirs(rep: Report) -> None:
    """--all 时补查 tasks/ 下的公共目录（Component / GameUi / GlobalGame / Utils / Script / LBS）。

    这些目录不是"玩法任务"，套不了 T/C/M/A 那几组规则（没有 script_task.py、config.py），
    但里面的代码被几十个任务共用（GeneralBattle 40 个、SwitchSoul 38 个），
    S 系列的防死循环 / 连点保护反而更该查。实测 79 个文件只多 14 条 S03 WARN、零 ERROR。

    module/ 故意不在范围内：module/server/ 的常驻循环（log_stats.py:502 等）是正常写法，
    套 S01 会全是误报。
    """
    for d in sorted(TASKS_DIR.iterdir()):
        if not d.is_dir() or d.name not in NON_TASK_DIRS:
            continue
        for p in sorted(d.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            try:
                src = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            rep.files += 1
            lint_safety(src, str(p.relative_to(ROOT)).replace("\\", "/"), rep)


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
def print_report(rep: Report, verbose: bool) -> None:
    errors = rep.errors
    warnings = rep.warnings

    for i in sorted(errors, key=lambda x: (x.file, x.line, x.code)):
        loc = f"{i.file}:{i.line}" if i.line else i.file
        print(f"ERROR  {i.code}  {loc:<44} {i.msg}")

    if verbose:
        for i in sorted(warnings, key=lambda x: (x.file, x.line, x.code)):
            loc = f"{i.file}:{i.line}" if i.line else i.file
            print(f"WARN   {i.code}  {loc:<44} {i.msg}")
    elif warnings:
        by_code: dict[str, int] = {}
        for i in warnings:
            by_code[i.code] = by_code.get(i.code, 0) + 1
        detail = ", ".join(f"{k}x{v}" for k, v in sorted(by_code.items()))
        print(f"WARN   {len(warnings)} 处（{detail}）—— 加 --verbose 看明细")

    verdict = "PASS" if not errors else "FAIL"
    print(f"SUMMARY: {rep.files} files scanned, errors={len(errors)} "
          f"warnings={len(warnings)} -> {verdict}")


def main(argv: list[str]) -> int:
    global STRICT
    ap = argparse.ArgumentParser(
        prog="oas_lint", description="OAS 任务规范自检：写完或改完任务后跑一遍"
    )
    ap.add_argument("targets", nargs="*", help="任务目录，例如 tasks/NewTask（可多个）")
    ap.add_argument("--all", action="store_true", help="全仓体检（校准规则用）")
    ap.add_argument("--self", action="store_true", help="检查 dev_tools/scaffold 模板自身")
    ap.add_argument("--strict", action="store_true", help="额外开启 S02（while 1 无 Timer）")
    ap.add_argument("--verbose", action="store_true", help="逐条列出 WARN")
    args = ap.parse_args(argv)
    STRICT = args.strict

    rep = Report()

    if args.self:
        rendered = render_scaffold()
        if not rendered:
            print(f"ERROR  模板目录为空：{SCAFFOLD_DIR}", file=sys.stderr)
            return 2
        lint_task_dir(SCAFFOLD_DIR, rep, rendered=rendered, name_override="LintSelfCheck")
        print(f"[self] scaffold 模板（占位符替换为 LintSelfCheck 后检查）")
        print_report(rep, args.verbose)
        return 0 if not rep.errors else 1

    targets = list(args.targets)
    if args.all:
        targets = [str(p) for p in find_task_dirs()]

    if not targets:
        ap.print_help()
        return 2

    for t in targets:
        p = Path(t)
        if not p.is_absolute():
            p = ROOT / p
        if not p.is_dir():
            print(f"ERROR  T02  {t:<44} 目录不存在", file=sys.stderr)
            rep.add("ERROR", "T02", t, 0, "目录不存在")
            continue
        lint_task_dir(p, rep)

    if args.all:
        lint_shared_dirs(rep)

    print_report(rep, args.verbose)
    return 0 if not rep.errors else 1


STRICT = False

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    sys.exit(main(sys.argv[1:]))
