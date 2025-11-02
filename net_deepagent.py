from typing_extensions import TypedDict
import operator
#from langgraph.checkpoint.sqlite import SqliteSaver
from langchain_core.messages import AnyMessage, SystemMessage, HumanMessage, AIMessage, ChatMessage
from langchain_core.tools import tool
import creds
import os
from pydantic import BaseModel, Field
#from langgraph.prebuilt import ToolNode, create_react_agent, InjectedState
from langgraph.graph import START, END, StateGraph, MessagesState
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_core.tools import BaseTool
import traceback
from time import sleep
from deepagents import create_deep_agent
from langchain_mcp_adapters.client import MultiServerMCPClient
import asyncio
from langchain.agents import create_agent
from prompts import network_activity_planner_agent_template, LAN_subagent_template


## models keys
os.environ["OPENAI_API_KEY"] = creds.OPENAI_KEY
os.environ["ANTHROPIC_API_KEY"] = creds.ANTHROPIC_KEY



# models
thinking_model = ChatOpenAI(model="gpt-5-mini", api_key=creds.OPENAI_KEY)
action_minimal_thinking_model = ChatOpenAI(model="gpt-5-mini", api_key=creds.OPENAI_KEY, reasoning={"effort": "minimal"})
multi_purpose_model = ChatOpenAI(model="gpt-4.1", api_key=creds.OPENAI_KEY)
bias_removal_model = ChatAnthropic(model="claude-4", api_key=creds.ANTHROPIC_KEY)

#r= thinking_model.invoke("what is the capital city of Australia?")
r1= action_minimal_thinking_model.invoke("what is the capital city of Australia?")
print(r1)
#print(r)

async def main():

    ## MCP client and tools
    client = MultiServerMCPClient(
        {
            "network": {
                # Make sure you start your weather server on port 8000
                "url": "http://localhost:8000/mcp",
                "transport": "streamable_http",
            }
        }
    )
    tools = await client.get_tools()

    ## agents testing
    #agent = create_agent(thinking_model, tools)
    #math_response = await agent.ainvoke({"messages": "what's (3 + 5) x 12?"})
    #print(math_response)
    #network_response = await agent.ainvoke({"messages": "what are interface on device with IP 10.10.10.1"})
    #print(network_response)

    ## Create Subagents 
    LAN_subagent = {
        "name": "LAN_subagent",
        "description": "Agent specialized in the LAN network activities such as routing and switching related tasks.",
        "system_prompt": LAN_subagent_template,
        "tools": tools,
        "model": action_minimal_thinking_model,  # Optional override, defaults to main agent model
        }
    subagents = [LAN_subagent]


    ## create deep agent
    net_deep_agent =  create_deep_agent(
        tools=tools,
        system_prompt=network_activity_planner_agent_template,
        subagents=subagents,
        model=thinking_model,
        )
    
    async for chunk in net_deep_agent.astream({"messages": """There is a connectivity issue in the LAN network and user with IP 10.10.10.4 and mac address aaaa.bb12.3456"
    is unable to reach any application, check why. it is connected on switch with management IP 10.10.10.254"""}):
        print("New chunk received:.......................................................\n")
        print(chunk)
        if "messages" in chunk:
            chunk["messages"][-1].pretty_print()



asyncio.run(main())



