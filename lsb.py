"""图片工具插件: LSB 隐写 (写入 / 读取 / 清除)。

自研格式 ANRLSB1 (与项目原有的 stealth_pngcomp 互不影响, 读取时两种都会尝试):

    魔数 "ANRLSB1\\0" (8B) + 标志 (1B) + 通道 (1B) + 载荷长度 (4B, 大端)
    + 盐 (16B) + HMAC-SHA256 (32B) + 载荷

    标志位: bit0 = 已加密, bit1 = 已压缩 (zlib)

载荷按位顺序写入指定通道的最低位 (每像素只改 1 个最低位, 肉眼不可见, 行优先扫描)。
默认写入 R 通道: NovelAI 的 stealth_pngcomp 占用 Alpha 通道, 避开可避免互相覆盖。
读取时自动尝试 A/R/G/B 四个通道定位魔数, 因此不需要记住写入时用的通道。
加密为 PBKDF2-HMAC-SHA256 派生密钥 + SHA-256 计数器流 + HMAC-SHA256 完整性校验,
仅依赖标准库 (属隐写混淆用途, 不等同于专业加密工具)。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import zlib

import numpy as np
from PIL import Image

from plugins.anr_plugin_image_tools.common import (
    build_result,
    each_image,
    grab_meta,
    human_size,
    open_image,
    resolve_output,
    save_image,
)
from utils.logger import logger

MAGIC = b"ANRLSB1\x00"
CHANNELS = {"R": 0, "G": 1, "B": 2, "A": 3}
CHANNEL_NAMES = {value: key for key, value in CHANNELS.items()}
# 魔数 8 + 标志 1 + 通道 1 + 长度 4 + 盐 16 + 校验 32
HEADER_SIZE = len(MAGIC) + 1 + 1 + 4 + 16 + 32
PBKDF2_ITERATIONS = 120_000


# ---------------------------------------------------------------- 底层位操作


def _work_image(image: Image.Image, channel: str) -> Image.Image:
    """按目标通道准备图像: 写 Alpha 通道需要 RGBA, 写 RGB 通道尽量保持原模式。"""
    if channel == "A":
        return image.convert("RGBA")
    if image.mode in ("RGB", "RGBA"):
        return image.copy()
    return image.convert("RGBA" if "A" in image.getbands() else "RGB")


def _flat(array: np.ndarray, index: int) -> np.ndarray:
    """取某通道的一维视图 (行优先); 通过它赋值可直接写回原数组。"""
    return array.reshape(-1, array.shape[2])[:, index]


def _write_bits(flat: np.ndarray, data: bytes, offset_bits: int = 0) -> None:
    bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8))
    end = offset_bits + bits.size
    if end > flat.size:
        raise ValueError("图片容量不足")
    flat[offset_bits:end] = (flat[offset_bits:end] & 0xFE) | bits


def _read_bits(flat: np.ndarray, n_bytes: int, offset_bits: int = 0) -> bytes:
    end = offset_bits + n_bytes * 8
    if n_bytes <= 0 or end > flat.size:
        return b""
    return np.packbits(flat[offset_bits:end] & 1).tobytes()


# ---------------------------------------------------------------- 加解密


def _derive_keys(password: str, salt: bytes) -> tuple[bytes, bytes]:
    material = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS, dklen=64)
    return material[:32], material[32:]


def _xor_stream(data: bytes, key: bytes) -> bytes:
    """SHA-256 计数器流: 用密钥 + 块序号派生密钥流后异或 (对称, 加解密同一函数)。"""
    out = bytearray(len(data))
    block = 32
    for offset in range(0, len(data), block):
        chunk = data[offset : offset + block]
        stream = hashlib.sha256(key + offset.to_bytes(8, "big")).digest()
        out[offset : offset + len(chunk)] = bytes(a ^ b for a, b in zip(chunk, stream))
    return bytes(out)


# ---------------------------------------------------------------- 容量 / 载荷


def capacity(image: Image.Image) -> int:
    """可写入的载荷字节数 (单通道, 每像素 1 位)。"""
    width, height = image.size
    return max(0, width * height - HEADER_SIZE)


def _build_stream(data: bytes, channel: str, password: str, compress: bool) -> tuple[bytes, dict]:
    flags = 0
    if compress:
        packed = zlib.compress(data, 9)
        if len(packed) < len(data):
            data, flags = packed, flags | 0x02
    salt, tag = bytes(16), bytes(32)
    if password:
        salt = os.urandom(16)
        enc_key, mac_key = _derive_keys(password, salt)
        data = _xor_stream(data, enc_key)
        tag = hmac.new(mac_key, data, hashlib.sha256).digest()
        flags |= 0x01
    header = MAGIC + bytes([flags, CHANNELS[channel]]) + len(data).to_bytes(4, "big") + salt + tag
    info = {
        "channel": channel,
        "encrypted": bool(flags & 0x01),
        "compressed": bool(flags & 0x02),
        "size": len(data),
    }
    return header + data, info


def embed(image: Image.Image, data: bytes, channel: str = "R", password: str = "", compress: bool = True):
    """把数据写入图片 LSB, 返回 (新图片, 说明字典)。无损格式 (PNG) 保存后数据仍在。"""
    if not data:
        raise ValueError("没有要写入的内容")
    channel = channel if channel in CHANNELS else "R"
    limit = capacity(image)
    if len(data) > limit:
        raise ValueError(
            f"图片容量不足: 最多可写入 {limit} 字节, 当前需要 {len(data)} 字节 " f"(可换更大的图片, 或改用更短的文本)"
        )
    work = _work_image(image, channel)
    array = np.array(work)
    flat = _flat(array, CHANNELS[channel])
    stream, info = _build_stream(data, channel, password, compress)
    _write_bits(flat, stream)
    logger.info(
        f"隐写写入完成: {info['size']} 字节 → {channel} 通道"
        f"{' (已压缩)' if info['compressed'] else ''}{' (已加密)' if info['encrypted'] else ''}"
    )
    return Image.fromarray(array), info


def extract(image: Image.Image, password: str = "", channel: str | None = None) -> dict:
    """从图片 LSB 读取数据; 返回 {found, channel, encrypted, compressed, size, data}。"""
    array = np.array(image.convert("RGBA"))
    order: list[int] = []
    if channel in CHANNELS:
        order.append(CHANNELS[channel])
    order.extend(index for index in (3, 0, 1, 2) if index not in order)

    for index in order:
        flat = _flat(array, index)
        if flat.size < HEADER_SIZE * 8:
            continue
        head = _read_bits(flat, HEADER_SIZE)
        if head[: len(MAGIC)] != MAGIC:
            continue
        offset = len(MAGIC)
        flags = head[offset]
        if head[offset + 1] != index:
            # 通道字节对不上: 大概率是像素里碰巧出现魔数, 继续找下一个通道
            continue
        length = int.from_bytes(head[offset + 2 : offset + 6], "big")
        salt = head[offset + 6 : offset + 22]
        tag = head[offset + 22 : offset + 54]
        if length < 0 or HEADER_SIZE + length > flat.size // 8:
            raise ValueError("隐写数据长度异常, 图片可能被重新压缩或裁剪过")
        data = _read_bits(flat, length, HEADER_SIZE * 8)
        info = {
            "found": True,
            "channel": CHANNEL_NAMES.get(index, "?"),
            "encrypted": bool(flags & 0x01),
            "compressed": bool(flags & 0x02),
            "size": length,
            "data": data,
        }
        if flags & 0x01:
            if not password:
                raise ValueError("该图片的隐写数据已加密, 请输入密码")
            enc_key, mac_key = _derive_keys(password, salt)
            if not hmac.compare_digest(hmac.new(mac_key, data, hashlib.sha256).digest(), tag):
                raise ValueError("密码错误或数据已损坏 (完整性校验失败)")
            data = _xor_stream(data, enc_key)
        if flags & 0x02:
            data = zlib.decompress(data)
        info["data"] = data
        return info
    return {"found": False}


def erase(image: Image.Image):
    """抹除本插件写入的隐写数据 (把对应区域的 LSB 清零), 返回 (新图片, 说明列表)。"""
    band_count = len(image.getbands())
    work = image if band_count >= 3 else image.convert("RGB")
    array = np.array(work)
    notes: list[str] = []
    candidates = [3, 0, 1, 2] if band_count >= 4 else [0, 1, 2]
    for index in candidates:
        flat = _flat(array, index)
        if flat.size < HEADER_SIZE * 8:
            continue
        head = _read_bits(flat, HEADER_SIZE)
        if head[: len(MAGIC)] != MAGIC or head[len(MAGIC) + 1] != index:
            continue
        length = int.from_bytes(head[len(MAGIC) + 2 : len(MAGIC) + 6], "big")
        end = min(HEADER_SIZE + length, flat.size // 8) * 8
        flat[:end] &= 0xFE
        notes.append(f"已清除自定义隐写数据 ({CHANNEL_NAMES[index]} 通道, {length} 字节)")
        break
    return Image.fromarray(array), notes


def has_nai_data(image: Image.Image) -> bool:
    """检测 NovelAI 的 stealth_pngcomp 隐写数据。"""
    try:
        from utils.naimeta import extract_data

        extract_data(image)
        return True
    except Exception:
        return False


# NovelAI 隐写数据的魔数与头部布局: "stealth_pngcomp" + 4 字节载荷位数
NAI_MAGIC = b"stealth_pngcomp"


def _column_major_index(rows: int, cols: int, n_bits: int, offset_bits: int = 0) -> np.ndarray:
    """列优先 (逐列自上而下) 的第 n 个像素在行优先存储中的下标。

    NovelAI 的隐写写入器先走行再换列 (列优先), 与行优先展平的下标并不一致, 需要换算。
    """
    positions = np.arange(offset_bits, offset_bits + n_bits)
    return (positions % rows) * cols + (positions // rows)


def erase_nai(image: Image.Image):
    """抹除 NovelAI 的隐写数据 (把其占用的 Alpha 通道最低位清零), 返回 (图片, 说明)。

    比直接写入空载荷更彻底: 魔数一并清掉, 之后法术解析与其它工具都读不到隐写数据。
    """
    if "A" not in image.getbands():
        return image, None
    work = image if image.mode == "RGBA" else image.convert("RGBA")
    array = np.array(work)
    rows, cols = array.shape[0], array.shape[1]
    flat = _flat(array, 3)

    def read_column_major(n_bytes: int, offset_bytes: int = 0) -> bytes:
        n_bits = n_bytes * 8
        if n_bits + offset_bytes * 8 > flat.size:
            return b""
        return np.packbits(flat[_column_major_index(rows, cols, n_bits, offset_bytes * 8)] & 1).tobytes()

    head = read_column_major(len(NAI_MAGIC) + 4)
    if head[: len(NAI_MAGIC)] != NAI_MAGIC:
        return work, None
    length = int.from_bytes(head[len(NAI_MAGIC) : len(NAI_MAGIC) + 4], "big") // 8
    total = len(NAI_MAGIC) + 4 + max(0, length)
    if total * 8 > flat.size:
        total = flat.size // 8
        logger.warning("NovelAI 隐写数据长度异常, 已按图片容量清除")
    flat[_column_major_index(rows, cols, total * 8)] &= 0xFE
    logger.info(f"已清除 NovelAI 隐写数据 ({total} 字节)")
    return Image.fromarray(array), f"已清除 NovelAI 隐写数据 ({total} 字节)"


def payload_preview(data: bytes, limit: int = 4000) -> str:
    """载荷预览: 能按 UTF-8 解码时显示文本, 否则显示十六进制摘要。"""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is not None and all(ch.isprintable() or ch in "\r\n\t" for ch in text):
        return text if len(text) <= limit else text[:limit] + f"\n... (共 {len(text)} 字符, 已截断)"
    head = data[:256].hex(" ")
    return f"[二进制数据 {len(data)} 字节] 前 256 字节:\n{head}"


def is_text_payload(data: bytes) -> bool:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return all(ch.isprintable() or ch in "\r\n\t" for ch in text)


def nai_payload(image: Image.Image) -> dict | None:
    """读取 NovelAI 的 stealth_pngcomp 隐写数据 (无数据返回 None)。"""
    try:
        from utils.naimeta import extract_data

        return extract_data(image)
    except Exception:
        return None


# ---------------------------------------------------------------- 动作入口


def _read_source(values: dict) -> tuple[bytes, str]:
    """取要写入的内容: 优先文件 (values["file"]), 其次文本 (values["text"])。"""
    file_path = (values.get("file") or "").strip()
    if file_path:
        if not os.path.isfile(file_path):
            raise ValueError(f"文件不存在: {file_path}")
        with open(file_path, "rb") as f:
            data = f.read()
        if not data:
            raise ValueError("选择的文件是空文件")
        return data, f"文件 {os.path.basename(file_path)} ({human_size(len(data))})"
    text = values.get("text") or ""
    if not text.strip():
        raise ValueError("请填写要写入的文本, 或选择要写入的文件")
    return text.encode("utf-8"), f"文本 {len(text)} 字符"


def write_action(values: dict) -> dict:
    """动作: 写入 LSB 隐写数据。"""
    data, source_desc = _read_source(values)
    password = values.get("password") or ""
    channel = values.get("channel") or "R"
    compress = bool(values.get("compress", True))
    suffix = (values.get("suffix") or "_lsb").strip() or "_lsb"

    def worker(src: str):
        image = open_image(src)
        meta = grab_meta(image)
        stamped, info = embed(image, data, channel=channel, password=password, compress=compress)
        dst = resolve_output(
            src,
            bool(values.get("overwrite")),
            suffix,
            ext=".png",  # 隐写数据必须落在无损格式, 统一转存 PNG
            out_dir=(values.get("out_dir") or "").strip() or None,
        )
        path, size = save_image(stamped, dst, meta, fmt="PNG", png_level=int(values.get("png_level") or 6))
        stamped.close()
        image.close()
        note = " · 已转存 PNG (保证隐写数据完整)" if not src.lower().endswith(".png") else ""
        detail = (
            f"{os.path.basename(src)} → {os.path.basename(path)} · {source_desc} → {info['channel']} 通道"
            f" ({info['size']} 字节{' 压缩' if info['compressed'] else ''}{' 加密' if info['encrypted'] else ''})"
            f" · {human_size(size)}{note}"
        )
        return [path], detail

    outputs, errors, texts = each_image(
        (values.get("path") or "").strip() or None,
        values.get("image"),
        worker,
        label="写入隐写数据",
    )
    return build_result(outputs, errors, texts, action_name="写入隐写数据")
