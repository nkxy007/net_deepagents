# CoworkerX Networking — CLI Features

A living reference of the features supported by the `net_deepagent_cli`.

---

## 🤖 Agent Core

| Feature | Command / Detail |
|---|---|
| Interactive chat with the main agent | Type any message at the `>` prompt |
| Streaming responses | Agent responses stream token-by-token in real-time |
| Tool execution visibility | Each tool call is displayed with name and arguments |
| Security approval for sensitive tools | Prompted before `execute_shell_command`, `execute_generated_code`, etc. |
| Human-in-the-loop clarification | Agent can ask user questions via `user_clarification_and_action_tool` |
| Auto-approve mode | `--auto-approve` CLI flag bypasses approval prompts |
| Skill Management CLI | `workerxy skill <args>` — Launches the skill-manager CLI |
| Independent Screen Logging | `--monitor <LEVEL>` — Set console level separately from file level |
| Custom `NOTICE` Level | Level `25` (between INFO/WARNING) — supported in all logging flags |

---

## 🔌 A2A Agent Management (`/agents`)

Dynamically attach external Agent-to-Agent (A2A) specialist agents at runtime.

| Command | Description |
|---|---|
| `/agents` or `/agents list` | Show all agents in the registry with session loaded/unloaded status |
| `/agents add <name> <url>` | Persist agent to `agents_registry.json` and load it live |
| `/agents remove <name>` | Remove agent from registry **and** unload from session |
| `/agents unload <name>` | Unload from session only — registry entry kept for later |
| `/agents load` | Reload all registry agents into the current session |

**Built-in A2A agents:**
- `dns_deepagent` — DNS resolution and diagnostics
- `dhcp_deepagent` — DHCP lease/pool management and troubleshooting

---

## 🧠 Memory

| Feature | Command / Detail |
|---|---|
| Persistent agent memory | Stored at `~/.net-deepagent/<agent-name>/agent.md` |
| Memory injected at each turn | Automatically prepended to system prompt |
| View current memory | `/memory` |

---

## 🛠 Skills

| Feature | Command / Detail |
|---|---|
| List available skills | `/skills` or `/skills list` |
| Extract skill from document | `/skills add <path>` — **Async**: Runs in background after name prompt |
| Update skill from context | `/skills update [name]` — **Async**: Checks for updates in background |
| Auto-injected into system prompt | Skills discovered from `~/.net-deepagent/<name>/skills/` and `./skills/` |

---

## 💾 Session Management

| Feature | Command / Detail |
|---|---|
| Save conversation to file | `/save <name>` |
| Resume saved session | `/resume <name>` |
| List all saved sessions | `/sessions` |
| Start a new session | `/session new` — Prompt to save current session then clear it fresh |
| Delete a saved session | `/session delete <name>` — Delete a saved session file |
| Adjust drift sensitivity | `/session threshold <val>` | Adjust topic drift sensitivity (0.0-1.0) |
| `/session window <days>` | Adjust past interaction lookback window |
| Automatic context detection | `--automatic-context-detection` (startup flag) |
| Association window | `--association-window <days>` (startup flag) |
| Persistent prompt history | Stored at `~/.net-deepagent/<name>/history` |
| Centralized Credential Management | `~/.net-deepagent/creds.json` — Unified API keys and SSH config |

---

## ⚙️ Automata (Background Tasks)

Scheduled tasks that run autonomously in the background while you continue chatting.

| Feature | Command / Detail |
|---|---|
| `/automata` | Open the interactive automata management menu |
| `/automata list` | List all scheduled background tasks |
| `/automata add` | Schedule a new recurring task (regex + LLM parsing) |
| `/automata remove <id>` | Remove a task by ID |
| `/automata stop <id>` | Stop a task without removing it |
| `/automata resume <id>` | Resume a stale/paused task |
| `/automata logs <id>` | List timestamped log files for a task |
| `/automata view <filename>` | View content of a specific execution log |
| **Agent Scheduling** | The `automata_agent` subagent can create and manage jobs programmatically from chat |
| **Immediate Execution** | Newly created jobs trigger their first run immediately by default |

Logs are persisted to `~/.net-deepagent/<name>/automata_results/`.

---

## 🔌 Middleware Management (`/middlewares`)

Hot-reloadable custom middlewares that process model inputs and outputs in real-time.

| Command | Description |
|---|---|
| `/middlewares` | Open the interactive middleware configuration menu |

**Available Middlewares:**
- **Advanced Context Pruning** — Automatically prunes skills, old tool outputs, and summarizes context at 85% limit.
- **NetPII Pseudonymization** — Masks sensitive network info like IPs, MAC addresses, and URLs. Supports per-type selection.
- **Security Guardrail** — Basic security checks for model inputs/outputs (Rebuff).

**Key Features:**
- **Hot-Reloading**: Changes take effect on the very next message without restarting the CLI.
- **Interactive Configuration**: Selectively enable PII types or tune context pruning parameters via sub-menus.

---

## 📊 Context & Diagnostics

| Feature | Command / Detail |
|---|---|
| Token usage | `/tokens` — total tokens used this session |
| Context analysis | `/context` — breakdown by message type + estimated depth |
| Clear conversation | `/clear` |

---

## 🔧 MCP Tools (via MCP server)

The agent connects to an MCP server on startup and dynamically loads all registered tools. Examples include:

- LAN networking tools (routing, switching, SSH config)
- Cloud tools (AWS, Azure, GCP)
- Network design / Draw.io diagram tools
- Datacenter and ISP tools
- Retriever/Archiver tools (conversation memory, RAG queries)

---

## 🎨 UI & Autocompletion

| Feature | Detail |
|---|---|
| Rich terminal UI | Colours, panels, markdown rendering, progress spinners |
| Command autocompletion | Tab-complete for all `/commands` and their sub-commands |
| Partial match suggestions | Typing `/ag` suggests `/agents` |
| Sub-command hints | After `/agents ` tab shows `list`, `add`, `remove`, `unload`, `load` |
| Prompt history | Up/down arrows cycle through previous inputs |

---

---

## 📡 Headless & Remote Communication

Decoupled communication system for remote agent interaction via Discord and RabbitMQ.

| Component | Description | Launch Command |
|---|---|---|
| **Discord Bot** | Bridges Discord messages to RabbitMQ | `python net_deepagent_cli/communication/discord_bot.py` |
| **Headless Agent** | Processes jobs from RabbitMQ (Worker Mode) | `python net_deepagent_cli/headless.py` |
| **RabbitMQ Broker** | Reliable message delivery between bot and agent | `sudo systemctl start rabbitmq-server` |

### Key Features:
- **Mention-to-Job**: Mentioning the bot on Discord automatically creates a job for the agent.
- **Automatic Replies**: The agent's final textual response is automatically sent back to the Discord channel where it was mentioned.
- **Channel Routing**: Supports routing messages to specific channels by name (e.g., `Network-jobs`).
- **Structured Logging**: All activity is logged to `~/.net-deepagent/logs/main.log`. Use `--log-level` (default INFO) to control file detail, and `--monitor` (default matches log-level) to control screen detail.
- **Worker Mode**: `headless.py` allows the agent to run 24/7 as a background worker without a terminal UI.

---

### ⚡ Topic-Drift Detection & Interaction Association

When started with `--automatic-context-detection`, the agent monitors your questions for two things:

1.  **Topic Drift**: If it detects a significant shift in topic (e.g., from BGP troubleshooting to AWS lambda functions), it suggests starting a new session.
2.  **Interaction Association**: If you refer to past work (e.g., "remember that BGP issue from yesterday?"), it searches your recent sessions (default 5-day window) and offers to resume the relevant one automatically.

- **Sensitivity**: Adjust drift sensitivity via `/session threshold <0.0-1.0>`.
- **Lookback**: Adjust association window via `/session window <days>`.
