"""assessment-agent：多模态招聘测评解题 Agent。

模块划分（Skill / 知识库 / 求解工具三层分离，便于后续接入 DSH、MCP 或蒸馏）：

- ``config``      环境与模型配置（OpenAI 兼容接口）
- ``schemas``     全局结构化数据结构（Pydantic）
- ``llm``         OpenAI 兼容客户端（文本 + 视觉）
- ``router``      题型路由
- ``solver``      求解编排
- ``verifier``    独立校验
- ``skills``      八个题型 Skill
- ``tools``       求解工具（SymPy / Z3 / 图形工具接口）
- ``knowledge``   知识库加载
- ``artifacts``   原始题图 / 提取结果 / 解析记录
- ``pipeline``    端到端流程
- ``evaluation``  训练 / 验证 / 测试集评测
"""

__version__ = "0.1.0"
