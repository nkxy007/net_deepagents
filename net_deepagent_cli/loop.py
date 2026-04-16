from net_deepagent_cli.communication.logger import setup_logger
from net_deepagent_cli.communication.logger import setup_logger, set_process_log_file
from typing import List, Dict, Any
from net_deepagent_cli.ui import TerminalUI
from net_deepagent_cli.commands import handle_command
from net_deepagent_cli.automata import AutomataManager, handle_automata_ui
from net_deepagent_cli.drift import TopicDriftDetector
from net_deepagent_cli.association import InteractionAssociationEngine
import json
import datetime
from pathlib import Path
from langchain_core.messages import message_to_dict, HumanMessage, AIMessage, ToolMessage
from net_deepagent_cli.communication.session import save_session, load_session
from utils.session_archiver import fire_and_forget_archive

# Set unified log file for the entire process
set_process_log_file("main.log")

# Configure logging for net_deepagent_cli
logger = setup_logger("net_deepagent_cli")

async def interactive_loop(agent, args, ui: TerminalUI):
    """Main interactive loop"""
    logger.info(f"Starting interactive session for agent: {ui.agent_name}")
    ui.print_banner()
    
    # Start background tasks
    # Initialize Automata Manager
    # We assume 'agent_name' is available in ui or args?
    # ui.agent_name is available.
    automata_manager = AutomataManager(ui.agent_name, agent)
    automata_manager.start()
    
    # Wire the live manager into automata_tools so the main agent's @tool functions
    # can create / manage jobs programmatically (in addition to the /automata CLI).
    from tools_helpers.automata_tools import set_automata_manager
    set_automata_manager(automata_manager)
    
    # Session state - store as LangChain messages
    messages: List[Any] = []
    
    # Initialize Drift Detector if flag is set
    drift_detector = TopicDriftDetector() if getattr(args, 'automatic_context_detection', False) else None
    
    # Initialize Association Engine (Lookback)
    lookback_window = getattr(args, 'association_window', 5)
    association_engine = InteractionAssociationEngine(ui.agent_name, lookback_days=lookback_window)
    # Build cache in background-ish way but we want it ready
    await association_engine.build_initial_cache()
    
    if hasattr(agent, 'base_agent'):
        agent.drift_detector = drift_detector
        agent.association_engine = association_engine
    else:
        # In case it's not wrapped for some reason
        agent.drift_detector = drift_detector
        agent.association_engine = association_engine
    
    try:
        while True:
            # Get user input
            user_input = await ui.get_user_input()
            
            if user_input is None:
                # Ctrl+C or EOF
                ui.console.print("\n[yellow]Goodbye![/yellow]")
                break
            
            if not user_input:
                continue
                
            # Handle special commands
            if user_input.startswith("/"):
                if user_input.strip() == "/automata":
                    await handle_automata_ui(ui, automata_manager)
                    continue
                elif user_input.startswith("/automata "):
                     from net_deepagent_cli.automata import process_automata_command
                     arg_str = user_input[len("/automata "):].strip()
                     if arg_str:
                         await process_automata_command(automata_manager, arg_str, ui)
                         continue

                try:
                    await handle_command(user_input, ui, messages, agent=agent)
                except EOFError:
                    break
                continue
            # 1. Check for Past Reference (Association Researcher)
            context_to_inject = None
            if getattr(args, 'automatic_context_detection', False) and association_engine:
                ref_info = await association_engine.detect_reference(user_input, agent)
                if ref_info.get("is_past_reference"):
                    match = ref_info.get("match")
                    context_to_inject = ref_info.get("context_summary")
                    
                    if match and match.get("is_strong_match"):
                        if await ui.prompt_resume_session(match["name"], match["time_hint"]):
                            await handle_command(f"/session resume {match['name']}", ui, messages, agent=agent)
                            continue # Skip current turn as we resumed
                    else:
                        # Clarification logic - Wait for input
                        best_info = f" (Closest match: [bold]{match['name']}[/bold] score {match['score']:.2f})" if match else ""
                        ui.print_message(
                            f"I detected a reference to a past discussion, but I couldn't find a strong matching session.{best_info} "
                            "Could you specify more details or press Enter to skip search?",
                            role="assistant"
                        )
                        
                        # BLOCKING WAIT for details
                        details = await ui.get_user_input()
                        if details:
                            # Try one more time with added details
                            new_query = f"{user_input} - {details}"
                            ref_info = await association_engine.detect_reference(new_query, agent)
                            match = ref_info.get("match")
                            context_to_inject = ref_info.get("context_summary")
                            
                            if match and match.get("is_strong_match"):
                                if await ui.prompt_resume_session(match["name"], match["time_hint"]):
                                    await handle_command(f"/session resume {match['name']}", ui, messages, agent=agent)
                                    continue
                            elif match:
                                ui.print_message(f"Still no perfect match (Best: {match['name']} @ {match['score']:.2f}). Proceeding with current session.", role="system")
                        # If no details or still no match, we just fall through to the normal agent call
            
            # 2. Check for Topic Drift
            if drift_detector and messages:
                print("⚙ Understanding the task connection to current context .....")
                drift_info = await drift_detector.check_drift(messages, user_input)
                # Log detailed info as requested
                logger.info(f"Topic Drift Check: Similarity={drift_info['similarity']:.2f}, "
                            f"Current Topic='{drift_info['current_topic']}', "
                            f"New Input='{drift_info['new_topic']}'")
                
                if drift_info["drift"]:
                    if await ui.prompt_new_session_drift():
                        await handle_command("/session new", ui, messages, agent=agent)
            
            # Add context if researcher found some technical facts
            if context_to_inject:
                from langchain_core.messages import SystemMessage
                injection = f"[SYSTEM RECALL]: Relevant facts from past sessions:\n{context_to_inject}\nUse this to answer the user's question about past topics."
                messages.append(SystemMessage(content=injection))
                logger.info("Context summary injected into main conversation.")

            messages.append(HumanMessage(content=user_input))
            
            # Stream agent response
            await stream_agent_response(agent, messages, ui, args.auto_approve)
            
            # Check for skill updates (if middleware is attached)
            if hasattr(agent, 'skill_learning_middleware'):
                from net_deepagent_cli.automata_skills_ui import handle_skill_updates
                await handle_skill_updates(agent.skill_learning_middleware, ui)
                
    except Exception as e:
        from rich.markup import escape
        ui.console.print(f"[red]Error in interactive loop: {escape(str(e))}[/red]")
    finally:
        if 'automata_manager' in locals():
            automata_manager.stop()

async def stream_agent_response(agent, messages, ui: TerminalUI, auto_approve: bool, **kwargs):
    """Stream agent response with real-time updates"""
    
    # In LangGraph/DeepAgent, the state is passed. 
    try:
        chat_channel_id = kwargs.get("chat_channel_id") or kwargs.get("discord_channel_id")
        author = kwargs.get("author")
        session_id = kwargs.get("session_id")
        
        # We use a Live display for the assistant's growing message
        with ui.show_progress("Agent is thinking...") as progress:
            task = progress.add_task("Processing request...", total=100)
            
            # Since we're using deepagents which wraps a langgraph, 
            # we'll use astream with the messages
            
            last_message_count = len(messages)
            
            # Build the full input state including any extra metadata (like chat_channel_id)
            input_state = {"messages": messages}
            input_state.update(kwargs)
            
            async for chunk in agent.astream(
                input_state,
                stream_mode="values" # This gives the full state at each step
            ):
                if "messages" in chunk:
                    all_messages = chunk["messages"]
                    # If there are new messages, we process them
                    if len(all_messages) > last_message_count:
                        new_msgs = all_messages[last_message_count:]
                        for msg in new_msgs:
                            if isinstance(msg, AIMessage):
                                # Update token counts from standardized usage_metadata or legacy response_metadata
                                total = 0
                                if hasattr(msg, "usage_metadata") and msg.usage_metadata:
                                    total = msg.usage_metadata.get("total_tokens", 0)
                                elif hasattr(msg, "response_metadata") and msg.response_metadata:
                                    usage = msg.response_metadata.get("token_usage", {})
                                    total = usage.get("total_tokens", 0)
                                
                                if total:
                                    ui.total_tokens += total

                                if msg.content:
                                    ui.print_message(msg.content, role="assistant")
                                
                                if hasattr(msg, "tool_calls") and msg.tool_calls:
                                    for tool_call in msg.tool_calls:
                                        # Update progress based on tool
                                        tool_name = tool_call["name"]
                                        if tool_name.startswith("communicate_with_"):
                                            # Derive agent name (e.g., communicate_with_dns_deepagent -> dns-deepagent)
                                            agent_name = tool_name.replace("communicate_with_", "").replace("_", "-")
                                            progress.update(task, description=f"[bold cyan]Waiting for {agent_name}...[/bold cyan]")
                                        else:
                                            progress.update(task, description=f"[bold yellow]Executing {tool_name}...[/bold yellow]")
                                            
                                        ui.print_tool_call(tool_name, tool_call["args"])
                                        
                                        # Human-in-the-loop approval
                                        if not auto_approve and requires_approval(tool_call["name"]):
                                            approval = await ui.request_approval(
                                                tool_call["name"], 
                                                str(tool_call["args"])
                                            )
                                            if approval == "reject":
                                                # How to handle rejection in LangGraph?
                                                # One way is to inject a "user rejected" message or error.
                                                # For now, we'll just log it. In a real implementation, 
                                                # we'd need to stop the execution or send a ToolMessage with failure.
                                                ui.print_message(f"Tool {tool_call['name']} was rejected by user.", role="system")
                                            elif approval == "edit":
                                                ui.print_message("Editing tool calls not yet implemented in this MVP.", role="system")
                            
                            elif isinstance(msg, ToolMessage):
                                # Tool has returned, update progress
                                progress.update(task, description="[bold green]Processing tool output...[/bold green]")
                                pass
                            
                            elif isinstance(msg, AIMessage) and msg.content:
                                # Final response is being generated
                                progress.update(task, description="[bold blue]Finalizing response...[/bold blue]")
                                ui.print_message(msg.content, role="assistant")
                                
                        last_message_count = len(all_messages)
                        # Update the messages history for the loop
                        messages.clear()
                        messages.extend(all_messages)
                        
                        # Pass new messages to skill learning middleware
                        if hasattr(agent, 'skill_learning_middleware'):
                            for msg in new_msgs:
                                # Convert LangChain message to simple dict for processing
                                content = ui.normalize_content(msg.content) if msg.content else ""
                                if isinstance(msg, AIMessage):
                                    role = "assistant"
                                elif isinstance(msg, ToolMessage):
                                    role = "tool"
                                    # For tool messages, we might want to include tool name if available
                                    # But process_message handles raw text anyway
                                else:
                                    role = "user"
                                
                                agent.skill_learning_middleware.process_message({'role': role, 'content': content})

        # --- Phase 1: Discord auto-reply (headless mode only) ---
        # Fires once after the full agent turn completes (all tool iterations done).
        # No-op in interactive CLI mode — chat_channel_id is never present there.
        chat_channel_id = kwargs.get("chat_channel_id") or kwargs.get("discord_channel_id")
        author = kwargs.get("author")
        if chat_channel_id:
            final_ai = next(
                (
                    m for m in reversed(messages)
                    if isinstance(m, AIMessage) and not getattr(m, "tool_calls", None)
                ),
                None,
            )
            if final_ai and final_ai.content:
                from net_deepagent_cli.communication.tools import send_final_reply_to_chat
                logger.info("Phase 1: Sending final AI response to Chat channel...")
                await send_final_reply_to_chat(
                    final_ai,
                    channel_id=chat_channel_id,
                    channel_name=kwargs.get("channel_name"),
                )

        # --- Memory Feature: Persistent Session Saving ---
        if session_id:
            try:
                # We load the existing session state to append the NEW messages generated in this turn
                session = load_session(session_id)
                session["messages"] = messages
                save_session(session_id, session)
                logger.info(f"Memory synced for session {session_id}")
                # Archive to ChromaDB asynchronously (fire-and-forget, non-blocking)
                fire_and_forget_archive(messages, session_id)
            except Exception as e:
                logger.error(f"Failed to sync memory for session {session_id}: {e}")
        
        # --- Legacy/Fallback Session Saving (per author per timestamp) ---
        elif author and (chat_channel_id or kwargs.get("channel_name")):
            try:
                # Target directory: ~/.net_deepagent/online_chat_sessions/<user-id>/
                base_dir = Path.home() / ".net_deepagent" / "online_chat_sessions" / str(author)
                base_dir.mkdir(parents=True, exist_ok=True)
                
                # Filename: timestamp.json
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                filepath = base_dir / f"{timestamp}.json"
                
                # Serialize and save
                serialized_messages = [message_to_dict(m) for m in messages]
                with open(filepath, 'w') as f:
                    json.dump(serialized_messages, f, indent=2)
                    
                logger.info(f"Online session saved for user {author} to {filepath}")
                # Archive to ChromaDB asynchronously (fire-and-forget, non-blocking)
                fire_and_forget_archive(messages, f"{author}_{timestamp}")
            except Exception as e:
                logger.error(f"Failed to save online session for {author}: {e}")

    except Exception as e:
        ui.print_message(f"An error occurred: {str(e)}", role="error")
        import traceback
        from rich.markup import escape
        ui.console.print(f"[dim]{escape(traceback.format_exc())}[/dim]")

def requires_approval(tool_name: str) -> bool:
    """Check if tool requires human approval"""
    # Sensitive tools that should be approved by default
    sensitive_tools = [
        "Unified_SSH_configuration_tool", 
        "ssh_tool", 
        "write_file", 
        "delete_file", 
        "aws_tool", 
        "azure_tool", 
        "gcp_tool"
    ]
    return tool_name in sensitive_tools
