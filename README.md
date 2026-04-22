# 🤖 WorkerXY

**The Ultimate AI Copilot for Network Operations & Automation.**

WorkerXY is a high-performance, agentic AI system designed to streamline network diagnostics, cloud management, and datacenter operations. From troubleshooting network and systems drifts to managing global AWS clusters, WorkerXY is your specialist operational companion.

<div align="center">

![WorkerXY Dashboard](screenshots/workerxy_page.png)

</div>

---

## 🌟 Core Features

### 🤖 Intelligent Agent Core
1. **Agent-to-Agent (A2A)**: Dynamically attach specialist agents at runtime via `/agents`.
2. **Third-Party Plugins**: Define custom subagents declaratively in JSON files for runtime extensibility. ([Plugin Guide](file:///workerxy/documentation/PLUGINS.md))
3. **Streaming TUI**: Real-time token-streaming cockpit with rich formatting.
4. **Security Guardrails**: Mandatory approval for sensitive ops like shell/code execution.
5. **Tool Announcements**: Real-time console feedback with human-readable descriptions for every tool call.

### 🧠 Persistent Memory & Skills
*   **Long-term Memory**: Remembers past interactions across sessions.
*   **Dynamic Skills**: Extract new capabilities from documentation instantly via `/skills add <path>`.
*   **Auto-Learning**: Updates skills based on session context.

### ⚙️ Automata (Autonomous Ops)
*   **Agent-Driven Scheduling**: The `automata_agent` subagent can programmatically schedule recurring tasks like "verify server status every 15 mins".
*   **Autonomous Execution**: WorkerXY handles the execution and logging even while you're offline.
*   **Full Lifecycle & Verification**: List, pause, resume, and inspect detailed execution logs via `/automata` or directly through the agent.

### 🛡️ Smart Middlewares
*   **NetPII Pseudonymization**: Automatically masks IPs, MACs, and URLs for privacy.
*   **Context Pruning**: Intelligent summarization as you approach token limits (85% threshold).
*   **Input Protection**: Integrated guardrails to prevent prompt injections.

---

## 🎨 User Interfaces

### 🏎️ Agent Cockpit (CLI)
A high-performance terminal UI designed for operational awareness.
- **Unified View**: Chat history, live tool execution, and logs in one place.
- **Autocomplete**: Full tab-autocompletion for all Slash-commands.

### 🌐 WorkerXY Dashboard (Streamlit)
- **Visual Logs**: Watch tool execution and agent thoughts in real-time.
- **Hot-Configuration**: Reload middlewares and settings without restarting.

---

## 🔧 Deep Tool Integrations

WorkerXY leverages the **Model Context Protocol (MCP)** to interact with your stack:

- **🌐 Browsing**: Real-time research and documentation extraction.
- **💻 Infrastructure**: SSH & CLI interaction with Cisco, Juniper, Arista, and Linux.
- **📋 ITSM**: Full **ServiceNow** integration for Incidents, Changes, and CMDB.
- **📐 NetDesign**: Automated **Draw.io** diagram generation from natural language.
- **☁️ Cloud**: Native operators for AWS, Azure, and GCP.

---

## ⌨️ Essential Slash Commands

| Command | Description |
| :--- | :--- |
| `/bootup` | Trigger a startup briefing and check for automated tasks. |
| `/session new` | Save current session and start a fresh one. |
| `/agents list` | Manage specialist A2A agents. |
| `/skills list` | View and add capabilities to the agent. |
| `/automata` | List, add, or stop background tasks. |
| `/middlewares` | Configure PII masking and context pruning. |
| `/memory` | View the agent's current persistent knowledge. |

---

## 📖 Usage Examples

### 🎮 Powered-Up Interactive CLI
Run the agent with intelligent context detection and a specific model configuration:
```bash
workerxy cli --model gpt-5.1-medium --subagent-model gpt-5.1-no-thinking --automatic-context-detection --association-window 5
```

### 🤖 Custom Headless Worker
Standard headless execution without the association overhead:
```bash
workerxy headless --model gpt-5.1-medium --subagent-model gpt-5.1-no-thinking
```

### 🔇 Independent Logging
Control screen and file logging levels independently. Use the custom `NOTICE` level for a perfect balance of visibility:
```bash
# Verbose file logging (DEBUG) but cleaner screen (NOTICE)
workerxy cli --log-level DEBUG --monitor NOTICE
```
- `--log-level`: Sets the level for the file log (`~/.net-deepagent/logs/main.log`).
- `--monitor`: Sets the level for the screen/console output only.
- Available levels: `DEBUG`, `INFO`, `NOTICE` (25), `WARNING`, `ERROR`, `CRITICAL`.

---

## ⚙️ Quick Start

### 📋 Prerequisites
- Python 3.12+
- `uv` (recommended) or `pip`
- RabbitMQ (required for Discord/Headless modes)

### 🐳 Docker Installation (Recommended)
The easiest way to get started with all system dependencies (like RabbitMQ and browsers):

```bash
# Clone and enter
git clone https://github.com/nkxy007/workerxy.git
cd workerxy

# (Linux only) Ensure your user has Docker permissions
sudo usermod -aG docker $USER && newgrp docker

# Build the unified image
docker compose build

# Option 1: Start everything (MCPs, Headless, Discord, UI) in one container
docker compose up all

# Option 2: Run the smart CLI (Interactive Terminal with dual MCPs running in background)
docker compose run --rm cli

# Option 3: Start the smart Headless Worker (Headless loop + dual MCPs + Discord Bot)
docker compose up headless

# Option 4: Start the smart GUI (Streamlit Web UI + dual MCPs)
docker compose up gui
```

### 🌍 Run From Anywhere (Global Alias)
To avoid having to `cd` into the `workerxy` directory every time, simply create a global alias in your shell configuration (e.g., `~/.bashrc` or `~/.zshrc`):

```bash
# Add this line to your ~/.bashrc or ~/.zshrc
alias workerxy-docker='docker compose -f /path/to/your/cloned/workerxy/docker-compose.yml --project-directory /path/to/your/cloned/workerxy'

# Reload your shell
source ~/.bashrc

# Now you can start the UI from anywhere:
workerxy-docker up gui
```

### 🛠 Manual Installation
```bash
# Clone and enter
git clone https://github.com/nkxy007/workerxy.git
cd workerxy

# Install dependencies
pip install -e .

# Run the initialization script
python initializer.py
```

## 🔐 Credential Security & Vault

WorkerXY supports advanced credential management to keep your API keys secure. You can use either a **Local Encrypted Vault** or a **HashiCorp Vault** instance.

- **Local Vault**: Encrypts `creds.json` into `~/.creds.vault` with a password.
- **HashiCorp Vault**: Syncs credentials from a centralized KVv2 store.

For full setup instructions, see the [Vault Setup Guide](file:///home/<user>/workspace/workerxy/documentation/VAULT_SETUP.md).

## 🚀 Activation Matrix

After installation and initialization, start your components using the `workerxy` multi-tool CLI.

| Command | Component | Description |
| :--- | :--- | :--- |
| `workerxy start-cli` | **Smart CLI** | Multi-tool: Starts MCPs + launches Interactive TUI. |
| `workerxy start-headless` | **Smart Worker** | Multi-tool: Starts MCPs & Discord + launches Headless worker. |
| `workerxy start-gui` | **Smart Dashboard** | Multi-tool: Starts MCPs + launches Streamlit UI. |
| `workerxy start-all` | **Full Stack** | Multi-tool: Starts MCPs, Discord, Headless, and UI in one go. |
| `workerxy mcp base` | **Core MCP** | Launch the primary network/ITSM tool server (Port 8000). |
| `workerxy mcp lab` | **Lab MCP** | Launch the EVE-NG/Lab automation server (Port 8001). |
| `workerxy cli` | **Direct CLI** | Launch the TUI directly (requires background MCPs). |
| `workerxy ui` | **Direct UI** | Launch Streamlit directly (requires background MCPs). |
| `workerxy skill` | **Skill Manager** | CLI tool for creating and managing agent capabilities. |

Extra examples:
 `workerxy cli --model gpt-5.1-medium --subagent-model gpt-5.1-no-thinking --automatic-context-detection --association-window 5`

 `workerxy headless --model gpt-5.1-medium --subagent-model gpt-5.1-no-thinking`

 `docker compose run --rm cli --model gpt-5.1-medium --subagent-model gpt-5.1-no-thinking --automatic-context-detection --association-window 5 --log-level WARNING`

 `streamlit run ui/app.py --logger.level=debug`

---

Built with ❤️ by the XTOFTech Advanced Agentic Coding team.

