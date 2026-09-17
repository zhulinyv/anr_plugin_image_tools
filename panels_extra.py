"""图片工具插件: 新增面板的声明 (调色 / 拼合 / 动图 / 装饰 / 整理)。

面板声明与原有 5 个面板 (变换 / 压缩转换 / 读取元数据 / 写入数据 / 清除元数据) 一样,
每段都是「左列分区 + 右列输出框 + 说明排右列底部」:

- 每个功能分区的第一个字段是 radio 开关 (`decl.switch`), 其余参数用 `show_if` 挂在开关上;
  开关是权威 —— 后端 (`enhance` / `compose` / `decorate` / `organize`) 也必须按开关判断,
  不能只看参数是否为 0, 否则面板上关掉了却会被记住的历史值处理掉。
- 出图动作不设 `show_output=False`, 也不用 `set_field` 把结果写进文本框:
  那样输出框不会被创建, 结果里的图片就全都不显示了 (只有纯文本结果的面板才用 textarea)。
- 字段 id 一律带本面板前缀 (调色 co_ / 拼合 cg_ / 动图 an_ / 装饰 dw_ / 整理 og_),
  前端按 id 从全局注册表取值, 跨面板重名会串值。
"""

from __future__ import annotations

from plugins.anr_plugin_image_tools import animate, compose, decorate, enhance, organize
from plugins.anr_plugin_image_tools.common import GRID_POSITIONS, OUTPUT_FORMATS, strip_prefix
from plugins.anr_plugin_image_tools.decl import OUTPUT_FIELDS, SOURCE_FIELDS, fields, info, on_switch, section, switch
from utils.plugins import Action, Field, Panel

# ---------------------------------------------------------------- 调色


def color_panel() -> Panel:
    return Panel(
        id="color",
        title="调色",
        icon="🎨",
        description="亮度 / 对比度 / 饱和度 / 色相 / 锐度 / 伽马 · 一键滤镜 · 自动校正 · 模糊锐化降噪",
        fields=[
            section("co_sec_input", "输入", "单张图片或整个目录批处理"),
            *fields("co_", "_color", SOURCE_FIELDS),
            section("co_sec_auto", "自动校正", "按统计结果自动定调, 是修图流程的第一步"),
            switch("co_auto", "自动校正", "自动校正", "不校正"),
            Field(
                id="co_auto_items",
                label="校正项 (可多选, 按此顺序执行)",
                type="checkbox_group",
                options=enhance.AUTO_ITEMS,
                default=["自动对比度"],
                show_if=on_switch("co_auto", "自动校正"),
            ),
            section("co_sec_adjust", "色彩调整", "参数为 0 的项会被跳过 (伽马 100 = 原样) · 两两并排"),
            switch("co_adjust", "色彩调整", "调整", "不调整"),
            Field(
                id="co_brightness",
                label="亮度 (负 = 变暗)",
                type="slider",
                min=-100,
                max=100,
                step=1,
                default=0,
                row_group="co_adj_a",
                show_if=on_switch("co_adjust", "调整"),
            ),
            Field(
                id="co_contrast",
                label="对比度 (负 = 更平)",
                type="slider",
                min=-100,
                max=100,
                step=1,
                default=0,
                row_group="co_adj_a",
                show_if=on_switch("co_adjust", "调整"),
            ),
            Field(
                id="co_saturation",
                label="饱和度 (负 = 更灰)",
                type="slider",
                min=-100,
                max=100,
                step=1,
                default=0,
                row_group="co_adj_b",
                show_if=on_switch("co_adjust", "调整"),
            ),
            Field(
                id="co_hue",
                label="色相旋转 (度)",
                type="slider",
                min=-180,
                max=180,
                step=1,
                default=0,
                row_group="co_adj_b",
                show_if=on_switch("co_adjust", "调整"),
            ),
            Field(
                id="co_sharpness",
                label="锐度 (负 = 柔化)",
                type="slider",
                min=-100,
                max=100,
                step=1,
                default=0,
                row_group="co_adj_c",
                show_if=on_switch("co_adjust", "调整"),
            ),
            Field(
                id="co_gamma",
                label="伽马 % (>100 提亮)",
                type="slider",
                min=10,
                max=300,
                step=5,
                default=100,
                row_group="co_adj_c",
                show_if=on_switch("co_adjust", "调整"),
            ),
            section("co_sec_filter", "滤镜", "整体风格, 一次只套一种 (要叠加微调请用上面的色彩调整)"),
            Field(id="co_filter", label="滤镜", type="radio", options=enhance.FILTERS, default="不滤镜"),
            section("co_sec_blur", "模糊 / 锐化 / 降噪", "USM 锐化 = 反遮罩锐化, 阈值越大越只对边缘生效"),
            switch("co_blur", "模糊 / 锐化 / 降噪", "处理", "不处理"),
            Field(
                id="co_blur_mode",
                label="处理方式",
                type="radio",
                options=enhance.BLUR_MODES,
                default="高斯模糊",
                show_if=on_switch("co_blur", "处理"),
            ),
            Field(
                id="co_blur_radius",
                label="半径 (采样范围)",
                type="number",
                default=2,
                min=0.1,
                step=0.1,
                row_group="co_blur_n",
                show_if=[on_switch("co_blur", "处理"), {"field": "co_blur_mode", "in": ["高斯模糊", "方框模糊", "USM 锐化", "高斯降噪"]}],
            ),
            Field(
                id="co_blur_size",
                label="中值窗口 (奇数)",
                type="number",
                default=3,
                min=3,
                step=2,
                row_group="co_blur_n",
                show_if=[on_switch("co_blur", "处理"), {"field": "co_blur_mode", "in": ["中值降噪", "高斯降噪"]}],
            ),
            Field(
                id="co_blur_percent",
                label="USM 强度 %",
                type="number",
                default=150,
                min=1,
                step=10,
                row_group="co_blur_usm",
                show_if=[on_switch("co_blur", "处理"), {"field": "co_blur_mode", "equals": "USM 锐化"}],
            ),
            Field(
                id="co_blur_threshold",
                label="USM 阈值 (越低越锐)",
                type="number",
                default=3,
                min=0,
                step=1,
                row_group="co_blur_usm",
                show_if=[on_switch("co_blur", "处理"), {"field": "co_blur_mode", "equals": "USM 锐化"}],
            ),
            section("co_sec_output", "输出", "默认不覆盖原图, 输出为「原名 + 后缀」; 也可指定输出目录"),
            Field(id="co_out_format", label="输出格式", type="radio", options=OUTPUT_FORMATS, default="保持原格式"),
            Field(
                id="co_quality",
                label="质量 (jpg / webp / avif)",
                type="slider",
                min=1,
                max=100,
                step=1,
                default=95,
                row_group="co_out_meta",
            ),
            Field(
                id="co_keep_meta",
                label="保留元数据",
                type="checkbox",
                default=True,
                row_group="co_out_meta",
            ),
        ]
        + fields("co_", "_color", OUTPUT_FIELDS)
        + [
            info(
                "co_info",
                "处理顺序与说明",
                "依次执行: <b>自动校正</b> → <b>色彩调整</b> → <b>滤镜</b> → <b>模糊 / 锐化 / 降噪</b><br>"
                "· 先自动定调、再手工微调、最后套滤镜, 与修图软件的常规流程一致<br>"
                "· 模糊 / 锐化放最后, 避免前面的滤镜把锐化痕迹放大<br>"
                "· <b>自动白平衡</b>用灰度世界算法: 假设整图平均色应是灰, 逐通道缩放到同一均值, 偏色照片很有效<br>"
                "· <b>伽马</b>只动中间调, 黑白点不变, 比亮度更不容易发灰<br>"
                "· 调色会重写像素, 图片里的 LSB 隐写数据 (含 NovelAI 隐写) 必然失效, 结果里会提示",
            ),
        ],
        actions=[
            Action(
                id="run",
                label="应用调色",
                uses_novelai=False,
                # 出图动作: show_output 保持默认 True, 结果里的图片走右列输出框的画廊
                inputs=[
                    "co_path",
                    "co_image",
                    "co_auto",
                    "co_auto_items",
                    "co_adjust",
                    "co_brightness",
                    "co_contrast",
                    "co_saturation",
                    "co_hue",
                    "co_sharpness",
                    "co_gamma",
                    "co_filter",
                    "co_blur",
                    "co_blur_mode",
                    "co_blur_radius",
                    "co_blur_size",
                    "co_blur_percent",
                    "co_blur_threshold",
                    "co_out_format",
                    "co_quality",
                    "co_keep_meta",
                    "co_suffix",
                    "co_overwrite",
                    "co_out_dir",
                ],
                handler=lambda v: enhance.enhance_action(strip_prefix(v, "co_")),
            ),
        ],
    )


# ---------------------------------------------------------------- 拼合


def compose_panel() -> Panel:
    not_split = {"field": "cg_mode", "not_equals": "分割大图"}
    is_split = {"field": "cg_mode", "equals": "分割大图"}
    return Panel(
        id="compose",
        title="拼合",
        icon="🧩",
        description="多张图合成一张 (网格拼图 / 横向长图 / 纵向长图), 或把一张大图按行列切块",
        fields=[
            section("cg_sec_input", "输入", "选目录 · 按文件名自然排序 (2.png 排在 10.png 前面)"),
            *fields("cg_", "_merge", SOURCE_FIELDS),
            section("cg_sec_mode", "处理方式", "拼合是「多张 → 一张」, 分割是「一张 → 多张」"),
            Field(id="cg_mode", label="处理方式", type="radio", options=compose.COMPOSE_MODES, default="网格拼图"),
            # ===== 拼图参数 =====
            section("cg_sec_merge", "拼图参数", "网格与长图共用间距与底色 · 长图统一宽/高: 纵向长图统一宽, 横向长图统一高"),
            Field(
                id="cg_cols",
                label="列数 (0 = 自动)",
                type="number",
                default=0,
                min=0,
                step=1,
                row_group="cg_grid",
                show_if=[not_split, {"field": "cg_mode", "equals": "网格拼图"}],
            ),
            Field(
                id="cg_cell",
                label="单元格边长 (0 = 最大图)",
                type="number",
                default=0,
                min=0,
                step=1,
                row_group="cg_grid",
                show_if=[not_split, {"field": "cg_mode", "equals": "网格拼图"}],
            ),
            Field(
                id="cg_fit",
                label="单元格填充",
                type="radio",
                options=compose.CELL_FITS,
                default="保持比例居中留白",
                show_if=[not_split, {"field": "cg_mode", "equals": "网格拼图"}],
            ),
            Field(
                id="cg_strip_side",
                label="统一宽/高 (0 = 原尺寸)",
                type="number",
                default=0,
                min=0,
                step=1,
                row_group="cg_strip",
                show_if=[not_split, {"field": "cg_mode", "in": ["横向长图", "纵向长图"]}],
            ),
            Field(
                id="cg_align",
                label="对齐方式",
                type="radio",
                options=compose.STRIP_ALIGNS,
                default="居中",
                row_group="cg_strip",
                show_if=[not_split, {"field": "cg_mode", "in": ["横向长图", "纵向长图"]}],
            ),
            Field(
                id="cg_gap",
                label="图片间距 px",
                type="number",
                default=0,
                min=0,
                step=1,
                row_group="cg_layout",
                show_if=not_split,
            ),
            Field(
                id="cg_bg_mode",
                label="底色类型",
                type="radio",
                options=["纯色", "透明"],
                default="纯色",
                row_group="cg_layout",
                show_if=not_split,
            ),
            Field(
                id="cg_bg",
                label="底色",
                type="color",
                default="#ffffff",
                row_group="cg_bg_label",
                show_if=[not_split, {"field": "cg_bg_mode", "equals": "纯色"}],
            ),
            Field(
                id="cg_label",
                label="标注文件名",
                type="checkbox",
                default=False,
                row_group="cg_bg_label",
                show_if=not_split,
            ),
            Field(
                id="cg_label_size",
                label="标注字号",
                type="number",
                default=18,
                min=8,
                step=1,
                row_group="cg_label_opt",
                show_if=[not_split, {"field": "cg_label", "equals": True}],
            ),
            Field(
                id="cg_label_pos",
                label="标注位置",
                type="radio",
                options=compose.LABEL_POSITIONS,
                default="左上",
                row_group="cg_label_opt",
                show_if=[not_split, {"field": "cg_label", "equals": True}],
            ),
            # ===== 分割参数 =====
            section("cg_sec_split", "分割参数", "按行列等分切块, 最后一行/列吃掉除不尽的余数, 不丢像素"),
            Field(
                id="cg_split_rows",
                label="行数",
                type="number",
                default=2,
                min=1,
                step=1,
                row_group="cg_split",
                show_if=is_split,
            ),
            Field(
                id="cg_split_cols",
                label="列数",
                type="number",
                default=2,
                min=1,
                step=1,
                row_group="cg_split",
                show_if=is_split,
            ),
            Field(
                id="cg_split_suffix",
                label="切片输出后缀",
                type="text",
                default="_part",
                row_group="cg_split_more",
                show_if=is_split,
            ),
            Field(
                id="cg_keep_meta",
                label="切片保留元数据",
                type="checkbox",
                default=True,
                row_group="cg_split_more",
                show_if=is_split,
            ),
            section("cg_sec_output", "输出", "输出目录 > 源图所在目录, 再拼上子目录名"),
            Field(id="cg_out_dir", label="输出目录 (留空 = 源图所在目录)", type="path", folder=True, file=False),
            Field(id="cg_sub_dir", label="输出子目录", type="text", default="merged", row_group="cg_outname"),
            Field(
                id="cg_name",
                label="输出文件名 (仅拼合)",
                type="text",
                default="collage",
                row_group="cg_outname",
            ),
            Field(
                id="cg_out_format",
                label="输出格式",
                type="radio",
                options=["保持原格式", "png", "jpg", "webp"],
                default="png",
                row_group="cg_outfmt",
            ),
            Field(
                id="cg_quality",
                label="质量 (jpg / webp)",
                type="slider",
                min=1,
                max=100,
                step=1,
                default=92,
                row_group="cg_outfmt",
            ),
        ]
        + [
            info(
                "cg_info",
                "说明",
                "· 拼合顺序按<b>文件名自然排序</b>, 序列帧 / 漫画分镜这类顺序敏感的素材不会错位<br>"
                "· <b>保持比例居中留白</b>不会放大超过单元格; <b>裁剪填满</b>会等比放大后从中心裁掉多余部分<br>"
                "· 分割结果的目录结构: <code>输出目录/子目录/原文件名/原文件名_part_行-列.png</code><br>"
                "· 分割切片默认保留原图元数据, 每块都能被自带的法术解析读回生成参数<br>"
                "· 拼图输出<b>不带元数据</b> (多张图的参数无法合并且没有意义)",
            ),
        ],
        actions=[
            Action(
                id="run",
                label="拼合 / 分割",
                uses_novelai=False,
                inputs=[
                    "cg_path",
                    "cg_image",
                    "cg_mode",
                    "cg_cols",
                    "cg_cell",
                    "cg_fit",
                    "cg_strip_side",
                    "cg_align",
                    "cg_gap",
                    "cg_bg_mode",
                    "cg_bg",
                    "cg_label",
                    "cg_label_size",
                    "cg_label_pos",
                    "cg_split_rows",
                    "cg_split_cols",
                    "cg_split_suffix",
                    "cg_keep_meta",
                    "cg_out_dir",
                    "cg_sub_dir",
                    "cg_name",
                    "cg_out_format",
                    "cg_quality",
                ],
                handler=lambda v: compose.compose_action(strip_prefix(v, "cg_")),
            ),
        ],
    )


# ---------------------------------------------------------------- 动图


def animate_panel() -> Panel:
    return Panel(
        id="animate",
        title="动图",
        icon="🎞️",
        description="目录里的序列帧合成 GIF / 动态 WebP, 按文件名自然排序, 可做往返播放",
        fields=[
            section("an_sec_input", "输入", "整个目录的图片按文件名自然排序当作序列帧"),
            Field(
                id="an_path",
                label="序列帧目录",
                type="path",
                folder=True,
                file=False,
                placeholder="选择包含序列帧的目录 (按文件名自然排序)",
            ),
            section("an_sec_params", "动图参数", "所有帧会被统一到同一尺寸, 长宽比不同的帧不会被拉伸"),
            Field(id="an_format", label="输出格式", type="radio", options=animate.ANIMATE_FORMATS, default="GIF"),
            Field(
                id="an_duration",
                label="帧间隔 ms (1000 ÷ 帧率)",
                type="number",
                default=100,
                min=10,
                step=10,
                row_group="an_time",
            ),
            Field(
                id="an_loop",
                label="循环次数 (0 = 无限)",
                type="number",
                default=0,
                min=0,
                step=1,
                row_group="an_time",
            ),
            Field(
                id="an_base",
                label="尺寸基准",
                type="radio",
                options=animate.BASE_FRAMES,
                default="按最大帧统一",
                row_group="an_size",
            ),
            Field(
                id="an_side",
                label="统一最长边 (0 = 不缩放)",
                type="number",
                default=0,
                min=0,
                step=1,
                row_group="an_size",
            ),
            Field(id="an_bg", label="留白底色", type="color", default="#ffffff", row_group="an_flag"),
            Field(
                id="an_alpha",
                label="保留透明通道 (仅 WebP)",
                type="checkbox",
                default=False,
                row_group="an_flag",
            ),
            Field(
                id="an_boomerang",
                label="往返播放 (正序 + 倒序)",
                type="checkbox",
                default=False,
                row_group="an_boom",
            ),
            Field(
                id="an_quality",
                label="质量 (仅 WebP 动图)",
                type="slider",
                min=1,
                max=100,
                step=1,
                default=90,
                row_group="an_boom",
                show_if={"field": "an_format", "equals": "WebP 动图"},
            ),
            section("an_sec_output", "输出", "输出目录 > 序列帧所在目录, 再拼上子目录名"),
            Field(id="an_out_dir", label="输出目录 (留空 = 序列帧所在目录)", type="path", folder=True, file=False),
            Field(id="an_sub_dir", label="输出子目录", type="text", default="", row_group="an_outname"),
            Field(
                id="an_name",
                label="输出文件名",
                type="text",
                default="animation",
                placeholder="不含扩展名",
                row_group="an_outname",
            ),
        ]
        + [
            info(
                "an_info",
                "说明",
                "· 序列帧顺序按<b>文件名自然排序</b>: <code>2.png</code> 排在 <code>10.png</code> 前面<br>"
                "· 动图要求所有帧同尺寸, 每帧会等比缩放后居中贴到统一画布上, <b>不会拉伸变形</b><br>"
                "· 长宽比不一致的帧会在留白处补「留白底色」; 要完全避免留白请先用「变换」面板统一尺寸<br>"
                "· 帧间隔 = 1000 ÷ 帧率: 12 fps ≈ 83 ms, 8 fps = 125 ms<br>"
                "· <b>往返播放</b>会把正序之后接上倒序 (去掉首尾避免衔接处重复), 适合做来回摆动的循环<br>"
                "· GIF 只有 256 色, 渐变会有色带; 要更好的画质请选<b>动态 WebP</b>",
            ),
        ],
        actions=[
            Action(
                id="run",
                label="合成动图",
                uses_novelai=False,
                inputs=[
                    "an_path",
                    "an_format",
                    "an_duration",
                    "an_loop",
                    "an_base",
                    "an_side",
                    "an_bg",
                    "an_alpha",
                    "an_boomerang",
                    "an_quality",
                    "an_out_dir",
                    "an_sub_dir",
                    "an_name",
                ],
                handler=lambda v: animate.animate_action(strip_prefix(v, "an_")),
            ),
        ],
    )


# ---------------------------------------------------------------- 装饰


def decorate_panel() -> Panel:
    return Panel(
        id="decorate",
        title="装饰",
        icon="🖌️",
        description="加水印 (文字 / 图片, 可平铺旋转) 与加边框 (描边 / 圆角 / 阴影 / 拍立得 / 圆形头像)",
        fields=[
            section("dw_sec_input", "输入", "单张图片或整个目录批处理"),
            *fields("dw_", "_deco", SOURCE_FIELDS),
            section("dw_sec_wm", "水印", "先加边框再打水印 —— 水印落在最终画布上, 所见即所得"),
            switch("dw_wm", "水印", "加水印", "不加水印"),
            Field(
                id="dw_wm_type",
                label="水印类型",
                type="radio",
                options=decorate.WATERMARK_TYPES,
                default="文字水印",
                show_if=on_switch("dw_wm", "加水印"),
            ),
            Field(
                id="dw_wm_text",
                label="水印文字 (支持占位符)",
                type="text",
                default="{name}",
                placeholder=decorate.PLACEHOLDERS,
                row_group="dw_wm_text_row",
                show_if=[on_switch("dw_wm", "加水印"), {"field": "dw_wm_type", "equals": "文字水印"}],
            ),
            Field(
                id="dw_wm_size",
                label="字号",
                type="number",
                default=32,
                min=8,
                step=1,
                row_group="dw_wm_text_row",
                show_if=[on_switch("dw_wm", "加水印"), {"field": "dw_wm_type", "equals": "文字水印"}],
            ),
            Field(
                id="dw_wm_color",
                label="文字颜色",
                type="color",
                default="#ffffff",
                row_group="dw_wm_style",
                show_if=[on_switch("dw_wm", "加水印"), {"field": "dw_wm_type", "equals": "文字水印"}],
            ),
            Field(
                id="dw_wm_stroke",
                label="加描边 (深/浅底都看得清)",
                type="checkbox",
                default=True,
                row_group="dw_wm_style",
                show_if=[on_switch("dw_wm", "加水印"), {"field": "dw_wm_type", "equals": "文字水印"}],
            ),
            Field(
                id="dw_wm_stroke_width",
                label="描边宽度",
                type="number",
                default=2,
                min=1,
                step=1,
                row_group="dw_wm_stroke_row",
                show_if=[on_switch("dw_wm", "加水印"), {"field": "dw_wm_stroke", "equals": True}],
            ),
            Field(
                id="dw_wm_stroke_color",
                label="描边颜色",
                type="color",
                default="#000000",
                row_group="dw_wm_stroke_row",
                show_if=[on_switch("dw_wm", "加水印"), {"field": "dw_wm_stroke", "equals": True}],
            ),
            Field(
                id="dw_wm_image",
                label="水印图片 (建议用透明底 png)",
                type="image",
                show_if=[on_switch("dw_wm", "加水印"), {"field": "dw_wm_type", "equals": "图片水印"}],
            ),
            # 缩放/不透明度 · 旋转/平铺 · 位置/边距/间距: 三组各自相关, 两两或三个并排
            # (位置/边距 与 平铺间距 互斥显示, 并排后任一时刻只有一组可见, 单独可见时自动占满整行)
            Field(
                id="dw_wm_scale",
                label="水印宽度 %",
                type="slider",
                min=1,
                max=100,
                step=1,
                default=20,
                row_group="dw_wm_alpha",
                show_if=[on_switch("dw_wm", "加水印"), {"field": "dw_wm_type", "equals": "图片水印"}],
            ),
            Field(
                id="dw_wm_opacity",
                label="不透明度 %",
                type="slider",
                min=0,
                max=100,
                step=1,
                default=60,
                row_group="dw_wm_alpha",
                show_if=on_switch("dw_wm", "加水印"),
            ),
            Field(
                id="dw_wm_rotate",
                label="旋转角度 (正 = 顺时针)",
                type="slider",
                min=-180,
                max=180,
                step=1,
                default=0,
                row_group="dw_wm_rot",
                show_if=on_switch("dw_wm", "加水印"),
            ),
            Field(
                id="dw_wm_tile",
                label="平铺整张图",
                type="checkbox",
                default=False,
                row_group="dw_wm_rot",
                show_if=on_switch("dw_wm", "加水印"),
            ),
            Field(
                id="dw_wm_pos",
                label="水印位置",
                type="radio",
                options=GRID_POSITIONS,
                default="右下",
                row_group="dw_wm_place",
                show_if=[on_switch("dw_wm", "加水印"), {"field": "dw_wm_tile", "equals": False}],
            ),
            Field(
                id="dw_wm_margin",
                label="边距 px",
                type="number",
                default=24,
                min=0,
                step=1,
                row_group="dw_wm_place",
                show_if=[on_switch("dw_wm", "加水印"), {"field": "dw_wm_tile", "equals": False}],
            ),
            Field(
                id="dw_wm_gap",
                label="平铺间距 px",
                type="number",
                default=60,
                min=0,
                step=1,
                row_group="dw_wm_place",
                show_if=[on_switch("dw_wm", "加水印"), {"field": "dw_wm_tile", "equals": True}],
            ),
            # ===== 边框 =====
            section("dw_sec_frame", "边框", "圆角 / 阴影 / 圆形头像会产生透明区域, 只有 png / webp 能保留"),
            switch("dw_frame", "边框", "加边框", "不加边框"),
            Field(
                id="dw_frame_type",
                label="边框样式",
                type="radio",
                options=decorate.FRAME_TYPES,
                default="纯色描边",
                show_if=on_switch("dw_frame", "加边框"),
            ),
            Field(
                id="dw_frame_width",
                label="宽度 px",
                type="number",
                default=20,
                min=0,
                step=1,
                row_group="dw_frame_row",
                show_if=on_switch("dw_frame", "加边框"),
            ),
            Field(
                id="dw_frame_color",
                label="边框颜色",
                type="color",
                default="#ffffff",
                row_group="dw_frame_row",
                show_if=[on_switch("dw_frame", "加边框"), {"field": "dw_frame_type", "not_in": ["圆角", "阴影"]}],
            ),
            Field(
                id="dw_frame_bottom",
                label="底边加宽 px (拍立得)",
                type="number",
                default=60,
                min=0,
                step=1,
                row_group="dw_frame_row",
                show_if=[on_switch("dw_frame", "加边框"), {"field": "dw_frame_type", "equals": "拍立得"}],
            ),
            section("dw_sec_output", "输出", "默认不覆盖原图, 输出为「原名 + 后缀」; 透明背景请选 png / webp"),
            Field(id="dw_out_format", label="输出格式", type="radio", options=OUTPUT_FORMATS, default="保持原格式"),
            Field(
                id="dw_quality",
                label="质量 (jpg / webp / avif)",
                type="slider",
                min=1,
                max=100,
                step=1,
                default=95,
                row_group="dw_out_meta",
            ),
            Field(
                id="dw_keep_meta",
                label="保留元数据",
                type="checkbox",
                default=True,
                row_group="dw_out_meta",
            ),
        ]
        + fields("dw_", "_deco", OUTPUT_FIELDS)
        + [
            info(
                "dw_info",
                "说明",
                f"· 水印文字占位符: {decorate.PLACEHOLDERS}<br>"
                "· <b>执行顺序是「先加边框, 再打水印」</b>: 水印落在最终画布上, 所见即所得<br>"
                "· 图片水印按「占图片宽度百分比」缩放, 透明底 png 做水印最自然<br>"
                "· 文字水印建议开描边, 深浅背景上都能看清<br>"
                "· <b>透明相关</b>: 圆角 / 阴影 / 圆形头像 会产生透明区域, 只有 png / webp 能保留, "
                "转 jpg / bmp 时透明区会按「压缩转换」的底色规则压平 (默认白色)<br>"
                "· 添加边框 / 水印会重写像素, 图片里的隐写数据会失效 (结果里会提示)",
            ),
        ],
        actions=[
            Action(
                id="run",
                label="添加水印 / 边框",
                uses_novelai=False,
                inputs=[
                    "dw_path",
                    "dw_image",
                    "dw_wm",
                    "dw_wm_type",
                    "dw_wm_text",
                    "dw_wm_size",
                    "dw_wm_color",
                    "dw_wm_stroke",
                    "dw_wm_stroke_width",
                    "dw_wm_stroke_color",
                    "dw_wm_image",
                    "dw_wm_scale",
                    "dw_wm_opacity",
                    "dw_wm_rotate",
                    "dw_wm_tile",
                    "dw_wm_pos",
                    "dw_wm_margin",
                    "dw_wm_gap",
                    "dw_frame",
                    "dw_frame_type",
                    "dw_frame_width",
                    "dw_frame_color",
                    "dw_frame_bottom",
                    "dw_out_format",
                    "dw_quality",
                    "dw_keep_meta",
                    "dw_suffix",
                    "dw_overwrite",
                    "dw_out_dir",
                ],
                handler=lambda v: decorate.decorate_action(strip_prefix(v, "dw_")),
            ),
        ],
    )


# ---------------------------------------------------------------- 整理


def organize_panel() -> Panel:
    scanning = on_switch("og_quality", "筛查")
    return Panel(
        id="organize",
        title="整理",
        icon="🗂️",
        description="感知哈希查重 + 低质量筛查 · 模板化批量重命名 · 生成参数清单 CSV",
        fields=[
            section("og_sec_input", "输入", "下面三个动作都基于这个目录"),
            Field(
                id="og_path",
                label="处理目录",
                type="path",
                folder=True,
                file=False,
                placeholder="选择要查重 / 重命名 / 导出清单的目录",
            ),
            section("og_sec_dedupe", "查重与质量筛查", "dHash 感知哈希 + 汉明距离, 重编码过的副本也能识别"),
            Field(
                id="og_threshold",
                label="相似度阈值 (汉明距离)",
                type="slider",
                min=0,
                max=32,
                step=1,
                default=5,
                placeholder="0 = 完全一致, 越大越宽松",
            ),
            switch("og_quality", "质量筛查", "筛查", "不筛查"),
            Field(
                id="og_quality_items",
                label="筛查项",
                type="checkbox_group",
                options=organize.QUALITY_ITEMS,
                default=list(organize.QUALITY_ITEMS),
                show_if=scanning,
            ),
            Field(
                id="og_blur_threshold",
                label="模糊阈值 (越小越模糊)",
                type="number",
                default=8,
                min=0,
                step=0.5,
                row_group="og_th1",
                show_if=[scanning, {"field": "og_quality_items", "contains": "模糊"}],
            ),
            Field(
                id="og_flat_threshold",
                label="纯色阈值",
                type="number",
                default=3,
                min=0,
                step=0.5,
                row_group="og_th1",
                show_if=[scanning, {"field": "og_quality_items", "contains": "纯色"}],
            ),
            Field(
                id="og_exposure_threshold",
                label="过曝阈值",
                type="number",
                default=245,
                min=0,
                max=255,
                step=1,
                row_group="og_th2",
                show_if=[scanning, {"field": "og_quality_items", "contains": "过曝"}],
            ),
            Field(
                id="og_dark_threshold",
                label="欠曝阈值",
                type="number",
                default=12,
                min=0,
                max=255,
                step=1,
                row_group="og_th2",
                show_if=[scanning, {"field": "og_quality_items", "contains": "欠曝"}],
            ),
            Field(
                id="og_handle",
                label="重复图处理",
                type="radio",
                options=["仅报告", "把重复图移到子目录"],
                default="仅报告",
                row_group="og_handle_row",
            ),
            Field(
                id="og_dup_dir",
                label="归集子目录",
                type="text",
                default="_duplicates",
                row_group="og_handle_row",
                show_if={"field": "og_handle", "equals": "把重复图移到子目录"},
            ),
            section("og_sec_rename", "批量重命名", "默认只预览不落盘, 确认无误再切到「直接重命名」"),
            Field(id="og_mode", label="重命名方式", type="radio", options=["仅预览", "直接重命名"], default="仅预览"),
            Field(
                id="og_template",
                label="文件名模板",
                type="text",
                default="{index:03d}_{name}",
                placeholder="{index:03d} 序号 · {name} 原名 · {date} 日期 · {seed} 种子 · {prompt} 提示词",
                row_group="og_template_row",
            ),
            Field(
                id="og_start",
                label="序号起始值",
                type="number",
                default=1,
                min=0,
                step=1,
                row_group="og_template_row",
            ),
            Field(id="og_prefix", label="前缀", type="text", default="", row_group="og_affix"),
            Field(id="og_suffix", label="后缀", type="text", default="", row_group="og_affix"),
            Field(
                id="og_prompt_len",
                label="{prompt} 截取字符数",
                type="number",
                default=40,
                min=8,
                step=1,
                row_group="og_affix",
            ),
            section("og_sec_manifest", "生成参数清单", "每张图一行, 含尺寸 / 体积 / prompt / seed / 步数 等"),
            Field(id="og_name", label="清单文件名 (不含扩展名)", type="text", default="manifest"),
            section("og_sec_output", "输出", "报告与清单的落盘目录"),
            Field(id="og_out_dir", label="输出目录 (留空 = 处理目录自身)", type="path", folder=True, file=False),
        ]
        + [
            info(
                "og_info",
                "说明",
                "· <b>查重</b>用 dHash (差值感知哈希): 把图缩成 9×8 灰度后逐行比较相邻像素得到 64 位指纹, "
                "再按汉明距离聚类 —— 对「同一张图重新保存 / 轻微缩放 / 改质量」不敏感, 能识别内容重复, "
                "比精确字节比对更实用<br>"
                "· 每组的<b>第一张</b>作为保留图, 其余视为重复; 归集时按文件名依次移动, 重名会自动加序号<br>"
                "· <b>质量筛查</b>的清晰度用边缘强度标准差衡量 (越低越模糊), 另配纯色 / 过曝 / 欠曝判定; "
                "纯色图边缘强度也低, 会被同时标记为「纯色」与「模糊」<br>"
                "· 报告导出为 <code>输出目录/dedupe_report.csv</code> (utf-8-sig, Excel 双击不乱码)<br>"
                "· <b>重命名</b>默认「仅预览」, 不会改动任何文件; 切到「直接重命名」才落盘, "
                "重名会自动加序号避免覆盖<br>"
                "· 模板占位符: <code>{index}</code> <code>{index:03d}</code> 序号 · <code>{name}</code> 原文件名 · "
                "<code>{date}</code> <code>{time}</code> <code>{datetime}</code> 修改时间 · "
                "<code>{w}</code> <code>{h}</code> 尺寸 · <code>{seed}</code> <code>{steps}</code> "
                "<code>{scale}</code> <code>{sampler}</code> <code>{model}</code> <code>{prompt}</code> 生成参数<br>"
                "· 文件名里的 <code>\\ / : * ? \" &lt; &gt; |</code> 会在重命名时自动剔除",
            ),
        ],
        actions=[
            Action(
                id="scan",
                label="扫描查重",
                uses_novelai=False,
                inputs=[
                    "og_path",
                    "og_threshold",
                    "og_quality",
                    "og_quality_items",
                    "og_blur_threshold",
                    "og_flat_threshold",
                    "og_exposure_threshold",
                    "og_dark_threshold",
                    "og_handle",
                    "og_dup_dir",
                    "og_out_dir",
                ],
                handler=lambda v: organize.dedupe_action(strip_prefix(v, "og_")),
            ),
            Action(
                id="rename",
                label="批量重命名",
                uses_novelai=False,
                stop=False,
                inputs=[
                    "og_path",
                    "og_mode",
                    "og_template",
                    "og_start",
                    "og_prefix",
                    "og_suffix",
                    "og_prompt_len",
                ],
                handler=lambda v: organize.rename_action(strip_prefix(v, "og_")),
            ),
            Action(
                id="manifest",
                label="生成参数清单",
                uses_novelai=False,
                stop=False,
                inputs=["og_path", "og_name", "og_out_dir"],
                handler=lambda v: organize.manifest_action(strip_prefix(v, "og_")),
            ),
        ],
    )


def extra_panels() -> list[Panel]:
    """按面板页签顺序返回新增的 5 个面板。"""
    return [color_panel(), compose_panel(), animate_panel(), decorate_panel(), organize_panel()]
