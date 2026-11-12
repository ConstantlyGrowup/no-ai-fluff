# no-ai-fluff: setup guide for coding agents

This page is written for an AI coding agent (Claude Code, Codex, Qoder, WorkBuddy, ZCode, DeepSeek Harness, etc.) that has been asked to install no-ai-fluff for its user.
Follow the steps in order. Ask the user only where a step says so.

## 0. Pick the edition

| The user writes mostly in | Install | Command | MCP server name |
|---|---|---|---|
| Chinese | Chinese edition | `no-ai-fluff` | `no-ai-fluff` |
| English | English edition | `no-ai-fluff-en` | `no-ai-fluff-en` |

Both come from the same package. If unsure, ask the user which language their long replies and documents are usually in. Installing both is fine.

## 1. Check Python

```bash
python --version   # needs 3.10 or newer
```

If Python is older than 3.10, stop and tell the user.

## 2. Install the package

```bash
pip install "git+https://github.com/ConstantlyGrowup/no-ai-fluff.git"
```

Then confirm the command is on PATH:

```bash
no-ai-fluff --version        # Chinese edition
no-ai-fluff-en --version     # English edition
```

If the command is not found, pip installed it into a user scripts folder that is not on PATH (pip prints the folder in a warning). Either add that folder to PATH or run the CLI as `python -m no_ai_fluff.cli` / `python -m no_ai_fluff.en.cli`.

## 3. Register with the agent

First work out which agent you are running in (you usually know your own product name; otherwise look for its config folder in the user's home directory or project: `.claude/`, `.codex/`, `.qoder/`, `.workbuddy/`, `.zcode/`). Then follow the matching part below. If you are not sure, ask the user.

### 3a. Claude Code

Run this in the user's project root:

```bash
no-ai-fluff install claude-code        # Chinese edition
no-ai-fluff-en install claude-code     # English edition
```

This writes:

- `.claude/agents/no-ai-fluff.md` (or `no-ai-fluff-en.md`): the reviewer subagent;
- `.claude/skills/no-ai-fluff/SKILL.md` (or `no-ai-fluff-en`): the `/no-ai-fluff` command;
- an entry in `.mcp.json` that starts the MCP server.

Files that already exist with different content are not overwritten unless `--force` is passed. For a user-level install use `--scope user` and then run the `claude mcp add -s user ...` command it prints.

### 3b. Other agents

Agents other than Claude Code do not understand Claude Code's subagent files, so they get a portable skill instead (a `SKILL.md` in the Agent Skills format) plus a short section in `AGENTS.md`. Both tell the agent how to call the two MCP tools, to add the one-line "ask me to rewrite a paragraph" note after delivery, and the seven rules for that rewrite. Run the line for your agent in the user's project root (use `no-ai-fluff-en` for the English edition):

| Agent | Command | Skill goes to | Also writes | Register the MCP server |
|---|---|---|---|---|
| Codex | `no-ai-fluff install codex` | `.agents/skills/no-ai-fluff/` | `AGENTS.md` section | `codex mcp add no-ai-fluff -- no-ai-fluff mcp` |
| ZCode | `no-ai-fluff install zcode` | `.agents/skills/no-ai-fluff/` | `AGENTS.md` section | ZCode's MCP settings |
| Qoder | `no-ai-fluff install qoder` | `.qoder/skills/no-ai-fluff/` | `AGENTS.md` section | Qoder's MCP settings |
| WorkBuddy | `no-ai-fluff install workbuddy` | `~/.workbuddy/skills/no-ai-fluff/` (user level only) | — | WorkBuddy's MCP settings |
| DeepSeek Harness and anything else | `no-ai-fluff install generic` | `.agents/skills/no-ai-fluff/` | `AGENTS.md` section | the agent's MCP settings or plugin |

Notes:

- `--scope user` installs into the user's home folder instead (Codex and ZCode also get `~/.codex/AGENTS.md` / `~/.zcode/AGENTS.md`).
- The `AGENTS.md` section sits between `<!-- no-ai-fluff:start -->` and `<!-- no-ai-fluff:end -->`; the rest of the file is left alone. Re-running is safe. If the user edited that section, it is not replaced unless `--force` is passed.
- If your agent keeps skills or rules somewhere not listed here, run `no-ai-fluff install generic --dir <some temp folder>` and copy the generated `SKILL.md` (and the `AGENTS.md` section) to where your agent reads them. Tell the user where you put them.
- Every MCP server entry is the same: a stdio server with command `no-ai-fluff` and argument `mcp` (or `no-ai-fluff-en` / `mcp`). In JSON configs: `{"mcpServers": {"no-ai-fluff": {"command": "no-ai-fluff", "args": ["mcp"]}}}`.
- Even without the skill or `AGENTS.md`, the rewrite note still reaches the agent: `finalize_patched_delivery` returns it after the final text, together with the seven rules.

## 4. Configure the API key (ask the user)

no-ai-fluff calls one small translation model (hy-mt2) for the round trip. It needs one key:

| Provider | Environment variable | Where the user gets it |
|---|---|---|
| OpenRouter (recommended) | `OPENROUTER_API_KEY` | <https://openrouter.ai/keys> |
| Tencent Cloud TokenHub (fallback) | `TENCENTCLOUD_API_KEY` | Tencent Cloud console, TokenHub, model `hy-mt2-pro` |

Recommend OpenRouter. Tencent Cloud allows 60 calls per minute by default and about 1,200 characters per call, so long documents are slower there. The round trip on OpenRouter cost about $0.002 per document in our evaluation.

Rules for handling the key:

1. Ask the user to paste the key, or to put it in `.env` themselves.
2. Write it to `.env` in the project root as `OPENROUTER_API_KEY=...` (or `TENCENTCLOUD_API_KEY=...`).
3. Make sure `.env` is listed in `.gitignore`. Never commit it, print it, or write it into logs.

If both keys are present, OpenRouter is used. If only the Tencent Cloud key is present, it is used automatically.

## 5. Verify

Run one sample through the round trip and check that it did not fall back to the original:

```bash
echo "本次重构以 SingleFlight 为底座收口回源请求，打通两级缓存的失效闭环，显著降低调用方心智负担。" | no-ai-fluff prepare - --exit file
echo "This sprint we drove alignment across the platform workstream and unlocked a step-function improvement in tail latency." | no-ai-fluff-en prepare - --exit file
```

In the JSON output, `"fallback": false` means the key works. `"fallback": true` with a reason such as `OPENROUTER_API_KEY ... not set` or `HTTP 401` means the key is missing or wrong.

## 6. Finish

Tell the user to restart the agent and approve the `no-ai-fluff` (or `no-ai-fluff-en`) MCP server when asked. From then on they can type `/no-ai-fluff` in Claude Code, or in any agent say "finalize this with no-ai-fluff" after a long reply or document.

After a final text is delivered, the user can name a paragraph that is still too dense and ask for a full rewrite of just that part. The installed skill (and the `AGENTS.md` section, and the MCP result itself) carries the rules for this: rewrite only the named part, keep code, paths, formulas and numbers exactly, and add nothing the original did not say.

Recommend running the reviewer subagent with high reasoning effort: in our tests, low effort missed factual errors in 5 of 5 documents, high effort in 1 of 5.

## Optional settings

| Variable | Default | Meaning |
|---|---|---|
| `NO_AI_FLUFF_PROVIDER` / `NO_AI_FLUFF_EN_PROVIDER` | auto | `openrouter` or `tencent` |
| `NO_AI_FLUFF_MODEL` / `NO_AI_FLUFF_EN_MODEL` | provider default | translation model |
| `NO_AI_FLUFF_BRIDGE` / `NO_AI_FLUFF_EN_BRIDGE` | `ru` / `ja` | bridge language for the round trip |
| `NO_AI_FLUFF_TENCENT_RPM` | `60` | Tencent Cloud calls per minute (client-side limiter) |
| `NO_AI_FLUFF_TELEMETRY` | `1` | `0` turns off local statistics (counts only, never text) |
