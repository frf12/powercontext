<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/readme-hero-zh-dark.gif" />
    <img src="docs/assets/readme-hero-zh-light.gif" width="100%" alt="PowerContext 在 SWE-bench Pro 同 24 题上的对比：两组解出相同的 16 题。Token 从 2641 万降到 1781 万，减少 32.57%，其中解题 1577 万、记忆生成 204 万。仅解题 Token 减少 40.30%；计入记忆生成后估算模型成本降低 15.42%；单题最高 Token 降幅 63.49%。" />
  </picture>
</p>

<p align="center">
  <a href="#快速开始"><b>开始使用</b></a> &nbsp; · &nbsp;
  <a href="#效果证据">效果证据</a> &nbsp; · &nbsp;
  <a href="#让上下文在更多场景中发挥作用">应用场景</a> &nbsp; · &nbsp;
  <a href="https://powercontext.oceanbase.io/zh/docs/">文档</a> &nbsp; · &nbsp;
  <a href="https://discord.com/invite/74cF8vbNEs">Discord</a>
</p>
<p align="center">
  <a href="README.md">English</a> · <a href="README_CN.md">中文</a> · <a href="README_JP.md">日本語</a> &nbsp; / &nbsp;
  <a href="https://pypi.org/project/powercontext/">Python package</a> · <a href="LICENSE">Apache 2.0</a>
</p>

## 为什么选 PowerContext？

**准、快、省。让 Agent 找准上下文，少走弯路。** PowerContext 把相关知识、用户偏好、任务进展和已有经验带到当前任务，帮助 Agent 减少重复搜索、反复确认和无效尝试，把时间和 Token 用在推进工作上。

- **准：提供当前需要的上下文。** 按任务查找相关知识和代码，保留依据，使用代码前检查是否已更新。
- **快：少摸索，少往返。** 接着已有进度做，参考已确认的方法和约束，减少重新理解背景与反复试错的时间。
- **省：减少完成任务的总开销。** 让一次模型调用更有效，减少不必要的推理和工具往返，也少让用户等待、重讲背景。

## PowerContext 是什么？

**PowerContext 是面向 AI Agent 的开源上下文服务。** 它把对话、资料和工作记录整理成可检索、可更新、可追溯的知识，让 Agent 了解这个人、掌握这个项目、接着上次的进度工作。

[丰富制品，让知识各得其所 →](https://powercontext.oceanbase.io/zh/docs/workflows/artifacts/) · [Dream 自进化，让经验持续更新 →](https://powercontext.oceanbase.io/zh/docs/workflows/review-dream-revisions/) · [灵活插件，按业务扩展 →](https://powercontext.oceanbase.io/zh/docs/develop/plugins/)

可以直接运行服务，通过 HTTP、MCP、Python 或 Agent 集成接入，也可以将 Core 嵌入自己的应用。[了解产品与工作流 →](https://powercontext.oceanbase.io/zh/docs/workflows/memory-and-handoff/)

## 效果证据

**省下 Token，也省下模型成本。** 在 SWE-bench Pro 同 24 道题的对比中，计入记忆生成后，Token 总量减少 **32.57%**，估算模型成本减少 **15.42%**；两组解出的是**相同的 16 题**。

**只看解题，Token 减少 40.30%；计入记忆生成成本后，仍减少 32.57%。** 记忆还可以在后续会话中反复复用，让已积累的知识继续发挥价值。本次测试衡量当轮开销，后续复用的收益尚未计入。

**单题最高省下 63.49% Token、51.98% 估算模型成本。** 第 456 题，两组均通过，Token 从 230 万降到 84 万，已计入记忆生成。

[查看 24 题评测：逐题结果、统计口径与复现配置 →](docs/benchmarks/token-savings/README_CN.md)

### 编程场景：少走弯路，少花 Token

**让每次推理更有效，减少完成任务所需的往返。** 相关代码、明确的工作要求和已有经验，能帮助 Agent 更快找到下一步该做什么。我们关注完成整项任务所花的时间和 Token，把重复探索、重读背景和无效尝试作为优化重点。

本次评测采用**网关模式**，同时开启代码检索、工作指导和项目记忆：

<img src="docs/assets/readme-capabilities-zh.svg" width="100%" alt="代码检索：查找相关代码，检查代码是否已更新。工作指导：明确读取、修改和验证要求。项目记忆：保存项目决定，供后续会话使用。" />

**生成记忆，也复用已有缓存。** 上面的 32.57% 已计入记忆生成开销。PowerContext 在网关模式下可以开启前缀缓存记忆提取，在满足条件时复用解题请求的上下文前缀，降低缓存命中部分的输入成本。本次评测中，记录了缓存字段的记忆生成调用，97% 的输入命中了缓存。 [查看独立的记忆提取成本与质量评测 →](https://gist.github.com/jasper-yl/d02bfb7d9b45ca175bf74ad09cfa5ba4#file-01-report-zh-md)

## 让上下文在更多场景中发挥作用

<!-- Each card is editable HTML; keep text and links aligned across the three READMEs. -->
<table width="100%">
  <tr>
    <td colspan="2" width="1000" valign="top">
      <img src="docs/assets/scenarios/rules.svg" width="40" height="40" align="right" alt="" />
      <p><strong>每个人都能定制自己的记忆规则</strong></p>
      <h3>你的记忆，你来定义</h3>
      <blockquote><p>“只保留长期决策和约束，把临时讨论留在对话里。”</p></blockquote>
      <p>在个人 Scope 中定制提示词，决定记住什么、怎样整理。</p>
      <p><a href="https://powercontext.oceanbase.io/zh/docs/workflows/manage-prompts/"><b>定制我的记忆规则 →</b></a></p>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/assets/scenarios/profile.svg" width="32" height="32" align="right" alt="" />
      <p><sub>Profile</sub></p>
      <h3>记住你的习惯</h3>
      <blockquote><p>“设计评审放到语雀。”</p></blockquote>
      <p>记下你的工具偏好、表达风格和工作习惯。</p>
      <p><a href="https://powercontext.oceanbase.io/zh/docs/workflows/use-profiles/">了解个人画像 →</a></p>
    </td>
    <td width="50%" valign="top">
      <img src="docs/assets/scenarios/topic.svg" width="32" height="32" align="right" alt="" />
      <p><sub>Topic Memory</sub></p>
      <h3>项目有来龙去脉</h3>
      <p><code>日常讨论</code> → <code>主题摘要</code> → <code>按需找回细节</code></p>
      <p>先看主题概况，需要时再找到细节和依据。</p>
      <p><a href="https://powercontext.oceanbase.io/zh/docs/workflows/topic-memory/">整理项目知识 →</a></p>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/assets/scenarios/dream.svg" width="32" height="32" align="right" alt="" />
      <p><sub>Dream</sub></p>
      <h3>让经验持续进化</h3>
      <p><code>历史依据</code> → <code>修订建议</code> → <code>你来审核</code></p>
      <p>根据过往交互提出记忆和提示词修订，审核后生效。</p>
      <p><a href="https://powercontext.oceanbase.io/zh/docs/workflows/review-dream-revisions/">了解 Dream →</a></p>
    </td>
    <td width="50%" valign="top">
      <img src="docs/assets/scenarios/handoff.svg" width="32" height="32" align="right" alt="" />
      <p><sub>Handoff</sub></p>
      <h3>换个会话接着做</h3>
      <p><code>已验证进度</code> → <code>下一步</code> → <code>新会话接续</code></p>
      <p>保存目标、进展和待办，核对现状后继续推进。</p>
      <p><a href="https://powercontext.oceanbase.io/zh/docs/workflows/memory-and-handoff/">接续任务 →</a></p>
    </td>
  </tr>
</table>

## 快速开始

需要 Python 3.11+、Git 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)；接入 Agent 时还需安装对应的 CLI。以下命令安装 **1.5.2**，适用于 macOS 和 Linux；Windows 为实验性支持。需要自动准备 Python 和 uv 时，可使用[安装脚本](https://powercontext.oceanbase.io/zh/docs/get-started/install-and-run/#使用推荐安装脚本)。

首次体验可在向导中选择**基础记忆**，显式保存与召回不需要为 Server 配置模型 API；自动提取需要选择**完整记忆能力**并配置生成模型和 Embedding 凭据。存储可先选 SQLite。

```bash
uv tool install --force "powercontext[cli,server]==1.5.2"
mkdir -p powercontext-config && cd powercontext-config
powercontext config init --language zh --output .env
powercontext config validate --env-file .env
powercontext service install --env-file .env
powercontext service status
```

关闭终端后，服务仍会继续运行。Linux 需要可用的 `systemd --user` 管理器。向导生成的 `.env` 和 `.env.next-steps.md` 可能包含凭据，请勿提交到 Git。

加载生成的配置，检查服务状态：

```bash
set -a
. ./.env
set +a
powercontext ready
powercontext capabilities
```

按 **`.env.next-steps.md`** 创建并绑定 Scope，使用创建请求返回的真实 `scope_id`。Agent 和 Dashboard 要连接同一个 Server 和 Scope；切换工作目录不会自动隔离项目。修改 `.env` 后，重新加载环境文件，并执行 `powercontext service install --env-file .env` 使配置生效。

接入 Codex 时，安装对应版本的集成：

```bash
powercontext setup codex --env-file .env --ref powercontext-v1.5.2
powercontext doctor codex
```

在新 Codex 会话发送第一条消息前，打开 **`/hooks`** 检查并按提示信任 PowerContext Hook。即使 MCP 已连通，自动采集输入和召回上下文仍需要启用 `UserPromptSubmit` Hook。[首次会话检查与恢复 →](https://powercontext.oceanbase.io/zh/docs/integrations/codex/#首次会话-hook-信任)

按所选模式检查是否生效：

- **基础记忆：** 明确要求 Agent 保存一条项目决定，再用同一 Scope 开启新会话，要求它带来源找回。
- **完整记忆能力：** 确认输入已进入 Source，等待配置的后台处理生成 Topic Memory，再检查相关更新是否被记录，并能在新会话中找回。

`doctor codex` 只检查安装与连接。采集到 Source 不等于已生成记忆，自动处理还需要调度和模型运行。基础记忆模式下，Agent 仍使用自己的模型。[完整安装流程 →](https://powercontext.oceanbase.io/zh/docs/get-started/quickstart/)

<sub>以上命令用于启动服务。要复现测试中的网关、代码检索和工作指导配置，请查看详细报告。</sub>

## 接入你的工作流

<!-- Add integrations using an original logo asset, a product name, and a documentation link. -->
<table width="100%">
  <tr>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/zh/docs/integrations/codex/"><img src="docs/assets/codex.png" height="48" alt="Codex" /><br />Codex</a></td>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/zh/docs/integrations/claude-code/"><img src="docs/assets/claude-code.png" height="48" alt="Claude Code" /><br />Claude Code</a></td>
    <td align="center" width="20%"><a href="https://github.com/oceanbase/powercontext/tree/master/integrations/minimax#readme"><img src="docs/assets/minimax-code.png" height="48" alt="MiniMax Code" /><br />MiniMax Code</a></td>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/zh/docs/integrations/dsh/"><img src="docs/assets/deepseek.png" height="48" alt="DeepSeek Harness" /><br />DeepSeek Harness</a></td>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/zh/docs/integrations/zcode/"><img src="docs/assets/zcode.png" height="48" alt="ZCode" /><br />ZCode</a></td>
  </tr>
  <tr>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/zh/docs/integrations/kimi-code/"><img src="docs/assets/kimi.png" height="48" alt="Kimi Code" /><br />Kimi Code</a></td>
    <td align="center" width="20%"><a href="https://github.com/oceanbase/powercontext/tree/master/integrations/hermes#readme"><img src="docs/assets/hermes.png" height="48" alt="Hermes Agent" /><br />Hermes Agent</a></td>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/zh/docs/integrations/pi/"><img src="docs/assets/pi.png" height="48" alt="Pi Coding Agent" /><br />Pi Coding Agent</a></td>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/zh/docs/integrations/openclaw/"><img src="docs/assets/openclaw.png" height="48" alt="OpenClaw" /><br />OpenClaw</a></td>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/zh/docs/integrations/opencode/"><img src="docs/assets/opencode.png" height="48" alt="OpenCode" /><br />OpenCode</a></td>
  </tr>
  <tr>
    <td align="center" width="20%"><a href="https://github.com/oceanbase/powercontext/tree/master/integrations/workbuddy#readme"><img src="docs/assets/workbuddy.svg" height="48" alt="WorkBuddy" /><br />WorkBuddy</a></td>
    <td align="center" width="20%"><a href="https://github.com/oceanbase/powercontext/tree/master/integrations/bub#readme"><img src="docs/assets/bub.png" height="48" alt="Bub" /><br />Bub</a></td>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/zh/docs/integrations/pydantic-ai/"><img src="docs/assets/pydantic.png" height="48" alt="Pydantic AI" /><br />Pydantic AI</a></td>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/zh/docs/integrations/langchain/"><img src="docs/assets/langchain.png" height="48" alt="LangChain" /><br />LangChain</a></td>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/zh/docs/integrations/langgraph/"><img src="docs/assets/langgraph.png" height="48" alt="LangGraph" /><br />LangGraph</a></td>
  </tr>
</table>

Codex 为官方维护；其余 Agent 和 Python 框架集成为社区维护；Bub 仅用于评测。各项支持范围见能力对照。

[其他集成与能力对照 →](https://powercontext.oceanbase.io/zh/docs/integrations/capabilities/)

**应用与团队** — [Python / HTTP / MCP](https://powercontext.oceanbase.io/zh/docs/develop/interfaces/) · [记忆与任务交接](https://powercontext.oceanbase.io/zh/docs/workflows/memory-and-handoff/) · [部署与访问控制](https://powercontext.oceanbase.io/zh/docs/workflows/scopes-and-access/)

---

[文档](https://powercontext.oceanbase.io/zh/docs/) · [参与贡献](https://github.com/oceanbase/powercontext/blob/master/CONTRIBUTING.md) · [报告问题](https://github.com/oceanbase/powercontext/issues) · [Discord](https://discord.com/invite/74cF8vbNEs)

PowerContext 是[PowerMem](https://www.powermem.ai/)的后续项目，采用[Apache 2.0](LICENSE)发布。
