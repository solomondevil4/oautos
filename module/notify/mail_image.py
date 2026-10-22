# This Python file uses the following encoding: utf-8
"""构造带截图的邮件消息, 供 onepush 的 smtp provider 使用.

onepush 的 SMTP provider 允许直接传入构造好的 ``EmailMessage``
(``providers/smtp.py`` 的 ``msg`` 参数), 因此这里可以自由拼装
HTML 正文、内嵌图片与附件, 不依赖任何图床。
"""

import mimetypes
import os
from email.message import EmailMessage
from html import escape
from typing import Iterable

from module.logger import logger

# 一封报错邮件只带一张图: 出错那一刻的画面
DEFAULT_MAX_IMAGES = 1

# 错误现场目录里除了截图还有 log.txt, 必须只挑图片
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"}


def _is_image(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in _IMAGE_EXTS


def _expand_images(images: Iterable) -> list[str]:
    """展开图片参数, 支持文件路径、目录和它们的嵌套列表.

    目录按文件名升序展开(时间戳命名时等于从旧到新), 便于外层取"最近"若干张。
    """
    files: list[str] = []
    if not images:
        return files
    if isinstance(images, (str, bytes, os.PathLike)):
        images = [images]
    for item in images:
        if isinstance(item, (list, tuple, set)):
            files.extend(_expand_images(item))
            continue
        if not item:
            continue
        if os.path.isdir(item):
            for name in sorted(os.listdir(item)):
                path = os.path.join(item, name)
                if _is_image(name) and os.path.isfile(path):
                    files.append(path)
        elif os.path.isfile(item) and _is_image(item):
            files.append(item)
    return files


def build_email(
    title: str = "",
    content: str = "",
    images: Iterable = None,
    sender: str = None,
    to: str = None,
    max_images: int = DEFAULT_MAX_IMAGES,
) -> EmailMessage:
    """构造一封带报错截图的邮件.

    :param title: 邮件主题
    :param content: 正文文字
    :param images: 图片路径 / 目录 / 路径列表
    :param sender: 发件人, 一般填配置里的 user
    :param to: 收件人, 留空时由 provider 回落到 user
    :param max_images: 最多带几张图
    :return: 可直接交给 smtp provider 的 EmailMessage
    """
    msg = EmailMessage()
    msg["Subject"] = title or "OAS"
    if sender:
        msg["From"] = sender
    if to:
        msg["To"] = to

    text = content or title or ""
    # 先放纯文本正文, 保证任何客户端都能看到文字
    msg.set_content(text)

    files = _expand_images(images)
    if max_images and len(files) > max_images:
        files = files[-max_images:]
    if not files:
        return msg

    html = ['<div style="font-family:sans-serif">']
    if text:
        html.append(f"<p>{escape(text)}</p>")
    for index in range(len(files)):
        html.append(
            "<p><img src=\"cid:oasimg{0}\" "
            'style="max-width:100%;border:1px solid #ccc"></p>'.format(index)
        )
    html.append("</div>")
    msg.add_alternative("".join(html), subtype="html")
    # 第 2 部分即上面加的 text/html, 图片内嵌到它里面
    html_part = msg.get_payload()[1]

    for index, path in enumerate(files):
        name = os.path.basename(path)
        mime, _ = mimetypes.guess_type(path)
        subtype = (mime or "image/png").split("/")[-1]
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            logger.warning(f"Read error image failed: {path} {e}")
            continue
        # 内嵌: 手机客户端可直接在正文里看到图
        try:
            html_part.add_related(
                data,
                maintype="image",
                subtype=subtype,
                cid=f"<oasimg{index}>",
                filename=name,
            )
        except Exception as e:
            logger.warning(f"Embed error image failed: {path} {e}")
        # 附件: 客户端屏蔽内嵌图时仍然能看到
        try:
            msg.add_attachment(
                data, maintype="image", subtype=subtype, filename=name
            )
        except Exception as e:
            logger.warning(f"Attach error image failed: {path} {e}")

    return msg
