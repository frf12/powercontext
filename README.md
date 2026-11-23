<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/readme-hero-en-dark.gif" />
    <img src="docs/assets/readme-hero-en-light.gif" width="100%" alt="PowerContext on 24 shared SWE-bench Pro tasks: both arms solved the same 16 tasks. Tokens fell from 26.41M to 17.81M (32.57% fewer), counting 15.77M for solving and 2.04M for memory generation. Solving only: 40.30% fewer tokens. Estimated model cost: 15.42% lower with memory generation. Best task: 63.49% fewer tokens." />
  </picture>
</p>

<p align="center">
  <a href="#quick-start"><b>Get started</b></a> &nbsp; · &nbsp;
  <a href="#proof-it-works">See the proof</a> &nbsp; · &nbsp;
  <a href="#put-context-to-work">Use cases</a> &nbsp; · &nbsp;
  <a href="https://powercontext.oceanbase.io/en/docs/">Documentation</a> &nbsp; · &nbsp;
  <a href="https://discord.com/invite/74cF8vbNEs">Discord</a>
</p>
<p align="center">
  <a href="README.md">English</a> · <a href="README_CN.md">中文</a> · <a href="README_JP.md">日本語</a> &nbsp; / &nbsp;
  <a href="https://pypi.org/project/powercontext/">Python package</a> · <a href="LICENSE">Apache 2.0</a>
</p>

## Why PowerContext?

**The right context. Faster progress. Fewer tokens.** PowerContext brings relevant knowledge, user preferences, task progress, and past experience into the current task, helping agents spend less time searching again, asking the same questions, and repeating unsuccessful attempts.

- **Relevant context, with evidence.** Find knowledge and code for the task, preserve sources, and check code freshness before use.
- **Faster progress, fewer round trips.** Pick up from existing progress, using confirmed approaches and constraints to reduce repeated discovery and trial and error.
- **Less total effort and token use.** Make each model call more useful, reduce unnecessary reasoning and tool round trips, and spend less time waiting or explaining the background again.

## What is PowerContext?

**PowerContext is an open-source context service for AI agents.** It turns conversations, documents, and work records into knowledge that can be searched, updated, and traced to its sources, helping agents understand the person, know the project, and continue from where work left off.

[Rich artifacts for different kinds of knowledge →](https://powercontext.oceanbase.io/en/docs/workflows/artifacts/) · [Evolve with Dream →](https://powercontext.oceanbase.io/en/docs/workflows/review-dream-revisions/) · [Extend with flexible plugins →](https://powercontext.oceanbase.io/en/docs/develop/plugins/)

Run it as a service through HTTP, MCP, Python, or Agent integrations, or embed Core in your own application. [Explore the product and workflows →](https://powercontext.oceanbase.io/en/docs/workflows/memory-and-handoff/)

## Proof it works

**Spend fewer tokens. Lower model costs.** On the same 24 SWE-bench Pro tasks, PowerContext used **32.57% fewer tokens** and delivered **15.42% lower estimated model cost**, including memory generation. Both groups solved the **same 16 tasks**.

**Solving alone uses 40.30% fewer tokens; including memory generation overhead, the reduction is 32.57%.** Saved memories can be reused in later sessions, extending the value of accumulated knowledge. This benchmark measures the current run; future reuse benefits have not been measured here.

**Best task: 63.49% fewer tokens and 51.98% lower estimated model cost.** Both runs passed case 456, with tokens falling from 2.30 million to 0.84 million, including memory generation.

[Explore the 24-task benchmark: results, methodology, and setup →](docs/benchmarks/token-savings/README.md)

### Coding: fewer detours, less wasted work

**Make each reasoning step more useful, with fewer round trips to finish the task.** Relevant code, clear working requirements, and past experience help agents find the next useful step. Our focus is the time and tokens needed for the whole task, reducing repeated exploration, rereading, and unproductive attempts.

This benchmark runs in **gateway mode**, with code retrieval, working guidance, and project memory enabled together:

<img src="docs/assets/readme-capabilities-en.svg" width="100%" alt="CodeGraph: relevant code with freshness checks. Steering: targeted reads, batched work and focused tests. Memory: saved decisions and bounded recall for later sessions." />

**Extract memory. Reuse the cache.** The 32.57% reduction above includes memory-generation tokens. In gateway mode, you can enable prefix-cached memory extraction to reuse context prefixes from solving requests when eligible, lowering input costs on cache hits. In this benchmark, 97% of input to memory-generation calls with recorded cache fields hit the cache. [Explore the separate memory-extraction cost and quality study →](https://gist.github.com/jasper-yl/d02bfb7d9b45ca175bf74ad09cfa5ba4#file-00-report-en-md)

## Put context to work

<!-- Each card is editable HTML; keep text and links aligned across the three READMEs. -->
<table width="100%">
  <tr>
    <td colspan="2" width="1000" valign="top">
      <img src="docs/assets/scenarios/rules.svg" width="40" height="40" align="right" alt="" />
      <p><strong>Everyone can define their own memory rules</strong></p>
      <h3>Your memory, your rules</h3>
      <blockquote><p>“Keep long-term decisions and constraints. Leave temporary discussion in the conversation.”</p></blockquote>
      <p>Customize prompts in your personal Scope to decide what to remember and how to organize it.</p>
      <p><a href="https://powercontext.oceanbase.io/en/docs/workflows/manage-prompts/"><b>Set my memory rules →</b></a></p>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/assets/scenarios/profile.svg" width="32" height="32" align="right" alt="" />
      <p><sub>Profile</sub></p>
      <h3>Built around you</h3>
      <blockquote><p>“Put design reviews in Yuque.”</p></blockquote>
      <p>Keep your preferred tools, writing style, and working habits in your profile.</p>
      <p><a href="https://powercontext.oceanbase.io/en/docs/workflows/use-profiles/">Explore profiles →</a></p>
    </td>
    <td width="50%" valign="top">
      <img src="docs/assets/scenarios/topic.svg" width="32" height="32" align="right" alt="" />
      <p><sub>Topic Memory</sub></p>
      <h3>Keep project context</h3>
      <p><code>Discussion</code> → <code>Topic summary</code> → <code>Details on demand</code></p>
      <p>Start with a topic summary, then follow the details and evidence when needed.</p>
      <p><a href="https://powercontext.oceanbase.io/en/docs/workflows/topic-memory/">Build project knowledge →</a></p>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/assets/scenarios/dream.svg" width="32" height="32" align="right" alt="" />
      <p><sub>Dream</sub></p>
      <h3>Learn from past work</h3>
      <p><code>Past evidence</code> → <code>Revision proposal</code> → <code>Your approval</code></p>
      <p>Dream proposes memory and prompt changes for you to review before they take effect.</p>
      <p><a href="https://powercontext.oceanbase.io/en/docs/workflows/review-dream-revisions/">Explore Dream →</a></p>
    </td>
    <td width="50%" valign="top">
      <img src="docs/assets/scenarios/handoff.svg" width="32" height="32" align="right" alt="" />
      <p><sub>Handoff</sub></p>
      <h3>Continue next session</h3>
      <p><code>Verified progress</code> → <code>Next steps</code> → <code>New session</code></p>
      <p>Keep goals, verified progress, and next steps. Check the current state and carry on.</p>
      <p><a href="https://powercontext.oceanbase.io/en/docs/workflows/memory-and-handoff/">Continue your work →</a></p>
    </td>
  </tr>
</table>

## Quick start

You need Python 3.11+, Git, and [uv](https://docs.astral.sh/uv/getting-started/installation/), plus the CLI of any Agent you want to connect. This setup pins version **1.5.2** for macOS and Linux; Windows support is experimental. The [installation script](https://powercontext.oceanbase.io/en/docs/get-started/install-and-run/#install-with-the-recommended-script) can also provision Python and uv.

Choose **Basic memory** in the wizard to try explicit saves and recall without a separate Server model API. Automatic extraction requires **Full memory** with Generation and Embedding credentials. SQLite is a simple first storage choice.

```bash
uv tool install --force "powercontext[cli,server]==1.5.2"
mkdir -p powercontext-config && cd powercontext-config
powercontext config init --language en --output .env
powercontext config validate --env-file .env
powercontext service install --env-file .env
powercontext service status
```

The service keeps running after you close the terminal. Linux requires an available `systemd --user` manager. The generated `.env` and `.env.next-steps.md` may contain credentials; keep them out of Git.

Load the generated configuration and check the service:

```bash
set -a
. ./.env
set +a
powercontext ready
powercontext capabilities
```

Follow **`.env.next-steps.md`** to create and bind your Scope, using the real `scope_id` returned by the creation request. Use the same Server and Scope in your Agent and Dashboard; changing directories does not create project isolation. After editing `.env`, reload it and rerun `powercontext service install --env-file .env` to apply the changes.

For Codex, install the matching integration:

```bash
powercontext setup codex --env-file .env --ref powercontext-v1.5.2
powercontext doctor codex
```

Before sending the first message in a new Codex session, open **`/hooks`** and trust the PowerContext hooks when prompted. The `UserPromptSubmit` hook must be enabled for automatic prompt capture and recall, even when MCP connects. [First-session checks and recovery →](https://powercontext.oceanbase.io/en/docs/integrations/codex/#first-session-hook-trust)

Verify the workflow you enabled:

- **Basic memory:** explicitly ask the Agent to save a project decision, then start a new session in the same Scope and recall it with a source reference.
- **Full memory:** confirm that an input appears as a Source, wait for configured processing to produce Topic Memory, then check that a related update is reflected and can be recalled in a new session.

`doctor codex` checks installation and connectivity only. Source capture alone does not create memory; automatic processing also depends on configured schedules and model work. The Agent still uses its own model in Basic memory mode. [Full setup guide →](https://powercontext.oceanbase.io/en/docs/get-started/quickstart/)

<sub>This starts the context service. It does not automatically reproduce the benchmark's Gateway, CodeGraph, or Steering configuration; see the evidence report.</sub>

## Fits your workflow

<!-- Add integrations using an original logo asset, a product name, and a documentation link. -->
<table width="100%">
  <tr>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/en/docs/integrations/codex/"><img src="docs/assets/codex.png" height="48" alt="Codex" /><br />Codex</a></td>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/en/docs/integrations/claude-code/"><img src="docs/assets/claude-code.png" height="48" alt="Claude Code" /><br />Claude Code</a></td>
    <td align="center" width="20%"><a href="https://github.com/oceanbase/powercontext/tree/master/integrations/minimax#readme"><img src="docs/assets/minimax-code.png" height="48" alt="MiniMax Code" /><br />MiniMax Code</a></td>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/en/docs/integrations/dsh/"><img src="docs/assets/deepseek.png" height="48" alt="DeepSeek Harness" /><br />DeepSeek Harness</a></td>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/en/docs/integrations/zcode/"><img src="docs/assets/zcode.png" height="48" alt="ZCode" /><br />ZCode</a></td>
  </tr>
  <tr>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/en/docs/integrations/kimi-code/"><img src="docs/assets/kimi.png" height="48" alt="Kimi Code" /><br />Kimi Code</a></td>
    <td align="center" width="20%"><a href="https://github.com/oceanbase/powercontext/tree/master/integrations/hermes#readme"><img src="docs/assets/hermes.png" height="48" alt="Hermes Agent" /><br />Hermes Agent</a></td>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/en/docs/integrations/pi/"><img src="docs/assets/pi.png" height="48" alt="Pi Coding Agent" /><br />Pi Coding Agent</a></td>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/en/docs/integrations/openclaw/"><img src="docs/assets/openclaw.png" height="48" alt="OpenClaw" /><br />OpenClaw</a></td>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/en/docs/integrations/opencode/"><img src="docs/assets/opencode.png" height="48" alt="OpenCode" /><br />OpenCode</a></td>
  </tr>
  <tr>
    <td align="center" width="20%"><a href="https://github.com/oceanbase/powercontext/tree/master/integrations/workbuddy#readme"><img src="docs/assets/workbuddy.svg" height="48" alt="WorkBuddy" /><br />WorkBuddy</a></td>
    <td align="center" width="20%"><a href="https://github.com/oceanbase/powercontext/tree/master/integrations/bub#readme"><img src="docs/assets/bub.png" height="48" alt="Bub" /><br />Bub</a></td>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/en/docs/integrations/pydantic-ai/"><img src="docs/assets/pydantic.png" height="48" alt="Pydantic AI" /><br />Pydantic AI</a></td>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/en/docs/integrations/langchain/"><img src="docs/assets/langchain.png" height="48" alt="LangChain" /><br />LangChain</a></td>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/en/docs/integrations/langgraph/"><img src="docs/assets/langgraph.png" height="48" alt="LangGraph" /><br />LangGraph</a></td>
  </tr>
</table>

Codex is officially maintained; other Agent and Python framework integrations are community-maintained. Bub is for evaluation only. See the capability matrix for supported features.

[More integrations and capability comparison →](https://powercontext.oceanbase.io/en/docs/integrations/capabilities/)

**Apps & teams** — [Python / HTTP / MCP](https://powercontext.oceanbase.io/en/docs/develop/interfaces/) · [Memory & Handoff](https://powercontext.oceanbase.io/en/docs/workflows/memory-and-handoff/) · [Deployment & access](https://powercontext.oceanbase.io/en/docs/workflows/scopes-and-access/)

---

[Documentation](https://powercontext.oceanbase.io/en/docs/) · [Contribute](https://github.com/oceanbase/powercontext/blob/master/CONTRIBUTING.md) · [Issues](https://github.com/oceanbase/powercontext/issues) · [Discord](https://discord.com/invite/74cF8vbNEs)

PowerContext is the successor to [PowerMem](https://www.powermem.ai/). Released under [Apache 2.0](LICENSE).
