"""知识库：与 Skill、工具分离的题型方法论文档（Markdown）。

第一阶段知识库用于：给未实现的题型提供“方法与边界说明”，并作为第二阶段
提示词 / 蒸馏数据的素材。Skill 不直接依赖知识库内容，只通过 ``KnowledgeBase``
按需读取。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

DEFAULT_MAX_CHARS = 2000


class KnowledgeBase:
    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root is not None else Path(__file__).resolve().parents[3] / "knowledge"

    def documents(self) -> Dict[str, Path]:
        if not self.root.is_dir():
            return {}
        return {
            path.stem: path
            for path in sorted(self.root.glob("*.md"))
            if path.is_file()
        }

    def has(self, skill_id: str) -> bool:
        return skill_id in self.documents()

    def load(self, skill_id: str, max_chars: int = DEFAULT_MAX_CHARS) -> Optional[str]:
        path = self.documents().get(skill_id)
        if path is None:
            return None
        text = path.read_text(encoding="utf-8").strip()
        return text[:max_chars]

    def titles(self) -> List[str]:
        return list(self.documents().keys())
