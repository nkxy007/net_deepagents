import asyncio
import argparse
import sys
from pathlib import Path
from net_deepagent_cli.config import AgentConfig
from net_deepagent_cli.ui import TerminalUI
from net_deepagent_cli.agent import create_cli_agent
from net_deepagent_cli.loop import interactive_loop
from warnings import filterwarnings
filterwarnings("ignore")

async def run_cli():
    """Main CLI entry point logic"""
    parser = argparse.ArgumentParser(description="Net DeepAgent CLI - A powerful terminal interface for network automation.")
    
    parser.add_argument("--agent", default="net-agent", help="Agent name (used for history and config)")
    parser.add_argument("--model", default="gpt-5-mini", help="Main model to use")
    parser.add_argument("--subagent-model", default="gpt-5-mini", help="Subagent model to use")
    parser.add_argument("--design-model", default="gpt-5.1", help="Design model to use")
    parser.add_argument("--mcp-server", default="http://localhost:8000/mcp", help="MCP server URL")
    parser.add_argument("--auto-approve", action="store_true", help="Auto-approve all tool calls")
    parser.add_argument("--automatic-context-detection", action="store_true", help="Proactively detect topic drift")
    parser.add_argument("--association-window", type=int, default=5, help="Lookback window for past session association in days")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL"], help="Set the log level for file logging")
    parser.add_argument("--monitor", default=None, choices=["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL"], help="Set the log level for screen/console output only (default: matches --log-level)")
    parser.add_argument("--hashicorp", action="store_true", help="Use HashiCorp Vault for credentials")
    
    args = parser.parse_args()

    # Set log levels immediately
    from net_deepagent_cli.communication.logger import set_log_level, set_screen_log_level
    set_log_level(args.log_level)
    # Screen level: --monitor if provided, else fall back to --log-level
    screen_level = args.monitor if args.monitor is not None else args.log_level
    set_screen_log_level(screen_level)
    
    # Load or create config
    config_manager = AgentConfig(args.agent)
    config_manager.save_config(args)
    config = config_manager.load_config()
    
    # Override config with CLI args if provided
    # (Simplified for now, just use args)
    
    ui = TerminalUI(args.agent)
    ui.print_message("Starting CoworkerX Networking CLI...", role="system")
    
    try:
        # Step 1: Create the agent
        ui.print_message(f"Initializing agent with model {args.model} and MCP {args.mcp_server}...", role="system")
        
        # We'll try to initialize the agent. If it hangs, the user sees the message above.
        agent = await create_cli_agent(
            agent_name=args.agent,
            mcp_server_url=args.mcp_server,
            main_model_name=args.model,
            subagent_model_name=args.subagent_model,
            design_model_name=args.design_model,
            auto_approve=args.auto_approve,
            ui=ui,
            extra_tools=[],
            use_hashicorp=args.hashicorp
        )
        
        ui.print_message("Agent initialized successfully.", role="system")
        
        
        # Step 2: Run interactive loop
        await interactive_loop(agent, args, ui)
        
    except Exception as e:
        ui.print_message(f"Failed to start agent: {str(e)}", role="error")
        import traceback
        from rich.markup import escape
        ui.console.print(f"[dim]{escape(traceback.format_exc())}[/dim]")
        sys.exit(1)
    finally:
        # Cleanup resources
        if 'agent' in locals() and hasattr(agent, 'cleanup'):
            ui.print_message("Cleaning up resources...", role="system")
            await agent.cleanup()

def main():
    """Synchronous entry point"""
    try:
        asyncio.run(run_cli())
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
