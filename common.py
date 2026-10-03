"""图片工具插件: 公共部分 (输入收集 / 输出路径 / 图片保存与元数据搬运)。"""

from __future__ import annotations

import csv
import os
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageFont
from PIL.PngImagePlugin import PngInfo

from utils.helpers import check_stop, playsound, reset_stop
from utils.images import IMAGE_EXTS  # noqa: F401  (对外仍以 common.IMAGE_EXTS 暴露)
from utils.images import collect_images as _collect_images
from utils.images import natural_key as _natural_key
from utils.images import sort_images as _sort_images
from utils.logger import logger

# 输出格式 (插件面板可选项)
OUTPUT_FORMATS = ["保持原格式", "png", "jpg", "webp", "avif", "tiff", "bmp"]

# 扩展名 <-> Pillow 格式名
_EXT_FORMAT = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".webp": "WEBP",
    ".avif": "AVIF",
    ".bmp": "BMP",
    ".tif": "TIFF",
    ".tiff": "TIFF",
    ".gif": "GIF",
    ".ico": "ICO",
}
FORMAT_EXT = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp", "AVIF": ".avif", "BMP": ".bmp", "TIFF": ".tiff"}

# 支持有损质量参数的格式 (目标体积压缩 / 质量滑块对这些格式生效)
LOSSY_FORMATS = {"JPEG", "WEBP", "AVIF"}

# Pillow 放在 info 里的非文本键 (读 PNG 文本块时过滤, 避免把 dpi / icc / exif 当成自定义文本)
_NON_TEXT_KEYS = {
    "dpi",
    "gamma",
    "transparency",
    "icc_profile",
    "exif",
    "srgb",
    "chromaticity",
    "aspect",
    "loop",
    "duration",
    "background",
    "bits",
    "palette",
    "frames",
    "compression",
    "interlace",
    "variety",
}

# 真二进制块: 读文本块时过滤, 且不像 comment 那样可以解码成文本
_BINARY_INFO_KEYS = {
    "icc_profile",
    "icc profile",
    "exif",
    "thumbnail",
    "photoshop",
}

# 非 PNG 格式的元数据键名不统一 (JPEG 只有小写的 comment), 归一成 NovelAI 的大写键
_INFO_KEY_ALIASES = {
    "comment": "Comment",
    "description": "Description",
    "software": "Software",
    "source": "Source",
}

# 透明底合成色 (转 jpg / bmp 等不支持透明通道的格式时使用)
ALPHA_BACKGROUNDS = {"白色": (255, 255, 255), "黑色": (0, 0, 0)}

# 九宫格锚点: 位置名 -> (列, 行), 0/1/2 分别表示 左中右 与 上中下
GRID_POSITIONS = ["左上", "上中", "右上", "左中", "居中", "右中", "左下", "下中", "右下"]
_GRID_CELLS = {
    "左上": (0, 0), "上中": (1, 0), "右上": (2, 0),
    "左中": (0, 1), "居中": (1, 1), "右中": (2, 1),
    "左下": (0, 2), "下中": (1, 2), "右下": (2, 2),
}

# 九宫格偏移系数 (0=贴左/上, 1=居中, 2=贴右/下)。
# 直接按系数算, 不再每次调用都新建 {0:..,1:..,2:..} 字典 (逐图热路径)。
_ANCHOR_NUM = (0, 1, 2)
_ANCHOR_DEN = (1, 2, 1)


def grid_anchor(pos: str, container: tuple[int, int], item: tuple[int, int]) -> tuple[int, int]:
    """按九宫格把 `item` 放进 `container` 的一角/一边/正中, 返回左上角坐标。

    容器比内容还小时按 0 处理 (不返回负数), 交给调用方决定是否跳过。
    """
    col, row = _GRID_CELLS.get(pos or "居中", (1, 1))
    width, height = container
    item_w, item_h = item
    # 0 -> 0; 1 -> (W-w)//2; 2 -> W-w
    left = 0 if col == 0 else ((width - item_w) // 2 if col == 1 else width - item_w)
    top = 0 if row == 0 else ((height - item_h) // 2 if row == 1 else height - item_h)
    return left, top


def parse_color(value: str | None, default: tuple[int, int, int] = (255, 255, 255)) -> tuple[int, int, int]:
    """把面板取到的颜色 (#rgb / #rrggbb / rgb(r,g,b)) 转成 RGB 元组, 解析不了就用默认色。"""
    text = (value or "").strip()
    if text.lower().startswith("rgb(") and text.endswith(")"):
        parts = [p.strip() for p in text[4:-1].split(",")]
        if len(parts) >= 3:
            try:
                return tuple(max(0, min(255, int(float(p)))) for p in parts[:3])  # type: ignore[return-value]
            except ValueError:
                return default
    text = text.lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) == 8:  # #rrggbbaa: 忽略 alpha (补边/底色都是不透明绘制)
        text = text[:6]
    if len(text) != 6:
        return default
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return default


# 标注 / 水印字体候选 (按平台顺序探测, 都找不到就用 Pillow 内置位图字体)
FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/Deng.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


@lru_cache(maxsize=32)
def load_font(size: int) -> tuple[Any, bool]:
    """取一个能画中文的字体, 返回 (字体对象, 是否成功加载到矢量字体)。

    加载不到矢量字体时回退到 Pillow 内置位图字体 (大小不可控, 中文可能画不出来),
    调用方据此在日志里给出提示。

    带 lru_cache: 原实现每张图都要 os.path.exists + ttf 解析一次, 500 张的批处理
    就是 500 次字体文件解析。字体对象是只读使用 (ImageDraw 不会修改字体)。
    """
    for candidate in FONT_CANDIDATES:
        if os.path.exists(candidate):
            try:
                return ImageFont.truetype(candidate, max(8, int(size))), True
            except Exception:
                continue
    try:
        return ImageFont.load_default(size=max(8, int(size))), False  # type: ignore[call-arg]
    except Exception:
        return ImageFont.load_default(), False


# 自然排序 / 收集图片 统一在 utils.images (原先本文件与另外 5 处各有一份实现)
natural_key = _natural_key
sort_images = _sort_images


# ---------------------------------------------------------------- 输入 / 输出


def collect_images(path: str | None, image: str | None) -> list[str]:
    """收集待处理图片: 先单张图片, 再目录内全部图片 (目录按文件名自然排序, 去重保留顺序)。

    统一委托给 utils.images.collect_images (全项目唯一实现)。
    """
    return _collect_images(path, image, exts=IMAGE_EXTS)


def open_image(path: str) -> Image.Image:
    """打开图片并立即读入像素 (读入后才允许覆盖原文件, 避免截断还在懒加载的源文件)。"""
    image = Image.open(path)
    image.load()
    return image


def resolve_output(
    src: str,
    overwrite: bool = False,
    suffix: str = "_out",
    ext: str | None = None,
    out_dir: str | None = None,
) -> str:
    """计算输出路径: 指定目录 > 覆盖原图 > 同目录加后缀 (换格式时替换扩展名)。"""
    src_path = Path(src)
    new_ext = (ext or src_path.suffix).lower()
    if not new_ext.startswith("."):
        new_ext = "." + new_ext
    if out_dir:
        return str(Path(out_dir) / f"{src_path.stem}{suffix}{new_ext}")
    if overwrite:
        return str(src_path.with_suffix(new_ext))
    return str(src_path.with_name(f"{src_path.stem}{suffix}{new_ext}"))


def resolve_format(fmt: str | None, dst: str) -> str:
    """确定输出格式: 面板选择优先, 否则按输出扩展名推断。"""
    if fmt and fmt != "保持原格式":
        name = fmt.strip().lower()
        return "JPEG" if name in ("jpg", "jpeg") else name.upper()
    return _EXT_FORMAT.get(Path(dst).suffix.lower(), "PNG")


def human_size(size: int | float) -> str:
    """字节数转可读大小。"""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def write_csv(path: str | Path, header: list[str], rows: list[list]) -> str:
    """写 CSV (utf-8-sig: Excel 双击打开不乱码), 返回路径。父目录自动创建。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    return str(target)


def output_ext(values: dict, key: str = "out_format") -> str | None:
    """按面板的「输出格式」选择算出目标扩展名 (None = 保持原格式, 沿用源文件扩展名)。"""
    choice = (values.get(key) or "保持原格式").strip()
    if choice == "保持原格式":
        return None
    return FORMAT_EXT.get("JPEG" if choice.lower() in ("jpg", "jpeg") else choice.upper())


def save_suffix(values: dict, default: str) -> str:
    """输出后缀: 面板留空时回退到该面板的默认后缀。"""
    return (values.get("suffix") or default).strip() or default


def strip_prefix(values: dict, prefix: str) -> dict:
    """去掉面板字段前缀。

    前端按字段 id 从全局注册表取值, 多个面板不能重名 (如都叫 path 会串到别的面板),
    因此面板字段统一带前缀, 处理函数内再统一还原成无前缀的键名。
    """
    return {(key[len(prefix) :] if key.startswith(prefix) else key): value for key, value in values.items()}


# ---------------------------------------------------------------- 批量执行


# 批处理并发度: Pillow 在解码/编码时基本都会释放 GIL, 多线程能吃到多核。
# 原先是纯串行 for 循环, 300 张的批处理耗时是必要时间的 N 倍。
# 上限压得比较保守: 同时开太多会把内存 (每张解码后的位图) 顶上去。
BATCH_WORKERS = min(8, (os.cpu_count() or 4))
# 单次批处理的提示音只放一次 (并发时由主线程在收尾处放)


def each_image(path: str | None, image: str | None, worker, label: str = "处理"):
    """遍历输入图片执行 worker(src) -> (输出路径列表, 文本行)。

    返回 (输出图片, 错误信息, 文本行); 支持"停止"按钮 (每张图片前检查停止信号)。

    用有界线程池并发处理 (见 BATCH_WORKERS); 结果顺序与输入顺序一致, 保证
    提示文案/输出列表的顺序稳定。停止信号在提交每一项之前检查, 停止后不再提交新任务。
    """
    files = collect_images(path, image)
    if not files:
        raise ValueError("请提供单张图片或批处理路径")
    reset_stop()
    outputs: list[str] = []
    errors: list[str] = []
    texts: list[str] = []

    def _one(src: str):
        outs, text = worker(src)
        return outs or [], text

    stopped = False
    # 单张不折腾线程池 (省掉建池开销)
    if len(files) == 1:
        try:
            outs, text = _one(files[0])
            outputs.extend(outs)
            if text:
                texts.append(text)
        except Exception as e:
            logger.error(f"{label}失败 ({files[0]}): {e}")
            logger.opt(exception=True).debug(f"{label}失败堆栈:")
            errors.append(f"{os.path.basename(files[0])}: {e}")
        return outputs, errors, texts

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=BATCH_WORKERS, thread_name_prefix="anr-batch") as ex:
        futures = []
        for src in files:
            if check_stop():
                stopped = True
                logger.warning("已停止处理!")
                break
            futures.append((src, ex.submit(_one, src)))
        # 按提交顺序回收, 保证输出顺序 = 输入顺序
        for src, fut in futures:
            try:
                outs, text = fut.result()
                outputs.extend(outs)
                if text:
                    texts.append(text)
            except Exception as e:
                logger.error(f"{label}失败 ({src}): {e}")
                logger.opt(exception=True).debug(f"{label}失败堆栈:")
                errors.append(f"{os.path.basename(src)}: {e}")
    if stopped:
        texts.append("⏹ 已手动停止, 剩余图片未处理")
    return outputs, errors, texts


def build_result(
    outputs: list[str],
    errors: list[str],
    texts: list[str],
    *,
    action_name: str,
    text_limit: int = 30,
) -> dict:
    """把 each_image 的三元结果整理成插件动作返回值 (images + message + dir)。"""
    lines: list[str] = []
    if texts:
        shown = texts[:text_limit]
        lines.extend(shown)
        if len(texts) > text_limit:
            lines.append(f"... 另有 {len(texts) - text_limit} 项未显示")
    if errors:
        lines.append("")
        lines.append(f"❌ 失败 {len(errors)} 项:")
        lines.extend(errors[:10])
    head = f"{action_name}完成: 成功 {len(outputs)} 项" + (f", 失败 {len(errors)} 项" if errors else "")
    result: dict = {"images": outputs, "message": head}
    if lines:
        result["text"] = head + "\n" + "\n".join(lines)
    if outputs:
        result["dir"] = str(Path(outputs[-1]).parent)
        playsound("./assets/finish.mp3")
    return result


# ---------------------------------------------------------------- 元数据快照


def read_png_text(image: Image.Image) -> dict[str, str]:
    """读取 PNG 文本块 (tEXt/iTXt/zTXt); 非 PNG 回退到 info 中的文本项。

    JPEG 的 NovelAI 参数在 COM 注释段里, Pillow 暴露为 bytes 类型的 info["comment"],
    这里解码后按 NovelAI 的惯例归一成 "Comment" 键, 与 PNG 文本块走同一条通路。
    """
    data: dict[str, str] = {}
    text = getattr(image, "text", None)
    if isinstance(text, dict):
        data.update({str(k): str(v) for k, v in text.items()})
    for key, value in (image.info or {}).items():
        lower = key.lower() if isinstance(key, str) else key
        if lower in _NON_TEXT_KEYS or lower in _BINARY_INFO_KEYS:
            continue
        if isinstance(value, (bytes, bytearray)):
            try:
                value = bytes(value).decode("utf-8", "replace")
            except Exception:
                continue
        if not isinstance(value, str):
            continue
        data[_INFO_KEY_ALIASES.get(lower, key)] = value
    return data


def grab_meta(image: Image.Image) -> dict:
    """抓取源图元数据快照 (文本块 / EXIF / ICC)。

    变换 (裁剪/旋转/缩放) 会生成新对象, 元数据需在变换前抓取、保存时再写回。
    """
    info = image.info or {}
    return {
        "text": read_png_text(image),
        "exif": info.get("exif"),
        "icc": info.get("icc_profile"),
    }


def build_pnginfo(text: dict[str, str] | None) -> PngInfo:
    """把字典转成 PNG 文本块集合 (值统一转字符串, 非 ASCII 走 iTXt 由 Pillow 自动处理)。"""
    pnginfo = PngInfo()
    for key, value in (text or {}).items():
        if value is None:
            continue
        try:
            pnginfo.add_text(str(key), value if isinstance(value, str) else str(value))
        except Exception as e:
            logger.warning(f"文本块 {key} 写入失败, 已跳过: {e}")
            logger.opt(exception=True).debug("写入文本块失败堆栈:")
    return pnginfo


def flatten_alpha(image: Image.Image, background: str = "白色") -> Image.Image:
    """把带透明通道的图片合成到纯色底 (转 jpg / bmp 等格式时使用)。"""
    color = ALPHA_BACKGROUNDS.get(background or "白色", ALPHA_BACKGROUNDS["白色"])
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in (image.info or {})):
        rgba = image.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, color)
        canvas.paste(rgba, mask=rgba.split()[-1])
        return canvas
    return image.convert("RGB")


def _prepare_mode(image: Image.Image, fmt: str, background: str) -> Image.Image:
    """按目标格式规整颜色模式 (无透明通道的格式先合成底色)。"""
    if fmt in ("JPEG", "BMP"):
        return flatten_alpha(image, background)
    if image.mode == "P":
        return image.convert("RGBA" if "transparency" in (image.info or {}) else "RGB")
    if image.mode in ("CMYK", "YCbCr", "LAB", "HSV", "I", "F", "I;16"):
        return image.convert("RGB")
    if fmt in ("PNG", "WEBP", "TIFF", "AVIF") and image.mode == "LA":
        return image.convert("RGBA")
    return image


def _save_once(image: Image.Image, fmt: str, quality: int, png_level: int, meta: dict | None, out) -> None:
    """按格式写出一次 (out 可以是文件路径或 BytesIO)。"""
    kwargs: dict = {}
    text = (meta or {}).get("text")
    exif = (meta or {}).get("exif")
    icc = (meta or {}).get("icc")
    if fmt == "PNG":
        # Pillow 不会自动携带文本块, 必须显式传入 (meta 为空字典即等于清空文本块)
        if meta is not None:
            kwargs["pnginfo"] = build_pnginfo(text)
        kwargs["optimize"] = True
        kwargs["compress_level"] = max(0, min(9, int(png_level)))
    elif fmt == "JPEG":
        kwargs.update(quality=max(1, min(100, int(quality))), optimize=True, progressive=True)
    elif fmt == "WEBP":
        kwargs.update(quality=max(1, min(100, int(quality))), method=6)
    elif fmt == "AVIF":
        kwargs.update(quality=max(1, min(100, int(quality))))
    elif fmt == "TIFF":
        kwargs.update(compression="tiff_deflate")
    # 元数据: PNG / JPEG / WEBP / AVIF / TIFF 支持 exif 与 icc, BMP 不支持 (跳过)
    if fmt in ("PNG", "JPEG", "WEBP", "AVIF", "TIFF"):
        if exif:
            kwargs["exif"] = exif
        if icc:
            kwargs["icc_profile"] = icc
        elif fmt in ("JPEG", "WEBP", "AVIF"):
            kwargs["icc_profile"] = None  # Pillow 会从 info 自动带出 ICC, 显式 None 才能丢弃
    image.save(out, format=fmt, **kwargs)


def _search_quality(image: Image.Image, fmt: str, target_kb: int, png_level: int, meta: dict | None) -> int:
    """二分查找满足目标体积的最高质量 (仅对有损格式有效)。"""
    low, high = 5, 98
    best = low
    while low <= high:
        mid = (low + high) // 2
        buffer = BytesIO()
        _save_once(image, fmt, mid, png_level, meta, buffer)
        if buffer.tell() <= target_kb * 1024:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return best


def save_image(
    image: Image.Image,
    dst: str,
    meta: dict | None = None,
    *,
    fmt: str | None = None,
    quality: int = 90,
    png_level: int = 6,
    background: str = "白色",
    target_kb: int = 0,
) -> tuple[str, int]:
    """保存图片并返回 (路径, 字节数)。

    - meta: `grab_meta()` 的快照, 传 None 表示不写入任何元数据 (含清空)
    - fmt: 目标格式 (None/保持原格式 时按 dst 扩展名推断)
    - target_kb: >0 时对有损格式二分质量直到体积达标
    """
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    resolved = resolve_format(fmt, dst)
    prepared = _prepare_mode(image, resolved, background)
    if target_kb > 0:
        if resolved in LOSSY_FORMATS:
            quality = _search_quality(prepared, resolved, target_kb, png_level, meta)
            buffer = BytesIO()
            _save_once(prepared, resolved, quality, png_level, meta, buffer)
            if buffer.tell() > target_kb * 1024:
                logger.warning(f"已用最低质量仍超过目标体积 ({human_size(buffer.tell())} > {target_kb} KB)")
        else:
            logger.warning(f"{resolved} 为无损格式, 目标体积压缩不生效 (仅 jpg/webp/avif 支持)")
    _save_once(prepared, resolved, quality, png_level, meta, str(dst_path))
    size = os.path.getsize(dst_path)
    logger.success(f"已保存: {dst_path} ({human_size(size)})")
    return str(dst_path), size
