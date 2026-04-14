import os
import asyncio
from typing import Optional, List, Dict, Any
from langchain_core.tools import tool
from browser_use import Agent, ChatOpenAI
from utils.credentials_helper import get_credential
from subagents.prompts import NMS_BROWSER_PROMPT

# Set OPENAI_API_KEY for langchain compatibility if needed
os.environ["OPENAI_API_KEY"] = get_credential("OPENAI_KEY")

_llm = ChatOpenAI(
    model=os.environ.get("NMS_BROWSER_MODEL", "gpt-4o"),
    api_key=os.environ["OPENAI_API_KEY"],
)

@tool
def nms_browser(task: str, trajectories: Optional[List[Dict[str, Any]]] = None) -> str:
    """
    Use this to navigate a GUI NMS or search the web for vendor documentation,
    configuration guides, or network operations research.
    The task must include the vendor/platform name and what needs to be done.
    If provided, the trajectories argument should be a list of browser-use dict-based actions
    for the agent to execute initially. Example: [{"navigate": {"url": "..."}}]
    """
    #TODO: move this tool to the tools folder so that it can be used by ather agents
    async def _run():
        agent = Agent(task=task, llm=_llm, initial_actions=trajectories)
        return str(await agent.run())

    return asyncio.run(_run())

nms_browser_agent = {
    "name": "nms_browser_agent",
    "description": (
        "Use this subagent when the task requires navigating a GUI NMS with no API endpoint available in our skillset"
        "(SolarWinds, PRTG, Zabbix, etc.) or researching vendor documentation, "
        "configuration guides, or CVEs on the web. Do Not Use it for API calls or for internet search."
    ),
    "system_prompt": NMS_BROWSER_PROMPT,
    "tools": [nms_browser],
}
