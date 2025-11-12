<p align="center">
    <a href="https://github.com/oceanbase/oceanbase">
        <img alt="OceanBase Logo" src="docs/images/oceanbase_Logo.png" width="50%" />
    </a>
</p>

<p align="center">
  <a href="https://powermem.ai">Learn more</a>
  ·
  <a href="https://discord.com/invite/74cF8vbNEs">Join Discord</a>
  ·
  <a href="https://powermem.ai/benchmark">Benchmark Result</a>
</p>

<p align="center">
    <a href="https://pepy.tech/project/powermem">
        <img src="https://img.shields.io/pypi/dm/powermem" alt="PowerMem PyPI - Downloads">
    </a>
    <a href="https://github.com/oceanbase/powermem">
        <img src="https://img.shields.io/github/commit-activity/m/oceanbase/powermem?style=flat-square" alt="GitHub commit activity">
    </a>
    <a href="https://pypi.org/project/powermem" target="blank">
        <img src="https://img.shields.io/pypi/v/powermem?color=%2334D058&label=pypi%20package" alt="Package version">
    </a>
    <a href="https://github.com/oceanbase/powermem/blob/master/LICENSE">
        <img alt="license" src="https://img.shields.io/badge/license-Apache%202.0-green.svg" />
    </a>
    <a href="https://img.shields.io/badge/python%20-3.10.0%2B-blue.svg">
        <img alt="pyversions" src="https://img.shields.io/badge/python%20-3.10.0%2B-blue.svg" />
    </a>
    <a href="https://deepwiki.com/oceanbase/powermem">
        <img alt="Ask DeepWiki" src="https://deepwiki.com/badge.svg" />
    </a>
</p>

[English](README.md) | [中文](README_CN.md) | [日本語](README_JP.md)

## 亮点

<div align="center">

<img src="docs/images/benchmark_metrics_cn.svg" alt="PowerMem LOCOMO 压测指标" width="900"/>

</div>

- **更准**：**[准确率提升 48.77%]** 在 LOCOMO 基准测试中，相比于 full-context 更准确（78.70% VS 52.9%）
- **更快**：**[响应速度快 91.83%]** 相比于 full-context，检索的 p95 延迟显著降低（1.44s VS 17.12s）
- **更省**：**[Token 用量降低 96.53%]** 相比于full-context，在不牺牲性能的前提下显著降低成本（0.9k VS 26k）

- [Benchmark 详情参见](https://powermem.ai/benchmark)

# PowerMem - 智能AI记忆系统

在 AI 应用开发中，如何让大语言模型持久化地"记住"历史对话、用户偏好和上下文信息是一个核心挑战。PowerMem 融合向量检索、全文检索和图数据库的混合存储架构，并引入认知科学的艾宾浩斯遗忘曲线理论，为 AI 应用构建了强大的记忆基础设施。系统还提供完善的多智能体支持能力，包括智能体记忆隔离、跨智能体协作共享、细粒度权限控制和隐私保护机制，让多个 AI 智能体能够在保持独立记忆空间的同时实现高效协作。

## 核心特性（下面的要跳转demo）

### 智能记忆管理
- **[记忆的智能提取](docs/examples/scenario_2_intelligent_memory.md)**：通过 LLM 模型进行记忆的提取
- **艾宾浩斯遗忘曲线**：基于认知科学的智能记忆优化
- **记忆衰减与强化**：基于使用模式的动态记忆保留

### 多智能体支持
- **[智能体隔离](docs/examples/scenario_3_multi_agent.md)**：为不同智能体提供独立的记忆空间

### 多模态支持
- **文本与图像记忆**：支持文本和图像两种模态的记忆存储与检索，实现更丰富的上下文理解

### 深度优化数据存储
- **支持子存储（Sub Stores）**：通过子存储实现数据的分区管理，应对超大规模数据
- **混合检索**：支持向量检索、全文搜索以及图检索的混合检索能力
- **图检索**：支持 LLM 提取实体和关系以构建知识图谱，通过多跳图遍历，用于检索复杂的记忆关系
- **混合存储**：结合向量搜索和图关系以增强检索

### 开发者友好
- **轻量级接入方式**：支持Python SDK/MCP 的接入方式，兼容mem0的使用；

## 快速开始

### 安装

```bash
pip install powermem
```

### 基本使用

**✨ 最简单的方式**：从 `.env` 文件读取配置自动创建记忆！[配置文件参考](configs/env.example)

```python
from powermem import create_memory

# 自动从 .env 加载配置
memory = create_memory()

# 添加记忆
memory.add("用户喜欢咖啡", user_id="user123")

# 搜索记忆
memories = memory.search("用户偏好", user_id="user123")
for memory in memories:
    print(f"- {memory.get('memory')}")
```

更多详细示例和使用模式，请参阅[入门指南](docs/guides/0001-getting_started.md)。

## 集成与演示

- **LangChain 集成**: 使用 LangChain + PowerMem + OceanBase 构建医疗支持机器人([Example](examples/langchain/README.md))
- **Langgraph 集成**: 使用 LangGraph + PowerMem 构建客户机器人 ([Example](examples))

## 文档

- **[入门指南](docs/guides/0001-getting_started.md)**：安装和快速开始指南
- **[配置指南](docs/guides/0002-configuration.md)**：完整的配置选项
- **[多智能体指南](docs/guides/0004-multi_agent.md)**：多智能体场景和示例
- **[集成指南](docs/guides/0005-integrations.md)**：LLM 和嵌入提供商集成
- **[子存储指南](docs/guides/0006-sub_stores.md)**：子存储的使用方法和示例
- **[API 文档](docs/api/overview.md)**：完整的 API 参考
- **[架构指南](docs/architecture/overview.md)**：系统架构和设计
- **[示例](docs/examples/overview.md)**：交互式 Jupyter 笔记本和使用案例

## 开发

### 设置开发环境

```bash
# 克隆仓库
git clone https://github.com/powermem/powermem.git
cd powermem

# 安装开发依赖
pip install -e ".[dev,test]"
```

## 贡献

我们欢迎贡献！请参阅我们的贡献指南和行为准则。

## 支持

- **问题反馈**：[GitHub Issues](https://github.com/oceanbase/powermem/issues)
- **讨论交流**：[GitHub Discussions](https://github.com/oceanbase/powermem/discussions)

---

## 许可证

本项目采用 Apache License 2.0 许可证 - 详情请参阅 [LICENSE](LICENSE) 文件。