"""图片工具插件: 调色 / 滤镜 / 自动校正 / 模糊锐化降噪。

全部只用 Pillow 自带能力 (ImageEnhance / ImageFilter / ImageOps), 不引入新依赖、不加载模型,
处理是纯像素运算, 因此对整目录批处理也很快。

执行顺序: 自动校正 → 色彩调整 → 滤镜 → 模糊/锐化/降噪。
先校正再手工微调再套滤镜, 与修图软件的常规流程一致; 模糊/锐化放最后,
避免前面的滤镜把锐化痕迹放大。

注意: 调色会重写像素, 图片里的 LSB 隐写数据 (含 NovelAI 隐写) 必然失效, 结果里会给出提示。
"""

from __future__ import annotations

import os

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from plugins.anr_plugin_image_tools import lsb
from plugins.anr_plugin_image_tools.common import (
    build_result,
    each_image,
    grab_meta,
    human_size,
    open_image,
    output_ext,
    resolve_output,
    save_image,
    save_suffix,
)

# 一键滤镜 (单选: 滤镜是整体风格, 叠加多个会互相打架, 需要叠加时用「色彩调整」微调)
FILTERS = [
    "不滤镜",
    "灰度",
    "反色",
    "复古 (棕褐)",
    "冷色调",
    "暖色调",
    "高对比",
    "柔和 (低对比)",
    "褪色 (胶片)",
]

# 自动校正项 (可多选, 按此顺序依次执行)
AUTO_ITEMS = ["自动对比度", "直方图均衡", "自动白平衡"]

# 模糊 / 锐化 / 降噪方式
BLUR_MODES = ["高斯模糊", "方框模糊", "USM 锐化", "中值降噪", "高斯降噪"]


# ---------------------------------------------------------------- 通道工具


def _merge_alpha(rgb: Image.Image, original: Image.Image) -> Image.Image:
    """把处理后的 RGB 结果贴回原图的 alpha 通道 (透明图调色后不能丢掉透明区)。"""
    if original.mode == "RGBA":
        out = rgb.convert("RGBA")
        out.putalpha(original.split()[-1])
        return out
    return rgb


def _map_bands(image: Image.Image, func) -> Image.Image:
    """对每个颜色通道应用同一个 func(band), alpha 通道原样保留。"""
    if image.mode == "L":
        return func(image)
    if image.mode == "RGBA":
        r, g, b, a = image.split()
        return Image.merge("RGBA", (func(r), func(g), func(b), a))
    r, g, b = image.convert("RGB").split()
    return Image.merge("RGB", (func(r), func(g), func(b)))


def _map_rgb3(image: Image.Image, funcs: tuple) -> Image.Image:
    """RGB 三个通道分别应用不同的 func (冷暖色调这类逐通道缩放用)。"""
    alpha = image.split()[-1] if image.mode == "RGBA" else None
    r, g, b = image.convert("RGB").split()
    out = Image.merge("RGB", (funcs[0](r), funcs[1](g), funcs[2](b)))
    if alpha is not None:
        out = out.convert("RGBA")
        out.putalpha(alpha)
    return out


def _scale_lut(factor: float) -> list[int]:
    return [max(0, min(255, int(round(i * factor)))) for i in range(256)]


# ---------------------------------------------------------------- 自动校正


def _white_balance(image: Image.Image, original: Image.Image) -> Image.Image:
    """灰度世界算法的自动白平衡: 假设整图平均色应为灰, 逐通道缩放到同一均值。"""
    rgb = image.convert("RGB")
    bands = rgb.split()
    means: list[float] = []
    for band in bands:
        hist = band.histogram()
        total = sum(hist) or 1
        means.append(sum(i * count for i, count in enumerate(hist)) / total)
    gray = sum(means) / 3
    funcs = []
    for mean in means:
        if mean < 1:
            funcs.append(lambda band: band)
            continue
        lut = _scale_lut(gray / mean)
        funcs.append(lambda band, lut=lut: band.point(lut))
    out = Image.merge("RGB", tuple(f(b) for f, b in zip(funcs, bands)))
    return _merge_alpha(out, original)


def _auto(image: Image.Image, values: dict, steps: list[str]) -> Image.Image:
    if (values.get("auto") or "不校正") != "自动校正":
        return image
    items = values.get("auto_items") or []
    order = [item for item in AUTO_ITEMS if item in items]
    if not order:
        steps.append("自动校正: 没有勾选任何校正项, 已跳过")
        return image
    work = image
    for item in order:
        if item == "自动对比度":
            work = _merge_alpha(ImageOps.autocontrast(work.convert("RGB")), image)
        elif item == "直方图均衡":
            work = _merge_alpha(ImageOps.equalize(work.convert("RGB")), image)
        else:
            work = _white_balance(work, image)
    steps.append("自动校正 (" + ", ".join(order) + ")")
    return work


# ---------------------------------------------------------------- 色彩调整


def _rotate_hue(image: Image.Image, degrees: int) -> Image.Image:
    alpha = image.split()[-1] if image.mode == "RGBA" else None
    hsv = image.convert("RGB").convert("HSV")
    hue, sat, val = hsv.split()
    shift = int(round(degrees / 360 * 256))
    hue = hue.point(lambda p: (p + shift) % 256)
    out = Image.merge("HSV", (hue, sat, val)).convert("RGB")
    if alpha is not None:
        out = out.convert("RGBA")
        out.putalpha(alpha)
    return out


def _apply_gamma(image: Image.Image, gamma: float) -> Image.Image:
    """伽马校正: >1 提亮中间调, <1 压暗中间调 (黑白点不动)。"""
    value = max(0.05, min(5.0, gamma))
    lut = [max(0, min(255, int(round(255 * (i / 255) ** (1 / value))))) for i in range(256)]
    return _map_bands(image, lambda band: band.point(lut))


def _adjust(image: Image.Image, values: dict, steps: list[str]) -> Image.Image:
    """亮度 / 对比度 / 饱和度 / 色相 / 锐度 / 伽马, 参数为 0 (伽马 100) 的项直接跳过。"""
    if (values.get("adjust") or "不调整") != "调整":
        return image
    notes: list[str] = []
    work = image

    brightness = int(values.get("brightness") or 0)
    if brightness:
        work = ImageEnhance.Brightness(work).enhance(max(0.0, 1 + brightness / 100))
        notes.append(f"亮度 {brightness:+d}")
    contrast = int(values.get("contrast") or 0)
    if contrast:
        work = ImageEnhance.Contrast(work).enhance(max(0.0, 1 + contrast / 100))
        notes.append(f"对比度 {contrast:+d}")
    saturation = int(values.get("saturation") or 0)
    if saturation:
        work = ImageEnhance.Color(work).enhance(max(0.0, 1 + saturation / 100))
        notes.append(f"饱和度 {saturation:+d}")
    hue = int(values.get("hue") or 0)
    if hue:
        work = _rotate_hue(work, hue)
        notes.append(f"色相 {hue:+d}°")
    sharpness = int(values.get("sharpness") or 0)
    if sharpness:
        # ImageEnhance.Sharpness: 0 = 完全模糊, 1 = 原图, 2 = 锐化一倍
        work = ImageEnhance.Sharpness(work).enhance(max(0.0, 1 + sharpness / 100))
        notes.append(f"锐度 {sharpness:+d}")
    gamma = int(values.get("gamma") or 100)
    if gamma != 100:
        work = _apply_gamma(work, gamma / 100)
        notes.append(f"伽马 {gamma}%")

    if not notes:
        steps.append("色彩调整: 参数全为默认值, 已跳过")
        return image
    steps.append("调色 (" + ", ".join(notes) + ")")
    return work


# ---------------------------------------------------------------- 滤镜


def _filter(image: Image.Image, values: dict, steps: list[str]) -> Image.Image:
    name = values.get("filter") or "不滤镜"
    if name == "不滤镜":
        return image
    if name == "灰度":
        out = _merge_alpha(ImageOps.grayscale(image).convert("RGB"), image)
    elif name == "反色":
        out = _merge_alpha(ImageOps.invert(image.convert("RGB")), image)
    elif name == "复古 (棕褐)":
        gray = ImageOps.grayscale(image)
        out = _merge_alpha(ImageOps.colorize(gray, black=(38, 24, 12), white=(255, 238, 202)).convert("RGB"), image)
    elif name == "冷色调":
        funcs = tuple((lambda band, lut=_scale_lut(f): band.point(lut)) for f in (0.90, 1.01, 1.14))
        out = _map_rgb3(image, funcs)
    elif name == "暖色调":
        funcs = tuple((lambda band, lut=_scale_lut(f): band.point(lut)) for f in (1.12, 1.01, 0.88))
        out = _map_rgb3(image, funcs)
    elif name == "高对比":
        work = ImageEnhance.Contrast(image.convert("RGB")).enhance(1.35)
        work = ImageEnhance.Color(work).enhance(1.15)
        out = _merge_alpha(work, image)
    elif name == "柔和 (低对比)":
        work = ImageEnhance.Contrast(image.convert("RGB")).enhance(0.82)
        work = ImageEnhance.Brightness(work).enhance(1.06)
        out = _merge_alpha(work, image)
    else:  # 褪色 (胶片): 低对比 + 低饱和 + 抬黑位 (让暗部发灰) + 轻微偏暖
        work = ImageEnhance.Contrast(image.convert("RGB")).enhance(0.85)
        work = ImageEnhance.Color(work).enhance(0.55)
        lift = [min(255, int(round(20 + i * 0.92))) for i in range(256)]
        work = work.point(lift * 3)
        funcs = tuple((lambda band, lut=_scale_lut(f): band.point(lut)) for f in (1.05, 1.0, 0.94))
        out = _map_rgb3(_merge_alpha(work, image), funcs)
    steps.append(f"滤镜 {name}")
    return out


# ---------------------------------------------------------------- 模糊 / 锐化 / 降噪


def _odd(value: int, minimum: int = 3) -> int:
    """中值滤波的窗口必须是 >=3 的奇数。"""
    size = max(minimum, int(value))
    return size + 1 if size % 2 == 0 else size


def _blur(image: Image.Image, values: dict, steps: list[str]) -> Image.Image:
    if (values.get("blur") or "不处理") != "处理":
        return image
    mode = values.get("blur_mode") or "高斯模糊"
    radius = max(0.1, float(values.get("blur_radius") or 2))
    size = _odd(int(values.get("blur_size") or 3))
    alpha = image.split()[-1] if image.mode == "RGBA" else None
    rgb = image.convert("RGB")
    note = ""
    if mode == "高斯模糊":
        out = rgb.filter(ImageFilter.GaussianBlur(radius))
        note = f"半径 {radius:g}"
    elif mode == "方框模糊":
        out = rgb.filter(ImageFilter.BoxBlur(radius))
        note = f"半径 {radius:g}"
    elif mode == "USM 锐化":
        percent = max(1, int(values.get("blur_percent") or 150))
        threshold = max(0, int(values.get("blur_threshold") or 3))
        out = rgb.filter(ImageFilter.UnsharpMask(radius=radius, percent=percent, threshold=threshold))
        note = f"半径 {radius:g} · 强度 {percent}% · 阈值 {threshold}"
    elif mode == "中值降噪":
        out = rgb.filter(ImageFilter.MedianFilter(size=size))
        note = f"窗口 {size}×{size}"
    else:  # 高斯降噪: 先中值去掉椒盐噪点, 再轻微高斯抹平
        out = rgb.filter(ImageFilter.MedianFilter(size=size)).filter(ImageFilter.GaussianBlur(max(0.3, radius / 3)))
        note = f"窗口 {size}×{size} + 高斯 {max(0.3, radius / 3):.1f}"
    if alpha is not None:
        out = out.convert("RGBA")
        out.putalpha(alpha)
    steps.append(f"{mode} ({note})")
    return out


# ---------------------------------------------------------------- 主流程


def enhance_image(src: str, values: dict) -> tuple[Image.Image, dict, list[str], bool]:
    """按面板参数依次执行: 自动校正 → 色彩调整 → 滤镜 → 模糊/锐化/降噪。

    返回 (图片, 元数据快照, 步骤说明, 原图是否含隐写数据)。
    """
    image = open_image(src)
    # 一次解码同时判断两种隐写 (原先 extract + has_nai_data 各解一遍全图)
    had_lsb, _stego = lsb.has_any_stego(image)
    if image.mode == "P":
        image = image.convert("RGBA" if "transparency" in (image.info or {}) else "RGB")
    if image.mode in ("CMYK", "YCbCr", "LAB", "HSV", "I", "F", "I;16"):
        image = image.convert("RGB")
    meta = grab_meta(image)
    steps: list[str] = []
    image = _auto(image, values, steps)
    image = _adjust(image, values, steps)
    image = _filter(image, values, steps)
    image = _blur(image, values, steps)
    return image, meta, steps, had_lsb


def _configured(values: dict) -> bool:
    """是否配置了至少一项调色动作 (用于提前报错, 避免逐张图片重复失败)。"""
    if (values.get("adjust") or "不调整") == "调整":
        return True
    if (values.get("filter") or "不滤镜") != "不滤镜":
        return True
    if (values.get("auto") or "不校正") == "自动校正" and (values.get("auto_items") or []):
        return True
    if (values.get("blur") or "不处理") == "处理":
        return True
    return False


def enhance_action(values: dict) -> dict:
    """动作: 调色 / 滤镜 / 自动校正 / 模糊锐化降噪。"""
    if not _configured(values):
        raise ValueError("没有可执行的调色: 请至少开启色彩调整 / 选择滤镜 / 勾选自动校正 / 选用一种模糊锐化")
    keep_meta = bool(values.get("keep_meta", True))
    suffix = save_suffix(values, "_color")
    ext = output_ext(values)

    def worker(src: str):
        image, meta, steps, had_lsb = enhance_image(src, values)
        if not steps:
            image.close()
            return [], f"{os.path.basename(src)}: 参数对该图无需改动, 已跳过"
        warn = " ⚠️ 原图含隐写数据, 调色重写像素后已失效" if had_lsb else ""
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
        detail = f"{os.path.basename(src)} → {os.path.basename(path)} · {' + '.join(steps)} · {human_size(size)}{warn}"
        return [path], detail

    outputs, errors, texts = each_image(
        (values.get("path") or "").strip() or None,
        values.get("image"),
        worker,
        label="调色",
    )
    return build_result(outputs, errors, texts, action_name="调色")
