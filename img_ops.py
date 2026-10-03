"""图片工具插件: 图像变换 (裁剪 / 旋转 / 翻转 / 缩放) 与压缩 / 格式转换。"""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageChops, ImageOps

from plugins.anr_plugin_image_tools import lsb
from plugins.anr_plugin_image_tools.common import (
    FORMAT_EXT,
    GRID_POSITIONS,
    build_result,

    each_image,
    grab_meta,
    grid_anchor,
    human_size,
    open_image,
    output_ext,
    parse_color,
    resolve_output,
    save_image,
    save_suffix,
)

RESAMPLE_MODES = {
    "Lanczos (最清晰)": Image.Resampling.LANCZOS,
    "Bicubic": Image.Resampling.BICUBIC,
    "Bilinear": Image.Resampling.BILINEAR,
    "Nearest (像素风)": Image.Resampling.NEAREST,
    "Box": Image.Resampling.BOX,
}
ROTATE_MODES = ["不旋转", "顺时针 90°", "逆时针 90°", "180°", "任意角度"]
FLIP_MODES = ["不翻转", "水平翻转", "垂直翻转", "水平+垂直"]
RESIZE_MODES = ["不缩放", "按倍数", "指定最长边", "指定尺寸"]
CROP_MODES = ["居中", "九宫格", "指定起点"]
CROP_POSITIONS = GRID_POSITIONS
SET_SIZE_MODES = ["不调整", "按宽高", "仅改宽度", "仅改高度"]

# 定比例裁剪: 选项名 -> (宽, 高); None = 手动填宽高
CROP_RATIOS = {
    "自由尺寸": None,
    "1:1 方形": (1, 1),
    "4:5 竖版": (4, 5),
    "5:4 横版": (5, 4),
    "3:2 横版": (3, 2),
    "2:3 竖版": (2, 3),
    "4:3 横版": (4, 3),
    "3:4 竖版": (3, 4),
    "16:9 宽屏": (16, 9),
    "9:16 竖屏": (9, 16),
    "2:1 超宽": (2, 1),
    "1:2 超高": (1, 2),
}

# 自动裁边: 参考色来源
TRIM_REFS = ["边角取样", "白色", "黑色", "透明边"]
PAD_MODES = ["按比例补边", "按尺寸补边"]


def _rotate(image: Image.Image, values: dict, steps: list[str]) -> Image.Image:
    mode = values.get("rotate") or "不旋转"
    if mode == "顺时针 90°":
        steps.append("顺时针旋转 90°")
        return image.transpose(Image.Transpose.ROTATE_270)
    if mode == "逆时针 90°":
        steps.append("逆时针旋转 90°")
        return image.transpose(Image.Transpose.ROTATE_90)
    if mode == "180°":
        steps.append("旋转 180°")
        return image.transpose(Image.Transpose.ROTATE_180)
    if mode == "任意角度":
        angle = float(values.get("angle_value") or 0)
        if angle % 360 == 0:
            return image
        # Pillow 的 rotate 逆时针为正, 这里取反让"正数 = 顺时针"符合直觉
        expand = bool(values.get("rotate_expand", True))
        fill = (0, 0, 0, 0) if image.mode in ("RGBA", "LA") else (255, 255, 255)
        steps.append(f"旋转 {angle:g}°{'(扩展画布)' if expand else ''}")
        return image.rotate(-angle, resample=Image.Resampling.BICUBIC, expand=expand, fillcolor=fill)
    return image


def _flip(image: Image.Image, values: dict, steps: list[str]) -> Image.Image:
    mode = values.get("flip") or "不翻转"
    if mode == "水平翻转":
        steps.append("水平翻转")
        return image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if mode == "垂直翻转":
        steps.append("垂直翻转")
        return image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    if mode == "水平+垂直":
        steps.append("水平+垂直翻转")
        return image.transpose(Image.Transpose.ROTATE_180)
    return image


def crop_ratio(values: dict) -> tuple[int, int] | None:
    """定比例裁剪选中的比例 (宽, 高); 选「自由尺寸」返回 None。"""
    return CROP_RATIOS.get((values.get("crop_ratio") or "自由尺寸").strip())


def crop_enabled(values: dict) -> bool:
    """是否要执行裁剪。

    裁剪开关是权威 (与旋转/翻转/缩放一样由 radio 控制): 选「不裁剪」时即使宽高还留着旧值也不裁剪,
    避免面板上明明关掉了、却因为记住的历史值把图裁了。

    定比例裁剪不依赖手动宽高 (由原图尺寸与比例推出), 所以选了比例就算已配置。
    """
    if (values.get("crop") or "不裁剪") != "裁剪":
        return False
    if crop_ratio(values) is not None:
        return True
    return int(values.get("crop_w") or 0) > 0 and int(values.get("crop_h") or 0) > 0


def _crop(image: Image.Image, values: dict, steps: list[str]) -> Image.Image:
    if not crop_enabled(values):
        return image
    width, height = image.size
    ratio = crop_ratio(values)
    if ratio is not None:
        # 定比例: 取「能塞进原图的最大同比例矩形」(等比放大到贴边), 再按定位方式摆放
        rw, rh = ratio
        scale = min(width / rw, height / rh)
        crop_w, crop_h = max(1, round(rw * scale)), max(1, round(rh * scale))
        tag = f"{rw}:{rh}"
    else:
        crop_w = int(values.get("crop_w") or 0)
        crop_h = int(values.get("crop_h") or 0)
        if values.get("crop_percent"):
            crop_w, crop_h = round(width * crop_w / 100), round(height * crop_h / 100)
        tag = "手动"
    crop_w, crop_h = max(1, min(crop_w, width)), max(1, min(crop_h, height))
    crop_mode = values.get("crop_mode") or "居中"
    if crop_mode == "指定起点":
        left = max(0, min(int(values.get("crop_x") or 0), width - crop_w))
        top = max(0, min(int(values.get("crop_y") or 0), height - crop_h))
        where = f"指定起点 ({left},{top})"
    else:
        # 居中 + 九宫格: 按 3x3 九个点定位裁剪框 (九宫格的"居中"即原来的居中裁剪)
        pos = "居中" if crop_mode == "居中" else (values.get("crop_pos") or "居中")
        left, top = grid_anchor(pos, (width, height), (crop_w, crop_h))
        where = f"{crop_mode}{(' ' + pos) if crop_mode == '九宫格' else ''}"
    steps.append(f"裁剪 {crop_w}×{crop_h} @ ({left},{top}) [{where} · {tag}]")
    return image.crop((left, top, left + crop_w, top + crop_h))


def trim_enabled(values: dict) -> bool:
    """是否要执行自动裁边 (开关是权威)。"""
    return (values.get("trim") or "不裁边") == "裁边"


def _trim(image: Image.Image, values: dict, steps: list[str]) -> Image.Image:
    """自动裁掉四周的纯色 / 透明边 (容差内视为边色)。

    只按「整行整列都是边色」的外框收缩, 不处理内部空洞, 因此比阈值二值化更安全。
    """
    if not trim_enabled(values):
        return image
    ref = values.get("trim_ref") or "边角取样"
    tolerance = max(0, min(255, int(values.get("trim_tolerance") or 0)))
    if ref == "透明边":
        if "A" not in image.getbands():
            steps.append("自动裁边: 图片没有透明通道, 已跳过")
            return image
        alpha = image.convert("RGBA").split()[-1]
        if tolerance > 0:
            alpha = alpha.point(lambda p: 255 if p > tolerance else 0)
        bbox = alpha.getbbox()
    else:
        rgb = image.convert("RGB")
        if ref == "白色":
            color = (255, 255, 255)
        elif ref == "黑色":
            color = (0, 0, 0)
        else:  # 边角取样: 取四角里出现次数最多的颜色
            corners = [
                rgb.getpixel((0, 0)),
                rgb.getpixel((rgb.width - 1, 0)),
                rgb.getpixel((0, rgb.height - 1)),
                rgb.getpixel((rgb.width - 1, rgb.height - 1)),
            ]
            color = max(set(corners), key=corners.count)
        diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, color))
        bands = diff.split()
        # 逐通道取最大值 (不是转灰度求平均): 只有一个通道偏离很多时也能算作内容
        gray = ImageChops.lighter(ImageChops.lighter(bands[0], bands[1]), bands[2])
        if tolerance > 0:
            gray = gray.point(lambda p: 255 if p > tolerance else 0)
        bbox = gray.getbbox()
    if not bbox:
        steps.append("自动裁边: 整张图都是边色, 已跳过")
        return image
    pad = max(0, int(values.get("trim_pad") or 0))
    left = max(0, bbox[0] - pad)
    top = max(0, bbox[1] - pad)
    right = min(image.width, bbox[2] + pad)
    bottom = min(image.height, bbox[3] + pad)
    if (left, top, right, bottom) == (0, 0, image.width, image.height):
        steps.append("自动裁边: 没有可裁的边缘, 已跳过")
        return image
    steps.append(f"自动裁边 {image.width}×{image.height} → {right - left}×{bottom - top} ({ref})")
    return image.crop((left, top, right, bottom))


def pad_enabled(values: dict) -> bool:
    """是否要执行扩展画布 (开关是权威)。"""
    if (values.get("pad") or "不扩展") != "扩展":
        return False
    if (values.get("pad_mode") or "按比例补边") == "按比例补边":
        return int(values.get("pad_ratio_w") or 0) > 0 and int(values.get("pad_ratio_h") or 0) > 0
    return int(values.get("pad_w") or 0) > 0 and int(values.get("pad_h") or 0) > 0


def _pad(image: Image.Image, values: dict, steps: list[str]) -> Image.Image:
    """扩展画布: 把图贴到更大的画布上 (补边不缩放), 用于补成目标比例 / 目标尺寸。"""
    if not pad_enabled(values):
        return image
    width, height = image.size
    if (values.get("pad_mode") or "按比例补边") == "按比例补边":
        rw = int(values.get("pad_ratio_w") or 0)
        rh = int(values.get("pad_ratio_h") or 0)
        # 目标 = 能容纳原图的最小 rw:rh 画布 (短的那一维补边)
        target_w, target_h = width, height
        if width * rh < height * rw:
            target_w = max(width, round(height * rw / rh))
        else:
            target_h = max(height, round(width * rh / rw))
        tag = f"比例 {rw}:{rh}"
    else:
        target_w = max(width, int(values.get("pad_w") or 0))
        target_h = max(height, int(values.get("pad_h") or 0))
        tag = "指定尺寸"
    if (target_w, target_h) == (width, height):
        steps.append("扩展画布: 目标不比原图大, 已跳过")
        return image
    transparent = (values.get("pad_bg") or "自定义颜色") == "透明"
    left, top = grid_anchor(values.get("pad_pos") or "居中", (target_w, target_h), (width, height))
    if transparent:
        canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
        canvas.paste(image.convert("RGBA"), (left, top))
    else:
        canvas = Image.new("RGB", (target_w, target_h), parse_color(values.get("pad_color")))
        canvas.paste(image.convert("RGB"), (left, top))
    steps.append(f"扩展画布 {width}×{height} → {target_w}×{target_h} [{tag} · {values.get('pad_pos') or '居中'}]")
    return canvas


def _set_size(image: Image.Image, values: dict, steps: list[str]) -> Image.Image:
    """独立的长宽调整步骤: 直接在裁剪之后改到目标尺寸。

    支持「按宽高 / 仅改宽 / 仅改高」, 锁比例时其余维度按比例推导, 否则拉伸。
    """
    mode = values.get("set_size_mode") or "不调整"
    if mode == "不调整":
        return image
    width, height = image.size
    lock = bool(values.get("set_size_lock", False))
    fit = bool(values.get("set_size_keep_ratio", True))  # 锁比例时: True=适配框内, False=撑满框
    new_w = int(values.get("set_size_w") or 0)
    new_h = int(values.get("set_size_h") or 0)
    if mode == "仅改宽度":
        if new_w <= 0:
            return image
        target_w = new_w
        target_h = max(1, round(height * new_w / width)) if lock else height
    elif mode == "仅改高度":
        if new_h <= 0:
            return image
        target_h = new_h
        target_w = max(1, round(width * new_h / height)) if lock else width
    else:  # 按宽高
        if new_w <= 0 or new_h <= 0:
            return image
        if lock:
            scale = min(new_w / width, new_h / height) if fit else max(new_w / width, new_h / height)
            target_w, target_h = max(1, round(width * scale)), max(1, round(height * scale))
        else:
            target_w, target_h = new_w, new_h
    if (target_w, target_h) == (width, height):
        return image
    resample = RESAMPLE_MODES.get(values.get("resample") or "Lanczos (最清晰)", Image.Resampling.LANCZOS)
    steps.append(f"调整长宽 {width}×{height} → {target_w}×{target_h}{'(锁比例)' if lock else '(拉伸)'}")
    return image.resize((target_w, target_h), resample)


def _resize(image: Image.Image, values: dict, steps: list[str]) -> Image.Image:
    mode = values.get("resize_mode") or "不缩放"
    width, height = image.size
    target: tuple[int, int] | None = None
    if mode == "按倍数":
        factor = float(values.get("resize_factor") or 1)
        if factor > 0 and abs(factor - 1) > 1e-6:
            target = (max(1, round(width * factor)), max(1, round(height * factor)))
    elif mode == "指定最长边":
        longest = int(values.get("resize_max") or 0)
        if longest > 0 and max(width, height) != longest:
            if values.get("shrink_only", True) and max(width, height) < longest:
                return image
            scale = longest / max(width, height)
            target = (max(1, round(width * scale)), max(1, round(height * scale)))
    elif mode == "指定尺寸":
        target_w = int(values.get("resize_w") or 0)
        target_h = int(values.get("resize_h") or 0)
        if target_w > 0 and target_h > 0:
            if values.get("keep_ratio", True):
                scale = min(target_w / width, target_h / height)
                if values.get("shrink_only", False) and scale > 1:
                    scale = 1
                target = (max(1, round(width * scale)), max(1, round(height * scale)))
            else:
                target = (target_w, target_h)
    if target is None or target == (width, height):
        return image
    resample = RESAMPLE_MODES.get(values.get("resample") or "Lanczos (最清晰)", Image.Resampling.LANCZOS)
    steps.append(f"缩放 {width}×{height} → {target[0]}×{target[1]}")
    return image.resize(target, resample)


def transform_image(src: str, values: dict) -> tuple[Image.Image, dict, list[str], bool]:
    """按面板参数依次执行: EXIF 方向 → 裁剪 → 自动裁边 → 调整长宽 → 旋转 → 翻转 → 缩放 → 扩展画布。

    顺序与面板分区从上到下一致, 便于对着界面推算结果。
    返回 (图片, 元数据快照, 步骤说明, 原图是否含隐写数据)。
    """
    image = open_image(src)
    # 一次解码同时判断两种隐写 (原先 extract + has_nai_data 各解一遍全图)
    had_lsb, _stego = lsb.has_any_stego(image)
    steps: list[str] = []
    if values.get("auto_orient", True):
        orientation = image.getexif().get(0x0112)
        image = ImageOps.exif_transpose(image)
        if orientation and orientation != 1:
            steps.append(f"EXIF 方向纠正 ({orientation})")
    if image.mode == "P":
        image = image.convert("RGBA" if "transparency" in (image.info or {}) else "RGB")
    meta = grab_meta(image)
    image = _crop(image, values, steps)
    image = _set_size(image, values, steps)
    image = _rotate(image, values, steps)
    image = _flip(image, values, steps)
    image = _resize(image, values, steps)
    return image, meta, steps, had_lsb


# ---------------------------------------------------------------- 动作入口


def _configured(values: dict) -> bool:
    """是否配置了至少一项变换 (用于提前报错, 避免逐张图片重复失败)。"""
    return (
        crop_enabled(values)
        or trim_enabled(values)
        or pad_enabled(values)
        or (values.get("rotate") or "不旋转") != "不旋转"
        or (values.get("flip") or "不翻转") != "不翻转"
        or (values.get("resize_mode") or "不缩放") != "不缩放"
        or (values.get("set_size_mode") or "不调整") != "不调整"
    )


def transform_action(values: dict) -> dict:
    """动作: 应用变换 (裁剪 / 自动裁边 / 调整长宽 / 旋转 / 翻转 / 缩放 / 扩展画布)。"""
    if not _configured(values):
        raise ValueError("没有可执行的变换: 请在裁剪 / 自动裁边 / 调整长宽 / 旋转 / 翻转 / 缩放 / 扩展画布中至少配置一项")
    keep_meta = bool(values.get("keep_meta", True))
    suffix = save_suffix(values, "_edit")
    ext = output_ext(values)

    def worker(src: str):
        image, meta, steps, had_lsb = transform_image(src, values)
        if not steps:
            image.close()
            return [], f"{os.path.basename(src)}: 参数对该图无需改动, 已跳过"
        warned = " ⚠️ 原图含隐写数据, 变换后已失效" if (keep_meta and had_lsb) else ""
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
        detail = (
            f"{os.path.basename(src)} → {os.path.basename(path)} · {' + '.join(steps)} · {human_size(size)}{warned}"
        )
        return [path], detail

    outputs, errors, texts = each_image(
        (values.get("path") or "").strip() or None,
        values.get("image"),
        worker,
        label="变换",
    )
    return build_result(outputs, errors, texts, action_name="变换")


def convert_action(values: dict) -> dict:
    """动作: 压缩 / 格式转换 (可选最长边限制)。"""
    keep_meta = bool(values.get("keep_meta", True))
    suffix = save_suffix(values, "_out")
    ext = output_ext(values)
    target_kb = int(values.get("target_kb") or 0)
    max_side = int(values.get("max_side") or 0)
    thumb_on = (values.get("thumb") or "不生成") == "生成缩略图"
    thumb_side = max(16, int(values.get("thumb_side") or 512))
    thumb_dir = (values.get("thumb_dir") or "thumbs").strip() or "thumbs"
    thumb_fmt = values.get("thumb_format") or "保持原格式"

    def _make_thumb(image: Image.Image, dst: str) -> tuple[str, int]:
        """在主输出同级目录的 thumbs 子目录里生成缩略图 (不带元数据, 体积更小)。

        用 resize() 而不是 copy()+thumbnail(): 后者会先把整张全尺寸图复制一份
        (4K 图约 48MB), 而这里只需要缩小后的结果。
        """
        scale = min(thumb_side / image.width, thumb_side / image.height, 1.0)
        new_size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        thumb = image if new_size == image.size else image.resize(new_size, Image.Resampling.LANCZOS)
        t_ext = Path(dst).suffix
        if thumb_fmt != "保持原格式":
            t_ext = FORMAT_EXT.get("JPEG" if thumb_fmt.lower() in ("jpg", "jpeg") else thumb_fmt.upper(), t_ext)
        t_dst = Path(dst).parent / thumb_dir / f"{Path(dst).stem}{t_ext}"
        path, size = save_image(thumb, str(t_dst), None, fmt=thumb_fmt, quality=85)
        thumb.close()
        return path, size

    def worker(src: str):
        image = open_image(src)
        meta = grab_meta(image)
        steps: list[str] = []
        if max_side > 0 and max(image.size) > max_side:
            scale = max_side / max(image.size)
            target = (max(1, round(image.size[0] * scale)), max(1, round(image.size[1] * scale)))
            image = image.resize(target, Image.Resampling.LANCZOS)
            steps.append(f"缩放 → {target[0]}×{target[1]}")
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
            quality=int(values.get("quality") or 90),
            png_level=int(values.get("png_level") or 6),
            background=values.get("background") or "白色",
            target_kb=target_kb,
        )
        thumb_note = ""
        if thumb_on:
            _, thumb_size = _make_thumb(image, path)
            thumb_note = f" · 缩略图 {human_size(thumb_size)} → {thumb_dir}/"
        before = os.path.getsize(src)
        image.close()
        ratio = f" (原 {human_size(before)} → {size / before * 100:.0f}%)" if before else ""
        detail = f"{os.path.basename(src)} → {os.path.basename(path)} · {human_size(size)}{ratio}"
        if steps:
            detail += f" · {' + '.join(steps)}"
        return [path], detail + thumb_note

    outputs, errors, texts = each_image(
        (values.get("path") or "").strip() or None,
        values.get("image"),
        worker,
        label="压缩转换",
    )
    return build_result(outputs, errors, texts, action_name="压缩转换")
