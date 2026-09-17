"""图片工具插件: 装饰 (水印 + 边框)。

执行顺序: **先加边框, 再打水印** —— 水印落在最终画布上, 所见即所得;
反过来会被边框外扩挤到偏移位置。

水印支持文字 (可带描边、平铺、旋转) 与图片 (PNG 透明图), 位置用九宫格 + 边距控制。
文字水印支持占位符: `{name}` 原文件名 `{index}` 序号 `{seed}` 生成种子 (从元数据读) `{width}` `{height}`。

**透明相关**: 圆角 / 阴影 / 圆形头像 会产生透明区域, 只有输出 png / webp 才能保留,
转 jpg / bmp 时透明区会按「压缩转换」的底色规则被压平 (默认白色)。
"""

from __future__ import annotations

import os

import ujson
from PIL import Image, ImageDraw, ImageFilter

from plugins.anr_plugin_image_tools import lsb
from plugins.anr_plugin_image_tools.common import (
    build_result,
    each_image,
    grab_meta,
    grid_anchor,
    human_size,
    load_font,
    open_image,
    output_ext,
    parse_color,
    read_png_text,
    resolve_output,
    save_image,
    save_suffix,
)
from utils.logger import logger

WATERMARK_TYPES = ["文字水印", "图片水印"]
FRAME_TYPES = ["纯色描边", "圆角", "阴影", "拍立得", "圆形头像"]

# 文字水印占位符说明 (面板说明与 README 共用)
PLACEHOLDERS = "{name} 原文件名 · {index} 序号 · {seed} 生成种子 · {width} {height} 尺寸"


# ---------------------------------------------------------------- 水印


def _extract_seed(image: Image.Image) -> str:
    """从元数据 / NovelAI 隐写里取 seed (取不到返回空串, 占位符留空而不是留 `{seed}`)。"""
    candidates: list[str] = [
        value
        for key, value in read_png_text(image).items()
        if key in ("Comment", "parameters", "Description", "prompt")
    ]
    try:
        candidates.extend(value for value in (lsb.nai_payload(image) or {}).values() if isinstance(value, str))
    except Exception:
        pass
    for raw in candidates:
        try:
            data = ujson.loads(raw)
        except Exception:
            continue
        if isinstance(data, dict) and data.get("seed") is not None:
            return str(data["seed"])
    return ""


def _substitute(template: str, src: str, index: int, image: Image.Image, seed: str) -> str:
    text = template or ""
    return (
        text.replace("{name}", os.path.splitext(os.path.basename(src))[0])
        .replace("{index}", str(index))
        .replace("{seed}", seed)
        .replace("{width}", str(image.width))
        .replace("{height}", str(image.height))
    )


def _text_watermark(text: str, values: dict) -> Image.Image:
    """把一行文字渲染成带透明底的水印贴图 (留出描边所需的内边距)。"""
    font, vector = load_font(int(values.get("size") or 32))
    if not vector:
        logger.warning("未找到可用的矢量字体, 文字水印可能无法正确显示中文")
    stroke = max(0, int(values.get("stroke_width") or 0)) if values.get("stroke") else 0
    pad = stroke + 4
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    left, top, right, bottom = probe.textbbox((0, 0), text, font=font, stroke_width=stroke)
    tile = Image.new("RGBA", (max(1, right - left) + pad * 2, max(1, bottom - top) + pad * 2), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text(
        (pad - left, pad - top),
        text,
        font=font,
        fill=parse_color(values.get("color"), (255, 255, 255)) + (255,),
        stroke_width=stroke,
        stroke_fill=parse_color(values.get("stroke_color"), (0, 0, 0)) + (255,),
    )
    return tile


def _image_watermark(path: str, canvas_width: int, values: dict) -> Image.Image:
    """读入图片水印并按「占画布宽度百分比」缩放。"""
    mark = open_image(path).convert("RGBA")
    scale = max(1, min(100, int(values.get("scale") or 20)))
    target_w = max(1, round(canvas_width * scale / 100))
    if mark.width != target_w:
        ratio = target_w / mark.width
        mark = mark.resize((target_w, max(1, round(mark.height * ratio))), Image.Resampling.LANCZOS)
    return mark


def _apply_opacity(mark: Image.Image, opacity: int) -> Image.Image:
    if opacity >= 100:
        return mark
    factor = max(0, min(100, opacity)) / 100
    out = mark.copy()
    out.putalpha(mark.split()[-1].point(lambda a: int(a * factor)))
    return out


def _rotate_mark(mark: Image.Image, angle: float) -> Image.Image:
    if not angle or abs(angle) % 360 < 0.01:
        return mark
    # Pillow 的 rotate 逆时针为正, 取反让"正数 = 顺时针"与变换面板一致
    return mark.rotate(-angle, resample=Image.Resampling.BICUBIC, expand=True)


def _apply_watermark(image: Image.Image, values: dict, src: str, index: int, steps: list[str]) -> Image.Image:
    if (values.get("wm") or "不加水印") != "加水印":
        return image
    kind = values.get("wm_type") or "文字水印"
    if kind == "图片水印":
        path = (values.get("wm_image") or "").strip()
        if not path:
            steps.append("水印: 未选择水印图片, 已跳过")
            return image
        mark = _image_watermark(path, image.width, values)
        label = f"图片水印 ({os.path.basename(path)})"
    else:
        seed = _extract_seed(image) if "{seed}" in (values.get("wm_text") or "") else ""
        text = _substitute(values.get("wm_text") or "", src, index, image, seed).strip()
        if not text:
            steps.append("水印: 文字为空, 已跳过")
            return image
        mark = _text_watermark(text, values)
        label = f"文字水印「{text}」"

    mark = _rotate_mark(mark, float(values.get("wm_rotate") or 0))
    mark = _apply_opacity(mark, int(values.get("wm_opacity") if values.get("wm_opacity") is not None else 60))

    keep_alpha = image.mode == "RGBA"
    canvas = image.convert("RGBA")
    if values.get("wm_tile"):
        gap = max(0, int(values.get("wm_gap") or 0))
        step_x, step_y = mark.width + gap, mark.height + gap
        for y in range(0, max(1, canvas.height), step_y):
            for x in range(0, max(1, canvas.width), step_x):
                canvas.alpha_composite(mark, (x, y))
        label += " · 平铺"
    else:
        margin = max(0, int(values.get("wm_margin") or 0))
        pos = values.get("wm_pos") or "右下"
        x, y = grid_anchor(pos, (max(1, canvas.width - margin * 2), max(1, canvas.height - margin * 2)), mark.size)
        canvas.alpha_composite(mark, (max(0, x + margin), max(0, y + margin)))
        label += f" · {pos} · 边距 {margin}"
    mark.close()
    if not keep_alpha and canvas.split()[-1].getextrema()[0] == 255:
        canvas = canvas.convert("RGB")  # 全不透明就没必要多带一个 alpha 通道
    steps.append(label)
    return canvas


# ---------------------------------------------------------------- 边框


def _circular(image: Image.Image, width: int, color: tuple[int, int, int]) -> Image.Image:
    """圆形头像: 居中裁成正方形后套圆形遮罩, 边框宽度 >0 时再套一圈圆环。"""
    side = min(image.width, image.height)
    left, top = (image.width - side) // 2, (image.height - side) // 2
    avatar = image.convert("RGBA").crop((left, top, left + side, top + side))
    # 4 倍超采样画遮罩再缩小, 边缘没有锯齿
    scale = 4
    mask = Image.new("L", (side * scale, side * scale), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, side * scale - 1, side * scale - 1), fill=255)
    avatar.putalpha(mask.resize((side, side), Image.Resampling.LANCZOS))
    if width <= 0:
        return avatar
    outer = side + width * 2
    ring = Image.new("L", (outer * scale, outer * scale), 0)
    draw = ImageDraw.Draw(ring)
    draw.ellipse((0, 0, outer * scale - 1, outer * scale - 1), fill=255)
    draw.ellipse(
        (width * scale, width * scale, (width + side) * scale - 1, (width + side) * scale - 1), fill=0
    )
    canvas = Image.new("RGBA", (outer, outer), color + (0,))
    canvas.putalpha(ring.resize((outer, outer), Image.Resampling.LANCZOS))
    canvas.alpha_composite(avatar, (width, width))
    return canvas


def _apply_frame(image: Image.Image, values: dict, steps: list[str]) -> Image.Image:
    if (values.get("frame") or "不加边框") != "加边框":
        return image
    style = values.get("frame_type") or "纯色描边"
    width = max(0, int(values.get("frame_width") or 0))
    color = parse_color(values.get("frame_color"), (255, 255, 255))

    if style == "圆形头像":
        out = _circular(image, width, color)
        steps.append(f"边框 圆形头像 (直径 {min(image.width, image.height)}" + (f" + 圆环 {width}px)" if width else ")"))
        return out

    if style == "圆角":
        radius = width or 24
        work = image.convert("RGBA")
        mask = Image.new("L", work.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, work.width - 1, work.height - 1), radius=radius, fill=255)
        work.putalpha(mask)
        steps.append(f"边框 圆角 (半径 {radius}px)")
        return work

    if style == "阴影":
        offset = max(2, width or 8)
        blur = max(2, width or 8)
        pad = blur  # 阴影高斯模糊向外扩散的余量
        work = image.convert("RGBA")
        canvas = Image.new("RGBA", (work.width + offset + pad * 2, work.height + offset + pad * 2), (0, 0, 0, 0))
        shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        shadow.paste(Image.new("RGBA", work.size, (0, 0, 0, 150)), (pad + offset, pad + offset))
        canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(blur)))
        canvas.alpha_composite(work, (pad, pad))
        steps.append(f"边框 阴影 (偏移 {offset}px · 模糊 {blur}px)")
        return canvas

    if style == "拍立得":
        border = width or 20
        bottom = border + max(0, int(values.get("frame_bottom") or 0))
        canvas = Image.new("RGBA", (image.width + border * 2, image.height + border + bottom), color + (255,))
        canvas.paste(image.convert("RGBA"), (border, border))
        steps.append(f"边框 拍立得 (边 {border}px · 底 {bottom}px)")
        return canvas

    # 纯色描边
    if width <= 0:
        steps.append("边框: 宽度为 0, 已跳过")
        return image
    canvas = Image.new("RGBA", (image.width + width * 2, image.height + width * 2), color + (255,))
    canvas.paste(image.convert("RGBA"), (width, width))
    steps.append(f"边框 纯色描边 ({width}px)")
    return canvas


# ---------------------------------------------------------------- 主流程


def decorate_image(src: str, values: dict, index: int) -> tuple[Image.Image, dict, list[str], bool]:
    """先加边框再打水印, 返回 (图片, 元数据快照, 步骤说明, 原图是否含隐写数据)。"""
    image = open_image(src)
    had_lsb = lsb.extract(image).get("found") or lsb.has_nai_data(image)
    if image.mode == "P":
        image = image.convert("RGBA" if "transparency" in (image.info or {}) else "RGB")
    meta = grab_meta(image)
    steps: list[str] = []
    image = _apply_frame(image, values, steps)
    image = _apply_watermark(image, values, src, index, steps)
    return image, meta, steps, had_lsb


def _configured(values: dict) -> bool:
    if (values.get("wm") or "不加水印") == "加水印":
        return True
    if (values.get("frame") or "不加边框") == "加边框":
        return True
    return False


def decorate_action(values: dict) -> dict:
    """动作: 加水印 / 加边框 (可同时)。"""
    if not _configured(values):
        raise ValueError("没有可执行的装饰: 请开启「加水印」或「加边框」")
    keep_meta = bool(values.get("keep_meta", True))
    suffix = save_suffix(values, "_deco")
    ext = output_ext(values)
    counter = {"index": 0}

    def worker(src: str):
        counter["index"] += 1
        image, meta, steps, had_lsb = decorate_image(src, values, counter["index"])
        if not steps:
            image.close()
            return [], f"{os.path.basename(src)}: 没有可执行的装饰步骤, 已跳过"
        dst = resolve_output(
            src,
            bool(values.get("overwrite")),
            suffix,
            ext=ext,
            out_dir=(values.get("out_dir") or "").strip() or None,
        )
        path, size = save_image(
            image,
            dst,
            meta if keep_meta else None,
            fmt=values.get("out_format"),
            quality=int(values.get("quality") or 95),
            png_level=int(values.get("png_level") or 6),
        )
        image.close()
        warn = " ⚠️ 原图含隐写数据, 装饰会改写像素, 隐写已失效" if had_lsb else ""
        detail = f"{os.path.basename(src)} → {os.path.basename(path)} · {' + '.join(steps)} · {human_size(size)}{warn}"
        return [path], detail

    outputs, errors, texts = each_image(
        (values.get("path") or "").strip() or None,
        values.get("image"),
        worker,
        label="装饰",
    )
    return build_result(outputs, errors, texts, action_name="装饰")
