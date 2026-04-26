from typing_extensions import TypedDict
import operator
from typing import Optional, Callable, Any, Dict, List
from pathlib import Path
import logging
from net_deepagent_cli.communication.logger import setup_logger
from net_deepagent_cli.communication.notifications import dispatch_clarification_webhook
from net_deepagent_cli.communication.logger import setup_logger, set_process_log_file
from langchain_core.messages import AnyMessage, SystemMessage, HumanMessage, AIMessage, ChatMessage
from langchain_core.tools import tool
from utils.credentials_helper import get_credential, get_helper
import os
from pydantic import BaseModel, Field

# Initialize credentials with auto-discovery (local file-based)
# Note: CLI entrypoints often re-initialize this with --hashicorp if requested.
get_helper()

# Set unified log file for the entire process
set_process_log_file("main.log")

# Configure logging using centralized utility
logger = setup_logger("net_deepagent")
#from langgraph.prebuilt import ToolNode, create_react_agent, InjectedState
from langgraph.graph import START, END, StateGraph, MessagesState
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore
from langchain_core.tools import BaseTool
from utils.llm_provider import LLMFactory
import traceback
from time import sleep
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_mcp_adapters.client import MultiServerMCPClient
import asyncio
from langchain.agents import create_agent
from prompts import network_activity_planner_agent_template, LAN_subagent_template

from langchain.agents.middleware import PIIMiddleware
from custom_middleware.netpii_middlewares import PIIPseudonymizationMiddleware
from langchain.agents.middleware import ModelFallbackMiddleware






# models
thinking_model_mini = LLMFactory.get_llm(model_name="gpt-5-mini", api_key=get_credential("OPENAI_KEY"))
thinking_model = LLMFactory.get_llm(model_name="gpt-5.1", api_key=get_credential("OPENAI_KEY"), use_responses_api=True, reasoning={"effort": "low"})
thinking_model_medium = LLMFactory.get_llm(model_name="gpt-5.1", api_key=get_credential("OPENAI_KEY"), use_responses_api=True, reasoning={"effort": "medium"})
thinking_model_high = LLMFactory.get_llm(model_name="gpt-5.1", api_key=get_credential("OPENAI_KEY"), use_responses_api=True, reasoning={"effort": "high"})
thinking_model_medium_mini = LLMFactory.get_llm(model_name="gpt-5-mini", api_key=get_credential("OPENAI_KEY"), use_responses_api=True, reasoning={"effort": "medium"})
thinking_model_high_mini = LLMFactory.get_llm(model_name="gpt-5-mini", api_key=get_credential("OPENAI_KEY"), use_responses_api=True, reasoning={"effort": "high"})
thinking_model_response = LLMFactory.get_llm(model_name="gpt-5.1", api_key=get_credential("OPENAI_KEY"), use_responses_api=True)
action_minimal_thinking_model = LLMFactory.get_llm(model_name="gpt-5-mini", api_key=get_credential("OPENAI_KEY"), reasoning={"effort": "minimal"}, use_responses_api=True)
multi_purpose_model = LLMFactory.get_llm(model_name="gpt-5.1", api_key=get_credential("OPENAI_KEY"), use_responses_api=True)
coding_model = LLMFactory.get_llm(model_name="gpt-5.1-codex", api_key=get_credential("OPENAI_KEY"))
bias_removal_model = LLMFactory.get_llm(model_name="claude-sonnet-4-5-20250929", api_key=get_credential("ANTHROPIC_KEY"))
googla_light_model = LLMFactory.get_llm(model_name="gemini-3-flash-preview", api_key=get_credential("GEMINI_KEY"))
googla_heavy_model = LLMFactory.get_llm(model_name="gemini-3-pro", api_key=get_credential("GEMINI_KEY"))
gui_navigator_model = LLMFactory.get_llm(model_name="gpt-4o", api_key=get_credential("OPENAI_KEY"))

# New models (GPT-5.4, GPT-5.2, Gemini 3.1)
gpt_5_4_model = LLMFactory.get_llm(model_name="gpt-5.4", api_key=get_credential("OPENAI_KEY"), use_responses_api=True)
gpt_5_4_mini_model = LLMFactory.get_llm(model_name="gpt-5.4-mini", api_key=get_credential("OPENAI_KEY"), use_responses_api=True)
gpt_5_4_nano_model = LLMFactory.get_llm(model_name="gpt-5.4-nano", api_key=get_credential("OPENAI_KEY"), use_responses_api=True)
gpt_5_2_model = LLMFactory.get_llm(model_name="gpt-5.2", api_key=get_credential("OPENAI_KEY"), use_responses_api=True)
gemini_3_1_flash_lite_model = LLMFactory.get_llm(model_name="gemini-3.1-flash-lite-preview", api_key=get_credential("GEMINI_KEY"))
gemini_3_1_pro_model = LLMFactory.get_llm(model_name="gemini-3.1-pro-preview", api_key=get_credential("GEMINI_KEY"))
groq_model = LLMFactory.get_llm(model_name="openai/gpt-oss-120b", api_key=get_credential("GROQ_KEY"))

# DeepSeek Models
deepseek_4_high_model = LLMFactory.get_llm(model_name="deepseek-v4-pro", api_key=get_credential("DEEPSEEK_API_KEY"), reasoning_effort="high")
deepseek_4_flash_model = LLMFactory.get_llm(model_name="deepseek-v4-flash", api_key=get_credential("DEEPSEEK_API_KEY"))
deepseek_4_medium_model = LLMFactory.get_llm(model_name="deepseek-v4-pro", api_key=get_credential("DEEPSEEK_API_KEY"), reasoning_effort="medium")

# Global callback for user clarification (can be overridden by UI)
_user_clarification_callback: Optional[Callable[[str, str], str]] = None

# fallback Middleware 
fallback_middleware = ModelFallbackMiddleware(gpt_5_4_nano_model,bias_removal_model,groq_model)


def set_user_clarification_callback(callback: Optional[Callable[[str, str], str]]):
    """
    Set the callback function for user clarification requests.

    Args:
        callback: Function that takes (question, intention) and returns response string
    """
    global _user_clarification_callback
    _user_clarification_callback = callback


# local tools
@tool
def search_internet(query: str, confidence: Optional[float] = None) -> str:
    """Search the internet for a given query and return summarized results.
    This tool is used if the model has a confidence level below 90% on the immediate response to the query.
    Args:
        query (str): A comprehensive search query like a sentence of what we are after and main keywords.
        confidence (Optional[float]): The confidence level the model had in its own direct response that triggers the use of this tool to increase confidence.
    Returns:
        str: search results.
    """
    logger.debug(f"Executing local tool: search_internet with args: query={query}, confidence={confidence}")
    # Simulate an internet search
    print("="*20)
    print(f"Searching the internet for: {query}")
    logger.info(f"Searching the internet for: {query}")
    if confidence:
        logger.info(f"Confidence level encountered that triggered the use of this tool: {confidence}")
    response = thinking_model.invoke(
        [HumanMessage(content=f"Search the internet for: {query}")],
        tools=[{"type":"web_search"}])
    result = f"Search results for '{query}' are: {response.content}"
    logger.debug(f"Local tool search_internet output: {result}")
    return result

@tool
def user_clarification_and_action_tool(question: str, intention:str="") -> str:
    """Ask the user for clarification on a given question or for user to take action.
    This tool is used if the model needs more information from the user to provide an accurate response.
    It can be used when the model encounters a complexe situation and is unable to get out of it without user intervention. i.e some knowlede gap...
    It can also be used by the AI model to ask user to take action and report when the action is
    completed such actions can be like ping from user laptop, get info from user`s machine. or any other action to be taken by a remote user.
    Args:
        question (str): The question or action to ask the user. it can be a multi part question.
        intention (str): The intention behind the question or action (optional).
    Returns:
        str: user's clarification.
    """
    logger.debug(f"Executing local tool: user_clarification_and_action_tool with args: question={question}, intention={intention}")
    # Notify comms channels (Discord / Slack) — fire-and-forget, 2s timeout
    dispatch_clarification_webhook(question, intention, logger=logger)

    # Use callback if set (for UI integration), otherwise use input() for CLI
    if _user_clarification_callback:
        response = _user_clarification_callback(question, intention)
    else:
        # Enable readline so backspace / arrow keys work correctly in the terminal
        import readline  # noqa: F401 — side-effect import activates GNU readline for input()
        print(f"Agent needs more information to answer: {question} - intention: {intention}")
        response = input(f"Agent needs more information to continue:\n {question}\nPlease provide clarification: ")

    result = f"User clarification for '{question}': {response=}"
    logger.debug(f"Local tool user_clarification_and_action_tool output: {result}")
    return result

@tool
def skill_generator_from_documentation(documentation: str) -> str:
    """Generate a list of skills from the provided documentation.
    This tool is used to extract relevant skills that can be used by the agent.
    Args:
        documentation (str): The documentation text.
    Returns:
        str: A list of skills extracted from the documentation.
    """
    #TODO: extract the know how from the documentation and generate skill and save skill under skills directory
    # in appropriate directory and SKILL.md file
    # this tool learns the knowledge from the documentation and create a trajectory or a memory of what is in the document
    # how to use that knowledge to solve problems. if it is an application documentation it can learne steps of how to use the application
    # for instance configure ip address: menu > settings > network settings > configure IP address, etc.
    # this is a placeholder implementation
    print(f"Generating skills from documentation.")

@tool
async def navigate_the_gui(url:str, question: str, browse_instruction:str="") -> str:
    """Function to scroll the gui of different devices or monitoring systems that use the graphical User interface.
    As long as it has the URL and what to look for and if needed browser-instructions it can allow to navigate to the
    right resource and get the information or even modify the information. This use browser_use as its agent to do the work"""
    
    logger.debug(f"Executing local tool: navigate_the_gui with args: url={url}, question={question}, browse_instruction={browse_instruction}")
    print("="*20)
    print(f"I am scrolling the gui of {url} to find information on {question}")
    from browser_use import Agent
    from langchain_openai import ChatOpenAI
    from browser_use.browser.browser import Browser, BrowserConfig
    
    full_question = f"{question} at {url} using thes instruction {browse_instruction}"
    browser = Browser(
        config=BrowserConfig(
            headless=True,
        )
    )
    
    try:
        agent = Agent(
            task=full_question,
            llm=gui_navigator_model,
            browser=browser,
        )
        
        # Directly await the agent run since we are in an async function
        real_answer_obj = await agent.run()
        real_answer = str(real_answer_obj)
        print(real_answer)
        
    except Exception as e:
        # Fallback to the mocked response if there's an error
        mocked_response = "you need to add a new route to the device and run command ip route x.x.x.x y.y.y.y z.z.z.z"
        print(f"Error obtaining expert advice: {e}")
        real_answer = f"Expert advise on {question} is: {mocked_response}"
    finally:
        # Close the browser to prevent resource leaks
        await browser.close()
        
    logger.debug(f"Local tool navigate_the_gui output: {real_answer}")
    return real_answer


def get_network_skills(skills_dir: Optional[str] = None) -> List[str]:
    """Dynamically discover network-related skills by name matching.
    
    Args:
        skills_dir: Path to the skills directory (defaults to ./skills relative to this file)
        
    Returns:
        List of absolute paths to network-related skill directories
    """
    if skills_dir is None:
        # Use relative path from this file's location
        skills_dir = str(Path(__file__).parent / "skills")
    
    skills_path = Path(skills_dir)
    logger.info(f"Skills directory: {skills_dir}")
    network_skills = []
    
    if not skills_path.exists() or not skills_path.is_dir():
        logger.warning(f"Skills directory not found: {skills_dir}")
        return network_skills
    
    for skill_dir in skills_path.iterdir():
        if skill_dir.is_dir():
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                # Include skills with 'network' in name or specific network tools
                if ('network' in skill_dir.name.lower() or 
                    skill_dir.name in ['ansible-for-networks', 'drawio-network-diagram', 'prisma-sdwan', 'mist-wifi-api']):
                    network_skills.append(str(skill_dir.absolute()) + "/")
                    logger.info(f"Loaded network skill: {skill_dir.name}")
    
    logger.info(f"Found {len(network_skills)} network-related skills")
    return network_skills


def filter_tools_by_category(tools: List[BaseTool], category: str) -> List[BaseTool]:
    """
    Filters a list of tools based on their name matching specific category keywords or prefixes.
    
    Args:
        tools: List of tools to filter
        category: The category to filter for ('cloud', 'lan', 'design', 'datacenter', 'isp')
        
    Returns:
        Filtered list of tools
    """
    filtered = []
    category = category.lower()
    
    for tool in tools:
        name = tool.name.lower()
        
        if category == 'cloud':
            if any(kw in name for kw in ['aws', 'azure', 'gcp', 'cloud']):
                filtered.append(tool)
        elif category == 'lan':
            if name.startswith('net_'):
                filtered.append(tool)
        elif category == 'design':
            if any(kw in name for kw in ['diagram', 'design']):
                filtered.append(tool)
        elif category == 'datacenter':
            if name.startswith('datacentre_'):
                filtered.append(tool)
            elif any(kw in name for kw in ['ssh', 'shell', '_code']):
                filtered.append(tool)
        elif category == 'isp':
            if name.startswith('isp_'):
                filtered.append(tool)
        elif category == 'lab':
            if name.startswith('eveng_'):
                filtered.append(tool)
        elif category == 'servicenow':
            if name.startswith('servicenow_'):
                filtered.append(tool)
        elif category == 'jira':
            if name.startswith('jira_'):
                filtered.append(tool)
                
    return filtered

# Dictionary of available models for selection
AVAILABLE_MODELS = {
    "gpt-5-mini": thinking_model_mini,
    "gpt-5.1": thinking_model,
    "gpt-5.4": gpt_5_4_model,
    "gpt-5.4-mini": gpt_5_4_mini_model,
    "gpt-5.4-nano": gpt_5_4_nano_model,
    "gpt-5.2": gpt_5_2_model,
    "gemini-3.1-flash-lite": gemini_3_1_flash_lite_model,
    "gemini-3.1-pro": gemini_3_1_pro_model,
    "gpt-5-response": thinking_model_response,
    "gpt-5-mini-minimal": action_minimal_thinking_model,
    "gpt-5.1-no-thinking": multi_purpose_model,
    "gpt-5.1-codex": coding_model,
    "claude-4.5-sonnet": bias_removal_model,
    "gemini-3-flash": googla_light_model,
    "gemini-3-pro": googla_heavy_model,
    "gpt-5.1-medium": thinking_model_medium,
    "gpt-5.1-high": thinking_model_high,
    "gpt-5-medium-mini": thinking_model_medium_mini,
    "gpt-5-high-mini": thinking_model_high_mini,
    "deepseek-4-high": deepseek_4_high_model,
    "deepseek-4-flash": deepseek_4_flash_model,
    "deepseek-4-medium": deepseek_4_medium_model,
}

from langchain.agents.middleware import before_model, after_model, AgentState
from langgraph.runtime import Runtime
from custom_middleware.tool_announcer_middleware import announce_tool_call

@before_model
def log_before_calling_model(state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
    # Get last message to log context (Human or Tool result)
    last_message = state['messages'][-1] if state['messages'] else None
    msg_type = type(last_message).__name__ if last_message else "None"
    msg_content = last_message.content if last_message else "No content"
    
    logger.debug(f"--- Before Model Call ---")
    logger.debug(f"Last message type: {msg_type}")
    logger.debug(f"Last message content: {msg_content}")
    print("⚙ Calling intelligence model ......")
    return None

@after_model
def log_after_model(state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
    # Log the AI's response
    last_message = state['messages'][-1] if state['messages'] else None
    if isinstance(last_message, AIMessage):
        logger.debug(f"--- After Model Call ---")
        logger.debug(f"AI Message: {last_message.content}")
        if last_message.tool_calls:
            logger.debug(f"AI requested tool calls: {last_message.tool_calls}")
    return None

async def create_network_agent(
    mcp_server_url: str = "http://localhost:8080/mcp",
    main_model_name: str = "gpt-5.1",
    subagent_model_name: str = "gpt-5-mini-minimal",
    design_model_name: str = "gpt-5.1",
    custom_system_prompt: Optional[str] = None,
    extra_tools: List[Any] = [],
    tool_wrapper: Optional[Callable[[List[Any]], List[Any]]] = None,
    custom_middlewares: List[Any] = None,
    use_hashicorp: bool = False,
):
    """
    Create and configure the network deep agent with subagents.

    Args:
        mcp_server_url: URL for the MCP server
        main_model_name: Model to use for main agent (default: gpt-5-mini)
        subagent_model_name: Model to use for subagents (default: gpt-5-mini-minimal)
        design_model_name: Model to use for design subagent (default: gpt-5.1)
        custom_system_prompt: Optional custom system prompt for main agent
        extra_tools: Optional list of additional tools to make available to the agent
        use_hashicorp: Whether to fetch credentials from HashiCorp Vault

    Returns:
        Configured deep agent instance
    """
    logger.info("=== create_network_agent() called ===")
    logger.debug(f"MCP URL: {mcp_server_url}")
    logger.debug(f"Main model: {main_model_name}")
    logger.debug(f"HashiCorp Vault enabled: {use_hashicorp}")

    # Ensure credentials are fresh and from the correct source
    get_helper(use_hashicorp=use_hashicorp)

    ## MCP client and tools
    tools = []
    
    # 1. Main Network MCP Server (Critical)
    logger.info("Connecting to core network MCP server...")
    try:
        client = MultiServerMCPClient(
            {
                "network": {
                    "url": mcp_server_url,
                    "transport": "streamable_http",
                }
            }
        )
        network_tools = await client.get_tools()
        tools.extend(network_tools)
        logger.info(f"Got {len(network_tools)} tools from network MCP")
    except BaseException as e:
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        logger.error(f"CRITICAL: Failed to connect to core network MCP server: {e}", exc_info=True)
        raise

    # 2. Lab MCP Server (Optional)
    logger.info("Attempting to connect to lab MCP server...")
    try:
        lab_client = MultiServerMCPClient(
            {
                "lab": {
                    "url": "http://localhost:8001/mcp",
                    "transport": "streamable_http",
                }
            }
        )
        lab_tools_list = await lab_client.get_tools()
        tools.extend(lab_tools_list)
        logger.info(f"Successfully added {len(lab_tools_list)} tools from lab MCP")
    except BaseException as e:
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        logger.warning(f"Lab MCP server not available at http://localhost:8081/mcp. Lab functions won't be available. Error: {e}")

    # Dynamic tool filtering
    design_tools = filter_tools_by_category(tools, 'design')
    cloud_tools = filter_tools_by_category(tools, 'cloud')
    lan_tools = filter_tools_by_category(tools, 'lan')
    lab_tools = filter_tools_by_category(tools, 'lab')
    servicenow_tools = filter_tools_by_category(tools, 'servicenow')
    jira_tools = filter_tools_by_category(tools, 'jira')
    
    # Filter out specialized tools from the main agent
    main_agent_tools = [
        t for t in tools 
        if not any(t.name.lower().startswith(p) for p in ['net_', 'isp_', 'datacentre_', 'cloud', 'eveng_', 'servicenow_', 'jira_'])
    ]
    
    logger.info(f"Filtered tools: {len(design_tools)} design, {len(cloud_tools)} cloud, {len(lan_tools)} LAN")
    logger.info(f"Main agent tools: {len(main_agent_tools)} (specialized tools excluded)")

    # Add extra tools if provided (e.g. A2A communicate_with_* tools).
    # These must be added to BOTH main_agent_tools (so the main LLM can see them)
    # AND the full tools list (so any tool wrapper is applied to them too).
    if extra_tools:
        tools.extend(extra_tools)
        main_agent_tools.extend(extra_tools)
        logger.info(f"Added {len(extra_tools)} extra tool(s) to main agent: {[t.name for t in extra_tools]}")

    # Build automata tool list — these are injected into the automata_agent subagent below,
    # NOT into main_agent_tools. The main agent delegates scheduling tasks to the subagent.
    from tools_helpers.automata_tools import (
        automata_create_job,
        automata_list_jobs,
        automata_stop_job,
        automata_remove_job,
        automata_get_job_logs,
        automata_read_job_log,
        automata_update_job,
    )
    automata_tool_list = [
        automata_create_job,
        automata_list_jobs,
        automata_stop_job,
        automata_remove_job,
        automata_get_job_logs,
        automata_read_job_log,
        automata_update_job,
    ]
    logger.info("Built 7 automata tools for automata_agent subagent")

    # Allow caller to wrap/modify tools (e.g. for security).
    # Applied to the full list; main_agent_tools is rebuilt from the wrapped set.
    if tool_wrapper:
        logger.info("Applying tool wrapper...")
        tools = tool_wrapper(tools)
        # Rebuild main_agent_tools from the wrapped list so wrappers apply to A2A tools too
        wrapped_names = {t.name for t in tools}
        main_agent_tools = [t for t in tools if t.name in {m.name for m in main_agent_tools}]

    # Get models
    def _resolve_model(m_name: str, fallback):
        model = AVAILABLE_MODELS.get(m_name)
        if model:
            return model
        if m_name.startswith("airgap"):
            return LLMFactory.get_llm(model_name=m_name, api_key=get_credential("AIRGAP_API_KEY"))
        elif m_name.startswith("ollama"):
            return LLMFactory.get_llm(model_name=m_name)
        return fallback

    main_model = _resolve_model(main_model_name, thinking_model_mini)
    subagent_model = _resolve_model(subagent_model_name, action_minimal_thinking_model)
    design_model = _resolve_model(design_model_name, multi_purpose_model)

    ## Create Subagents
    knowledge_acquisition_subagent = {
        "name": "recent_knowledge_and_extra_knowledge_acquisition_subagent",
        "description": "Subagent specialized in acquiring additional knowledge from various sources such as internet to get the most recent information or directly from a user to get complete contextual information. This subagent can be used where the model direct response confidence level is below 90% or if more clarification is needed from the user. The acquired knowledge can be on networking modern design, devices commands, devices data sheets, site information, systems, api endpoints, and in general any information that can help the main agent accomplish its task. This knowledge acquisition is necessary where the model confidence is low on the information provided by the user or if more clarification is needed from the user. Subagent may need what was the confidence level that triggered its call.",
        "system_prompt": "You are a knowledge acquisition expert agent. You help acquiring knowledge from various sources such as internet, expert user input, search and online documentation, etc. You get invoked only if there is a need to enhance the information given by the user or if clarification to what the user is asking can be acquired from the documents or from the user, in some cases where you have no knowledge you can run a tool to ask user for clarification. your goal is to acquire more knowledge and share with the main agent to help the main agent accomplish its task.",
        "tools": [search_internet, user_clarification_and_action_tool],
        "model": subagent_model,
        "middleware": [log_before_calling_model, log_after_model, announce_tool_call],
    }

    LAN_subagent = {
        "name": "LAN_subagent",
        "description": "Agent specialized in the LAN network activities such as routing, switching, Wireless related tasks for an entreprise network. It has access to necessary tools and credentials needed for access.",
        "system_prompt": LAN_subagent_template,
        "tools": lan_tools + [search_internet, user_clarification_and_action_tool],
        "model": subagent_model,
        "skills": get_network_skills(),
        "middleware": [log_before_calling_model, log_after_model, announce_tool_call],
    }

    cloud_computing_subagent = {
        "name": "cloud_computing_subagent",
        "description": "Agent specialized in cloud computing related tasks such as AWS, Azure, GCP,  etc.",
        "system_prompt": "You are a senior cloud operations engineer with deep expertise across AWS, GCP, and Azure. Your responsibilities include: infrastructure provisioning and IaC review (Terraform, Pulumi, CloudFormation), incident triage and root cause analysis, cost anomaly detection and FinOps recommendations, IAM and security posture review, CI/CD pipeline troubleshooting, and SLO/SLA compliance monitoring. Approach problems methodically — state your assumptions, identify the blast radius of any change, and flag destructive actions before proceeding. Output CLI commands, runbooks, and configs in fenced code blocks with the correct language tag. When multiple cloud providers are relevant, note provider-specific differences.",
        "tools": cloud_tools + [search_internet, user_clarification_and_action_tool],
        "model": subagent_model,
        "middleware": [log_before_calling_model, log_after_model,announce_tool_call],
    }

    # Integrate the design interpreter as a compiled subagent
    from graphs.design_interpretor import get_design_interpretor_subagent
    design_interpretor_subagent = get_design_interpretor_subagent(model_name=design_model_name, api_key=get_credential("OPENAI_KEY"))

    # Integrate network_operator as a compiled datacentre subagent
    from graphs.network_operator.subagent_bridge import get_datacenter_subagent
    datacenter_tools = filter_tools_by_category(tools, 'datacenter')
    datacentre_subagent = get_datacenter_subagent(
        tools=datacenter_tools + [search_internet, user_clarification_and_action_tool],
    )
    logger.info(f"datacentre_subagent built with {len(datacenter_tools)} datacenter tools")

    from subagents.nms_browser_agent import nms_browser_agent
    from subagents.net_lab_agent import net_lab_agent
    from subagents.service_desk_agent import service_desk_agent
    from subagents.ticket_scout_agent import ticket_scout_agent
    from subagents.automata_agent import automata_agent

    # Inject tools and middleware into subagents
    net_lab_agent["tools"] = lab_tools + [search_internet, user_clarification_and_action_tool]
    service_desk_agent["tools"] = servicenow_tools + [user_clarification_and_action_tool]
    ticket_scout_agent["tools"] = jira_tools + [user_clarification_and_action_tool]
    automata_agent["tools"] = automata_tool_list

    subagents = [
        LAN_subagent,
        knowledge_acquisition_subagent,
        #network_design_subagent,
        cloud_computing_subagent,
        design_interpretor_subagent,
        datacentre_subagent,
        nms_browser_agent,
        net_lab_agent,
        service_desk_agent,
        ticket_scout_agent,
        automata_agent,
    ]

    # --- Dynamic third-party plugin subagents ---
    # Discovers ~/.net-deepagent/net-agent/subagents/*/agent.json at startup.
    from utils.third_party_subagent_loader import load_third_party_subagents
    _builtin_tools_map = {
        "search_internet": search_internet,
        "user_clarification_and_action_tool": user_clarification_and_action_tool,
    }
    _third_party_subagents = load_third_party_subagents(
        all_mcp_tools=tools,
        available_models=AVAILABLE_MODELS,
        builtin_tools=_builtin_tools_map,
    )
    subagents.extend(_third_party_subagents)
    logger.info(f"Loaded {len(_third_party_subagents)} third-party plugin subagent(s)")

    # Propagate tool announcer to ALL subagents (inline, imported, or compiled)
    # Note: Compiled subagents currently ignore the middleware key in deepagents,
    # but we include them here for completeness and future-proofing.
    for _sa in subagents:
        _sa["middleware"] = [log_before_calling_model, log_after_model, announce_tool_call, fallback_middleware]


    ## create deep agent
    # TODO: add PII middleware to mask sensitive info like IPs, MACs, etc.
    # TODO: add way to load skills from skill.md and add it to the different system_prompt
    system_prompt = custom_system_prompt if custom_system_prompt else network_activity_planner_agent_template

    logger.info("Creating deep agent with create_deep_agent()...")
    try:
        net_deep_agent = create_deep_agent(
            tools=main_agent_tools,
            system_prompt=system_prompt,
            subagents=subagents,
            model=main_model,
            backend=FilesystemBackend(),
            store=InMemoryStore(),
            middleware=[log_before_calling_model, log_after_model, announce_tool_call, fallback_middleware] + (custom_middlewares or []),
        )
        
        # Expose the raw LLM so the automata manager can bypass tool-calling when parsing schedules
        net_deep_agent.raw_llm = main_model
        
        logger.info(f"Deep agent created successfully! Type: {type(net_deep_agent)}")
        logger.debug(f"Agent has astream: {hasattr(net_deep_agent, 'astream')}")
    except Exception as e:
        logger.error(f"Failed to create deep agent: {e}", exc_info=True)
        raise

    logger.info("=== create_network_agent() complete ===")
    return net_deep_agent


async def main():
    """
    Main function for standalone execution.
    Demonstrates agent usage with optional TruLens evaluation.
    """
    # Create the network agent using the factory function
    net_deep_agent = await create_network_agent()
    
    question_1 = """There is a connectivity issue in the LAN network and user with IP 10.10.10.4 and mac address aaaa.bb12.3456"
    is unable to reach any application, check why. it is connected on switch with management IP address of 192.168.81.222"""
    
    question_1_1 = """There is a connectivity issue in Brisbane LAN network and user with IP 10.10.10.4 and mac address 00:50:79:66:68:07"
    is unable to reach any application, check why. it is connected on switch, switch model is huaweis5530"""
    
    question_2 = """There is a connectivity issue in Brisbane LAN network and user with IP 10.10.10.4 and mac address 00:50:79:66:68:07"
    is unable to reach any application, check why. it is connected on switch"""

    question_3 = """There is a connectivity issue incident reported by Nirali Patel work on it"""
    
    question_4 = "what is the broadcast address of 192.168.16.32/28"
    
    question_5 = """for Headquaters site devices create a change to update log servers to 10.99.99.99, our organization uses ansible for deployment. only create a standalone ansible yaml file
                 """

    print("\n" + "="*50)
    print("      NETWORK DEEP AGENT - EVALUATION MENU")
    print("="*50)
    print("1. Run Agent (Standard)")
    print("2. Run Agent with TruLens Evaluation")
    print("3. Exit")
    print("="*50)
    
    choice = input("\nSelect an option (1-3): ").strip()

    if choice == '3':
        print("Exiting...")
        return
    
    if choice == '1':
        print("\nRunning in Standard Mode...")
        chunks = []
        async for chunk in net_deep_agent.astream({"messages": [{"role": "user", "content": question_5}]}):
            chunks.append(chunk)
            # Print chunks as they come
            if "model" in chunk and "messages" in chunk["model"]:
                chunk["model"]["messages"][-1].pretty_print()
            elif "tools" in chunk and "messages" in chunk["tools"]:
                chunk["tools"]["messages"][-1].pretty_print()
        
        print("\n" + "--"*40)
        print(f"Final Response: {chunks[-1] if chunks else 'No response'}")
        print("--"*40)

    elif choice == '2':
        print("\nRunning in Evaluation Mode (TruLens)...")
        # Evaluation with Trulens imports
        from trulens.core import Feedback, TruSession, Select
        from trulens.core.feedback.selector import Selector
        from trulens.providers.openai import OpenAI as TrulensOpenAI
        from trulens.apps.langgraph import TruGraph
        
        # evaluation layer 
        session = TruSession()
        session.reset_database()
        # Goal-Plan-Act evaluation provider
        gpa_eval_provider = TrulensOpenAI(model_engine="gpt-5.1",
                                          api_key=get_credential("OPENAI_KEY"))
        
        # Goal-Plan-Act: Logical consistency of trace
        f_logical_consistency = Feedback(
            gpa_eval_provider.logical_consistency_with_cot_reasons,
            name="Logical Consistency",
        ).on({
            "trace": Selector(trace_level=True),
        })
        
        # Goal-Plan-Act: Execution efficiency of trace
        f_execution_efficiency = Feedback(
            gpa_eval_provider.execution_efficiency_with_cot_reasons,
            name="Execution Efficiency",
        ).on({
            "trace": Selector(trace_level=True),
        })
        
        # Goal-Plan-Act: Plan adherence
        f_plan_adherence = Feedback(
            gpa_eval_provider.plan_adherence_with_cot_reasons,
            name="Plan Adherence",
        ).on({
            "trace": Selector(trace_level=True),
        })
        
        # Goal-Plan-Act: Plan quality
        f_plan_quality = Feedback(
            gpa_eval_provider.plan_quality_with_cot_reasons,
            name="Plan Quality",
        ).on({
            "trace": Selector(trace_level=True),
        })
        
        tru_recorder = TruGraph(net_deep_agent,
                                app_name="coworkerx_agent_app",
                                app_version="0.2",
                                feedbacks=[
                                    f_logical_consistency,
                                    f_execution_efficiency,
                                    f_plan_adherence,
                                    f_plan_quality]
        )
        
        chunks = []
        with tru_recorder as recording:
            async for chunk in net_deep_agent.astream({"messages": [{"role": "user", "content": question_5}]}):
                chunks.append(chunk)
                # Print chunks as they come (optional, but good for feedback)
                if "model" in chunk and "messages" in chunk["model"]:
                    chunk["model"]["messages"][-1].pretty_print()
                elif "tools" in chunk and "messages" in chunk["tools"]:
                    chunk["tools"]["messages"][-1].pretty_print()
        
        print("\nFinal Response received:.......................................................\n")
        print("--"*40)
        print(f"Final Response: {chunks[-1] if chunks else 'No response'}")
        print("--"*40)
        
        from trulens.core import Tru
        import time
        print("Waiting for feedbacks to complete...")
        time.sleep(10)  # Wait for feedbacks to complete
        
        # Get the record
        record = recording.get()
        
        # Check if _wait_for_record is async
        if hasattr(record._wait_for_record, '__await__'):
            await record._wait_for_record()
        else:
            record._wait_for_record()
        
        # Fix: Use the correct app_name here!
        records_df, feedback_cols = session.get_records_and_feedback(app_ids=["coworkerx_agent_app"])
        print(f"Records in DB: {len(records_df)}")
        print(f"Feedback columns: {feedback_cols}")
        
        if len(records_df) > 0:
            print(records_df[['input', 'output'] + feedback_cols])
        
        print("\nStarting TruLens Dashboard...")
        session.run_dashboard()
    else:
        print("Invalid choice. Please select 1, 2, or 3.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nAborted by user.")



