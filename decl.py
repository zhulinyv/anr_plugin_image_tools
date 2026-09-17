"""图片工具插件: 面板字段的共享声明助手。

输入 / 输出模板字段与分区标题原先写在 `__init__.py` 里, 面板变多之后单独抽到这里,
供 `__init__.py` (原有面板) 与 `panels_extra.py` (调色 / 拼合 / 动图 / 装饰 / 查重 / 重命名) 共用,
避免两边各维护一份、改一处漏一处。
"""

from __future__ import annotations

from utils.plugins import Field

# 输入: 单张图片 或 整个目录批处理
SOURCE_FIELDS = [
    Field(
        id="{p}path", label="批处理路径", type="path", folder=True, file=False, placeholder="选择目录, 处理其中全部图片"
    ),
    Field(id="{p}image", label="或单张图片", type="image"),
]

# 输出: 后缀 / 覆盖原图 / 输出目录
# 「后缀」与「覆盖原图」并排成一行 (都是决定"输出到哪"的短选项); 输出目录要选路径, 单独占一行。
# 并排字段的标签必须短 —— .field-row 里标签是 nowrap, 太长会溢出到相邻字段上;
# 补充说明改写进 placeholder (输入框里的灰字) 或分区说明。
OUTPUT_FIELDS = [
    Field(
        id="{p}suffix",
        label="{l}输出后缀",
        type="text",
        default="{s}",
        placeholder="不覆盖原图时使用",
        row_group="{p}out_flags",
    ),
    Field(
        id="{p}overwrite",
        label="{l}覆盖原图",
        type="checkbox",
        default=False,
        row_group="{p}out_flags",
    ),
    Field(id="{p}out_dir", label="{l}输出目录 (留空 = 与原图同目录)", type="path", folder=True, file=False),
]


def section(id: str, label: str, description: str = "") -> Field:
    """分区标题: 前端渲染成一条带竖线的分隔标题, 把同类功能的参数划分到同一区域。"""
    return Field(id=id, label=label, type="section", description=description)


def fields(prefix: str, suffix: str, templates: list[Field], label_prefix: str = "") -> list[Field]:
    """把模板字段里的 {p}/{s} 占位替换成实际前缀与默认后缀 ({l} = 标签前缀)。"""
    return [
        Field(
            **{
                key: (
                    value.replace("{p}", prefix).replace("{s}", suffix).replace("{l}", label_prefix)
                    if isinstance(value, str)
                    else value
                )
                for key, value in f.__dict__.items()
            }
        )
        for f in templates
    ]


def info(id: str, label: str, html: str) -> Field:
    """说明字段: 一律排右列底部 (输出区下方), 不挤占处理结果的位置。"""
    return Field(id=id, label=label, type="info", column="right_bottom", default=html)


def switch(id: str, label: str, on: str, off: str, *, description: str = "") -> Field:
    """功能开关 radio: 每个功能分区的第一个字段, 选中 `on` 后才展开该功能的参数。

    开关是权威: 后端也必须按开关判断 (选 `off` 时即使参数还留着历史值也不执行),
    否则面板上明明关掉了, 却会被记住的历史值处理掉。
    """
    return Field(id=id, label=label, type="radio", options=[off, on], default=off, description=description)


def on_switch(id: str, on: str = "启用") -> dict:
    """配合 `switch()` 使用的 show_if: 只在开关选中 `on` 时显示。"""
    return {"field": id, "equals": on}
