import os
import sys
import re
import json

# Add workspace root to path so ai_helper (and other sibling modules) are importable
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from typing import Annotated, List, Optional, TypedDict
import operator
from pydantic import BaseModel, Field
from langgraph.graph import START, END, StateGraph
from langchain_core.messages import AnyMessage, HumanMessage, AIMessage, SystemMessage
from utils.ai_helper import AIHelper, get_models_config

# ---------------------------------------------------------------------------
# Data Models (shared by both branches)
# ---------------------------------------------------------------------------

class Device(BaseModel):
    name: str = Field(description="The hostname or name of the device")
    management_ip: Optional[str] = Field(description="Management IP address of the device", default=None)
    brand: str = Field(description="Brand of the device (Cisco, Juniper, etc.)")
    role: Optional[str] = Field(description="Role of the device (Router, Switch, PC, etc.)", default=None)

class Link(BaseModel):
    from_device: str = Field(description="Name of the source device")
    from_interface: str = Field(description="Interface name on the source device")
    to_device: str = Field(description="Name of the destination device")
    to_interface: str = Field(description="Interface name on the destination device")
    link_ip: Optional[str] = Field(description="IP address or subnet of the link", default=None)

class Protocol(BaseModel):
    name: str = Field(description="Name of the protocol (OSPF, BGP, MPLS, VXLAN, etc.)")
    devices: List[str] = Field(description="List of devices participating in the protocol")
    details: str = Field(description="Details such as Area ID, AS Number, VNI, etc.")

# ---------------------------------------------------------------------------
# Image Branch Model
# ---------------------------------------------------------------------------

class FullDesign(BaseModel):
    devices: List[Device] = Field(description="List of all devices in the topology")
    links: List[Link] = Field(description="List of all links between devices")
    protocols: List[Protocol] = Field(description="List of all protocols configured")
    summary: str = Field(description="A high-level summary of the network design")

# ---------------------------------------------------------------------------
# Document Branch Model
# ---------------------------------------------------------------------------

class DesignDocumentInfo(BaseModel):
    """Structured information extracted from a PDF network design document."""
    site_name: Optional[str] = Field(description="The name of the site or project", default=None)
    site_id: Optional[str] = Field(description="The site ID if present", default=None)
    location: Optional[str] = Field(description="Physical location of the site", default=None)
    devices: List[Device] = Field(description="List of devices found in the document", default=[])
    management_subnet: Optional[str] = Field(description="Management subnet in CIDR notation", default=None)
    vlans: Optional[List[int]] = Field(description="VLAN IDs referenced in the document", default=None)
    routing_protocols: Optional[List[str]] = Field(description="Routing protocols mentioned (OSPF, BGP, MPLS, etc.)", default=None)
    dns_servers: Optional[List[str]] = Field(description="DNS server IPs", default=None)
    ntp_servers: Optional[List[str]] = Field(description="NTP server IPs", default=None)
    dhcp_servers: Optional[List[str]] = Field(description="DHCP server IPs", default=None)
    aaa_config: Optional[List[str]] = Field(description="AAA/TACACS/RADIUS configuration notes", default=None)
    links: List[Link] = Field(description="Network links between devices found in the document", default=[])
    protocols: List[Protocol] = Field(description="Detailed protocol configurations found", default=[])
    summary: str = Field(description="High-level summary of the design document content", default="")

# ---------------------------------------------------------------------------
# Model used by the RAG extraction step in the document branch.
# Override by setting the RAG_LLM environment variable, falls back to config.
_config = get_models_config()
RAG_LLM: str = os.environ.get("RAG_LLM", _config.get("rag_llm_image", "gpt-5-mini"))

# ---------------------------------------------------------------------------
# Comprehensive search topics for RAG-based document extraction
# ---------------------------------------------------------------------------

SEARCH_TOPICS = [
    # Identity & Location
    "site name, site ID, project name, customer name, location, address, timezone",

    # Devices & Hardware
    "network devices, hostnames, management IP addresses, device vendor, device model, device role (router, switch, firewall, load balancer, server)",

    # IP Addressing & Subnets
    "IP addressing scheme, subnets, supernets, CIDR, IPv4, IPv6, address plan, management subnet",

    # VLANs & Layer 2
    "VLANs, VLAN IDs, trunking, access ports, STP, MSTP, RSTP, 802.1Q, port-channels, LACP, bonding",

    # Routing Protocols (IGP)
    "OSPF, OSPF area, OSPF process ID, EIGRP, ISIS, RIP, static routes, default route, redistribution",

    # Routing Protocols (BGP)
    "BGP, eBGP, iBGP, AS number, ASN, BGP neighbors, route reflector, BGP communities, BGP policy, prefix filtering",

    # MPLS & Segment Routing
    "MPLS, LDP, RSVP, MPLS-TE, traffic engineering, segment routing, SR-MPLS, SRv6, LSP, VRF, route distinguisher, route target",

    # Layer 3 VPN & VRF
    "L3VPN, VRF, MPLS VPN, route distinguisher RD, route target RT, inter-VRF routing, VRF-lite",

    # Layer 2 VPN & EVPN
    "EVPN, BGP EVPN, L2VPN, VPLS, VPWS, pseudowire, MAC-VRF, IP-VRF, EVPN type 2, EVPN type 5",

    # VXLAN & Overlay Fabrics
    "VXLAN, VNI, VTEP, NVE, underlay, overlay, spine-leaf, fabric, BGP EVPN VXLAN, flood-and-learn",

    # Tunnels (GRE, IPsec, etc.)
    "GRE tunnel, IPsec, IKE, ISAKMP, tunnel source, tunnel destination, DMVPN, FlexVPN, mGRE, crypto map, IPsec profile",

    # SD-WAN
    "SD-WAN, SDWAN, vEdge, vSmart, vBond, vManage, Cisco SDWAN, Viptela, Meraki, Fortinet SD-WAN, underlay WAN, overlay SD-WAN, ZTP, OMP",

    # Wireless & WLAN
    "wireless, WLAN, WiFi, SSID, WLC, access point, AP, 802.11, RF, radio, band, channel",

    # Firewalls & Security
    "firewall, ACL, access control list, zone, policy, NAT, PAT, security group, ZBFW, ASA, Palo Alto, FortiGate, inspection, DMZ",

    # Load Balancing & HA
    "load balancer, F5, NSX, VIP, pool, health check, HSRP, VRRP, GLBP, failover, high availability, redundancy, clustering",

    # QoS & Traffic Engineering
    "QoS, DSCP, CoS, traffic shaping, policing, queuing, CBWFQ, priority queue, marking, classification",

    # Multicast
    "multicast, PIM, IGMP, RP, rendezvous point, SSM, ASM, multicast group, mVPN",

    # Management Services
    "DNS servers, NTP servers, DHCP servers, syslog servers, SNMP servers, SNMP community, SNMP v3, flow exporter, NetFlow, IPFIX",

    # Authentication & AAA
    "AAA, TACACS+, RADIUS, LDAP, 802.1X, authentication, authorization, accounting, ISE, ClearPass",

    # Network Links & Physical Connectivity
    "network links, point-to-point links, interface IP, link subnet, uplink, downlink, WAN link, bandwidth, circuit ID, provider",

    # Network Topology Summary
    "network topology overview, design summary, architecture description, site purpose, network diagram",
]

# ---------------------------------------------------------------------------
# State definition (unified for both branches)
# ---------------------------------------------------------------------------

class InterpretorState(TypedDict):
    messages: Annotated[List[AnyMessage], operator.add]
    # Image branch
    image_path: str
    model_name: str
    api_key: str
    result: Optional[FullDesign]
    # Document branch
    document_path: Optional[str]
    document_result: Optional[DesignDocumentInfo]
    # Shared
    error: Optional[str]
    fresh_run: Optional[bool]
    cached_result_found: Optional[bool]

# ---------------------------------------------------------------------------
# Image Branch Prompt
# ---------------------------------------------------------------------------

VISION_PROMPT = """
Analyze the provided network diagram and extract all relevant details. 
Return your findings in the following JSON format:

{
  "devices": [
    {
      "name": "device hostname",
      "management_ip": "IP if present",
      "brand": "Cisco/Juniper/etc",
      "role": "Router/Switch/etc"
    }
  ],
  "links": [
    {
      "from_device": "device name",
      "from_interface": "interface name",
      "to_device": "device name",
      "to_interface": "interface name",
      "link_ip": "IP address if present"
    }
  ],
  "protocols": [
    {
      "name": "OSPF/BGP/etc",
      "devices": ["list of devices"],
      "details": "Area X, ASN Y, etc"
    }
  ],
  "summary": "High-level overview of the topology"
}

IMPORTANT: Ensure all keys are present. If links or protocols are not found, return empty lists. Use 'name' for the device name/hostname.
"""

from langchain_core.runnables import RunnableConfig

# ---------------------------------------------------------------------------
# Node 1 (shared entry): detect_input_type
# ---------------------------------------------------------------------------

def detect_input_type(state: InterpretorState) -> dict:
    """Detect whether the input is a network diagram image or a PDF design document
    and populate the appropriate state path field."""
    # Already determined upstream
    if state.get("document_path") or state.get("image_path"):
        return {}

    last_msg = state["messages"][-1]
    content = str(last_msg.content)

    pdf_match = re.search(r"(/[^\s]+\.pdf)", content, re.IGNORECASE)
    img_match = re.search(r"(/[^\s]+\.(?:png|jpg|jpeg))", content, re.IGNORECASE)

    fresh_run = "--fresh" in content

    def check_cache(file_path: str, is_doc: bool):
        if fresh_run:
            return None
        basename = os.path.basename(file_path)
        cache_path = os.path.expanduser(f"~/.net-deepagent/design/{basename}.json")
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r") as f:
                    data = json.load(f)
                return data, cache_path
            except Exception as e:
                print(f"Failed to load cache from {cache_path}: {e}")
        return None

    if pdf_match:
        doc_path = pdf_match.group(1)
        cache_res = check_cache(doc_path, True)
        if cache_res:
            data, cache_path = cache_res
            doc_result = DesignDocumentInfo(**data)
            return {
                "document_path": doc_path,
                "document_result": doc_result,
                "cached_result_found": True,
                "messages": [AIMessage(content=f"Loaded cached design document result from {cache_path}\nSummary: {doc_result.summary}")]
            }
        return {"document_path": doc_path, "fresh_run": fresh_run}

    elif img_match:
        img_path = img_match.group(1)
        cache_res = check_cache(img_path, False)
        if cache_res:
            data, cache_path = cache_res
            result = FullDesign(**data)
            return {
                "image_path": img_path,
                "result": result,
                "cached_result_found": True,
                "messages": [AIMessage(content=f"Loaded cached image design result from {cache_path}\nSummary: {result.summary}")]
            }
        return {"image_path": img_path, "fresh_run": fresh_run}

    return {"error": "No image (.png/.jpg/.jpeg) or document (.pdf) path found in message"}


def route_by_input_type(state: InterpretorState) -> str:
    """Conditional router: send to the image branch or the document branch."""
    if state.get("error"):
        return END
    if state.get("cached_result_found"):
        return END
    if state.get("document_path"):
        return "read_design_document"
    return "extract_context"

# ---------------------------------------------------------------------------
# Image Branch Nodes (unchanged logic)
# ---------------------------------------------------------------------------

def extract_context(state: InterpretorState):
    """Extract image path from the last message if not already set."""
    if state.get("image_path"):
        return {}

    last_msg = state["messages"][-1]
    if hasattr(last_msg, "content"):
        content = last_msg.content
        match = re.search(r"(/[^\s]+\.(?:png|jpg|jpeg))", content, re.IGNORECASE)
        if match:
            return {"image_path": match.group(1)}

    return {"error": "Could not find image path in message"}


def interpret_image(state: InterpretorState, config: RunnableConfig):
    """Analyze the image using AIHelper and return the findings."""
    if state.get("error"):
        return {}

    image_path = state.get("image_path")

    configurable = config.get("configurable", {})
    model_name = configurable.get("model_name", "openai")
    api_key = configurable.get("api_key")

    if not image_path or not os.path.exists(image_path):
        return {"error": f"Image path {image_path} does not exist",
                "messages": [AIMessage(content=f"Error: Image path {image_path} not found.")]}

    ai_helper = AIHelper(api_key, model=model_name)
    try:
        print(f"Interpreting image {image_path} with model {model_name}...")
        encoded_image = ai_helper.encode_image(image_path)
        image_type = "png"
        if image_path.lower().endswith((".jpg", ".jpeg")):
            image_type = "jpeg"

        analysis_result = ai_helper.get_image_analysis(encoded_image, VISION_PROMPT,
                                                        temperature=0.5, image_type=image_type)
        return {"messages": [AIMessage(content=analysis_result)]}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"error": str(e),
                "messages": [AIMessage(content=f"Error during analysis: {str(e)}")]}


def parse_structured_data(state: InterpretorState):
    """Parse the LLM output into the FullDesign model."""
    if state.get("error"):
        return {}

    last_message = state["messages"][-1]
    content = last_message.content

    try:
        content_str = str(content)
        if "```json" in content_str:
            json_str = content_str.split("```json")[1].split("```")[0].strip()
        elif "```" in content_str:
            json_str = content_str.split("```")[1].split("```")[0].strip()
        else:
            match = re.search(r'(\{.*\})', content_str, re.DOTALL)
            if match:
                json_str = match.group(1)
            else:
                json_str = content_str

        data = json.loads(json_str)

        # Manual fix for common aliases
        if "devices" in data:
            for dev in data["devices"]:
                if "hostname" in dev and "name" not in dev:
                    dev["name"] = dev["hostname"]

        for key in ["devices", "links", "protocols"]:
            if key not in data:
                data[key] = []
        if "summary" not in data:
            data["summary"] = "No summary provided."

        result = FullDesign(**data)
        
        image_path = state.get("image_path", "")
        if image_path:
            basename = os.path.basename(image_path)
            cache_dir = os.path.expanduser("~/.net-deepagent/design")
            os.makedirs(cache_dir, exist_ok=True)
            cache_path = os.path.join(cache_dir, f"{basename}.json")
            try:
                with open(cache_path, "w") as f:
                    json.dump(result.model_dump(), f, indent=2)
            except Exception as e:
                print(f"Failed to save cache to {cache_path}: {e}")

        summary_msg = (
            f"Design Interpretation Complete.\n"
            f"Summary: {result.summary}\n"
            f"Devices: {len(result.devices)}, Links: {len(result.links)}, "
            f"Protocols: {len(result.protocols)}\n"
            f"entire design json: {json_str}\n"
        )
        return {"result": result, "messages": [AIMessage(content=summary_msg)]}
    except Exception as e:
        print(f"Structured parsing failed: {e}")
        return {"messages": [AIMessage(
            content=f"Note: Could not parse output into fully structured data ({str(e)}), "
                    "but raw analysis is available in previous message."
        )]}

# ---------------------------------------------------------------------------
# Document Branch Node
# ---------------------------------------------------------------------------

def read_design_document(state: InterpretorState, config: RunnableConfig) -> dict:
    """Read a PDF design document and extract structured network design information.

    Strategy: RAG (Chroma vector store) as primary approach for reliability and
    token efficiency on large documents. Falls back to page-by-page extraction
    if the vector store cannot be built.
    """
    doc_path = state.get("document_path")
    if not doc_path or not os.path.exists(doc_path):
        return {"error": f"Document not found: {doc_path}",
                "messages": [AIMessage(content=f"Error: Document path '{doc_path}' not found.")]}

    configurable = config.get("configurable", {})
    api_key = configurable.get("api_key") or os.environ.get("OPENAI_API_KEY")

    from langchain_community.document_loaders import PyPDFLoader
    from trustcall import create_extractor
    from langchain_openai import ChatOpenAI

    print(f"Loading design document: {doc_path}")
    loader = PyPDFLoader(doc_path)
    documents = loader.load()
    print(f"Loaded {len(documents)} pages from {doc_path}")

    design_details: dict = {}
    llm_extractor = ChatOpenAI(model=RAG_LLM, api_key=api_key)
    schema_extractor = create_extractor(
        llm_extractor,
        tools=[DesignDocumentInfo],
        tool_choice="DesignDocumentInfo"
    )

    # ------------------------------------------------------------------
    # PRIMARY: RAG approach — chunk → embed → similarity search → extract
    # ------------------------------------------------------------------
    rag_succeeded = False
    try:
        from langchain_chroma import Chroma
        from langchain_openai import OpenAIEmbeddings
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        vector_db_path = doc_path.rsplit(".", 1)[0] + "_vector_db"
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
        chunks = text_splitter.split_documents(documents)

        embeddings = OpenAIEmbeddings(api_key=api_key)

        # Load existing store or create a new one
        try:
            vector_store = Chroma(persist_directory=vector_db_path,
                                  embedding_function=embeddings)
            if not vector_store.get(limit=1)["ids"]:
                raise ValueError("Vector store is empty, needs to be created.")
            print("Loaded existing vector store.")
        except Exception:
            print("Creating new vector store...")
            vector_store = Chroma.from_documents(chunks, embeddings,
                                                 persist_directory=vector_db_path)
            print(f"Vector store created at {vector_db_path}.")

        # Query every topic and collect relevant chunks (deduplicated)
        retrieved_context: list[str] = []
        for topic in SEARCH_TOPICS:
            results = vector_store.similarity_search(topic, k=4)
            retrieved_context.extend([r.page_content for r in results])
        print(f"RAG retrieved {len(retrieved_context)} chunks across {len(SEARCH_TOPICS)} topics.")
        # print(retrieved_context)
        # Deduplicate while preserving order
        seen: set = set()
        unique_context: list[str] = []
        for chunk in retrieved_context:
            if chunk not in seen:
                seen.add(chunk)
                unique_context.append(chunk)

        combined_context = "\n---\n".join(unique_context)
        print(f"RAG retrieved {len(unique_context)} unique chunks across {len(SEARCH_TOPICS)} topics.")

        resp = schema_extractor.invoke({
            "messages": [HumanMessage(
                f"Extract all network design details from the following excerpts of a design document.\n\n"
                f"{combined_context}"
            )]
        })
        if resp.get("responses") and isinstance(resp["responses"][0], DesignDocumentInfo):
            design_details = resp["responses"][0].model_dump()
            rag_succeeded = True
            print("RAG extraction succeeded.")

    except Exception as rag_error:
        print(f"RAG approach failed ({rag_error}), falling back to page-by-page extraction.")

    # ------------------------------------------------------------------
    # FALLBACK: Page-by-page extraction
    # ------------------------------------------------------------------
    if not rag_succeeded:
        print("Running page-by-page extraction fallback...")
        extraction_prompt = (
            "Extract network design details from this document page. "
            "Information already found so far: {existing}\n\n"
            "Page content:\n{page}"
        )
        for i, page in enumerate(documents):
            print(f"  Processing page {i + 1}/{len(documents)}...")
            resp = schema_extractor.invoke({
                "messages": [HumanMessage(
                    extraction_prompt.format(
                        existing=design_details,
                        page=page.page_content
                    )
                )]
            })
            if resp.get("responses") and isinstance(resp["responses"][0], DesignDocumentInfo):
                extracted = resp["responses"][0].model_dump()
                for k, v in extracted.items():
                    if v and k not in design_details:
                        design_details[k] = v
                    elif v and isinstance(v, list) and design_details.get(k):
                        # Merge lists, avoiding duplicates using string key dedup
                        existing_list = design_details[k]
                        merged = {str(item): item for item in existing_list}
                        merged.update({str(item): item for item in v})
                        design_details[k] = list(merged.values())
            # Continue until ALL fields in DesignDocumentInfo have a meaningful value
            # (None, empty list, empty dict all count as still unpopulated)
            def _is_populated(v):
                if v is None:
                    return False
                if isinstance(v, (list, dict)) and len(v) == 0:
                    return False
                return True

            if all(_is_populated(design_details.get(k)) for k in DesignDocumentInfo.model_fields):
                print("  All DesignDocumentInfo fields populated, stopping early.")
                break

    # ------------------------------------------------------------------
    # Build result object
    # ------------------------------------------------------------------
    if not design_details.get("summary"):
        design_details["summary"] = f"Design document: {os.path.basename(doc_path)}"

    # Filter to only fields known by the model
    valid_fields = {k: v for k, v in design_details.items()
                    if k in DesignDocumentInfo.model_fields}

    try:
        doc_result = DesignDocumentInfo(**valid_fields)
        
        basename = os.path.basename(doc_path)
        cache_dir = os.path.expanduser("~/.net-deepagent/design")
        os.makedirs(cache_dir, exist_ok=True)
        cache_path = os.path.join(cache_dir, f"{basename}.json")
        try:
            with open(cache_path, "w") as f:
                json.dump(doc_result.model_dump(), f, indent=2)
        except Exception as e:
            print(f"Failed to save cache to {cache_path}: {e}")
            
    except Exception as e:
        print(f"Could not build DesignDocumentInfo: {e}")
        doc_result = DesignDocumentInfo(summary=f"Partial extraction from {os.path.basename(doc_path)}")

    summary_msg = (
        f"Design Document Analysis Complete.\n"
        f"Site: {doc_result.site_name or 'Unknown'} | "
        f"Location: {doc_result.location or 'N/A'}\n"
        f"Devices: {len(doc_result.devices)}, "
        f"Links: {len(doc_result.links)}, "
        f"Protocols: {len(doc_result.protocols)}\n"
        f"Routing protocols: {', '.join(doc_result.routing_protocols or []) or 'N/A'}\n"
        f"VLANs: {doc_result.vlans or 'N/A'}\n"
        f"Summary: {doc_result.summary}"
    )
    print(summary_msg)
    return {
        "document_result": doc_result,
        "summary": summary_msg
    }

# ---------------------------------------------------------------------------
# Build the Graph
# ---------------------------------------------------------------------------

workflow = StateGraph(InterpretorState)

# Shared entry node
workflow.add_node("detect_input_type", detect_input_type)

# Image branch nodes
workflow.add_node("extract_context", extract_context)
workflow.add_node("interpret_image", interpret_image)
workflow.add_node("parse_structured_data", parse_structured_data)

# Document branch node
workflow.add_node("read_design_document", read_design_document)

# Edges
workflow.add_edge(START, "detect_input_type")
workflow.add_conditional_edges(
    "detect_input_type",
    route_by_input_type,
    {
        "extract_context": "extract_context",
        "read_design_document": "read_design_document",
        END: END,
    }
)

# Image branch
workflow.add_edge("extract_context", "interpret_image")
workflow.add_edge("interpret_image", "parse_structured_data")
workflow.add_edge("parse_structured_data", END)

# Document branch
workflow.add_edge("read_design_document", END)

design_interpretor_graph = workflow.compile()

# ---------------------------------------------------------------------------
# Subagent integration helper
# ---------------------------------------------------------------------------

def get_design_interpretor_subagent(model_name: str = "openai", api_key: str = None):
    """Return a CompiledSubAgent configuration.

    The subagent now supports two input types:
    - Network diagram images (.png, .jpg, .jpeg): extracted via vision model,
      returns a FullDesign with devices, links, protocols and summary.
    - PDF design documents (.pdf): extracted via RAG + structured schema,
      returns a DesignDocumentInfo with site info, devices, VLANs, routing
      protocols (OSPF, BGP, MPLS, VXLAN, EVPN, SD-WAN, tunnels, ...) and more.

    Provide the absolute file path in your prompt message.
    """
    if api_key is None:
        api_key = os.environ.get("OPENAI_API_KEY")

    return {
        "name": "design_interpretor",
        "description": (
            "Specialized agent that reads network design inputs and converts them into structured data. "
            "Supports: (1) network diagram images (.png/.jpg/.jpeg) — returns devices, links, protocols and summary; "
            "(2) PDF design documents (.pdf) — returns site info, devices, management IPs, VLANs, routing protocols "
            "(OSPF, BGP, MPLS, EVPN, VXLAN, SD-WAN, GRE/IPsec tunnels, ...), links and a full summary. "
            "Provide the absolute path to the file in your prompt. "
            "To force a fresh interpretation and ignore cached results, include the flag '--fresh' in your prompt."
        ),
        "runnable": design_interpretor_graph.with_config({
            "configurable": {
                "model_name": model_name,
                "api_key": api_key
            }
        })
    }

# ---------------------------------------------------------------------------
# CLI test entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio
    from utils.credentials_helper import get_credential, get_helper
    get_helper()

    async def run_test():
        # Image branch test
        image_path = "test_docs/small_network_diagram.png"
        #if os.path.exists(image_path):
        #    print("\n=== Testing IMAGE branch ===")
        #    initial_state = {
        #        "messages": [HumanMessage(content=f"Analyze this image: {os.path.abspath(image_path)}")],
        #        "image_path": os.path.abspath(image_path),
        #        "model_name": "openai",
        #        "api_key": os.environ.get("OPENAI_API_KEY")
        #    }
        #    async for chunk in design_interpretor_graph.astream(initial_state):
        #        print(chunk)

        # Document branch test
        doc_path = "test_docs/Network_Design_Document_Small_Enterprise_Site.pdf"
        if os.path.exists(doc_path):
            print("\n=== Testing DOCUMENT branch ===")
            doc_state = {
                "messages": [HumanMessage(content=f"Read this design document: {os.path.abspath(doc_path)}")],
                "api_key": os.environ.get("OPENAI_API_KEY"),
            }
            async for chunk in design_interpretor_graph.astream(doc_state):
                print(chunk)

    asyncio.run(run_test())
