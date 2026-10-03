"""图片工具插件: 批处理整理 (查重 / 质量筛查 / 批量重命名)。

三件事都只用到 Pillow 与标准库, 不引入新依赖、不加载模型:

- **查重**: dHash (差值感知哈希) —— 把图缩到 9×8 灰度, 逐行比较相邻像素得到 64 位指纹,
  再按汉明距离聚类。对"同一张图重新保存 / 轻微缩放 / 改质量"这类改动不敏感, 能识别内容重复;
  相比精确字节比对, 不会漏掉重编码过的副本, 也不会把两张不同的纯色图误判成一张。
- **质量筛查**: 用边缘强度标准差当清晰度指标 (越低越模糊), 另配纯色 / 过曝 / 欠曝判定,
  结果写进同一份 CSV, 方便按表挑图删。
- **批量重命名**: 模板化改名, 支持序号 / 原文件名 / 时间 / 生成参数 (seed、prompt 等) 占位符,
  默认只预览不落地, 确认无误再执行。
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageFilter

from plugins.anr_plugin_image_tools.common import (
    collect_images,
    human_size,
    open_image,
    sort_images,
    write_csv,
)
from plugins.anr_plugin_image_tools.meta import read_params
from utils.logger import logger

# 质量判定默认阈值 (面板可调)
QUALITY_ITEMS = ["模糊", "纯色", "过曝", "欠曝"]

# 重命名模板里可以用到的生成参数字段 (从元数据读)
TEMPLATE_PARAM_KEYS = ["seed", "steps", "scale", "sampler", "noise_schedule", "model", "prompt"]

# 清单 CSV 的列 (顺序即输出顺序)
MANIFEST_COLUMNS = [
    "文件",
    "路径",
    "格式",
    "宽",
    "高",
    "大小",
    "prompt",
    "uc",
    "seed",
    "steps",
    "scale",
    "sampler",
    "noise_schedule",
    "model",
]

# 从元数据里认作"生成参数"的键
MANIFEST_KEYS = [
    "prompt",
    "uc",
    "seed",
    "steps",
    "scale",
    "sampler",
    "noise_schedule",
    "model",
    "sm",
    "sm_dyn",
    "cfg_rescale",
]


# ---------------------------------------------------------------- 感知哈希


def dhash(image: Image.Image, hash_size: int = 8, gray: Image.Image | None = None) -> int:
    """差值哈希: 缩成 (hash_size+1)×hash_size 灰度后逐行比较相邻像素, 得到 64 位指纹。

    gray: 调用方已转好的灰度图 (可选); 与 quality_metrics 共用可省一次 convert("L")。
    """
    small = (gray or image.convert("L")).resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
    pixels = list(small.getdata())
    bits = 0
    index = 0
    for row in range(hash_size):
        offset = row * (hash_size + 1)
        for col in range(hash_size):
            if pixels[offset + col] > pixels[offset + col + 1]:
                bits |= 1 << index
            index += 1
    return bits


def hamming(a: int, b: int) -> int:
    """两个指纹的汉明距离 (不同位的个数), 0 = 完全一致。

    用 int.bit_count() (Python 3.10+): 原先 bin(x).count("1") 每次比较都要先构造
    一个字符串, 在 O(n×组数) 的聚类里是明显的常数开销。
    """
    return (a ^ b).bit_count()


def group_duplicates(files: list[str], threshold: int) -> tuple[list[list[str]], dict[str, str]]:
    """按 dHash 聚类: 与已有组的代表图距离 <= 阈值就归入该组, 否则自成一组的代表图。

    返回 (分组列表, 指纹字典)。只用代表图比较 (而不是组内两两比较), 结果稳定且快。
    """
    groups: list[list[str]] = []
    hashes: dict[str, str] = {}
    fingerprints: list[tuple[int, str]] = []
    for src in files:
        try:
            image = open_image(src)
            try:
                fingerprint = dhash(image)
            finally:
                image.close()
        except Exception as e:
            logger.warning(f"计算指纹失败, 已跳过 ({src}): {e}")
            logger.opt(exception=True).debug("计算指纹失败堆栈:")
            continue
        hashes[src] = f"{fingerprint:016x}"
        for index, (ref_fingerprint, _ref) in enumerate(fingerprints):
            if hamming(fingerprint, ref_fingerprint) <= threshold:
                groups[index].append(src)
                break
        else:
            fingerprints.append((fingerprint, src))
            groups.append([src])
    return [group for group in groups if group], hashes


# ---------------------------------------------------------------- 质量指标


def quality_metrics(image: Image.Image, gray: Image.Image | None = None) -> dict:
    """算清晰度 (边缘强度标准差) 与灰度统计 (均值 / 饱和度 / 纯色占比)。

    gray: 调用方已经转好的灰度图 (可选)。dhash() 内部也会转一次灰度, 同一张图
    两处都转就白解一遍; 传入可复用。
    """
    if gray is None:
        gray = image.convert("L")
    hist = gray.histogram()
    total = sum(hist) or 1
    mean = sum(i * count for i, count in enumerate(hist)) / total
    deviation = (sum(((i - mean) ** 2) * count for i, count in enumerate(hist)) / total) ** 0.5
    edges = gray.filter(ImageFilter.FIND_EDGES)
    edge_hist = edges.histogram()
    edge_mean = sum(i * count for i, count in enumerate(edge_hist)) / total
    edge_std = (sum(((i - edge_mean) ** 2) * count for i, count in enumerate(edge_hist)) / total) ** 0.5
    clipped = (hist[255] + hist[0]) / total
    return {
        "sharpness": edge_std,
        "mean": mean,
        "std": deviation,
        "clipped": clipped,
    }


def screen_quality(src: str, values: dict, image: Image.Image | None = None) -> tuple[dict, list[str]]:
    """按阈值判定模糊 / 纯色 / 过曝 / 欠曝, 返回 (指标, 命中的问题标签)。

    image: 调用方已打开的图片 (可选), 传入可避免对同一个文件再解码一次
    (dedupe_action 在查重时已经解过一次)。
    """
    if image is not None:
        metrics = quality_metrics(image)
    else:
        opened = open_image(src)
        try:
            metrics = quality_metrics(opened)
        finally:
            opened.close()
    items = values.get("quality_items")
    if items is None:
        items = QUALITY_ITEMS
    flags: list[str] = []
    if "模糊" in items and metrics["sharpness"] < float(values.get("blur_threshold") or 8):
        flags.append("模糊")
    if "纯色" in items and metrics["std"] < float(values.get("flat_threshold") or 3):
        flags.append("纯色")
    if "过曝" in items and metrics["mean"] > float(values.get("exposure_threshold") or 245):
        flags.append("过曝")
    if "欠曝" in items and metrics["mean"] < float(values.get("dark_threshold") or 12):
        flags.append("欠曝")
    return metrics, flags


# ---------------------------------------------------------------- 查重 / 筛查动作


def dedupe_action(values: dict) -> dict:
    """动作: 扫描查重 (可选同时筛查质量), 生成 CSV 报告, 可把重复图归集到子目录。"""
    files = sort_images(collect_images((values.get("path") or "").strip() or None, None))
    if not files:
        raise ValueError("请选择要扫描的目录")
    threshold = max(0, min(64, int(values.get("threshold") or 5)))
    with_quality = (values.get("quality") or "不筛查") == "筛查"
    groups, hashes = group_duplicates(files, threshold)
    duplicate_groups = [group for group in groups if len(group) > 1]
    unique_count = len(groups) - len(duplicate_groups)

    root = Path((values.get("out_dir") or "").strip() or Path(files[0]).parent)
    rows: list[list] = []
    text_lines: list[str] = []
    keepers: list[str] = []
    if duplicate_groups:
        text_lines.append(f"🔁 重复 {len(duplicate_groups)} 组 (共 {sum(len(g) for g in duplicate_groups)} 张, 阈值 ≤{threshold})")
        for index, group in enumerate(duplicate_groups, 1):
            keepers.append(group[0])
            text_lines.append(f"  组 {index}: 保留 {os.path.basename(group[0])}")
            for extra in group[1:]:
                text_lines.append(f"       重复 {os.path.basename(extra)}")
    else:
        text_lines.append(f"🔁 未发现重复 (阈值 ≤{threshold})")
    # 预先算好每张图所属的组号 (重复组重新编号 1..n, 唯一图为空), 后面直接查表
    group_of: dict[str, str] = {}
    for number, group in enumerate(duplicate_groups, 1):
        for src in group:
            group_of[src] = str(number)
    for src in files:
        rows.append(
            [
                "重复组" if src in group_of else "唯一",
                group_of.get(src, ""),
                hashes.get(src, ""),
                os.path.basename(src),
                src,
            ]
        )
    if with_quality:
        flagged: list[str] = []
        # 用下标配对而不是 zip(rows, files): rows 与 files 必须严格一一对应,
        # zip 在两者长度不一致时会静默错位 (把 A 的指标写进 B 的行)。
        if len(rows) != len(files):
            raise RuntimeError(f"内部错误: 报告行数与文件数不一致 ({len(rows)} != {len(files)})")
        for index, src in enumerate(files):
            _metrics, flags = screen_quality(src, values)
            rows[index].append(" ".join(flags))
            if flags:
                flagged.append(f"  {os.path.basename(src)}: {' '.join(flags)}")
        text_lines.append("")
        if not (values.get("quality_items") or []):
            text_lines.append("🔎 质量筛查: 未勾选任何筛查项, 已跳过")
        text_lines.append(f"🔎 质量筛查: {len(flagged)} 张被标记")
        text_lines.extend(flagged[:30])
        if len(flagged) > 30:
            text_lines.append(f"  ... 另有 {len(flagged) - 30} 张, 详见 CSV")
        header = ["结论", "组号", "指纹", "文件", "路径", "质量问题"]
    else:
        header = ["结论", "组号", "指纹", "文件", "路径"]

    csv_path = write_csv(root / "dedupe_report.csv", header, rows)

    moved: list[str] = []
    if duplicate_groups and (values.get("handle") or "仅报告") == "把重复图移到子目录":
        target = root / ((values.get("dup_dir") or "_duplicates").strip() or "_duplicates")
        target.mkdir(parents=True, exist_ok=True)
        for group in duplicate_groups:
            for src in group[1:]:
                try:
                    destination = target / Path(src).name
                    counter = 1
                    while destination.exists():
                        destination = target / f"{Path(src).stem}_{counter}{Path(src).suffix}"
                        counter += 1
                    os.replace(src, destination)
                    moved.append(f"{os.path.basename(src)} → {target}")
                except Exception as e:
                    logger.error(f"归集重复图失败 ({src}): {e}")
                    logger.opt(exception=True).debug("归集重复图失败堆栈:")
        if moved:
            text_lines.append("")
            text_lines.append(f"📦 已归集 {len(moved)} 张重复图 → {target}")

    head = f"查重完成: {len(files)} 张 · 重复 {len(duplicate_groups)} 组 · 唯一 {unique_count} 张"
    if with_quality:
        head += " · 已含质量筛查"
    text_lines.append("")
    text_lines.append(f"报告已导出: {csv_path}")
    result: dict = {
        "images": keepers[:24],
        "message": head,
        "text": head + "\n" + "\n".join(text_lines),
        "dir": str(root),
    }
    return result


# ---------------------------------------------------------------- 清单 CSV


def manifest_rows(files: list[str]) -> tuple[list[list], int]:
    """把每张图的文件信息 + 生成参数整理成 CSV 行, 返回 (行列表, 参数字段逐个补全的张数)。"""
    rows: list[list] = []
    filled = 0
    for src in files:
        try:
            image = open_image(src)
            try:
                width, height = image.size
                fmt = image.format or Path(src).suffix.lstrip(".").upper()
            finally:
                image.close()
        except Exception as e:
            logger.warning(f"读取图片信息失败, 已跳过 ({src}): {e}")
            logger.opt(exception=True).debug("读取图片信息失败堆栈:")
            continue
        params = read_params(src)
        if params:
            filled += 1
        row = [
            os.path.basename(src),
            src,
            fmt,
            width,
            height,
            human_size(os.path.getsize(src)),
        ]
        for key in MANIFEST_COLUMNS[6:]:
            value = params.get(key, "")
            if isinstance(value, str) and len(value) > 500:
                value = value[:500] + f"... (共 {len(value)} 字符)"
            row.append(value)
        rows.append(row)
    return rows, filled


def manifest_action(values: dict) -> dict:
    """动作: 生成参数清单 CSV (每张图一行: 尺寸 / 体积 / prompt / seed / 步数 ...)。"""
    files = sort_images(collect_images((values.get("path") or "").strip() or None, None))
    if not files:
        raise ValueError("请选择要生成清单的目录")
    rows, filled = manifest_rows(files)
    if not rows:
        raise ValueError("清单生成失败: 没有可读取的图片")
    root = Path((values.get("out_dir") or "").strip() or Path(files[0]).parent)
    name = (values.get("name") or "manifest").strip() or "manifest"
    csv_path = write_csv(root / f"{name}.csv", MANIFEST_COLUMNS, rows)
    head = f"清单生成完成: {len(rows)} 行 · 含生成参数 {filled} 张"
    detail = f"CSV: {csv_path}\n列: {', '.join(MANIFEST_COLUMNS)}"
    return {
        "images": [],
        "message": head,
        "text": head + "\n" + detail,
        "dir": str(root),
    }


# ---------------------------------------------------------------- 批量重命名


def _template_values(src: str, index: int, values: dict) -> dict:
    """模板占位符的取值 (序号 / 原名 / 时间 / 尺寸 / 生成参数)。"""
    stat = os.stat(src)
    params = read_params(src)
    # 只读图片头部拿尺寸: open_image() 会 .load() 整幅像素 (4K PNG 要几百毫秒),
    # 而这里只需要两个整数。Image.open 本身是惰性的, 不 load 就能读到 .size。
    try:
        with Image.open(src) as _probe:
            width, height = _probe.size
    except Exception:
        width = height = 0
    prompt = str(params.get("prompt") or "")
    limit = max(8, int(values.get("prompt_len") or 40))
    return {
        "index": index,
        "name": Path(src).stem,
        "ext": Path(src).suffix.lstrip("."),
        "date": datetime.fromtimestamp(stat.st_mtime).strftime("%Y%m%d"),
        "time": datetime.fromtimestamp(stat.st_mtime).strftime("%H%M%S"),
        "datetime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y%m%d_%H%M%S"),
        "w": width,
        "h": height,
        "prompt": prompt[:limit].replace("/", "_").replace("\\", "_").strip(),
        **{key: params.get(key, "") for key in TEMPLATE_PARAM_KEYS},
    }


# 模板占位符正则 (模块级编译一次; 原先在 render_template 里每张图重编译一遍)
_TEMPLATE_RX = re.compile(r"\{(\w+)(?::([^}]*))?\}")


def render_template(template: str, mapping: dict) -> str:
    """渲染模板: `{index:03d}` 这种带格式说明的占位符走 Python 格式化, 其余取原值。"""

    def replace(match) -> str:
        key, spec = match.group(1), match.group(2)
        if key not in mapping:
            return match.group(0)  # 未知占位符原样保留, 便于用户发现写错了
        value = mapping[key]
        if spec:
            try:
                return format(value, spec)
            except (ValueError, TypeError):
                return str(value)
        return str(value)

    return _TEMPLATE_RX.sub(replace, template or "")


def rename_action(values: dict) -> dict:
    """动作: 按模板批量重命名 (默认只预览, 选「直接重命名」才落盘)。"""
    files = sort_images(collect_images((values.get("path") or "").strip() or None, None))
    if not files:
        raise ValueError("请选择要重命名的目录")
    template = (values.get("template") or "{index:03d}_{name}").strip()
    if not template:
        raise ValueError("请填写重命名模板")
    apply_changes = (values.get("mode") or "仅预览") == "直接重命名"
    start = int(values.get("start") or 1)
    prefix = values.get("prefix") or ""
    suffix = values.get("suffix") or ""

    lines: list[str] = []
    errors: list[str] = []
    used: set[str] = set()
    for offset, src in enumerate(files):
        try:
            mapping = _template_values(src, start + offset, values)
            stem = render_template(template, mapping).strip()
            new_name = f"{prefix}{stem}{suffix}{Path(src).suffix}"
            new_name = "".join(ch for ch in new_name if ch not in '\\/:*?"<>|').strip()
            if not new_name or new_name == Path(src).name:
                lines.append(f"{os.path.basename(src)}: 名称未变化, 已跳过")
                continue
            destination = Path(src).with_name(new_name)
            if destination.name in used or (destination.exists() and destination != Path(src)):
                base, ext = destination.stem, destination.suffix
                counter = 1
                while destination.name in used or (destination.exists() and destination != Path(src)):
                    destination = destination.with_name(f"{base}_{counter}{ext}")
                    counter += 1
            used.add(destination.name)
            if apply_changes:
                os.replace(src, destination)
                lines.append(f"{os.path.basename(src)} → {destination.name} ✅")
            else:
                lines.append(f"{os.path.basename(src)} → {destination.name}")
        except Exception as e:
            logger.error(f"重命名失败 ({src}): {e}")
            logger.opt(exception=True).debug("重命名失败堆栈:")
            errors.append(f"{os.path.basename(src)}: {e}")

    head = (
        f"重命名{'完成' if apply_changes else '预览'}: {len(files)} 张"
        + (f", 失败 {len(errors)} 项" if errors else "")
        + ("" if apply_changes else " (演示模式, 未改动文件; 选「直接重命名」才会落盘)")
    )
    if errors:
        lines.append("")
        lines.append("❌ 失败:")
        lines.extend(errors[:10])
    return {
        "images": [],
        "message": head,
        "text": head + "\n" + "\n".join(lines[:60]),
        "dir": str(Path(files[0]).parent),
    }
