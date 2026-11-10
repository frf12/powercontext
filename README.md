<p align="center">
  <img src="docs/assets/readme-hero-en.png" width="100%" alt="PowerContext: 32.57% fewer tokens including memory generation on 24 tasks; estimated model cost 15.42% lower at the observed cache-hit rate. Auxiliary costs excluded." />
</p>

<p align="center">
  <a href="#quick-start"><b>Get started</b></a> &nbsp; · &nbsp;
  <a href="#proof-it-works">See the proof</a> &nbsp; · &nbsp;
  <a href="https://powercontext.oceanbase.io/en/docs/">Documentation</a> &nbsp; · &nbsp;
  <a href="https://discord.com/invite/74cF8vbNEs">Discord</a>
</p>
<p align="center">
  <a href="README.md">English</a> · <a href="README_CN.md">中文</a> &nbsp; / &nbsp;
  <a href="https://pypi.org/project/powercontext/">Python package</a> · <a href="LICENSE">Apache 2.0</a>
</p>

## Why PowerContext?

**Your agent's tokens should move the task forward.** Less repeated searching, fewer unproductive round trips, and project knowledge that is still useful when the next session starts.

- **Focus on the task.** Bring relevant code and clear working guidance into the agent's path to a fix.
- **Keep your agent.** Connect Codex, Claude Code, and other supported hosts, or integrate with your own application.
- **Keep what you learn.** Retain decisions, constraints, and handoffs across sessions, agents, and teammates.

## What is PowerContext?

**An open-source context runtime for AI agents.** It manages code context, project memory, and task handoffs, bringing relevant context into the workflow when it is needed.

<img src="docs/assets/readme-capabilities-en.svg" width="100%" alt="CodeGraph: relevant code with freshness checks. Steering: targeted reads, batched work and focused tests. Memory: saved decisions and bounded recall for later sessions." />

Configure the capabilities you need. Context is organized into **Scopes**, accessible through HTTP, MCP, Python, and agent integrations. [How it fits together →](https://powercontext.oceanbase.io/en/docs/workflows/memory-and-handoff/)

## Proof it works

<table>
<tr>
<td width="50%" align="center"><h3>32.57% ↓</h3><b>Fewer tokens across 24 tasks</b><br/><sub>Memory generation included</sub></td>
<td width="50%" align="center"><h3>15.42% ↓</h3><b>Lower estimated cost across 24 tasks</b><br/><sub>Memory generation included</sub></td>
</tr>
<tr>
<td width="50%" align="center"><h3>63.49% ↓</h3><b>Largest single-task token reduction</b><br/><sub>Case 456 · both runs passed</sub></td>
<td width="50%" align="center"><h3>51.98% ↓</h3><b>Largest single-task estimated cost reduction</b><br/><sub>Case 456 · both runs passed</sub></td>
</tr>
</table>

Both groups solved **16/24 tasks**. Both single-task peaks come from case 456: including memory generation, tokens fell from **2,301,174 to 840,186**, and estimated cost from **33.53 to 16.10 points**. These peaks are not typical or guaranteed savings.

On the same 24 tasks, PowerContext used **17,808,978 tokens** for solving and memory generation, versus **26,410,221 tokens** for the control. The control uses the same gateway with CodeGraph, Steering, and Memory disabled.

**Tokens are measured; cost is estimated.** Cache fields are missing from 55 memory-generation records. The estimate applies the **96.994%** input-token cache-hit rate observed in the remaining records, at the experiment's fixed rates. Observed records are prefix extractions; missing records are ordinary generation calls, so this extrapolation may be optimistic. Embedding, readiness checks, and other auxiliary costs are excluded. This is not an actual bill. [Task-level results and calculation →](docs/benchmarks/token-savings/README.md)

Across all 121 tasks, CodeGraph + Steering used **36.18% fewer solving tokens**, with 82 tasks solved versus 83 for the control. The largest single-task reduction was **85.71%**: case 302 used 177,343 versus 1,240,658 tokens, and both runs passed. This is not an average.

<details>
<summary>Test scope, auxiliary costs, and retries</summary>

In the detailed results, **C = CodeGraph, S = Steering, M = Memory**. Combined letters mean those features are enabled together. **Gateway OFF** is the control with all three disabled.

- On the same 24 tasks: S saves 27.12% (15/24 solved), CS 33.06% (15/24), MCS 40.30% (16/24); Gateway OFF solves 16/24. This is a completed-task intersection, not a random sample.
- MCS adds 2,041,992 post-task memory-generation tokens. Including them gives a 32.57% reduction, before other auxiliary work. No nonempty Memory injection was observed during solving; the savings do not establish cross-task memory benefits.
- Across 121 tasks, CodeGraph alone uses 10.53% more tokens. CS solves one fewer task than OFF. Implementation and validation routes vary; these runs do not guarantee savings on every task or equal quality.
- Conditional retry selections, all-attempt costs, cached and uncached tokens are separate in the evidence report. They are not mixed into the headline. MCS did not finish all 121 tasks.
- Tested with Codex CLI 0.162.1, GPT-5.6 Sol medium, a frozen development build and fresh databases per attempt. S is explicit guidance, not an installation default. OFF is an earlier frozen batch.

[Methodology](docs/benchmarks/token-savings/README.md) · [Data](docs/benchmarks/token-savings/results.json) · [Offline verifier](docs/benchmarks/token-savings/verify.py)

</details>

## Quick start

You need Python 3.11+, Git, and [uv](https://docs.astral.sh/uv/getting-started/installation/). This setup pins version 1.5.0 for macOS and Linux.

```bash
uv tool install --force "powercontext[cli,server]==1.5.0"
mkdir -p powercontext-config && cd powercontext-config
powercontext config init --language en --output .env
powercontext config validate --env-file .env
powercontext server run --env-file .env
```

Keep the service running. Follow **`.env.next-steps.md`** to create and bind your Scope. Start with Basic memory; automatic extraction requires Generation and Embedding credentials.

For Codex, install the matching integration in another terminal:

```bash
powercontext setup codex --ref powercontext-v1.5.0
powercontext doctor codex
```

Start a new session and trust the hooks when prompted. Save a project decision, then recall it in a later session. [Full setup guide →](https://powercontext.oceanbase.io/en/docs/get-started/quickstart/)

<sub>This starts the context service. It does not automatically reproduce the benchmark's Gateway, CodeGraph, or Steering configuration; see the evidence report.</sub>

## Fits your workflow

**Agents** — [Codex](https://powercontext.oceanbase.io/en/docs/integrations/codex/) · [Claude Code](https://powercontext.oceanbase.io/en/docs/integrations/claude-code/) · [OpenCode, OpenClaw, Pi and capability matrix](https://powercontext.oceanbase.io/en/docs/integrations/capabilities/)

**Apps & teams** — [Python / HTTP / MCP](https://powercontext.oceanbase.io/en/docs/develop/interfaces/) · [Memory & Handoff](https://powercontext.oceanbase.io/en/docs/workflows/memory-and-handoff/) · [Deployment & access](https://powercontext.oceanbase.io/en/docs/workflows/scopes-and-access/)

---

[Documentation](https://powercontext.oceanbase.io/en/docs/) · [Contribute](https://github.com/oceanbase/powercontext/blob/main/CONTRIBUTING.md) · [Issues](https://github.com/oceanbase/powercontext/issues) · [Discord](https://discord.com/invite/74cF8vbNEs)

PowerContext is the successor to [PowerMem](https://www.powermem.ai/). Released under [Apache 2.0](LICENSE).
