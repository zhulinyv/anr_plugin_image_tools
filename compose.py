"""图片工具插件: 拼合 (网格拼图 / 横向长图 / 纵向长图) 与分割 (大图切块)。

与其它面板的区别在"输入 → 输出"的形态:
- 拼合: 目录里的**多张图** → **一张**结果图 (输出名固定, 不逐张加后缀)
- 分割: **一张图** → 多张切片 (每张切片单独落盘)

拼图顺序按文件名自然排序 (`2.png` 排在 `10.png` 前面), 序列帧 / 漫画分镜这类顺序敏感的素材不会错位。
只用到 Pillow, 不引入新依赖。
"""

from __future__ import annotations

import math
import os
from pathlib import Path

from PIL import Image, ImageDraw

from plugins.anr_plugin_image_tools.common import (
    collect_images,
    grab_meta,
    human_size,
    load_font,
    open_image,
    output_ext,
    parse_color,
    save_image,
    sort_images,
)
from utils.logger import logger

COMPOSE_MODES = ["网格拼图", "横向长图", "纵向长图", "分割大图"]
CELL_FITS = ["保持比例居中留白", "拉伸填满", "裁剪填满"]
STRIP_ALIGNS = ["居中", "起始对齐"]
LABEL_POSITIONS = ["左上", "上中", "右上", "左下", "下中", "右下"]


def _fit_cell(image: Image.Image, cell: tuple[int, int], fit: str, resample) -> Image.Image:
    """把单张图放进单元格: 留白 / 拉伸 / 裁剪填满。"""
    cell_w, cell_h = cell
    width, height = image.size
    if fit == "拉伸填满":
        return image.resize((cell_w, cell_h), resample)
    if fit == "裁剪填满":  # 等比放大到盖住单元格, 再从中心裁掉多出来的部分
        scale = max(cell_w / width, cell_h / height)
        resized = image.resize((max(1, round(width * scale)), max(1, round(height * scale))), resample)
        left = (resized.width - cell_w) // 2
        top = (resized.height - cell_h) // 2
        return resized.crop((left, top, left + cell_w, top + cell_h))
    scale = min(cell_w / width, cell_h / height)  # 保持比例居中留白 (不放大超过单元格)
    return image.resize((max(1, round(width * scale)), max(1, round(height * scale))), resample)


def _draw_label(canvas: Image.Image, text: str, box: tuple[int, int], pos: str, font) -> None:
    """在单元格里画文件名标注 (带半透明底色, 保证任意图上都能看清)。"""
    if not text:
        return
    draw = ImageDraw.Draw(canvas, "RGBA")
    x0, y0, x1, y1 = box
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = right - left, bottom - top
    pad = 4
    if pos in ("左上", "上中", "右上"):
        ty = y0 + pad
    else:
        ty = y1 - text_h - pad * 3
    if pos in ("左上", "左下"):
        tx = x0 + pad
    elif pos in ("上中", "下中"):
        tx = x0 + max(pad, (x1 - x0 - text_w) // 2)
    else:
        tx = x1 - text_w - pad * 2
    draw.rectangle((tx - pad, ty - pad, tx + text_w + pad, ty + text_h + pad * 2), fill=(0, 0, 0, 150))
    draw.text((tx, ty), text, font=font, fill=(255, 255, 255, 255))


def _new_canvas(size: tuple[int, int], transparent: bool, color: tuple[int, int, int]) -> Image.Image:
    if transparent:
        return Image.new("RGBA", size, (0, 0, 0, 0))
    return Image.new("RGB", size, color)


def _paste(canvas: Image.Image, image: Image.Image, box: tuple[int, int]) -> None:
    if canvas.mode == "RGBA":
        canvas.paste(image.convert("RGBA"), box)
    else:
        canvas.paste(image.convert("RGB"), box)


def build_grid(images: list[Image.Image], sources: list[str], values: dict) -> Image.Image:
    """网格拼图: 列数缺省按张数开方, 单元格边长缺省按最大图。"""
    count = len(images)
    resample = Image.Resampling.LANCZOS
    cols = int(values.get("cols") or 0)
    if cols <= 0:
        cols = max(1, math.ceil(math.sqrt(count)))
    cols = min(cols, count)
    rows = max(1, math.ceil(count / cols))
    gap = max(0, int(values.get("gap") or 0))
    fit = values.get("fit") or "保持比例居中留白"

    cell_side = int(values.get("cell") or 0)
    if cell_side > 0:
        cell = (cell_side, cell_side)
    else:
        cell = (max(img.width for img in images), max(img.height for img in images))

    canvas_w = cols * cell[0] + gap * (cols - 1)
    canvas_h = rows * cell[1] + gap * (rows - 1)
    canvas = _new_canvas((canvas_w, canvas_h), (values.get("bg_mode") or "纯色") == "透明", parse_color(values.get("bg")))

    label_on = bool(values.get("label"))
    font, vector = load_font(int(values.get("label_size") or 18))
    if label_on and not vector:
        logger.warning("未找到可用的矢量字体, 标注可能无法正确显示中文")
    for index, image in enumerate(images):
        row, col = divmod(index, cols)
        x0 = col * (cell[0] + gap)
        y0 = row * (cell[1] + gap)
        fitted = _fit_cell(image, cell, fit, resample)
        # 裁剪填满时 fitted 已经等于单元格; 其余方式居中摆放
        offset = ((cell[0] - fitted.width) // 2, (cell[1] - fitted.height) // 2)
        _paste(canvas, fitted, (x0 + offset[0], y0 + offset[1]))
        if label_on:
            _draw_label(
                canvas,
                os.path.basename(sources[index]),
                (x0, y0, x0 + cell[0], y0 + cell[1]),
                values.get("label_pos") or "左上",
                font,
            )
    return canvas


def build_strip(images: list[Image.Image], sources: list[str], values: dict, vertical: bool) -> Image.Image:
    """长图拼接: 纵向 = 统一宽度逐张往下贴, 横向 = 统一高度逐张往右贴。"""
    resample = Image.Resampling.LANCZOS
    gap = max(0, int(values.get("gap") or 0))
    side = int(values.get("strip_side") or 0)
    align = values.get("align") or "居中"

    prepared: list[Image.Image] = []
    for index, image in enumerate(images):
        base = image.width if vertical else image.height
        if side > 0 and base > 0 and base != side:
            scale = side / base
            resized = image.resize(
                (max(1, round(image.width * scale)), max(1, round(image.height * scale))), resample
            )
            resized.info["__src__"] = sources[index]  # resize 会丢掉 info, 标注需要源文件名
            prepared.append(resized)
        else:
            prepared.append(image)

    if vertical:
        canvas_w = max(img.width for img in prepared)
        canvas_h = sum(img.height for img in prepared) + gap * (len(prepared) - 1)
    else:
        canvas_w = sum(img.width for img in prepared) + gap * (len(prepared) - 1)
        canvas_h = max(img.height for img in prepared)
    canvas = _new_canvas((canvas_w, canvas_h), (values.get("bg_mode") or "纯色") == "透明", parse_color(values.get("bg")))

    label_on = bool(values.get("label"))
    font, _ = load_font(int(values.get("label_size") or 18))
    cursor = 0
    for index, image in enumerate(prepared):
        if vertical:
            box = (0 if align == "起始对齐" else (canvas_w - image.width) // 2, cursor)
        else:
            box = (cursor, 0 if align == "起始对齐" else (canvas_h - image.height) // 2)
        _paste(canvas, image, box)
        if label_on:
            _draw_label(
                canvas,
                os.path.basename(sources[index]),
                (box[0], box[1], box[0] + image.width, box[1] + image.height),
                values.get("label_pos") or "左上",
                font,
            )
        cursor += (image.height if vertical else image.width) + gap
    return canvas


def split_image(image: Image.Image, values: dict) -> list[Image.Image]:
    """把一张图按 行 × 列 切成等分块 (最后一行/列吃掉除不尽的余数, 不丢像素)。"""
    rows = max(1, int(values.get("split_rows") or 1))
    cols = max(1, int(values.get("split_cols") or 1))
    width, height = image.size
    tile_w = max(1, width // cols)
    tile_h = max(1, height // rows)
    tiles: list[Image.Image] = []
    for row in range(rows):
        for col in range(cols):
            left, top = col * tile_w, row * tile_h
            right = width if col == cols - 1 else left + tile_w
            bottom = height if row == rows - 1 else top + tile_h
            tiles.append(image.crop((left, top, right, bottom)))
    return tiles


# ---------------------------------------------------------------- 动作入口


def _out_dir(values: dict, first: str) -> Path:
    """结果目录: 指定输出目录 > 第一张图所在目录, 再拼上子目录名。"""
    root = (values.get("out_dir") or "").strip() or str(Path(first).parent)
    sub = (values.get("sub_dir") or "").strip()
    target = Path(root) / sub if sub else Path(root)
    target.mkdir(parents=True, exist_ok=True)
    return target


def compose_action(values: dict) -> dict:
    """动作: 拼合 (网格 / 长图) 与分割大图。"""
    mode = values.get("mode") or "网格拼图"
    files = sort_images(
        collect_images((values.get("path") or "").strip() or None, values.get("image"))
    )
    if not files:
        raise ValueError("请提供批处理路径 (目录) 或单张图片")

    if mode == "分割大图":
        return _split_action(files, values)
    return _merge_action(files, values, mode)


def _merge_action(files: list[str], values: dict, mode: str) -> dict:
    if len(files) < 2:
        raise ValueError(f"{mode} 至少需要 2 张图片 (当前只有 1 张), 请选择包含图片的目录")
    images: list[Image.Image] = []
    try:
        images = [open_image(src) for src in files]
        if mode == "网格拼图":
            canvas = build_grid(images, files, values)
        else:
            canvas = build_strip(images, files, values, mode == "纵向长图")
    finally:
        for image in images:
            try:
                image.close()
            except Exception:
                pass

    name = (values.get("name") or "collage").strip() or "collage"
    ext = output_ext(values) or ".png"
    dst = _out_dir(values, files[0]) / f"{name}{ext}"
    path, size = save_image(canvas, str(dst), None, fmt=values.get("out_format"), quality=int(values.get("quality") or 95))
    canvas_size = f"{canvas.width}×{canvas.height}"
    canvas.close()
    detail = f"{mode}: {len(files)} 张 → {os.path.basename(path)} ({canvas_size}) · {human_size(size)}"
    return {"images": [path], "message": f"{mode}完成: {len(files)} 张图已合成 1 张", "text": f"{mode}完成: {len(files)} 张图已合成 1 张\n{detail}", "dir": str(Path(path).parent)}


def _split_action(files: list[str], values: dict) -> dict:
    outputs: list[str] = []
    errors: list[str] = []
    texts: list[str] = []
    suffix = (values.get("split_suffix") or "_part").strip()
    ext = output_ext(values)
    keep_meta = bool(values.get("keep_meta", True))
    for src in files:
        try:
            image = open_image(src)
            meta = grab_meta(image)
            tiles = split_image(image, values)
            target = _out_dir(values, src) / Path(src).stem
            target.mkdir(parents=True, exist_ok=True)
            rows = max(1, int(values.get("split_rows") or 1))
            cols = max(1, int(values.get("split_cols") or 1))
            for index, tile in enumerate(tiles):
                row, col = divmod(index, cols)
                tile_ext = ext or Path(src).suffix
                dst = target / f"{Path(src).stem}{suffix}_{row + 1}-{col + 1}{tile_ext}"
                path, _ = save_image(
                    tile,
                    str(dst),
                    meta if keep_meta else None,
                    fmt=values.get("out_format"),
                    quality=int(values.get("quality") or 95),
                )
                tile.close()
                outputs.append(path)
            image.close()
            texts.append(f"{os.path.basename(src)} → {len(tiles)} 块 ({rows}×{cols}) · {target}")
        except Exception as e:
            logger.error(f"分割失败 ({src}): {e}")
            errors.append(f"{os.path.basename(src)}: {e}")
    if not outputs:
        raise ValueError("分割失败: " + ("; ".join(errors) if errors else "没有可处理的图片"))
    head = f"分割完成: {len(outputs)} 块" + (f", 失败 {len(errors)} 项" if errors else "")
    result: dict = {"images": outputs, "message": head, "text": head + "\n" + "\n".join(texts + errors), "dir": str(Path(outputs[-1]).parent)}
    return result
