import sys
import asyncio
import subprocess
import os
import logging
from warnings import filterwarnings

logger = logging.getLogger(__name__)

filterwarnings("ignore", category=DeprecationWarning)
filterwarnings("ignore", category=RuntimeWarning)
# ignore typing.NotRequired
filterwarnings("ignore", category=FutureWarning)
filterwarnings("ignore", category=UserWarning)


def main():
    valid_commands = ["cli", "headless", "discord", "slack", "ui", "mcp", "skill", "diagnose", "vault", "init",
                      "start-cli", "start-headless", "start-gui", "start-all"]
                      
    if len(sys.argv) == 2 and sys.argv[1] in ("--version", "-v", "-V"):
        try:
            from net_deepagent_cli.cli import get_version
            print(f"workerxy {get_version()}")
        except ImportError:
            print("workerxy (version unknown - failed to import cli)")
        sys.exit(0)
        
    if len(sys.argv) < 2 or sys.argv[1] not in valid_commands:
        print(f"Usage: workerxy {{{','.join(valid_commands)}}} [options]")
        sys.exit(1)

    command = sys.argv.pop(1)  # Remove the subcommand so existing parsers work cleanly

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if command == "cli":
        from net_deepagent_cli.cli import main as cli_main
        cli_main()
    elif command == "init":
        try:
            initializer_path = os.path.join(project_root, "initializer.py")
            subprocess.run([sys.executable, initializer_path] + sys.argv[1:], cwd=project_root)
        except KeyboardInterrupt:
            logger.info("Initializer stopped by user.")
    elif command == "headless":
        from net_deepagent_cli.headless import run_headless, logger as headless_logger
        try:
            asyncio.run(run_headless())
        except KeyboardInterrupt:
            # Matches existing headless.py behavior
            headless_logger.info("Headless agent stopped by user.")
    elif command == "discord":
        try:
            from net_deepagent_cli.communication.discord_bot import main as discord_main
            discord_main()
        except KeyboardInterrupt:
            # Matches existing discord.py behavior
            logger.info("Discord bot stopped by user.")
    elif command == "slack":
        try:
            from net_deepagent_cli.communication.slack_bot import main as slack_main
            slack_main()
        except KeyboardInterrupt:
            logger.info("Slack bot stopped by user.")
    elif command == "ui":
        try:
            ui_app_path = os.path.join(project_root, "ui", "app.py")
            subprocess.run(["streamlit", "run", ui_app_path] + sys.argv[1:], cwd=project_root)
        except KeyboardInterrupt:
            # Matches existing streamlit behavior
            logger.info("Streamlit app stopped by user.")
    elif command == "mcp":
        subcmd = sys.argv[1] if sys.argv[1:] else "base"
        try:
            if subcmd == "lab":
                try:
                    lab_mcp_servers_path = os.path.join(project_root, "net_lab_mcp_server.py")
                    subprocess.run([sys.executable, lab_mcp_servers_path] + sys.argv[2:], cwd=project_root)
                except Exception as e:
                    logger.error(f"Error starting lab MCP server: {e}")
            
            mcp_servers_path = os.path.join(project_root, "mcp_servers.py")
            args = sys.argv[2:] if sys.argv[1:] and sys.argv[1] == "base" else sys.argv[1:]
            subprocess.run([sys.executable, mcp_servers_path] + args, cwd=project_root)
        except KeyboardInterrupt:
            logger.info("MCP servers stopped by user.")
    elif command in ["start-cli", "start-headless", "start-gui", "start-all"]:
        import time
        from subprocess import Popen
        
        processes = []
        try:
            mcp_path = os.path.join(project_root, "mcp_servers.py")
            lab_mcp_path = os.path.join(project_root, "net_lab_mcp_server.py")
            
            logger.info("[Composite] Starting Base MCP Server...")
            processes.append(Popen([sys.executable, mcp_path] + sys.argv[1:], cwd=project_root))
            
            if os.path.exists(lab_mcp_path):
                logger.info("[Composite] Starting Lab MCP Server...")
                processes.append(Popen([sys.executable, lab_mcp_path] + sys.argv[1:], cwd=project_root))
                
            time.sleep(2)  # Give MCPs a moment to bind ports
            
            if command == "start-cli":
                logger.info("[Composite] Starting CLI...")
                from net_deepagent_cli.cli import main as cli_main
                cli_main()
                
            elif command == "start-headless":
                logger.info("[Composite] Starting Discord Bot...")
                processes.append(Popen(["workerxy", "discord"] + sys.argv[1:], cwd=project_root))
                
                try:
                    if project_root not in sys.path:
                        sys.path.insert(0, project_root)
                    import creds
                    if getattr(creds, "SLACK_BOT_AUTH_TOKEN", None) and getattr(creds, "SLACK_BOT_SOCKET_TOKEN", None):
                        logger.info("[Composite] Starting Slack Bot...")
                        processes.append(Popen(["workerxy", "slack"] + sys.argv[1:], cwd=project_root))
                except ImportError:
                    logger.warning("[Composite] Slack bot not started: creds.py not found.")
                
                logger.info("[Composite] Starting Headless Agent...")
                from net_deepagent_cli.headless import run_headless, logger as headless_logger
                asyncio.run(run_headless())
                
            elif command == "start-gui":
                logger.info("[Composite] Starting Streamlit UI...")
                ui_app_path = os.path.join(project_root, "ui", "app.py")
                p_ui = Popen(["streamlit", "run", ui_app_path] + sys.argv[1:], cwd=project_root)
                processes.append(p_ui)
                p_ui.wait()
                
            elif command == "start-all":
                logger.info("[Composite] Starting Discord Bot...")
                processes.append(Popen(["workerxy", "discord"] + sys.argv[1:], cwd=project_root))
                
                try:
                    if project_root not in sys.path:
                        sys.path.insert(0, project_root)
                    import creds
                    if getattr(creds, "SLACK_BOT_AUTH_TOKEN", None) and getattr(creds, "SLACK_BOT_SOCKET_TOKEN", None):
                        logger.info("[Composite] Starting Slack Bot...")
                        processes.append(Popen(["workerxy", "slack"] + sys.argv[1:], cwd=project_root))
                except ImportError:
                    logger.warning("[Composite] Slack bot not started: creds.py not found.")
                
                logger.info("[Composite] Starting Headless Agent...")
                processes.append(Popen(["workerxy", "headless"] + sys.argv[1:], cwd=project_root))
                logger.info("[Composite] Starting Streamlit UI...")
                ui_app_path = os.path.join(project_root, "ui", "app.py")
                p_ui = Popen(["streamlit", "run", ui_app_path] + sys.argv[1:], cwd=project_root)
                processes.append(p_ui)
                p_ui.wait()
                
        except KeyboardInterrupt:
            logger.info(f"{command} stopped by user.")
        finally:
            logger.info("Terminating background processes...")
            for p in processes:
                p.terminate()
            for p in processes:
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
    elif command == "skill":
        try:
            skill_cli_path = os.path.join(project_root, "utils", "skill_manager", "cli.py")
            subprocess.run([sys.executable, skill_cli_path] + sys.argv[1:], cwd=project_root)
        except KeyboardInterrupt:
            logger.info("Skill manager stopped by user.")
    elif command == "diagnose":
        try:
            from net_deepagent_cli.diagnose import main as diagnose_main
            diagnose_main()
        except KeyboardInterrupt:
            logger.info("Diagnostic tool stopped by user.")
    elif command == "vault":
        try:
            from net_deepagent_cli.vault import vault_main
            vault_main()
        except KeyboardInterrupt:
            logger.info("Vault manager stopped by user.")


if __name__ == "__main__":
    main()
