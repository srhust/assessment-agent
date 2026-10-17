"""图形推理工具接口（第二阶段能力预留）。

第一阶段**只提供真实可用的图像基础观测**（尺寸、灰度、九宫格墨迹密度），
规律识别相关的函数一律显式抛 ``NotImplementedError``，
避免对外提供看似能解题、实则编造的接口。

OpenCV 为可选依赖：未安装时 ``opencv`` 能力位为 False，接口仍可查询。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import Tool, ToolError

_STAGE2_OPS = {
    "detect_shapes": "形状检测（线段/顶点/圆/多边形）",
    "match_transform": "旋转/翻转/平移匹配",
    "grid_rule_inference": "九宫格 / 序列图形的规律推断",
    "symmetry_score": "对称轴与对称性评分",
    "overlay_compare": "叠加 / 去同存异比较",
}


def backend_status() -> Dict[str, bool]:
    status = {}
    for module in ("numpy", "PIL", "cv2"):
        try:
            __import__(module)
            status[module] = True
        except ImportError:
            status[module] = False
    return status


def load_gray(path: str | Path) -> Any:
    """读取图像为二维 float 数组（0=白，1=黑），用于墨迹密度等基础观测。"""
    try:
        import numpy as np
    except ImportError as exc:
        raise ToolError("缺少 numpy 依赖") from exc
    image_path = Path(path)
    if not image_path.is_file():
        raise ToolError(f"图像不存在: {image_path}")
    try:
        from PIL import Image
    except ImportError as exc:
        raise ToolError("缺少 pillow 依赖") from exc
    with Image.open(image_path) as image:
        gray = image.convert("L")
        array = np.asarray(gray, dtype="float32") / 255.0
    return 1.0 - array


def image_summary(path: str | Path) -> Dict[str, Any]:
    try:
        from PIL import Image
    except ImportError as exc:
        raise ToolError("缺少 pillow 依赖") from exc
    image_path = Path(path)
    if not image_path.is_file():
        raise ToolError(f"图像不存在: {image_path}")
    with Image.open(image_path) as image:
        width, height = image.size
        mode = image.mode
    ink = load_gray(image_path)
    ink_ratio = float((ink > 0.5).mean())
    return {
        "path": str(image_path),
        "width": width,
        "height": height,
        "mode": mode,
        "ink_ratio": round(ink_ratio, 6),
    }


def grid_ink_density(path: str | Path, rows: int = 3, cols: int = 3) -> Dict[str, Any]:
    """把图像切分为 rows×cols 网格，返回每格墨迹占比（九宫格图形题的基础观测）。"""
    try:
        import numpy as np
    except ImportError as exc:
        raise ToolError("缺少 numpy 依赖") from exc
    if rows <= 0 or cols <= 0:
        raise ToolError("rows / cols 必须为正整数")
    ink = load_gray(path)
    height, width = ink.shape
    cells: List[List[float]] = []
    for row in range(rows):
        row_values: List[float] = []
        top = int(height * row / rows)
        bottom = int(height * (row + 1) / rows)
        for col in range(cols):
            left = int(width * col / cols)
            right = int(width * (col + 1) / cols)
            block = ink[top:bottom, left:right]
            ratio = float((block > 0.5).mean()) if block.size else 0.0
            row_values.append(round(ratio, 6))
        cells.append(row_values)
    return {"rows": rows, "cols": cols, "density": cells, "shape": [int(height), int(width)]}


def _not_implemented(operation: str) -> NotImplementedError:
    detail = _STAGE2_OPS.get(operation, operation)
    return NotImplementedError(
        f"图形推理能力「{detail}」尚未实现（第二阶段，计划基于 OpenCV 实现），本阶段不提供推测性结论"
    )


class FigureTools(Tool):
    """图形题目工具：第一阶段的可用能力 + 第二阶段的显式占位接口。"""

    id = "figure_tools"
    description = "图像基础观测（尺寸/墨迹密度/九宫格切分）；图形规律识别为第二阶段预留接口"
    requires = ("numpy", "pillow")
    stage = 2

    def available(self) -> bool:
        status = backend_status()
        return status["numpy"] and status["PIL"]

    def unavailable_reason(self) -> Optional[str]:
        status = backend_status()
        missing = [name for name in ("numpy", "PIL") if not status[name]]
        return f"缺少依赖: {', '.join(missing)}" if missing else None

    def capabilities(self) -> Dict[str, Any]:
        return {
            "backends": backend_status(),
            "implemented": ["image_summary", "grid_ink_density"],
            "planned": dict(_STAGE2_OPS),
        }

    def run(self, operation: str = "capabilities", **kwargs: Any) -> Any:
        if operation == "capabilities":
            return self.capabilities()
        if operation == "image_summary":
            return image_summary(kwargs.get("path", ""))
        if operation == "grid_ink_density":
            return grid_ink_density(
                kwargs.get("path", ""),
                rows=int(kwargs.get("rows", 3)),
                cols=int(kwargs.get("cols", 3)),
            )
        raise _not_implemented(operation)
