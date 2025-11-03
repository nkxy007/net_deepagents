from langchain_core.tools import tool


@tool
def topology_discovery(site_name: str) -> str:
    """Discover network topology for a given site"""
    return f"Discovered topology for site {site_name}: Switch1 -- Router1 -- Firewall1"

@tool
def topology_discovery_via_user_input(site_name: str) -> str:
    """get the topology info of the site from user input"""
    topology_info = input(f"Please provide additional details for topology of {site_name}: ")
    return f"Discovered topology based on user input {topology_info}"

@tool
def clarification_from_user(question: str) -> str:
    """ask for clarification from user when agent is unsure about next steps"""
    answer = input(f"Agent needs clarification: {question}\nUser input: ")
    return answer     

@tool
def onliine_knowledge_acquisition_tool(subject_to_search: str) -> str:
    """Acquire knowledge from  internet sources for a given subject"""
    # 1. First remove any personal identifiable information from the subject_to_search
    # 2. Remove any company specific internal information from the subject_to_search
    # 3. run a search and summarize the findings
    acquired_info = f"Summary of findings on : This is what we got from internet"
    return f"Acquired knowledge on <browser> {acquired_info} </browser> from online sources."