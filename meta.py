"""图片工具插件: 元数据读取 / 写入 / 清除。

比项目自带的法术解析更彻底:
- 读取: PNG 文本块 (tEXt/iTXt/zTXt)、EXIF 全部标签、ICC / XMP、隐写数据、文件信息
- 写入: 任意键值 (JSON 或 键=值) 写入 PNG 文本块, 已知名称的键同时写入 EXIF
- 清除: 按类别勾选 (可只清除指定键), 覆盖 PNG 文本块 / 隐写数据 / EXIF / ICC / XMP
"""

from __future__ import annotations

import os
import time

import ujson
from PIL import ExifTags, Image

from plugins.anr_plugin_image_tools import lsb
from plugins.anr_plugin_image_tools.common import (
    build_result,
    each_image,
    grab_meta,
    human_size,
    open_image,
    read_png_text,
    resolve_output,
    save_image,
)
from utils.logger import logger

# 支持写入的 EXIF 文本标签: 名称 -> (piexif IFD, 标签号)
EXIF_TEXT_TAGS: dict[str, tuple[str, int]] = {
    "DocumentName": ("0th", 269),
    "ImageDescription": ("0th", 270),
    "Make": ("0th", 271),
    "Model": ("0th", 272),
    "Software": ("0th", 305),
    "DateTime": ("0th", 306),
    "Artist": ("0th", 315),
    "HostComputer": ("0th", 316),
    "Copyright": ("0th", 33432),
    "UserComment": ("Exif", 37510),
}

# PNG 中存放 XMP 的文本块键
XMP_KEY = "XML:com.adobe.xmp"
# EXIF 中存放 XMP 的标签号
XMP_TAG = 700

# 清除项 (面板勾选项)
CLEAR_TARGETS = ["PNG 文本块", "NovelAI 隐写数据", "自定义隐写数据", "EXIF", "ICC 色彩配置", "XMP"]

# 「从参考图搬运参数」时可勾选的生成参数字段
PARAM_TRANSFER_KEYS = ["prompt", "uc", "seed", "steps", "scale", "sampler", "noise_schedule", "model"]

# 参数来源 (写入面板)
PARAM_SOURCES = ["手填 / JSON", "从参考图搬运"]
PARAM_MODES = ["整份复制", "只搬指定字段"]

# 生成参数摘要: JSON 键 -> 中文名
_PARAM_LABELS = [
    ("prompt", "正面提示词"),
    ("uc", "负面提示词"),
    ("seed", "种子"),
    ("steps", "步数"),
    ("scale", "相关性"),
    ("sampler", "采样器"),
    ("noise_schedule", "调度器"),
    ("width", "宽"),
    ("height", "高"),
    ("sm", "SMEA"),
    ("sm_dyn", "SMEA DYN"),
    ("cfg_rescale", "CFG Rescale"),
]


# ---------------------------------------------------------------- 读取


def _fix_mojibake(text: str) -> str:
    """还原中文乱码。

    EXIF 文本标签在规范里是 ASCII 类型, 写入的中文以 UTF-8 字节存放,
    读回时会被按 latin-1 解码成乱码, 这里尝试按原字节重新解一次。
    """
    if not text or text.isascii():
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _decode(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        if raw.startswith(b"ASCII"):
            raw = raw.split(b"ASCII", 1)[1].lstrip(b"\x00")
        return _fix_mojibake(raw.decode("utf-8", "replace"))
    return _fix_mojibake(str(value))


def _collect_exif(image: Image.Image) -> dict[str, str]:
    """收集 EXIF 全部标签 (主 IFD + Exif 子 IFD + GPS), 键带 IFD 前缀。"""
    result: dict[str, str] = {}
    try:
        exif = image.getexif()
    except Exception:
        return result
    if not exif:
        return result
    for tag, value in exif.items():
        result[ExifTags.TAGS.get(tag, f"0x{tag:04X}")] = _decode(value)
    try:
        for tag, value in exif.get_ifd(0x8769).items():
            result[f"Exif.{ExifTags.TAGS.get(tag, f'0x{tag:04X}')}"] = _decode(value)
    except Exception:
        pass
    try:
        for tag, value in exif.get_ifd(0x8825).items():
            result[f"GPS.{ExifTags.GPSTAGS.get(tag, f'0x{tag:04X}')}"] = _decode(value)
    except Exception:
        pass
    return result


def _summarize_params(comment: str) -> list[str]:
    """从参数 JSON (Comment / LSB 数据) 里提取常用生成参数。"""
    try:
        data = ujson.loads(comment)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    lines = []
    for key, label in _PARAM_LABELS:
        if key not in data:
            continue
        value = data[key]
        if isinstance(value, str) and len(value) > 120:
            value = value[:120] + f"... (共 {len(value)} 字符)"
        lines.append(f"    {label}: {value}")
    if data.get("v4_prompt", {}).get("caption", {}).get("char_captions"):
        lines.append(f"    角色提示词: {len(data['v4_prompt']['caption']['char_captions'])} 个")
    return lines


def read_params(src: str) -> dict:
    """读取一张图的 NovelAI 生成参数 (PNG 文本块 Comment / NovelAI 隐写), 返回参数字典。

    供「写入数据」面板的「从参考图搬运参数」与「整理」面板的重命名模板 / 参数清单共用。
    读不到时返回空字典 (调用方据此报错, 而不是写入一份空参数)。
    """
    image = open_image(src)
    try:
        text = read_png_text(image)
        candidates: list[str] = [
            value
            for key, value in text.items()
            if key in ("Comment", "parameters", "Description", "prompt") and isinstance(value, str)
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
            if isinstance(data, dict) and any(key in data for key in ("prompt", "seed", "steps", "sampler")):
                return data
        return {}
    finally:
        image.close()


def _dump_payload(src: str, data: bytes, ext: str = "") -> str:
    """把读到的隐写载荷导出到 outputs/lsb_extract (报告里只展示预览, 二进制内容不丢)。"""
    root = os.path.join("outputs", "lsb_extract")
    os.makedirs(root, exist_ok=True)
    if not ext:
        ext = ".txt" if lsb.is_text_payload(data) else ".bin"
    path = os.path.join(root, f"{os.path.splitext(os.path.basename(src))[0]}_{int(time.time() * 1000)}{ext}")
    with open(path, "wb") as f:
        f.write(data)
    return os.path.abspath(path)


def read_report(src: str, password: str = "") -> tuple[str, dict]:
    """读取单张图片的全部元数据与隐写内容, 返回 (可读报告, 结构化数据)。"""
    image = open_image(src)
    struct: dict = {
        "path": src,
        "file": {},
        "png_text": {},
        "nai_lsb": None,
        "custom_lsb": None,
        "exif": {},
        "icc": 0,
        "xmp": "",
    }
    lines: list[str] = [f"📄 文件: {os.path.basename(src)}"]
    try:
        stat = os.stat(src)
        info = image.info or {}
        struct["file"] = {
            "size": stat.st_size,
            "format": image.format or "未知",
            "mode": image.mode,
            "width": image.size[0],
            "height": image.size[1],
            "frames": getattr(image, "n_frames", 1),
        }
        lines.append(f"    路径: {os.path.abspath(src)}")
        lines.append(
            f"    大小: {human_size(stat.st_size)} · 格式: {struct['file']['format']} · "
            f"尺寸: {struct['file']['width']}×{struct['file']['height']} · 模式: {image.mode}"
        )
        if struct["file"]["frames"] > 1:
            lines.append(f"    帧数: {struct['file']['frames']} (动图)")
        if info.get("dpi"):
            lines.append(f"    DPI: {info['dpi']}")
        if info.get("transparency") is not None or "A" in image.getbands():
            lines.append("    透明通道: 有")

        # ① PNG 文本块
        text = read_png_text(image)
        struct["png_text"] = text
        if text:
            lines.append(f"\n🏷️ PNG 文本块 ({len(text)} 项)")
            for key, value in text.items():
                shown = value if len(value) <= 300 else value[:300] + f"... (共 {len(value)} 字符)"
                lines.append(f"    {key}: {shown}")
                if key in ("Comment", "parameters", "Description", "prompt"):
                    lines.extend(_summarize_params(value))
        else:
            lines.append("\n🏷️ PNG 文本块: 无")

        # ② NovelAI 隐写数据
        nai = lsb.nai_payload(image)
        struct["nai_lsb"] = nai
        if nai:
            lines.append(f"\n📦 NovelAI 隐写数据 (stealth_pngcomp, {len(nai)} 项)")
            for key, value in nai.items():
                shown = value if not isinstance(value, str) or len(value) <= 300 else value[:300] + "..."
                lines.append(f"    {key}: {shown}")
                if isinstance(value, str):
                    lines.extend(_summarize_params(value))
            lines.append(
                f"    已导出: {_dump_payload(src, ujson.dumps(nai, ensure_ascii=False, indent=2).encode('utf-8'), '.json')}"
            )
        else:
            lines.append("\n📦 NovelAI 隐写数据: 无")

        # ③ 自定义隐写数据 (本插件写入, 加密载荷需填密码才能读出)
        try:
            custom = lsb.extract(image, password=password)
        except Exception as e:
            custom = {"found": False, "error": str(e)}
        struct["custom_lsb"] = custom
        if custom.get("found"):
            data = custom.get("data", b"")
            lines.append(
                f"\n🖊️ 自定义隐写数据 (ANRLSB1): {custom['size']} 字节 · {custom['channel']} 通道"
                f"{' · 已加密' if custom['encrypted'] else ''}{' · 已压缩' if custom['compressed'] else ''}"
            )
            lines.append("    " + lsb.payload_preview(data, 500).replace("\n", "\n    "))
            lines.append(f"    已导出: {_dump_payload(src, data)}")
        elif custom.get("error"):
            lines.append(f"\n🖊️ 自定义隐写数据 (ANRLSB1): 读取失败 · {custom['error']}")
        else:
            lines.append("\n🖊️ 自定义隐写数据 (ANRLSB1): 无")

        # ④ EXIF
        exif = _collect_exif(image)
        struct["exif"] = exif
        if exif:
            lines.append(f"\n🖼️ EXIF ({len(exif)} 项)")
            for key, value in exif.items():
                shown = value if len(value) <= 300 else value[:300] + "..."
                lines.append(f"    {key}: {shown}")
        else:
            lines.append("\n🖼️ EXIF: 无")

        # ⑤ ICC / XMP
        icc = info.get("icc_profile") or b""
        struct["icc"] = len(icc)
        lines.append(f"\n🎨 ICC 色彩配置: {f'{len(icc)} 字节' if icc else '无'}")
        xmp = text.get(XMP_KEY) or exif.get("XMLPacket") or ""
        struct["xmp"] = xmp
        lines.append(f"🧩 XMP: {f'{len(xmp)} 字符' if xmp else '无'}")
        return "\n".join(lines), struct
    finally:
        try:
            image.close()
        except Exception:
            pass


# ---------------------------------------------------------------- 写入


def parse_fields(text: str) -> dict[str, str]:
    """解析写入内容: 支持 JSON 对象, 或每行一个 `键=值`。"""
    raw = (text or "").strip()
    if not raw:
        return {}
    if raw.startswith("{"):
        try:
            data = ujson.loads(raw)
        except Exception as e:
            raise ValueError(f"JSON 解析失败: {e}") from e
        if not isinstance(data, dict):
            raise ValueError("写入内容必须是 JSON 对象 (键值对)")
        return {
            str(key): value if isinstance(value, str) else ujson.dumps(value, ensure_ascii=False)
            for key, value in data.items()
        }
    fields: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for sep in ("=", "：", ":"):
            if sep in line:
                key, _, value = line.partition(sep)
                fields[key.strip()] = value.strip()
                break
        else:
            raise ValueError(f"无法解析的行 (需要 `键=值` 或 JSON): {line}")
    return fields


def _load_exif_dict(image: Image.Image) -> dict:
    try:
        import piexif

        return piexif.load((image.info or {}).get("exif") or b"")
    except Exception:
        return {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "Interop": {}, "thumbnail": None}


def _has_exif(exif_dict: dict) -> bool:
    return any(exif_dict.get(key) for key in ("0th", "Exif", "GPS", "1st", "Interop"))


def _dump_exif(exif_dict: dict) -> bytes | None:
    if not _has_exif(exif_dict):
        return None
    try:
        import piexif

        return piexif.dump(exif_dict)
    except Exception as e:
        logger.warning(f"EXIF 序列化失败, 已跳过 EXIF 写入: {e}")
        return None


def apply_exif_fields(exif_dict: dict, fields: dict[str, str]) -> list[str]:
    """把已知名称的键写进 EXIF, 返回实际写入的键名。"""
    written = []
    for key, value in fields.items():
        target = EXIF_TEXT_TAGS.get(key)
        if target is None:
            continue
        ifd, tag = target
        bucket = exif_dict.setdefault(ifd, {})
        # UserComment 在 EXIF 规范里带字符集前缀, 与项目其它地方保持一致
        bucket[tag] = (b"ASCII\x00\x00\x00" if tag == 37510 else b"") + value.encode("utf-8")
        written.append(key)
    return written


def resolve_write_fields(values: dict) -> dict[str, str]:
    """算出这次要写入的键值: 手填内容 + (可选) 从参考图搬运过来的生成参数。

    搬运统一落地成 `Comment` 键 (值为 NAI 参数 JSON), 这样自带的法术解析能直接读回,
    与「写入元数据」原本的写法保持同一条通路。
    """
    fields = parse_fields(values.get("meta_text"))
    if (values.get("param_source") or "手填 / JSON") != "从参考图搬运":
        return fields
    ref = (values.get("ref_image") or "").strip()
    if not ref:
        raise ValueError("请选择参数参考图 (要搬运谁的生成参数)")
    params = read_params(ref)
    if not params:
        raise ValueError(f"参考图里没有找到生成参数: {os.path.basename(ref)}")
    if (values.get("param_mode") or "整份复制") == "只搬指定字段":
        keys = values.get("param_keys") or []
        if not keys:
            raise ValueError("请至少勾选一个要搬运的字段")
        params = {key: params[key] for key in keys if key in params}
        if not params:
            raise ValueError("参考图里没有勾选的这些字段, 请换一张参考图或改选字段")
    fields["Comment"] = ujson.dumps(params, ensure_ascii=False, separators=(",", ":"))
    return fields


def write_metadata(src: str, values: dict) -> tuple[list[str], str]:
    """把文本内容 / 搬运来的生成参数写入图片元数据 (原有元数据保留)。"""
    fields = resolve_write_fields(values)
    if not fields:
        raise ValueError("请填写要写入的内容 (JSON 或 `键=值`)")
    image = open_image(src)
    meta = grab_meta(image)
    text = dict(meta.get("text") or {})
    text.update(fields)
    exif_dict = _load_exif_dict(image)
    written_exif = apply_exif_fields(exif_dict, fields)
    if written_exif or _has_exif(exif_dict):
        meta["exif"] = _dump_exif(exif_dict)
    meta["text"] = text

    suffix = (values.get("suffix") or "_meta").strip() or "_meta"
    out_dir = (values.get("out_dir") or "").strip() or None
    dst = resolve_output(src, bool(values.get("overwrite")), suffix, out_dir=out_dir)
    path, size = save_image(
        image,
        dst,
        meta,
        quality=int(values.get("quality") or 95),
        png_level=int(values.get("png_level") or 6),
    )
    image.close()

    notes = [f"文本块 {len(fields)} 项"]
    if written_exif:
        notes.append(f"EXIF {len(written_exif)} 项 ({', '.join(written_exif)})")
    return [path], f"{os.path.basename(src)} → {os.path.basename(path)} · {' + '.join(notes)} · {human_size(size)}"


# ---------------------------------------------------------------- 清除


def clear_metadata(src: str, values: dict) -> tuple[list[str], str]:
    """按类别清除图片元数据。"""
    targets = set(values.get("clear_targets") or [])
    keys = [key.strip() for key in (values.get("clear_keys") or "").replace("，", ",").split(",") if key.strip()]
    custom_info = (values.get("custom_info") or "").strip()
    if not targets and not keys and not custom_info:
        raise ValueError("请至少选择一项要清除的内容")

    image = open_image(src)
    notes: list[str] = []
    text = read_png_text(image)
    if keys:
        removed = [key for key in keys if key in text]
        text = {key: value for key, value in text.items() if key not in keys}
        notes.append(f"文本块移除 {len(removed)} 项 ({', '.join(removed)})" if removed else "指定键在文本块中不存在")
    elif "PNG 文本块" in targets and text:
        notes.append(f"文本块清除 {len(text)} 项")
        text = {}
    if "XMP" in targets:
        had_xmp = bool(text.pop(XMP_KEY, None))
        if had_xmp:
            notes.append("已清除 XMP 文本块")

    # 隐写数据写在像素里, 只在无损格式下有意义 (有损格式重新编码本就会破坏)
    work = image
    if {"自定义隐写数据", "NovelAI 隐写数据"} & targets:
        if src.lower().endswith((".png", ".tif", ".tiff", ".bmp")):
            if "自定义隐写数据" in targets:
                work, lsb_notes = lsb.erase(work)
                notes.extend(lsb_notes or ["未检测到自定义隐写数据"])
            if "NovelAI 隐写数据" in targets:
                work, note = lsb.erase_nai(work)
                notes.append(note or "未检测到 NovelAI 隐写数据")
        else:
            notes.append("该格式为有损编码, 像素隐写数据在保存时本就会被破坏, 已跳过像素处理")

    exif_dict = _load_exif_dict(image)
    exif: bytes | None = (image.info or {}).get("exif")
    if "EXIF" in targets:
        if exif:
            exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "Interop": {}, "thumbnail": None}
            exif = None
            notes.append("已清除 EXIF")
        else:
            notes.append("未检测到 EXIF")
    elif "XMP" in targets and exif_dict.get("0th", {}).pop(XMP_TAG, None):
        exif = _dump_exif(exif_dict)
        notes.append("已清除 EXIF 中的 XMP")

    icc = (image.info or {}).get("icc_profile")
    if "ICC 色彩配置" in targets:
        if icc:
            notes.append(f"已清除 ICC 色彩配置 ({len(icc)} 字节)")
            icc = None
        else:
            notes.append("未检测到 ICC 色彩配置")

    if custom_info:
        text["Auto-NovelAI-Refactor"] = custom_info
        notes.append("已写入自定义信息 (Auto-NovelAI-Refactor)")

    meta = {"text": text, "exif": exif, "icc": icc}
    suffix = (values.get("suffix") or "_clean").strip() or "_clean"
    out_dir = (values.get("out_dir") or "").strip() or None
    dst = resolve_output(src, bool(values.get("overwrite")), suffix, out_dir=out_dir)
    path, size = save_image(
        work,
        dst,
        meta,
        quality=int(values.get("quality") or 95),
        png_level=int(values.get("png_level") or 6),
    )
    image.close()
    if not path.lower().endswith(".png") and ({"自定义隐写数据", "NovelAI 隐写数据"} & targets):
        notes.append("⚠️ 非 PNG 输出会重新编码, 隐写数据的清除结果无法保留")
    return [path], f"{os.path.basename(src)} → {os.path.basename(path)} · {'; '.join(notes)} · {human_size(size)}"


# ---------------------------------------------------------------- 动作入口


def read_action(values: dict) -> dict:
    """动作: 读取元数据与隐写内容 (报告写入结果文本框)。"""
    password = values.get("password") or ""
    reports: list[str] = []

    def worker(src: str):
        report, _ = read_report(src, password)
        reports.append(report)
        return [], None

    _, errors, _ = each_image(
        (values.get("path") or "").strip() or None,
        values.get("image"),
        worker,
        label="读取元数据",
    )
    if not reports:  # 全部失败: 直接报错 (前端提示更清晰)
        raise ValueError("读取失败: " + ("; ".join(errors) if errors else "请提供单张图片或批处理路径"))
    if errors:
        reports.append("❌ 读取失败:\n" + "\n".join(errors))
    limit = 20
    content = "\n\n".join(reports[:limit])
    if len(reports) > limit:
        content += f"\n\n... 另有 {len(reports) - limit} 张图片未显示"
    message = f"已读取 {len(reports) - (1 if errors else 0)} 张图片的元数据"
    return {"content": content, "text": message, "message": message}


def write_action(values: dict) -> dict:
    """动作: 写入元数据 (手填内容, 或从参考图搬运生成参数)。"""
    transfer = (values.get("param_source") or "手填 / JSON") == "从参考图搬运"
    if transfer:
        # 提前校验参考图, 避免逐张目标图都失败在同一个原因上
        resolve_write_fields(values)
    elif not parse_fields(values.get("meta_text")):  # 提前报错, 避免逐张图片重复失败
        raise ValueError("请填写要写入的内容 (JSON 或 `键=值`), 或把「参数来源」改为「从参考图搬运」")
    outputs, errors, texts = each_image(
        (values.get("path") or "").strip() or None,
        values.get("image"),
        lambda src: write_metadata(src, values),
        label="写入元数据",
    )
    return build_result(outputs, errors, texts, action_name="写入元数据")


def clear_action(values: dict) -> dict:
    """动作: 清除元数据。"""
    if not (
        values.get("clear_targets")
        or (values.get("clear_keys") or "").strip()
        or (values.get("custom_info") or "").strip()
    ):
        raise ValueError("请至少选择一项要清除的内容")
    outputs, errors, texts = each_image(
        (values.get("path") or "").strip() or None,
        values.get("image"),
        lambda src: clear_metadata(src, values),
        label="清除元数据",
    )
    result = build_result(outputs, errors, texts, action_name="清除元数据")
    result["text"] = result.get("text", result["message"]) + f"\n保存目录: {result.get('dir', '')}"
    return result
