# Claude Code runs

Odysseus can hand a task to Claude Code and stream back what it does. This is
the orchestrator shape (the way Multica, Conductor and similar tools drive
agent CLIs), not a model provider: Claude Code is never used as a completion
endpoint for Odysseus's own agent loop.

## How it works

1. The operator installs Claude Code on the Odysseus server, unmodified
   (`ODYSSEUS_CLAUDE_CODE_PATH` if it is not on `PATH`).
2. Each Odysseus user signs in to **their own** Claude Code, once, in a terminal
   on the server:

   ```bash
   python scripts/claude_code_login.py --user alice
   # Docker:
   docker compose exec -it odysseus python scripts/claude_code_login.py --user alice
   ```

   This runs Claude Code's own `claude auth login` with a private
   `CLAUDE_CONFIG_DIR` under `DATA_DIR/claude_code/<user>/.claude`. The user picks
   a Claude subscription or a Console API key in Anthropic's flow. Odysseus
   never sees the credential and does not restrict which sign-in method the
   binary offers.
3. `POST /api/claude-code/runs` starts a run and streams server-sent events:

   ```bash
   curl -N -X POST http://127.0.0.1:7000/api/claude-code/runs \
     -H 'Content-Type: application/json' -b cookies.txt \
     -d '{"prompt": "Summarise my open todos", "session_key": "demo", "scopes": ["todos:read"]}'
   ```

   Events: `init` (session id, model, permission mode, auth source), `text`
   (streamed reply), `tool` (each Claude Code tool call with a short summary),
   `result` (final text, turns, token usage) or `error`, then `[DONE]`.

Odysseus starts:

```
claude -p --output-format stream-json --input-format stream-json --verbose
       --include-partial-messages --permission-mode <mode>
       --disallowedTools AskUserQuestion [--model M] [--effort E] [--resume S]
```

in the user's own workspace directory, writes the task as one stream-json user
message, closes stdin and reads events until the result. Claude Code runs its
own loop, tools, system prompt and skills.

## What Claude Code can reach

- **Its own tools**, under `ODYSSEUS_CLAUDE_CODE_PERMISSION_MODE` (default
  `acceptEdits`, confined to the workspace by Claude Code's own rules).
  `bypassPermissions` is refused: the run executes on the Odysseus server, not on
  the user's laptop.
- **Odysseus data, only if asked.** With `scopes` in the request, Odysseus mints a
  short-lived API token for the caller with exactly those scopes, hands it to
  the run as `ODYSSEUS_API_TOKEN` with `ODYSSEUS_URL`, installs the bundled
  `odysseus` skill (`integrations/claude/skills/odysseus`) into the user's Claude
  config, allows only that skill's helper command in Bash, and deletes the token
  when the run ends. Everything goes through the scope-gated `/api/codex/*` API,
  so a `403` there is a real limit.
- **Not the server's environment.** The child gets an allowlisted environment
  (`PATH`, locale, proxies, CA bundles) plus its own `HOME`, `CLAUDE_CONFIG_DIR`
  and `TMPDIR`. The server's own `ANTHROPIC_API_KEY`, database URL and app keys
  never reach it, so one account is never billed for another user's run.

## Sessions and limits

- `session_key` maps to the Claude Code session id per user, and the next run
  with the same key passes `--resume`. A resume the CLI rejects is forgotten so
  the next run starts fresh. `GET /api/claude-code/sessions`,
  `DELETE /api/claude-code/sessions/{key}`.
- One run at a time per user, `ODYSSEUS_CLAUDE_CODE_MAX_RUNS` (default 2) per
  worker, `ODYSSEUS_CLAUDE_CODE_TIMEOUT` (default 1800 s). Closing the stream or
  hitting the timeout ends the CLI's whole process group (TERM, then KILL).
- Runs are admin-only unless `ODYSSEUS_CLAUDE_CODE_USERS=all`. Callers holding an
  API token (including a run's own token) cannot start runs.

## Why this shape

Anthropic's Claude Code terms
(<https://code.claude.com/docs/en/legal-and-compliance>) allow "an end user
signing in to the unmodified Claude Code binary with their own Claude
subscription, including where a platform hosts Claude Code", provided the
binary is unmodified, its sign-in methods are not restricted, and each end user
authenticates with their own credentials. They do not allow a product to route
its own model requests through plan credentials or to collect Claude.ai
tokens. So Odysseus hosts Claude Code doing the work as Claude Code; it does not
borrow a subscription for its own agent. The feature is described as "runs
Claude Code" in text only; the name is not used as a product or picker label.

## Not covered yet

- No UI: the API above is the whole surface. A chat-side "hand this to Claude
  Code" tool for Odysseus's own agent would reuse `src/claude_code_runner.py`.
- Odysseus approval gates do not apply to Claude Code's own tools; its
  permission mode is the control.
- Windows and macOS keychain storage are untested.
