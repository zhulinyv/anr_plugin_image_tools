"""图片工具插件: 序列帧合成动图 (GIF / 动态 WebP)。

把目录里的一组图片按**文件名自然排序**当作序列帧 (`2.png` 排在 `10.png` 前面),
合成为一张动图。只用到 Pillow, 不引入新依赖。

统一尺寸: 动图所有帧必须同尺寸, 所以先按「最大帧 / 第一帧」定基准, 再把每帧等比缩放后居中贴到
同尺寸画布上 (留白用底色补)。这样长宽比不一致的帧也不会被拉伸变形。
"""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image

from plugins.anr_plugin_image_tools.common import (
    collect_images,
    human_size,
    open_image,
    parse_color,
    sort_images,
)

ANIMATE_FORMATS = ["GIF", "WebP 动图"]
BASE_FRAMES = ["按最大帧统一", "按第一帧统一"]


def _target_size(frames: list[Image.Image], values: dict) -> tuple[int, int]:
    """统一后的帧尺寸: 基准帧尺寸再按「统一最长边」等比缩放。"""
    if (values.get("base") or "按最大帧统一") == "按第一帧统一":
        width, height = frames[0].size
    else:
        width = max(frame.width for frame in frames)
        height = max(frame.height for frame in frames)
    side = int(values.get("side") or 0)
    if side > 0 and max(width, height) != side:
        scale = side / max(width, height)
        width, height = max(1, round(width * scale)), max(1, round(height * scale))
    return width, height


def _fit_frame(frame: Image.Image, size: tuple[int, int], color: tuple[int, int, int], alpha: bool) -> Image.Image:
    """等比缩放到能放进目标尺寸, 再居中贴到同尺寸画布 (不拉伸变形)。"""
    scale = min(size[0] / frame.width, size[1] / frame.height)
    resized = frame.resize((max(1, round(frame.width * scale)), max(1, round(frame.height * scale))), Image.Resampling.LANCZOS)
    if alpha:
        canvas = Image.new("RGBA", size, (color[0], color[1], color[2], 0))
        canvas.paste(resized.convert("RGBA"), ((size[0] - resized.width) // 2, (size[1] - resized.height) // 2))
        return canvas
    canvas = Image.new("RGB", size, color)
    canvas.paste(resized.convert("RGB"), ((size[0] - resized.width) // 2, (size[1] - resized.height) // 2))
    return canvas


def build_frames(files: list[str], values: dict) -> tuple[list[Image.Image], tuple[int, int], bool]:
    """读入全部序列帧并统一尺寸, 返回 (帧列表, 画布尺寸, 是否带透明通道)。"""
    raw: list[Image.Image] = []
    try:
        raw = [open_image(src) for src in files]
        size = _target_size(raw, values)
        # 透明底只对 WebP 有意义 (GIF 的透明会被调色板量化吃掉), 这里统一按格式决定
        alpha = bool(values.get("alpha")) and (values.get("format") or "GIF") == "WebP 动图"
        color = parse_color(values.get("bg"))
        frames = [_fit_frame(frame, size, color, alpha) for frame in raw]
    finally:
        for frame in raw:
            try:
                frame.close()
            except Exception:
                pass
    return frames, size, alpha


def _save_gif(frames: list[Image.Image], dst: str, duration: int, loop: int) -> None:
    # 帧仍是 RGB/RGBA: Pillow 的 GifImagePlugin 会自动量化到自适应调色板
    frames[0].save(
        dst,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=duration,
        loop=loop,
        disposal=2,
        optimize=False,
    )


def _save_webp(frames: list[Image.Image], dst: str, duration: int, loop: int, quality: int) -> None:
    frames[0].save(
        dst,
        format="WEBP",
        save_all=True,
        append_images=frames[1:],
        duration=duration,
        loop=loop,
        quality=max(1, min(100, quality)),
        method=6,
    )


def animate_action(values: dict) -> dict:
    """动作: 序列帧合成 GIF / 动态 WebP。"""
    files = sort_images(collect_images((values.get("path") or "").strip() or None, None))
    if len(files) < 2:
        raise ValueError(f"动图至少需要 2 张序列帧 (当前 {len(files)} 张), 请选择包含图片的目录")
    fmt = values.get("format") or "GIF"
    duration = max(10, int(values.get("duration") or 100))
    loop = max(0, int(values.get("loop") or 0))

    frames, size, _alpha = build_frames(files, values)
    if values.get("boomerang"):  # 往返播放: 正序 + 去掉首尾的倒序, 衔接处不重复
        frames = frames + frames[-2:0:-1]

    root = (values.get("out_dir") or "").strip() or str(Path(files[0]).parent)
    sub = (values.get("sub_dir") or "").strip()
    target = Path(root) / sub if sub else Path(root)
    target.mkdir(parents=True, exist_ok=True)
    name = (values.get("name") or "animation").strip() or "animation"
    ext = ".gif" if fmt == "GIF" else ".webp"
    dst = target / f"{name}{ext}"

    if fmt == "GIF":
        _save_gif(frames, str(dst), duration, loop)
    else:
        _save_webp(frames, str(dst), duration, loop, int(values.get("quality") or 90))
    for frame in frames:
        try:
            frame.close()
        except Exception:
            pass

    size_bytes = os.path.getsize(dst)
    seconds = len(frames) * duration / 1000
    detail = (
        f"{len(files)} 帧{(' (+往返 ' + str(len(frames) - len(files)) + ' 帧)') if len(frames) != len(files) else ''}"
        f" → {dst.name} ({size[0]}×{size[1]}) · {duration} ms/帧 · 共 {seconds:.1f} s · "
        f"{'无限循环' if loop == 0 else f'循环 {loop} 次'} · {human_size(size_bytes)}"
    )
    return {
        "images": [str(dst)],
        "message": f"动图合成完成: {len(frames)} 帧",
        "text": f"动图合成完成: {len(frames)} 帧\n{detail}",
        "dir": str(target),
    }
