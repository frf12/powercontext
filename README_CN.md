# PowerContext

**用更少的 Token，把任务做完。**

面向 AI Agent 的开源上下文运行时：为任务提供相关代码与项目知识，引导 Agent 聚焦工作，并把有用的上下文留给下一次会话。

[PyPI](https://pypi.org/project/powercontext/) · [Apache 2.0](LICENSE) · [Discord](https://discord.com/invite/74cF8vbNEs)

[English](README.md) · [中文](README_CN.md)

[为什么选 PC](#为什么选-powercontext) · [PC 是什么](#powercontext-是什么) · [效果证据](#效果证据) · [快速开始](#快速开始) · [文档](https://powercontext.oceanbase.io/zh/docs/)

## 为什么选 PowerContext？

一次长编码任务，Token 不只花在修改上：搜索仓库、读取文件、把这些结果带进后续请求，再绕回已经失败的路线，都会持续增加开销。PowerContext 让你控制 Agent 收到的上下文和任务指导。

在同一批 **24 道编码任务**上，MCS 配置的**主模型总 Token 减少 40.30%**，与对照组均通过 16 题。其他配置的成绩也一起列出：

| 配置 | 总 Token | 相比 OFF 减少 | 通过题数 |
| :--- | ---: | ---: | ---: |
| Gateway OFF：对照组 | 26,410,221 | — | 16/24 |
| Steering（S） | 19,247,973 | **27.12%** | 15/24 |
| CodeGraph + Steering（CS） | 17,680,166 | **33.06%** | 15/24 |
| Memory + CodeGraph + Steering（MCS） | 15,766,986 | **40.30%** | 16/24 |

同一批题、相同模型配置，采用原始／冻结选中记录，失败题也计入用量。总 Token 包含缓存输入和输出，**不等于费用降幅**。MCS 另用了 2,041,992 Token 生成题后记忆；加上这一项，降幅为 **32.57%**，还未包含其他辅助开销。[统计口径与逐题数据 →](docs/benchmarks/token-savings/README_CN.md)

![同24题主模型Token实测：Gateway OFF 2641万、S 1925万、CS 1768万、MCS 1577万；记忆生成另计。](docs/assets/token-savings-24.gif)

## PowerContext 是什么？

**PowerContext 管理 Agent 工作所需的上下文。** 它把代码上下文、项目记忆和任务交接放在一个共享服务中，通过 HTTP、MCP、Python 和 Agent 集成接入。继续使用你的 Agent 和模型，按需要配置上下文能力。

| 能力 | 做什么 | 希望减少什么 |
| :--- | :--- | :--- |
| **CodeGraph** | 检索绑定到仓库快照的代码证据，复用时保留新鲜度校验。 | 每次都从整个仓库重新寻找线索。 |
| **Steering** | 显式提供任务指导：定向读取、合并独立操作、聚焦验证。 | 不必要的工具往返，以及过长的测试环境修复过程。 |
| **Memory** | 保存有用的决定与事实，在预算内召回相关上下文。 | 后续会话重新摸索已经确认的项目知识。 |

本次 S 配置使用了一段显式加入的 [489 字节指导文本](docs/benchmarks/token-savings/steering.txt)。它是实验配置，**不表示安装 PowerContext 后就默认开启**。CodeGraph 和 Memory 也需要相应配置，各 Agent 集成支持的能力不同。

```text
你的任务 + 项目上下文
          ↓
 PowerContext 选择与任务相关的上下文
          ↓
 Agent 搜索、修改、验证
          ↓
 有用的结果可以留给后续会话
```

上下文归属于一个 **Scope**。在支持的 Agent 之间切换、开启新会话，或把任务交给同事时，可以继续使用同一项目的决定和交接记录。除了省 Token，Memory 与 Handoff 也让接手的人能查看已保存的状态，继续工作。

## 效果证据

### 不只这 24 题：121 题表现如何？

完整的 **121 题**对照中，启用 Steering 的配置也使用了更少的主模型 Token：

| 配置 | 总 Token | 相比 Gateway OFF 减少 | 通过题数 |
| :--- | ---: | ---: | ---: |
| Gateway OFF | 141,869,214 | — | 83/121 |
| Steering（S） | 91,284,652 | **35.66%** | 81/121 |
| CodeGraph + Steering（CS） | 90,544,517 | **36.18%** | 82/121 |

这里采用原始已完成记录，没有用复跑成功记录替换。CS 比 OFF 少通过 1 题，不能据此宣称质量相同。单独开启 CodeGraph 的 C 组反而多用了 **10.53%** Token，增加能力并不保证更省。[五组完整结果与复跑开销 →](docs/benchmarks/token-savings/README_CN.md)

### 落到一道题，差别有多大？

**case 302，navidrome/navidrome**：Gateway OFF 与 CS 都通过了官方任务评分。

| Gateway OFF | CodeGraph + Steering | Token 减少 |
| ---: | ---: | ---: |
| 1,240,658 | 177,343 | **85.71%** |

这是 121 题中 CS 的最高单题降幅，不是平均值。模型选择的实现路线与验证步骤也会变化；观察到的节省，不能全部归因于代码检索。

### 数据能核对吗？

[评测证据包](docs/benchmarks/token-savings/README_CN.md)包含题目 ID、逐条用量、结果、来源摘要、原始与复跑选中记录，以及离线核算脚本：

```bash
python3 docs/benchmarks/token-savings/verify.py
```

脚本无需调用模型，就能重新计算公开表格。完整原始轨迹另行归档；这里的数值导出可以复算表格，但不能代替运行轨迹来独立复现评分。

**这次实验能说明 Memory 的效果吗？** 每次运行都从全新空库开始。24 条 MCS 记录中，解题期间的非空记忆注入为 0，记忆在答题后生成。因此，这些结果反映的是配置的用量，尚未验证跨题记忆复用的收益。MCS 也没有跑完全部 121 题。

## 快速开始

### 1. 启动上下文服务

需要 Python 3.11+、Git 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。下面固定使用 PowerContext 1.5.0，适用于 macOS 和 Linux。

```bash
uv tool install --force "powercontext[cli,server]==1.5.0"
mkdir -p powercontext-config && cd powercontext-config
powercontext config init --language zh --output .env
powercontext config validate --env-file .env
powercontext server run --env-file .env
```

保持服务运行。向导会生成 `.env.next-steps.md`，列出所选存储与 Agent 的后续配置。首次体验可选**基础记忆**，显式保存与召回不需要独立的服务端模型 API；自动提取则需要配置 Generation 和 Embedding 凭据。请妥善保管 `.env`。

### 2. 接入 Agent

按生成的后续步骤创建并绑定 Scope，再安装匹配版本的集成。以 Codex 为例：

```bash
powercontext setup codex --ref powercontext-v1.5.0
powercontext doctor codex
```

开启新 Agent 会话，按宿主要求启用并信任 PowerContext Hook。两个会话使用同一 Server 和 Scope，就可以体验“保存项目决定 → 新会话召回”。

[Codex 接入](https://powercontext.oceanbase.io/zh/docs/integrations/codex/) · [Claude Code 接入](https://powercontext.oceanbase.io/zh/docs/integrations/claude-code/) · [手动启动](https://powercontext.oceanbase.io/zh/docs/get-started/manual-start/)

### 3. 在自己的任务上衡量收益

上面的步骤用于启动上下文服务，**并不会自动复现评测配置**。本次测试使用冻结的开发构建、指定 Gateway 路由和显式功能开关；比较前请看[测试配置](docs/benchmarks/token-savings/README_CN.md#测试配置)。

自己做对照时，固定 Agent、模型、任务和验证标准，记录总输入、缓存输入、输出、任务成功情况，以及 PowerContext 额外调用的模型用量。比较完整任务，而不只是某一次被缩短的输入。

## 接入你的工作流

| 你想做什么 | 从这里开始 |
| :--- | :--- |
| 接入 Codex 或 Claude Code | [Agent 集成](https://powercontext.oceanbase.io/zh/docs/integrations/) |
| 接入 OpenCode、OpenClaw、Pi 等宿主 | [能力矩阵](https://powercontext.oceanbase.io/zh/docs/integrations/capabilities/) |
| 在自己的应用中使用 | [Python、HTTP、MCP 与 Core 接口](https://powercontext.oceanbase.io/zh/docs/develop/interfaces/) |
| 共享项目上下文、交接任务 | [Memory 与 Handoff](https://powercontext.oceanbase.io/zh/docs/workflows/memory-and-handoff/) |
| 本地存储、控制访问范围 | [部署](https://powercontext.oceanbase.io/zh/docs/get-started/install-and-run/) · [Scope 与访问控制](https://powercontext.oceanbase.io/zh/docs/workflows/scopes-and-access/) |

## 文档与贡献

[文档](https://powercontext.oceanbase.io/zh/docs/) · [评测证据](docs/benchmarks/token-savings/README_CN.md) · [报告问题](https://github.com/oceanbase/powercontext/issues) · [贡献指南](https://github.com/oceanbase/powercontext/blob/main/CONTRIBUTING.md) · [Discord](https://discord.com/invite/74cF8vbNEs)

PowerContext 是 [PowerMem](https://www.powermem.ai/) 的后续项目，基于 [Apache 2.0](LICENSE) 发布。
