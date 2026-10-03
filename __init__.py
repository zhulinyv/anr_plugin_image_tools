"""图片工具插件: 变换 / 压缩转换 / 调色 / 拼合 / 动图 / 装饰 / 整理 / 元数据与隐写。

面板 10 个: 变换 · 压缩转换 · 调色 · 拼合 · 动图 · 装饰 · 整理 · 读取元数据 · 写入数据 · 清除元数据。
前 5 个面板的声明写在 `__init__.py` (register 内), 后加的 5 个拆到 `panels_extra.py`;
字段模板与分区标题助手在 `decl.py`, 共用工具在 `common.py`。
本文件只做面板声明, 具体处理逻辑分散在各功能模块: img_ops (变换/压缩) · enhance (调色) ·
compose (拼合/分割) · animate (动图) · decorate (水印/边框) · organize (查重/重命名/清单) ·
meta (元数据读写清除) · lsb (隐写)。

面板字段 id 按面板分组带前缀 (变换 tf_ / 压缩转换 cv_ / 调色 co_ / 拼合 cg_ / 动图 an_ /
装饰 dw_ / 整理 og_ / 读取 rd_ / 写入 mw_ / 清除 cl_), 防前端全局 id 串值:
前端按 id 从全局注册表取值, 重名会串值, 处理函数内用 common.strip_prefix 还原成无前缀键名。

排版约定 (改动前先看这三条):
- 左列表单用 type="section" 字段分区 (输入 / 各功能 / 输出), 同类功能的参数划分到同一区域, 不再混在一起;
- 右列顶部是动作的输出框 (Action.show_output, 内含图片画廊 / 汇总 / 逐项明细 / 打开保存目录按钮),
  说明类字段用 column="right_bottom" 排在输出区下方 —— 处理结果里的图片只走输出框这条路,
  所以出图动作绝不能设 show_output=False, 也不要为了显示文字而用 set_field 把结果写进 textarea:
  那样输出框根本不会被创建, 结果里的图片就全都不显示了;
- 只有纯文本结果的面板 (读取元数据) 才用 textarea + set_field 承载结果, 且该 textarea 设 readonly=True
  (结果只给人看/复制; 只读只拦用户, set_field 走 JS 赋值照常写入)。

每个功能分区的第一个字段是该功能的 radio 开关 (decl.switch), 其余参数用 show_if 挂在开关上;
开关是权威 —— 后端也必须按开关判断 (如 img_ops.crop_enabled、enhance._configured),
不能只看参数是否为 0, 否则面板上关掉了, 却会被记住的历史值处理掉。

动作标签可以自带区分用的 emoji, 但**只能带一个**: 前端在标签不带 emoji 时才会补统一前缀
(按钮补 "▶️ "、输出区标题补 "📤 "), 自带则原样用 —— 所以写成 "🗜️📂 压缩并整理" 这种连用两个
emoji 的标签会原封不动显示出来。面板 / 插件图标与报告里的分隔 emoji 只选
web/assets/emoji/72x72 里有本地 SVG 的, 否则前端 Twemoji 统一化会回退成系统原生 emoji,
与其它界面元素风格不一致。

选项类字段一律用 type="radio" 而不是 type="select":
ui.js 的 el() 会把 selected=false 也 setAttribute 成 selected="false" (属性存在即生效),
导致所有 option 都被标记选中、下拉框实际停在最后一项 (现有插件的 select 同样如此),
radio 走的是 CSS class 判断, 不受影响。
"""

from __future__ import annotations

from plugins.anr_plugin_image_tools import img_ops, lsb, meta
from plugins.anr_plugin_image_tools.common import (
    GRID_POSITIONS,
    OUTPUT_FORMATS,
    strip_prefix,
)
from plugins.anr_plugin_image_tools.decl import (
    OUTPUT_FIELDS,
    SOURCE_FIELDS,
    fields,
    info,
    section,
    switch,
)
from plugins.anr_plugin_image_tools.img_ops import (
    CROP_RATIOS,
    FLIP_MODES,
    PAD_MODES,
    RESAMPLE_MODES,
    RESIZE_MODES,
    ROTATE_MODES,
    TRIM_REFS,
)
from plugins.anr_plugin_image_tools.meta import (
    CLEAR_TARGETS,
    PARAM_MODES,
    PARAM_SOURCES,
    PARAM_TRANSFER_KEYS,
)
from plugins.anr_plugin_image_tools.panels_extra import extra_panels
from utils.plugins import Action, Field, Panel, Plugin


def register(plugin: Plugin):
    plugin.title = "图片工具"
    plugin.icon = "🛠️"
    plugin.description = (
        "裁剪 / 旋转 / 翻转 / 缩放 / 压缩 / 格式转换 / 元数据读写清除 / LSB 隐写"
    )

    # ------------------------------------------------------------ 变换
    transform = Panel(
        id="transform",
        title="变换",
        icon="🖼️",
        description="裁剪 (定比例 / 多种定位) / 自动裁边 / 调整长宽 (可锁比例) / 旋转 / 翻转 / 缩放 / 扩展画布",
        fields=[
            section("tf_sec_input", "输入", "单张图片或整个目录批处理"),
            *fields("tf_", "_edit", SOURCE_FIELDS),
            Field(
                id="tf_auto_orient",
                label="自动纠正 EXIF 方向 (手机/相机竖拍图需勾选)",
                type="checkbox",
                default=True,
            ),
            # ===== 裁剪 =====
            section(
                "tf_sec_crop",
                "裁剪",
                "选中「裁剪」后才展开裁剪参数 · 定比例会自动算宽高",
            ),
            # 与其它分区一致: 开关 radio 是权威, 选「不裁剪」时下面的参数全部收起、后端也不会裁剪
            Field(
                id="tf_crop",
                label="裁剪",
                type="radio",
                options=["不裁剪", "裁剪"],
                default="不裁剪",
            ),
            Field(
                id="tf_crop_ratio",
                label="裁剪比例",
                type="radio",
                options=list(CROP_RATIOS),
                default="自由尺寸",
                show_if={"field": "tf_crop", "equals": "裁剪"},
            ),
            Field(
                id="tf_crop_w",
                label="裁剪宽",
                type="number",
                default=0,
                min=0,
                step=1,
                row_group="crop",
                show_if=[
                    {"field": "tf_crop", "equals": "裁剪"},
                    {"field": "tf_crop_ratio", "equals": "自由尺寸"},
                ],
            ),
            Field(
                id="tf_crop_h",
                label="裁剪高",
                type="number",
                default=0,
                min=0,
                step=1,
                row_group="crop",
                show_if=[
                    {"field": "tf_crop", "equals": "裁剪"},
                    {"field": "tf_crop_ratio", "equals": "自由尺寸"},
                ],
            ),
            Field(
                id="tf_crop_percent",
                label="按百分比",
                type="checkbox",
                default=False,
                row_group="crop_opt",
                show_if=[
                    {"field": "tf_crop", "equals": "裁剪"},
                    {"field": "tf_crop_ratio", "equals": "自由尺寸"},
                ],
            ),
            Field(
                id="tf_crop_mode",
                label="裁剪定位方式",
                type="radio",
                options=["居中", "九宫格", "指定起点"],
                default="居中",
                row_group="crop_opt",
                show_if={"field": "tf_crop", "equals": "裁剪"},
            ),
            # 三个字段互斥显示 (九宫格 / 指定起点), 并排后任一时刻只有一组可见:
            # 单独可见时自动占满整行, 整组隐藏时前端会把行一起收起
            Field(
                id="tf_crop_pos",
                label="九宫格位置",
                type="radio",
                options=GRID_POSITIONS,
                default="居中",
                row_group="crop_xy",
                show_if={"field": "tf_crop_mode", "equals": "九宫格"},
            ),
            Field(
                id="tf_crop_x",
                label="起点 X",
                type="number",
                default=0,
                min=0,
                step=1,
                row_group="crop_xy",
                show_if={"field": "tf_crop_mode", "equals": "指定起点"},
            ),
            Field(
                id="tf_crop_y",
                label="起点 Y",
                type="number",
                default=0,
                min=0,
                step=1,
                row_group="crop_xy",
                show_if={"field": "tf_crop_mode", "equals": "指定起点"},
            ),
            # ===== 自动裁边 =====
            section(
                "tf_sec_trim",
                "自动裁边",
                "裁掉四周的纯色 / 透明边 (容差内视为边色), 不处理内部空洞",
            ),
            switch("tf_trim", "自动裁边", "裁边", "不裁边"),
            Field(
                id="tf_trim_ref",
                label="边色来源",
                type="radio",
                options=TRIM_REFS,
                default="边角取样",
                row_group="trim_row",
                show_if={"field": "tf_trim", "equals": "裁边"},
            ),
            Field(
                id="tf_trim_tolerance",
                label="容差 (0 = 同色)",
                type="slider",
                min=0,
                max=255,
                step=1,
                default=8,
                row_group="trim_row",
                show_if={"field": "tf_trim", "equals": "裁边"},
            ),
            Field(
                id="tf_trim_pad",
                label="回退边距 px",
                type="number",
                default=0,
                min=0,
                step=1,
                row_group="trim_row",
                show_if={"field": "tf_trim", "equals": "裁边"},
            ),
            # ===== 调整长宽 =====
            section(
                "tf_sec_size",
                "调整长宽",
                "独立的尺寸步骤 · 锁比例时按适配框内推导另一维",
            ),
            Field(
                id="tf_set_size_mode",
                label="调整长宽",
                type="radio",
                options=["不调整", "按宽高", "仅改宽度", "仅改高度"],
                default="不调整",
            ),
            Field(
                id="tf_set_size_w",
                label="目标宽",
                type="number",
                default=832,
                min=1,
                step=1,
                row_group="ss_wh",
                show_if={"field": "tf_set_size_mode", "in": ["按宽高", "仅改宽度"]},
            ),
            Field(
                id="tf_set_size_h",
                label="目标高",
                type="number",
                default=1216,
                min=1,
                step=1,
                row_group="ss_wh",
                show_if={"field": "tf_set_size_mode", "in": ["按宽高", "仅改高度"]},
            ),
            Field(
                id="tf_set_size_lock",
                label="锁定宽高比",
                type="checkbox",
                default=True,
                row_group="ss_lock",
                show_if={"field": "tf_set_size_mode", "not_equals": "不调整"},
            ),
            Field(
                id="tf_set_size_keep_ratio",
                label="适配框内 (关 = 撑满)",
                type="checkbox",
                default=True,
                row_group="ss_lock",
                show_if={"field": "tf_set_size_lock", "equals": True},
            ),
            # ===== 旋转 / 翻转 =====
            section(
                "tf_sec_rotate",
                "旋转与翻转",
                "任意角度为正时顺时针旋转, 可扩展画布避免裁掉四角",
            ),
            Field(
                id="tf_rotate",
                label="旋转",
                type="radio",
                options=ROTATE_MODES,
                default="不旋转",
            ),
            Field(
                id="tf_angle_value",
                label="任意角度 (正 = 顺时针)",
                type="slider",
                min=-180,
                max=180,
                step=1,
                default=0,
                row_group="rot_extra",
                show_if={"field": "tf_rotate", "equals": "任意角度"},
            ),
            Field(
                id="tf_rotate_expand",
                label="扩展画布 (关 = 裁四角)",
                type="checkbox",
                default=True,
                row_group="rot_extra",
                show_if={"field": "tf_rotate", "equals": "任意角度"},
            ),
            Field(
                id="tf_flip",
                label="镜像翻转",
                type="radio",
                options=FLIP_MODES,
                default="不翻转",
            ),
            # ===== 缩放 =====
            section(
                "tf_sec_resize",
                "缩放",
                "按倍数 / 最长边 / 指定尺寸 · 下面的重采样滤镜同时作用于「调整长宽」",
            ),
            Field(
                id="tf_resize_mode",
                label="缩放方式",
                type="radio",
                options=RESIZE_MODES,
                default="不缩放",
            ),
            Field(
                id="tf_resize_factor",
                label="缩放倍数 (0.5 = 一半)",
                type="number",
                default=1,
                min=0.01,
                step=0.05,
                row_group="rz_val",
                show_if={"field": "tf_resize_mode", "equals": "按倍数"},
            ),
            Field(
                id="tf_resize_max",
                label="最长边像素",
                type="number",
                default=1024,
                min=1,
                step=1,
                row_group="rz_val",
                show_if={"field": "tf_resize_mode", "equals": "指定最长边"},
            ),
            Field(
                id="tf_resize_w",
                label="宽",
                type="number",
                default=832,
                min=1,
                step=1,
                row_group="rz_wh",
                show_if={"field": "tf_resize_mode", "equals": "指定尺寸"},
            ),
            Field(
                id="tf_resize_h",
                label="高",
                type="number",
                default=1216,
                min=1,
                step=1,
                row_group="rz_wh",
                show_if={"field": "tf_resize_mode", "equals": "指定尺寸"},
            ),
            Field(
                id="tf_keep_ratio",
                label="保持宽高比",
                type="checkbox",
                default=True,
                row_group="rz_flags",
                show_if={"field": "tf_resize_mode", "equals": "指定尺寸"},
            ),
            Field(
                id="tf_shrink_only",
                label="仅缩小不放大",
                type="checkbox",
                default=False,
                row_group="rz_flags",
                show_if=[{"field": "tf_resize_mode", "in": ["指定最长边", "指定尺寸"]}],
            ),
            Field(
                id="tf_resample",
                label="重采样滤镜",
                type="radio",
                options=list(RESAMPLE_MODES),
                default="Lanczos (最清晰)",
            ),
            # ===== 扩展画布 =====
            section(
                "tf_sec_pad",
                "扩展画布",
                "补边不缩放 · 把图贴到更大的画布上, 用于补成目标比例 / 目标尺寸",
            ),
            switch("tf_pad", "扩展画布", "扩展", "不扩展"),
            Field(
                id="tf_pad_mode",
                label="扩展方式",
                type="radio",
                options=PAD_MODES,
                default="按比例补边",
                show_if={"field": "tf_pad", "equals": "扩展"},
            ),
            Field(
                id="tf_pad_ratio_w",
                label="目标比例 宽",
                type="number",
                default=1,
                min=1,
                step=1,
                row_group="pad_ratio",
                show_if=[
                    {"field": "tf_pad", "equals": "扩展"},
                    {"field": "tf_pad_mode", "equals": "按比例补边"},
                ],
            ),
            Field(
                id="tf_pad_ratio_h",
                label="高",
                type="number",
                default=1,
                min=1,
                step=1,
                row_group="pad_ratio",
                show_if=[
                    {"field": "tf_pad", "equals": "扩展"},
                    {"field": "tf_pad_mode", "equals": "按比例补边"},
                ],
            ),
            Field(
                id="tf_pad_w",
                label="目标画布 宽",
                type="number",
                default=832,
                min=1,
                step=1,
                row_group="pad_wh",
                show_if=[
                    {"field": "tf_pad", "equals": "扩展"},
                    {"field": "tf_pad_mode", "equals": "按尺寸补边"},
                ],
            ),
            Field(
                id="tf_pad_h",
                label="高",
                type="number",
                default=1216,
                min=1,
                step=1,
                row_group="pad_wh",
                show_if=[
                    {"field": "tf_pad", "equals": "扩展"},
                    {"field": "tf_pad_mode", "equals": "按尺寸补边"},
                ],
            ),
            Field(
                id="tf_pad_pos",
                label="原图位置",
                type="radio",
                options=GRID_POSITIONS,
                default="居中",
                row_group="pad_misc",
                show_if={"field": "tf_pad", "equals": "扩展"},
            ),
            Field(
                id="tf_pad_bg",
                label="补边底色",
                type="radio",
                options=["自定义颜色", "透明"],
                default="自定义颜色",
                row_group="pad_misc",
                show_if={"field": "tf_pad", "equals": "扩展"},
            ),
            Field(
                id="tf_pad_color",
                label="补边颜色",
                type="color",
                default="#ffffff",
                row_group="pad_misc",
                show_if=[
                    {"field": "tf_pad", "equals": "扩展"},
                    {"field": "tf_pad_bg", "equals": "自定义颜色"},
                ],
            ),
            # ===== 输出 =====
            section(
                "tf_sec_output",
                "输出",
                "默认不覆盖原图, 输出为「原名 + 后缀」; 直接勾「覆盖原图」则改写原文件",
            ),
            Field(
                id="tf_out_format",
                label="输出格式",
                type="radio",
                options=OUTPUT_FORMATS,
                default="保持原格式",
            ),
            Field(
                id="tf_quality",
                label="质量 (jpg / webp / avif)",
                type="slider",
                min=1,
                max=100,
                step=1,
                default=95,
                row_group="out_meta",
            ),
            Field(
                id="tf_keep_meta",
                label="保留元数据",
                type="checkbox",
                default=True,
                row_group="out_meta",
            ),
        ]
        + fields("tf_", "_edit", OUTPUT_FIELDS)
        + [
            # 说明排在输出区下方 (column="right_bottom"), 不挤占处理结果的位置
            info(
                "tf_info",
                "处理顺序与说明",
                "依次执行: <b>EXIF 方向纠正</b> → <b>裁剪</b> → <b>自动裁边</b> → <b>调整长宽</b> → <b>旋转</b> → "
                "<b>翻转</b> → <b>缩放</b> → <b>扩展画布</b> (与面板从上到下一致, 便于对着界面推算结果)<br>"
                "· <b>EXIF 方向纠正</b>: 手机/相机竖拍时, 像素常是横着的, 仅靠 EXIF 的 Orientation 标签记录正确方向 "
                "(看图软件会自动摆正, 但像素本身没转)。开启后会按该标签真正旋转像素, 避免后续裁剪/旋转错位; "
                "图片本身已正向时勾选也不影响<br>"
                "· 参数保持默认 (不裁剪 / 不裁边 / 不扩展 / 不旋转 / 不翻转 / 不缩放 / 不调整) 的步骤会被跳过<br>"
                "· <b>裁剪</b>: 先选「裁剪」展开参数; <b>定比例</b>时会自动算出能塞进原图的最大同比例矩形, "
                "选「自由尺寸」时才用手填宽高 (都 &gt;0 才执行); 定位支持居中 / 九宫格九点 / 指定起点<br>"
                "· <b>自动裁边</b>: 只按「整行整列都是边色」的外框收缩, 不处理内部空洞, 比阈值二值化安全; "
                "容差内视为同色, 边色可取自四角出现最多的颜色 / 白 / 黑 / 透明<br>"
                "· <b>调整长宽</b>: 独立的尺寸步骤, 可「按宽高 / 仅改宽 / 仅改高」, 锁比例时按适配框内推导另一维<br>"
                "· <b>扩展画布</b>: 只补边不缩放, 用于把图补成 1:1 这类目标比例 (自动取能容纳原图的最小同比例画布) "
                "或指定尺寸; 补边颜色可选透明 (透明底只有 png / webp 能保留)<br>"
                "· 缩放倍数 &gt;1 为放大, &lt;1 为缩小; EXIF 方向纠正后可正常旋转手机竖拍图<br>"
                "· 裁剪 / 裁边 / 旋转 / 缩放 / 调整长宽 / 扩展画布都会破坏图片里的 LSB 隐写数据 (会在结果里提示)",
            ),
        ],
        actions=[
            Action(
                id="apply",
                label="应用变换",
                uses_novelai=False,
                inputs=[
                    "tf_path",
                    "tf_image",
                    "tf_crop",
                    "tf_crop_ratio",
                    "tf_crop_w",
                    "tf_crop_h",
                    "tf_crop_percent",
                    "tf_crop_mode",
                    "tf_crop_pos",
                    "tf_crop_x",
                    "tf_crop_y",
                    "tf_trim",
                    "tf_trim_ref",
                    "tf_trim_tolerance",
                    "tf_trim_pad",
                    "tf_set_size_mode",
                    "tf_set_size_w",
                    "tf_set_size_h",
                    "tf_set_size_lock",
                    "tf_set_size_keep_ratio",
                    "tf_rotate",
                    "tf_angle_value",
                    "tf_rotate_expand",
                    "tf_flip",
                    "tf_resize_mode",
                    "tf_resize_factor",
                    "tf_resize_max",
                    "tf_resize_w",
                    "tf_resize_h",
                    "tf_keep_ratio",
                    "tf_shrink_only",
                    "tf_resample",
                    "tf_pad",
                    "tf_pad_mode",
                    "tf_pad_ratio_w",
                    "tf_pad_ratio_h",
                    "tf_pad_w",
                    "tf_pad_h",
                    "tf_pad_pos",
                    "tf_pad_bg",
                    "tf_pad_color",
                    "tf_auto_orient",
                    "tf_out_format",
                    "tf_quality",
                    "tf_keep_meta",
                    "tf_suffix",
                    "tf_overwrite",
                    "tf_out_dir",
                ],
                # 结果 (图片 + 汇总 + 逐项明细) 由右列输出框显示:
                # show_output 保持默认 True, 且不要用 set_field 写进文本框 (那样输出框不会被创建, 图片不显示)
                handler=lambda v: img_ops.transform_action(strip_prefix(v, "tf_")),
            ),
        ],
    )

    # ------------------------------------------------------------ 压缩 / 格式转换
    convert = Panel(
        id="convert",
        title="压缩转换",
        icon="🗜️",
        description="压缩体积 (支持目标大小) / 转换格式 / 限制最长边, 支持批处理",
        fields=[
            section("cv_sec_input", "输入", "单张图片或整个目录批处理"),
            *fields("cv_", "_out", SOURCE_FIELDS),
            section(
                "cv_sec_format",
                "压缩与格式",
                "png 无损 · jpg / webp / avif 支持质量与目标体积",
            ),
            Field(
                id="cv_out_format",
                label="输出格式",
                type="radio",
                options=OUTPUT_FORMATS,
                default="保持原格式",
            ),
            Field(
                id="cv_quality",
                label="质量 (jpg / webp / avif)",
                type="slider",
                min=1,
                max=100,
                step=1,
                default=90,
                row_group="cv_level",
            ),
            Field(
                id="cv_png_level",
                label="PNG 压缩级别 (0 快 - 9 小)",
                type="slider",
                min=0,
                max=9,
                step=1,
                default=6,
                row_group="cv_level",
            ),
            Field(
                id="cv_target_kb",
                label="目标体积 KB (0 = 不限)",
                type="number",
                default=0,
                min=0,
                step=1,
                row_group="cv_limit",
                placeholder="0 = 不限, 仅 jpg / webp / avif",
            ),
            Field(
                id="cv_max_side",
                label="限制最长边 (0 = 不限)",
                type="number",
                default=0,
                min=0,
                step=1,
                row_group="cv_limit",
                placeholder="0 = 不限, 仅缩小",
            ),
            Field(
                id="cv_background",
                label="转 jpg / bmp 时底色",
                type="radio",
                options=["白色", "黑色"],
                default="白色",
                row_group="cv_bg_meta",
            ),
            Field(
                id="cv_keep_meta",
                label="保留元数据",
                type="checkbox",
                default=True,
                row_group="cv_bg_meta",
            ),
            section("cv_sec_thumb", "缩略图", "在原图旁边另存一份小图, 方便做图库预览"),
            switch("cv_thumb", "缩略图", "生成缩略图", "不生成"),
            Field(
                id="cv_thumb_side",
                label="最长边 px",
                type="number",
                default=512,
                min=16,
                step=1,
                row_group="cv_thumb_row",
                show_if={"field": "cv_thumb", "equals": "生成缩略图"},
            ),
            Field(
                id="cv_thumb_dir",
                label="子目录名",
                type="text",
                default="thumbs",
                placeholder="相对主输出同级",
                row_group="cv_thumb_row",
                show_if={"field": "cv_thumb", "equals": "生成缩略图"},
            ),
            Field(
                id="cv_thumb_format",
                label="格式",
                type="radio",
                options=["保持原格式", "jpg", "webp", "png"],
                default="保持原格式",
                row_group="cv_thumb_row",
                show_if={"field": "cv_thumb", "equals": "生成缩略图"},
            ),
            section(
                "cv_sec_output",
                "输出",
                "默认不覆盖原图, 输出为「原名 + 后缀」; 也可指定输出目录",
            ),
        ]
        + fields("cv_", "_out", OUTPUT_FIELDS)
        + [
            info(
                "cv_info",
                "说明",
                "· <b>保持原格式</b> 时按输出后缀推断, 默认沿用原扩展名<br>"
                "· png 无损; jpg / webp / avif 支持质量与目标体积<br>"
                "· 目标体积用二分法自动调整质量, 达不到会给出提示<br>"
                "· 限制最长边相当于等比缩小 (放大请用「变换」面板)<br>"
                "· <b>缩略图</b>会另存到「主输出所在目录 / 子目录名 /」下, 用同一文件名; "
                "缩略图<b>不带元数据</b> (体积更小, 也不会有生成参数)",
            ),
        ],
        actions=[
            Action(
                id="run",
                label="压缩 / 转换",
                uses_novelai=False,
                inputs=[
                    "cv_path",
                    "cv_image",
                    "cv_out_format",
                    "cv_quality",
                    "cv_png_level",
                    "cv_target_kb",
                    "cv_max_side",
                    "cv_background",
                    "cv_keep_meta",
                    "cv_thumb",
                    "cv_thumb_side",
                    "cv_thumb_dir",
                    "cv_thumb_format",
                    "cv_suffix",
                    "cv_overwrite",
                    "cv_out_dir",
                ],
                handler=lambda v: img_ops.convert_action(strip_prefix(v, "cv_")),
            ),
        ],
    )

    # ------------------------------------------------------------ 读取元数据与隐写
    meta_read = Panel(
        id="meta_read",
        title="读取元数据",
        icon="🔍",
        description="一次性读取全部: PNG 文本块 / NovelAI 隐写 / 自定义隐写载荷 / EXIF / ICC / XMP, 并摘要生成参数",
        fields=[
            section("rd_sec_input", "输入", "单张图片或整个目录批处理"),
            *fields("rd_", "_meta", SOURCE_FIELDS),
            section(
                "rd_sec_password",
                "隐写解密",
                "加密的自定义隐写载荷需要密码才能读出内容",
            ),
            Field(
                id="rd_password",
                label="隐写密码 (留空 = 不解密; 读取加密图片时填这里)",
                type="text",
                placeholder="PBKDF2 派生密钥 + SHA256 流加密 + HMAC 校验",
            ),
            # 纯文本结果面板: 结果由文本域承载 (不产出图片, 不需要输出框)。
            # readonly=True —— 这里只是展示读取结果, 让用户能选中复制即可;
            # 动作的 set_field 走 JS 赋值, 不受只读影响, 结果照样能写进来。
            Field(
                id="rd_out",
                label="读取结果 (只读, 可选中复制)",
                type="textarea",
                rows=20,
                column="right_bottom",
                readonly=True,
                placeholder="点击「读取元数据与隐写」后在此显示 (含隐写载荷预览与导出路径)",
            ),
            Field(
                id="rd_info",
                label="说明",
                type="info",
                column="right_bottom",
                default="读取不修改图片, 结果也会导出到 <code>outputs/lsb_extract/</code> (二进制内容不截断)。<br>"
                "加密的自定义隐写载荷需填「隐写密码」才能解密; 读取时自动尝试 A/R/G/B 通道, 无需记住写入参数。<br>"
                "写入 <code>Comment</code> 键 (值为 NAI 参数 JSON) 的图片, 会在此自动摘要出 prompt / seed / steps 等。",
            ),
        ],
        actions=[
            Action(
                id="read",
                label="读取元数据与隐写",
                uses_novelai=False,
                stop=False,
                show_output=False,
                set_field="rd_out",
                inputs=["rd_path", "rd_image", "rd_password"],
                handler=lambda v: meta.read_action(strip_prefix(v, "rd_")),
            ),
        ],
    )

    # ------------------------------------------------------------ 写入元数据 / 隐写 (开关切换)
    meta_write = Panel(
        id="meta_write",
        title="写入数据",
        icon="🏷️",
        description="把键值写进元数据; 打开「写入隐写」开关后改把文本/文件藏进像素最低位 (可选密码与压缩)",
        fields=[
            section("mw_sec_input", "输入", "单张图片或整个目录批处理"),
            *fields("mw_", "_meta", SOURCE_FIELDS),
            section("mw_sec_meta", "元数据", "手填键值, 或从参考图搬运生成参数"),
            # 参数来源仍是 radio 开关: 选「从参考图搬运」时下面的手填框收起, 只有参考图参数生效
            Field(
                id="mw_param_source",
                label="参数来源",
                type="radio",
                options=PARAM_SOURCES,
                default="手填 / JSON",
            ),
            Field(
                id="mw_meta_text",
                label="写入元数据内容 (JSON 或 每行一个 `键=值`)",
                type="textarea",
                rows=6,
                placeholder='{"Title": "我的作品", "Artist": "xxxxx"}\n或\nTitle=我的作品\nArtist=xxxxx',
                show_if={"field": "mw_param_source", "equals": "手填 / JSON"},
            ),
            Field(
                id="mw_ref_image",
                label="参数参考图 (从这张图搬运生成参数)",
                type="image",
                show_if={"field": "mw_param_source", "equals": "从参考图搬运"},
            ),
            Field(
                id="mw_param_mode",
                label="搬运方式",
                type="radio",
                options=PARAM_MODES,
                default="整份复制",
                row_group="mw_param_pick",
                show_if={"field": "mw_param_source", "equals": "从参考图搬运"},
            ),
            Field(
                id="mw_param_keys",
                label="要搬运的字段",
                type="checkbox_group",
                options=PARAM_TRANSFER_KEYS,
                default=[
                    "prompt",
                    "uc",
                    "seed",
                    "steps",
                    "scale",
                    "sampler",
                    "noise_schedule",
                    "model",
                ],
                row_group="mw_param_pick",
                show_if=[
                    {"field": "mw_param_source", "equals": "从参考图搬运"},
                    {"field": "mw_param_mode", "equals": "只搬指定字段"},
                ],
            ),
            section(
                "mw_sec_stego",
                "隐写数据",
                "开关开启后才写入像素最低位, 输出统一转存 PNG",
            ),
            Field(
                id="mw_use_stego",
                label="写入隐写 (关 = 只写元数据)",
                type="checkbox",
                default=False,
            ),
            Field(
                id="mw_text",
                label="隐写数据: 文本 (藏进像素最低位)",
                type="textarea",
                rows=5,
                placeholder="写入的文本会被压缩后藏进像素最低位, 肉眼不可见",
                show_if={"field": "mw_use_stego", "equals": True},
            ),
            Field(
                id="mw_file",
                label="或隐写文件内容 (优先于文本)",
                type="path",
                folder=False,
                file=True,
                show_if={"field": "mw_use_stego", "equals": True},
            ),
            Field(
                id="mw_password",
                label="隐写密码 (留空 = 不加密)",
                type="text",
                placeholder="PBKDF2 派生密钥 + SHA256 流加密 + HMAC 校验",
                row_group="mw_stego_row",
                show_if={"field": "mw_use_stego", "equals": True},
            ),
            Field(
                id="mw_channel",
                label="写入通道",
                type="radio",
                options=["R", "G", "B", "A"],
                default="R",
                row_group="mw_stego_row",
                show_if={"field": "mw_use_stego", "equals": True},
            ),
            Field(
                id="mw_compress",
                label="写入前压缩",
                type="checkbox",
                default=True,
                row_group="mw_stego_row",
                show_if={"field": "mw_use_stego", "equals": True},
            ),
            section(
                "mw_sec_output",
                "输出",
                "默认不覆盖原图, 输出为「原名 + 后缀」; 也可指定输出目录",
            ),
            # row_group 与共享输出字段同名 → 和「输出后缀 / 覆盖原图」并成一行
            Field(
                id="mw_quality",
                label="质量 (转 jpg / webp 时)",
                type="slider",
                min=1,
                max=100,
                step=1,
                default=95,
                row_group="mw_out_flags",
            ),
        ]
        + fields("mw_", "_meta", OUTPUT_FIELDS)
        + [
            Field(
                id="mw_info",
                label="说明",
                type="info",
                column="right_bottom",
                default="<b>写入元数据</b>: 键值写进 PNG 文本块; 键名是下面已知名称时会同时写进 EXIF "
                "(png / jpg / webp / avif / tiff):<br>"
                "<code>ImageDescription</code> <code>DocumentName</code> <code>Artist</code> <code>Copyright</code> "
                "<code>Software</code> <code>Make</code> <code>Model</code> <code>DateTime</code> "
                "<code>HostComputer</code> <code>UserComment</code><br>"
                "写入 <code>Comment</code> 键 (值为 NAI 参数 JSON) 即可让自带的法术解析重新读到生成参数。<br><br>"
                "<b>写入隐写</b> (开关开启时): 每像素只改 1 个最低位, 肉眼不可见; 默认写 <b>R 通道</b> "
                "(NovelAI 的隐写占 Alpha 通道, 避开可互不干扰), 读取时自动尝试 A/R/G/B, 无需记住写入参数。<br>"
                "输出统一转存 <b>PNG</b>: 重新压缩 / 裁剪 / 缩放会破坏隐写数据。"
                "可写容量 ≈ 宽 × 高 ÷ 8 字节 (832×1216 约 123 KB)。",
            ),
        ],
        actions=[
            Action(
                id="write",
                label="写入",
                uses_novelai=False,
                inputs=[
                    "mw_path",
                    "mw_image",
                    "mw_param_source",
                    "mw_meta_text",
                    "mw_ref_image",
                    "mw_param_mode",
                    "mw_param_keys",
                    "mw_use_stego",
                    "mw_text",
                    "mw_file",
                    "mw_password",
                    "mw_channel",
                    "mw_compress",
                    "mw_quality",
                    "mw_suffix",
                    "mw_overwrite",
                    "mw_out_dir",
                ],
                handler=lambda v: (
                    lsb.write_action(strip_prefix(v, "mw_"))
                    if strip_prefix(v, "mw_").get("use_stego")
                    else meta.write_action(strip_prefix(v, "mw_"))
                ),
            ),
        ],
    )

    # ------------------------------------------------------------ 清除元数据与隐写
    meta_clear = Panel(
        id="meta_clear",
        title="清除元数据",
        icon="🧹",
        description="按类别清除元数据 / 隐写数据, 可只清除指定键, 清除后写入自定义信息",
        fields=[
            section("cl_sec_input", "输入", "单张图片或整个目录批处理"),
            *fields("cl_", "_clean", SOURCE_FIELDS),
            section("cl_sec_targets", "清除项", "按类别勾选, 也可只清除指定键"),
            Field(
                id="cl_clear_targets",
                label="清除项",
                type="checkbox_group",
                options=CLEAR_TARGETS,
                default=list(CLEAR_TARGETS),
            ),
            Field(
                id="cl_clear_keys",
                label="仅清除指定键",
                type="text",
                placeholder="逗号分隔, 留空 = 清除所选类别全部; 例: Title, Software, Comment",
                row_group="cl_extra",
            ),
            Field(
                id="cl_custom_info",
                label="清除后写入信息",
                type="text",
                placeholder="可留空; 写入 Auto-NovelAI-Refactor 字段",
                row_group="cl_extra",
            ),
            section(
                "cl_sec_output",
                "输出",
                "默认不覆盖原图, 输出为「原名 + 后缀」; 也可指定输出目录",
            ),
            # row_group 与共享输出字段同名 → 和「输出后缀 / 覆盖原图」并成一行
            Field(
                id="cl_quality",
                label="质量 (转 jpg / webp 时)",
                type="slider",
                min=1,
                max=100,
                step=1,
                default=95,
                row_group="cl_out_flags",
            ),
        ]
        + fields("cl_", "_clean", OUTPUT_FIELDS)
        + [
            Field(
                id="cl_info",
                label="说明",
                type="info",
                column="right_bottom",
                default="<b>清除</b>: 按勾选类别清除, 也可只清除指定键 (如只删 <code>Comment</code> 保留其余); "
                "像素里的隐写数据只在 png / tif / bmp 下处理。",
            ),
        ],
        actions=[
            Action(
                id="clear",
                label="清除元数据与隐写",
                uses_novelai=False,
                inputs=[
                    "cl_path",
                    "cl_image",
                    "cl_clear_targets",
                    "cl_clear_keys",
                    "cl_custom_info",
                    "cl_quality",
                    "cl_suffix",
                    "cl_overwrite",
                    "cl_out_dir",
                ],
                handler=lambda v: meta.clear_action(strip_prefix(v, "cl_")),
            ),
        ],
    )

    # 页签顺序: 先图像处理 (变换 / 压缩转换 / 调色 / 拼合 / 动图 / 装饰 / 整理), 再元数据三件套
    plugin.panels.extend(
        [transform, convert, *extra_panels(), meta_read, meta_write, meta_clear]
    )
