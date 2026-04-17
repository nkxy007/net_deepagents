"""
EVE-NG MCP Server
=================
FastMCP server exposing EVE-NG REST API operations as MCP tools.

Inspired by:
  - mrbob0473/EVE-NG-CLI-connect  (node listing, telnet URL resolution)
  - TheNetworker/eve-ng-tools      (lab operations, connections, console ports)

Credentials are resolved from environment variables — never passed via LLM context.

Environment variables required:
  EVE_NG_HOST        EVE-NG server hostname or IP
  EVE_NG_USERNAME    API username  (default: admin)
  EVE_NG_PASSWORD    API password  (default: eve)
  EVE_NG_PORT        HTTP port     (default: 80)
  EVE_NG_PROTOCOL    http | https  (default: http)
  EVE_NG_VERIFY_SSL  true | false  (default: false)

Usage:
  pip install fastmcp httpx
  python eveng_mcp_server.py
"""

import os
import json
import asyncio
import logging
from typing import Optional
from urllib.parse import quote
import traceback

import httpx
import yaml
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from utils.eveng_helper import EvengClient, TopologyBuilder
import argparse
import sys

# ---------------------------------------------------------------------------
# CLI Arguments
# ---------------------------------------------------------------------------
parser = argparse.ArgumentParser(add_help=False)
parser.add_argument('--log-level', default='INFO', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], help='Set logging level')
args, remaining_argv = parser.parse_known_args()

# Update sys.argv to remove our custom flags so FastMCP doesn't error
sys.argv = [sys.argv[0]] + remaining_argv

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
numeric_level = getattr(logging, args.log_level.upper(), logging.INFO)
logging.basicConfig(level=numeric_level, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("eveng-mcp")

# ---------------------------------------------------------------------------
# Credential resolution (never exposed to LLM context)
# ---------------------------------------------------------------------------
def _cfg(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

def _base_url() -> str:
    proto = _cfg("EVE_NG_PROTOCOL", "http")
    host  = _cfg("EVE_NG_HOST", "")
    port  = _cfg("EVE_NG_PORT", "80")
    if not host:
        raise RuntimeError("EVE_NG_HOST environment variable is not set")
    return f"{proto}://{host}:{port}/api"

def _ssl_verify() -> bool:
    return _cfg("EVE_NG_VERIFY_SSL", "false").lower() == "true"

def _encode_path(path: str) -> str:
    """Encode a lab/folder path for EVE-NG API URLs.
    Preserves slashes (EVE-NG needs them raw); only encodes spaces etc.
    E.g.  '/test_lab.unl'  ->  '/test_lab.unl'
          '/My Lab.unl'    ->  '/My%20Lab.unl'
    """
    return quote(path, safe="/:@!$&'()*+,;=")

# ---------------------------------------------------------------------------
# Shared HTTP session with EVE-NG cookie auth
# ---------------------------------------------------------------------------
_session: Optional[httpx.AsyncClient] = None
_session_lock = asyncio.Lock()

async def _get_session() -> httpx.AsyncClient:
    global _session
    async with _session_lock:
        if _session is None or _session.is_closed:
            _session = httpx.AsyncClient(verify=_ssl_verify(), timeout=30)
            await _login(_session)
    return _session

async def _login(client: httpx.AsyncClient) -> None:
    url = f"{_base_url()}/auth/login"
    payload = {
        "username": _cfg("EVE_NG_USERNAME", "admin"),
        "password": _cfg("EVE_NG_PASSWORD", "eve"),
        "html5": "-1",
    }
    r = await client.post(url, json=payload)
    r.raise_for_status()
    log.info("EVE-NG session established")

async def _api(method: str, path: str, **kwargs) -> dict:
    """Thin wrapper — re-authenticates once on 401."""
    client = await _get_session()
    url = f"{_base_url()}{path}"
    r = await client.request(method, url, **kwargs)
    if r.status_code == 401:
        log.info("Session expired — re-authenticating")
        await _login(client)
        r = await client.request(method, url, **kwargs)
    r.raise_for_status()
    return r.json()

# ---------------------------------------------------------------------------
# FastMCP app
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "eve-ng",
    instructions=(
        "Interact with an EVE-NG network emulation server. "
        "Credentials are sourced from environment variables — never include them in tool arguments."
    ),
    host="0.0.0.0",
    port=8001,
)

# ============================================================
# AUTH / SESSION
# ============================================================

def _get_eveng_client() -> EvengClient:
    host = _cfg("EVE_NG_HOST")
    if not host:
        raise RuntimeError("EVE_NG_HOST environment variable is not set")
    proto = _cfg("EVE_NG_PROTOCOL", "http")
    port = int(_cfg("EVE_NG_PORT", "80"))
    user = _cfg("EVE_NG_USERNAME", "admin")
    password = _cfg("EVE_NG_PASSWORD", "eve")
    client = EvengClient(host, port=port, protocol=proto, ssl_verify=_ssl_verify(), username=user, password=password)
    return client

@mcp.tool(description="Test connectivity and authentication against the EVE-NG server.")
async def eveng_check_auth() -> str:
    log.debug(f"Executing tool: eveng_check_auth")
    try:
        data = await _api("GET", "/status")
        result = json.dumps(data, indent=2)
        log.debug(f"Tool eveng_check_auth output: {result}")
        return result
    except Exception as e:
        log.error(f"Error checking auth: {e}\n{traceback.format_exc()}")
        result = f"Error checking auth: {e}"
        log.debug(f"Tool eveng_check_auth output: {result}")
        return result


@mcp.tool(description="Return EVE-NG server status and version information.")
async def eveng_server_status() -> str:
    log.debug(f"Executing tool: eveng_server_status")
    try:
        data = await _api("GET", "/status")
        result = json.dumps(data, indent=2)
        log.debug(f"Tool eveng_server_status output: {result}")
        return result
    except Exception as e:
        log.error(f"Error getting server status: {e}\n{traceback.format_exc()}")
        result = f"Error getting server status: {e}"
        log.debug(f"Tool eveng_server_status output: {result}")
        return result

# ============================================================

@mcp.tool(description="Set an environment variable for the EVE-NG MCP server (e.g. EVE_NG_HOST, EVE_NG_USERNAME, EVE_NG_PASSWORD).")
async def eveng_set_env_var(key: str, value: str) -> str:
    log.debug(f"Executing tool: eveng_set_env_var with args: key={key}, value={value}")
    import os
    os.environ[key] = value
    
    # Reset httpx cache to pick up new creds on next use
    global _session
    async with _session_lock:
        if _session and not _session.is_closed:
            await _session.aclose()
        _session = None
        
    result = f"Environment variable {key} set to {value} successfully. Client will re-initialize on next use."
    log.debug(f"Tool eveng_set_env_var output: {result}")
    return result

# LABS
# ============================================================

@mcp.tool(description="List all labs available to the authenticated user, optionally filtered by folder path.")
async def eveng_list_labs(folder: str = "/") -> str:
    """
    List labs in a folder.

    Args:
        folder: EVE-NG folder path to list (default: root "/").
    """
    log.debug(f"Executing tool: eveng_list_labs with args: folder={folder}")
    try:
        encoded = _encode_path(folder)
        data = await _api("GET", f"/folders{encoded}")
        labs = data.get("data", {}).get("labs", [])
        if not labs:
            result = "No labs found in the specified folder."
            log.debug(f"Tool eveng_list_labs output: {result}")
            return result
        lines = [f"Labs in '{folder}':"]
        for lab in labs:
            name = lab.get("file", "unknown")
            lines.append(f"  • {name}")
        result = "\n".join(lines)
        log.debug(f"Tool eveng_list_labs output: {result}")
        return result
    except Exception as e:
        log.error(f"Error listing labs: {e}\n{traceback.format_exc()}")
        result = f"Error listing labs: {e}"
        log.debug(f"Tool eveng_list_labs output: {result}")
        return result


@mcp.tool(description="Describe a lab: return its metadata (author, description, version).")
async def eveng_describe_lab(lab_path: str) -> str:
    """
    Get lab metadata.

    Args:
        lab_path: Full lab path, e.g. '/MyLab.unl'
    """
    log.debug(f"Executing tool: eveng_describe_lab with args: lab_path={lab_path}")
    try:
        encoded = _encode_path(lab_path)
        data = await _api("GET", f"/labs{encoded}")
        result = json.dumps(data.get("data", data), indent=2)
        log.debug(f"Tool eveng_describe_lab output: {result}")
        return result
    except Exception as e:
        log.error(f"Error describing lab: {e}\n{traceback.format_exc()}")
        result = f"Error describing lab: {e}"
        log.debug(f"Tool eveng_describe_lab output: {result}")
        return result


@mcp.tool(description="Create a new lab in EVE-NG.")
def eveng_create_lab(name: str, description: str = "", path: str = "/") -> str:
    """
    Create a new lab.

    Args:
        name: Name of the lab.
        description: Description of the lab.
        path: Path where to create the lab (default: "/").
    """
    log.debug(f"Executing tool: eveng_create_lab with args: name={name}, description={description}, path={path}")
    client = _get_eveng_client()
    try:
        resp = client.create_lab(name=name, description=description, path=path)
        result = json.dumps(resp, indent=2)
        log.debug(f"Tool eveng_create_lab output: {result}")
        return result
    except Exception as e:
        log.error(f"Error creating lab: {e}\n{traceback.format_exc()}")
        result = f"Error creating lab: {str(e)}"
        log.debug(f"Tool eveng_create_lab output: {result}")
        return result
    finally:
        client.logout()


@mcp.tool(description="List all networks (bridges, clouds, etc.) configured inside a lab.")
async def eveng_list_lab_networks(lab_path: str) -> str:
    """
    List lab networks.

    Args:
        lab_path: Full lab path, e.g. '/MyLab.unl'
    """
    log.debug(f"Executing tool: eveng_list_lab_networks with args: lab_path={lab_path}")
    try:
        encoded = _encode_path(lab_path)
        data = await _api("GET", f"/labs{encoded}/networks")
        networks = data.get("data", {})
        if not networks:
            result = "No networks found in this lab."
            log.debug(f"Tool eveng_list_lab_networks output: {result}")
            return result
        lines = [f"Networks in '{lab_path}':"]
        for nid, net in networks.items():
            lines.append(f"  [{nid}] {net.get('name','?')}  type={net.get('type','?')}")
        result = "\n".join(lines)
        log.debug(f"Tool eveng_list_lab_networks output: {result}")
        return result
    except Exception as e:
        log.error(f"Error listing lab networks: {e}\n{traceback.format_exc()}")
        result = f"Error listing lab networks: {e}"
        log.debug(f"Tool eveng_list_lab_networks output: {result}")
        return result


@mcp.tool(description="Add a network (cloud/bridge) to a lab in EVE-NG.")
def eveng_add_network(lab_path: str, name: str, network_type: str = "bridge", visibility: int = 1) -> str:
    """
    Add a network to a lab.

    Args:
        lab_path: Full path to the lab (e.g., '/MyLab.unl').
        name: Name of the network.
        network_type: Type of network ('bridge', 'pnet1', etc.).
        visibility: Network visibility (1 for visible, 0 for hidden).
    """
    log.debug(f"Executing tool: eveng_add_network with args: lab_path={lab_path}, name={name}, network_type={network_type}, visibility={visibility}")
    client = _get_eveng_client()
    try:
        resp = client.add_lab_network(path=lab_path, name=name, network_type=network_type, visibility=visibility)
        result = json.dumps(resp, indent=2)
        log.debug(f"Tool eveng_add_network output: {result}")
        return result
    except Exception as e:
        log.error(f"Error adding network: {e}\n{traceback.format_exc()}")
        result = f"Error adding network: {str(e)}"
        log.debug(f"Tool eveng_add_network output: {result}")
        return result
    finally:
        client.logout()

# ============================================================
# NODES
# ============================================================

@mcp.tool(description="List all nodes in an EVE-NG lab with their status and telnet console URLs.")
async def eveng_list_nodes(lab_path: str) -> str:
    """
    List nodes in a lab.

    Args:
        lab_path: Full lab path, e.g. '/MyLab.unl'
    """
    log.debug(f"Executing tool: eveng_list_nodes with args: lab_path={lab_path}")
    try:
        encoded = _encode_path(lab_path)
        data = await _api("GET", f"/labs{encoded}/nodes")
        nodes = data.get("data", {})
        if not nodes:
            result = "No nodes found in this lab."
            log.debug(f"Tool eveng_list_nodes output: {result}")
            return result

        host = _cfg("EVE_NG_HOST")
        lines = [f"Nodes in '{lab_path}':"]
        for nid, node in nodes.items():
            name         = node.get("name", f"node-{nid}")
            status       = node.get("status", "?")
            node_url     = node.get("url", "")          # e.g. "telnet://127.0.0.1:32769"
            status_label = {0: "stopped", 2: "running"}.get(status, str(status))
            # Replace the embedded host with the configured EVE_NG_HOST so it works
            # when EVE-NG reports 127.0.0.1 in the url field
            if node_url:
                parts = node_url.rsplit(":", 1)
                console_str = f"telnet://{host}:{parts[-1]}" if len(parts) == 2 else node_url
            else:
                console_str = "N/A"
            lines.append(f"  [{nid}] {name:<20} status={status_label:<8} console={console_str}")
        result = "\n".join(lines)
        log.debug(f"Tool eveng_list_nodes output: {result}")
        return result
    except Exception as e:
        log.error(f"Error listing nodes: {e}\n{traceback.format_exc()}")
        result = f"Error listing nodes: {e}"
        log.debug(f"Tool eveng_list_nodes output: {result}")
        return result


@mcp.tool(description="Get detailed information about a single node in a lab.")
async def eveng_get_node(lab_path: str, node_id: str) -> str:
    """
    Get node details.

    Args:
        lab_path: Full lab path, e.g. '/MyLab.unl'
        node_id:  Node ID number (as a string)
    """
    log.debug(f"Executing tool: eveng_get_node with args: lab_path={lab_path}, node_id={node_id}")
    try:
        encoded = _encode_path(lab_path)
        data = await _api("GET", f"/labs{encoded}/nodes/{node_id}")
        result = json.dumps(data.get("data", data), indent=2)
        log.debug(f"Tool eveng_get_node output: {result}")
        return result
    except Exception as e:
        log.error(f"Error getting node details: {e}\n{traceback.format_exc()}")
        result = f"Error getting node details: {e}"
        log.debug(f"Tool eveng_get_node output: {result}")
        return result


@mcp.tool(description="Add a node to a lab in EVE-NG.")
def eveng_add_node(lab_path: str, name: str, template: str, image: str, left: int = 100, top: int = 100) -> str:
    """
    Add a node to an existing lab.

    Args:
        lab_path: Full path to the lab (e.g., '/MyLab.unl').
        name: Name of the node.
        template: Node template (e.g., 'veos', 'vios').
        image: Node image version.
        left: X coordinate in the topology.
        top: Y coordinate in the topology.
    """
    log.debug(f"Executing tool: eveng_add_node with args: lab_path={lab_path}, name={name}, template={template}, image={image}, left={left}, top={top}")
    client = _get_eveng_client()
    try:
        resp = client.add_node(path=lab_path, name=name, template=template, image=image, left=left, top=top)
        result = json.dumps(resp, indent=2)
        log.debug(f"Tool eveng_add_node output: {result}")
        return result
    except Exception as e:
        log.error(f"Error adding node: {e}\n{traceback.format_exc()}")
        result = f"Error adding node: {str(e)}"
        log.debug(f"Tool eveng_add_node output: {result}")
        return result
    finally:
        client.logout()

@mcp.tool(description="Build an EVE-NG topology from a YAML definition file.")
def eveng_build_topology_from_yaml(yaml_path: str, template_dir: str = "") -> str:
    """
    Deploy a complete lab topology from a YAML file.

    Args:
        yaml_path: Absolute path to the YAML topology definition.
        template_dir: Path to directory containing Jinja2 configuration templates (optional).
    """
    log.debug(f"Executing tool: eveng_build_topology_from_yaml with args: yaml_path={yaml_path}, template_dir={template_dir}")
    if not os.path.exists(yaml_path):
        result = f"Error: YAML file not found at {yaml_path}"
        log.debug(f"Tool eveng_build_topology_from_yaml output: {result}")
        return result
        
    client = _get_eveng_client()
    try:
        t_dir = template_dir if template_dir else "templates"
        builder = TopologyBuilder(client, template_dir=t_dir)
        builder.build(yaml_path)
        
        result = f"Successfully built topology from {yaml_path}"
        log.debug(f"Tool eveng_build_topology_from_yaml output: {result}")
        return result
    except Exception as e:
        log.error(f"Error building topology: {e}\n{traceback.format_exc()}")
        result = f"Error building topology: {str(e)}"
        log.debug(f"Tool eveng_build_topology_from_yaml output: {result}")
        return result
    finally:
        client.logout()


@mcp.tool(description="Export an EVE-NG lab topology to a YAML string, including resolved node-to-node connections.")
async def eveng_export_topology_yaml(lab_path: str) -> str:
    """
    Export lab topology (metadata, networks, nodes with interfaces, and resolved links) to YAML.

    Uses the native EVE-NG /topology endpoint to retrieve properly resolved peer-to-peer
    connections between nodes. Interfaces are returned by the API as a list and are
    handled correctly here.

    Args:
        lab_path: Full lab path, e.g. '/MyLab.unl'
    """
    log.debug(f"Executing tool: eveng_export_topology_yaml with args: lab_path={lab_path}")
    try:
        encoded = _encode_path(lab_path)

        # Fetch lab metadata
        lab_data = await _api("GET", f"/labs{encoded}")
        lab_details = lab_data.get("data", {})

        # Fetch networks (API returns {} for none, dict of id->net otherwise)
        net_data = await _api("GET", f"/labs{encoded}/networks")
        networks_raw = net_data.get("data", {})
        networks = []
        if isinstance(networks_raw, dict):
            for net_id, net in networks_raw.items():
                networks.append({
                    "id": net_id,
                    "name": net.get("name"),
                    "type": net.get("type"),
                    "visibility": net.get("visibility"),
                })

        # Fetch nodes (API returns {} for none, dict of id->node otherwise)
        nodes_data = await _api("GET", f"/labs{encoded}/nodes")
        nodes_raw = nodes_data.get("data", {})
        nodes = []
        if isinstance(nodes_raw, dict):
            for nid, node in nodes_raw.items():
                node_info = {
                    "id": nid,
                    "name": node.get("name"),
                    "template": node.get("template"),
                    "image": node.get("image"),
                    "ethernet": [],
                    "serial": [],
                }
                interfaces_resp = await _api("GET", f"/labs{encoded}/nodes/{nid}/interfaces")
                iface_data = interfaces_resp.get("data", {})

                # Ethernet and serial interfaces are returned as LISTS by the EVE-NG API.
                # Each element is a dict with keys: name, network_id, etc.
                for media in ("ethernet", "serial"):
                    iface_list = iface_data.get(media, [])
                    if not isinstance(iface_list, list):
                        iface_list = list(iface_list.values()) if isinstance(iface_list, dict) else []
                    for idx, iface in enumerate(iface_list):
                        node_info[media].append({
                            "index": idx,
                            "name": iface.get("name"),
                            "network_id": iface.get("network_id"),
                        })

                nodes.append(node_info)

        # Use the native /topology endpoint to get resolved peer-to-peer connections.
        # This is the authoritative way to resolve which node interfaces are connected
        # to which other node interfaces (the raw interface data only exposes network_id).
        topo_data = await _api("GET", f"/labs{encoded}/topology")
        topology_links = topo_data.get("data", [])
        links = []
        if isinstance(topology_links, list):
            for link in topology_links:
                links.append({
                    "type": link.get("type"),
                    "source": link.get("source"),
                    "source_type": link.get("source_type"),
                    "source_label": link.get("source_label"),
                    "destination": link.get("destination"),
                    "destination_type": link.get("destination_type"),
                    "destination_label": link.get("destination_label"),
                })

        topology = {
            "lab": {
                "name": lab_details.get("name"),
                "description": lab_details.get("description"),
                "version": lab_details.get("version"),
            },
            "networks": networks,
            "nodes": nodes,
            "links": links,
        }

        result = yaml.dump(topology, sort_keys=False)
        log.debug(f"Tool eveng_export_topology_yaml output: {result}")
        return result
    except Exception as e:
        log.error(f"Error exporting topology: {e}\n{traceback.format_exc()}")
        result = f"Error exporting topology: {e}"
        log.debug(f"Tool eveng_export_topology_yaml output: {result}")
        return result


@mcp.tool(description="Return the telnet console URL for a named node in a lab.")
async def eveng_get_node_console_url(lab_path: str, node_name: str) -> str:
    """
    Resolve the telnet console URL for a node by name.

    Args:
        lab_path:  Full lab path, e.g. '/MyLab.unl'
        node_name: Node name as shown in the lab (case-insensitive match)
    """
    log.debug(f"Executing tool: eveng_get_node_console_url with args: lab_path={lab_path}, node_name={node_name}")
    try:
        encoded = _encode_path(lab_path)
        data = await _api("GET", f"/labs{encoded}/nodes")
        nodes = data.get("data", {})
        host = _cfg("EVE_NG_HOST")

        for nid, node in nodes.items():
            if node.get("name", "").lower() == node_name.lower():
                node_url = node.get("url", "")
                if not node_url:
                    result = f"Node '{node_name}' has no console URL assigned (is it running?)."
                    log.debug(f"Tool eveng_get_node_console_url output: {result}")
                    return result
                # Rewrite embedded host with configured EVE_NG_HOST
                parts = node_url.rsplit(":", 1)
                result = f"telnet://{host}:{parts[-1]}" if len(parts) == 2 else node_url
                log.debug(f"Tool eveng_get_node_console_url output: {result}")
                return result

        result = f"Node '{node_name}' not found in '{lab_path}'."
        log.debug(f"Tool eveng_get_node_console_url output: {result}")
        return result
    except Exception as e:
        log.error(f"Error getting node console URL: {e}\n{traceback.format_exc()}")
        result = f"Error getting node console URL: {e}"
        log.debug(f"Tool eveng_get_node_console_url output: {result}")
        return result


@mcp.tool(description="Start one or all nodes in an EVE-NG lab.")
async def eveng_start_nodes(lab_path: str, node_id: Optional[str] = None) -> str:
    """
    Start lab nodes.

    Args:
        lab_path: Full lab path, e.g. '/MyLab.unl'
        node_id:  Specific node ID to start, or omit to start all nodes.
    """
    log.debug(f"Executing tool: eveng_start_nodes with args: lab_path={lab_path}, node_id={node_id}")
    try:
        encoded = _encode_path(lab_path)
        path = f"/labs{encoded}/nodes/{node_id}/start" if node_id else f"/labs{encoded}/nodes/start"
        data = await _api("GET", path)
        result = data.get("message", json.dumps(data))
        log.debug(f"Tool eveng_start_nodes output: {result}")
        return result
    except Exception as e:
        log.error(f"Error starting nodes: {e}\n{traceback.format_exc()}")
        result = f"Error starting nodes: {e}"
        log.debug(f"Tool eveng_start_nodes output: {result}")
        return result


@mcp.tool(description="Stop one or all nodes in an EVE-NG lab.")
async def eveng_stop_nodes(lab_path: str, node_id: Optional[str] = None) -> str:
    """
    Stop lab nodes.

    Args:
        lab_path: Full lab path, e.g. '/MyLab.unl'
        node_id:  Specific node ID to stop, or omit to stop all nodes.
    """
    log.debug(f"Executing tool: eveng_stop_nodes with args: lab_path={lab_path}, node_id={node_id}")
    try:
        encoded = _encode_path(lab_path)
        path = f"/labs{encoded}/nodes/{node_id}/stop" if node_id else f"/labs{encoded}/nodes/stop"
        data = await _api("GET", path)
        result = data.get("message", json.dumps(data))
        log.debug(f"Tool eveng_stop_nodes output: {result}")
        return result
    except Exception as e:
        log.error(f"Error stopping nodes: {e}\n{traceback.format_exc()}")
        result = f"Error stopping nodes: {e}"
        log.debug(f"Tool eveng_stop_nodes output: {result}")
        return result


@mcp.tool(description="Wipe (reset to factory defaults) one or all nodes in an EVE-NG lab.")
async def eveng_wipe_nodes(lab_path: str, node_id: Optional[str] = None) -> str:
    """
    Wipe node state (startup config and NVRAM).

    Args:
        lab_path: Full lab path, e.g. '/MyLab.unl'
        node_id:  Specific node ID to wipe, or omit to wipe all.
    """
    log.debug(f"Executing tool: eveng_wipe_nodes with args: lab_path={lab_path}, node_id={node_id}")
    try:
        encoded = _encode_path(lab_path)
        path = f"/labs{encoded}/nodes/{node_id}/wipe" if node_id else f"/labs{encoded}/nodes/wipe"
        data = await _api("GET", path)
        result = data.get("message", json.dumps(data))
        log.debug(f"Tool eveng_wipe_nodes output: {result}")
        return result
    except Exception as e:
        log.error(f"Error wiping nodes: {e}\n{traceback.format_exc()}")
        result = f"Error wiping nodes: {e}"
        log.debug(f"Tool eveng_wipe_nodes output: {result}")
        return result

# ============================================================
# NODE CONNECTIONS / TOPOLOGY
# ============================================================

@mcp.tool(description="List all inter-node connections (links) in a lab, showing which interfaces are wired together.")
async def eveng_list_lab_links(lab_path: str) -> str:
    """
    List all topology links in a lab as resolved peer-to-peer connections.

    Uses the native EVE-NG /topology endpoint which directly returns resolved
    source-to-destination node/interface pairs, including cloud/network connections.
    This avoids the need to manually correlate interface network_ids across nodes.

    Args:
        lab_path: Full lab path, e.g. '/MyLab.unl'
    """
    log.debug(f"Executing tool: eveng_list_lab_links with args: lab_path={lab_path}")
    try:
        encoded = _encode_path(lab_path)

        # Use the dedicated /topology endpoint — returns fully resolved connections
        # in the form {source, source_label, destination, destination_label, type}.
        # This is the same endpoint used by the evengsdk `get_lab_topology()` method.
        topo_data = await _api("GET", f"/labs{encoded}/topology")
        topology_links = topo_data.get("data", [])

        if not topology_links:
            result = f"No topology links found in '{lab_path}'. Is the lab empty or no interfaces wired?"
            log.debug(f"Tool eveng_list_lab_links output: {result}")
            return result

        lines = [f"Topology links in '{lab_path}':"]
        for link in topology_links:
            src       = link.get("source", "?")
            src_label = link.get("source_label", "?")
            src_type  = link.get("source_type", "")
            dst       = link.get("destination", "?")
            dst_label = link.get("destination_label", "?")
            dst_type  = link.get("destination_type", "")
            ltype     = link.get("type", "")

            src_str = f"{src} [{src_label}]" if src_label else src
            dst_str = f"{dst} [{dst_label}]" if dst_label else dst
            type_str = f" ({ltype})" if ltype else ""
            lines.append(f"  {src_str}  <-->  {dst_str}{type_str}")

        result = "\n".join(lines)
        log.debug(f"Tool eveng_list_lab_links output: {result}")
        return result
    except Exception as e:
        log.error(f"Error listing lab links: {e}\n{traceback.format_exc()}")
        result = f"Error listing lab links: {e}"
        log.debug(f"Tool eveng_list_lab_links output: {result}")
        return result


@mcp.tool(description="List all interfaces on a specific node and show which network each is connected to.")
async def eveng_list_node_interfaces(lab_path: str, node_id: str) -> str:
    """
    List interfaces for a node.

    Args:
        lab_path: Full lab path, e.g. '/MyLab.unl'
        node_id:  Node ID number (as a string)
    """
    log.debug(f"Executing tool: eveng_list_node_interfaces with args: lab_path={lab_path}, node_id={node_id}")
    try:
        encoded = _encode_path(lab_path)
        data = await _api("GET", f"/labs{encoded}/nodes/{node_id}/interfaces")
        result = json.dumps(data.get("data", data), indent=2)
        log.debug(f"Tool eveng_list_node_interfaces output: {result}")
        return result
    except Exception as e:
        log.error(f"Error listing node interfaces: {e}\n{traceback.format_exc()}")
        result = f"Error listing node interfaces: {e}"
        log.debug(f"Tool eveng_list_node_interfaces output: {result}")
        return result


@mcp.tool(
    description=(
        "Connect two node interfaces together inside a lab. "
        "Both src_node_id/src_if_id and dst_node_id/dst_if_id must be numeric IDs. "
        "Creates a new point-to-point network between the two interfaces."
    )
)
async def eveng_connect_nodes(
    lab_path: str,
    src_node_id: str,
    src_if_id: str,
    dst_node_id: str,
    dst_if_id: str,
) -> str:
    """
    Wire two node interfaces together (rack-and-stack).

    Args:
        lab_path:    Full lab path, e.g. '/MyLab.unl'
        src_node_id: Source node ID
        src_if_id:   Source interface ID (index)
        dst_node_id: Destination node ID
        dst_if_id:   Destination interface ID (index)
    """
    log.debug(f"Executing tool: eveng_connect_nodes with args: lab_path={lab_path}, src_node_id={src_node_id}, src_if_id={src_if_id}, dst_node_id={dst_node_id}, dst_if_id={dst_if_id}")
    try:
        encoded = _encode_path(lab_path)

        # Create a bridge network to connect the two nodes
        net_payload = {"type": "bridge", "name": f"link-n{src_node_id}i{src_if_id}-n{dst_node_id}i{dst_if_id}"}
        net_resp = await _api("POST", f"/labs{encoded}/networks", json=net_payload)
        net_id = net_resp.get("data", {}).get("id")
        if not net_id:
            result = f"Failed to create bridge network: {net_resp}"
            log.debug(f"Tool eveng_connect_nodes output: {result}")
            return result

        # Connect both ends to the bridge
        for node_id, if_id in [(src_node_id, src_if_id), (dst_node_id, dst_if_id)]:
            conn_payload = {"id": int(if_id), "network_id": int(net_id)}
            await _api("PUT", f"/labs{encoded}/nodes/{node_id}/interfaces", json=conn_payload)

        result = (
            f"Connected node {src_node_id} if {src_if_id} "
            f"<--> node {dst_node_id} if {dst_if_id} "
            f"via network_id={net_id}"
        )
        log.debug(f"Tool eveng_connect_nodes output: {result}")
        return result
    except Exception as e:
        log.error(f"Error connecting nodes: {e}\n{traceback.format_exc()}")
        result = f"Error connecting nodes: {e}"
        log.debug(f"Tool eveng_connect_nodes output: {result}")
        return result

# ============================================================
# TELNET HELPER (returns connection string for LLM / agent)
# ============================================================

@mcp.tool(
    description=(
        "Return a ready-to-use telnet command string to open a console session "
        "to a named node. The agent or operator can run this command directly in a terminal."
    )
)
async def eveng_telnet_command(lab_path: str, node_name: str) -> str:
    """
    Generate the telnet command for a node console.

    Args:
        lab_path:  Full lab path, e.g. '/MyLab.unl'
        node_name: Node name (case-insensitive)

    Returns:
        A string like: telnet 192.168.1.1 32769
    """
    log.debug(f"Executing tool: eveng_telnet_command with args: lab_path={lab_path}, node_name={node_name}")
    try:
        encoded = _encode_path(lab_path)
        data = await _api("GET", f"/labs{encoded}/nodes")
        nodes = data.get("data", {})
        host = _cfg("EVE_NG_HOST")

        for nid, node in nodes.items():
            if node.get("name", "").lower() == node_name.lower():
                status   = node.get("status", 0)
                node_url = node.get("url", "")
                parts    = node_url.rsplit(":", 1) if node_url else []
                port     = parts[-1] if len(parts) == 2 else None
                if status == 0:
                    port_hint = port or "?"
                    result = (
                        f"Node '{node_name}' is currently stopped. "
                        f"Start it first with eveng_start_nodes, then connect via: telnet {host} {port_hint}"
                    )
                    log.debug(f"Tool eveng_telnet_command output: {result}")
                    return result
                if not port:
                    result = f"Node '{node_name}' has no console port assigned."
                    log.debug(f"Tool eveng_telnet_command output: {result}")
                    return result
                result = f"telnet {host} {port}"
                log.debug(f"Tool eveng_telnet_command output: {result}")
                return result

        result = f"Node '{node_name}' not found in '{lab_path}'."
        log.debug(f"Tool eveng_telnet_command output: {result}")
        return result
    except Exception as e:
        log.error(f"Error generating telnet command: {e}\n{traceback.format_exc()}")
        result = f"Error generating telnet command: {e}"
        log.debug(f"Tool eveng_telnet_command output: {result}")
        return result


# ============================================================
# SNAPSHOTS  (inspired by TheNetworker/eve-ng-tools)
# ============================================================

@mcp.tool(description="List snapshots for a specific node in a lab.")
async def eveng_list_snapshots(lab_path: str, node_id: str) -> str:
    """
    List node snapshots.

    Args:
        lab_path: Full lab path, e.g. '/MyLab.unl'
        node_id:  Node ID number (as a string)
    """
    log.debug(f"Executing tool: eveng_list_snapshots with args: lab_path={lab_path}, node_id={node_id}")
    try:
        encoded = _encode_path(lab_path)
        data = await _api("GET", f"/labs{encoded}/nodes/{node_id}/export")
        result = json.dumps(data.get("data", data), indent=2)
        log.debug(f"Tool eveng_list_snapshots output: {result}")
        return result
    except Exception as e:
        log.error(f"Error listing snapshots: {e}\n{traceback.format_exc()}")
        result = f"Error listing snapshots: {e}"
        log.debug(f"Tool eveng_list_snapshots output: {result}")
        return result


# ============================================================
# SERVER INFO
# ============================================================

@mcp.tool(description="List all available node templates (image types) on the EVE-NG server.")
async def eveng_list_templates() -> str:
    """List all node templates available on the EVE-NG server."""
    log.debug(f"Executing tool: eveng_list_templates")
    try:
        data = await _api("GET", "/list/templates/")
        templates = data.get("data", {})
        if not templates:
            result = "No templates found."
            log.debug(f"Tool eveng_list_templates output: {result}")
            return result
        lines = ["Available node templates:"]
        for name, info in sorted(templates.items()):
            desc = info.get("description", "")
            lines.append(f"  • {name:<30} {desc}")
        result = "\n".join(lines)
        log.debug(f"Tool eveng_list_templates output: {result}")
        return result
    except Exception as e:
        log.error(f"Error listing templates: {e}\n{traceback.format_exc()}")
        result = f"Error listing templates: {e}"
        log.debug(f"Tool eveng_list_templates output: {result}")
        return result


@mcp.tool(description="List all users on the EVE-NG server (admin only).")
async def eveng_list_users() -> str:
    log.debug(f"Executing tool: eveng_list_users")
    try:
        data = await _api("GET", "/users/")
        result = json.dumps(data.get("data", data), indent=2)
        log.debug(f"Tool eveng_list_users output: {result}")
        return result
    except Exception as e:
        log.error(f"Error listing users: {e}\n{traceback.format_exc()}")
        result = f"Error listing users: {e}"
        log.debug(f"Tool eveng_list_users output: {result}")
        return result


# ============================================================
# TELNET CONSOLE — programmatic access via asyncio streams
# ============================================================

async def _resolve_console_port(lab_path: str, node_name: str) -> tuple[str, int]:
    """
    Internal helper: return (host, port) for a named node.
    Raises ValueError if node not found or has no console URL.
    """
    encoded = _encode_path(lab_path)
    data    = await _api("GET", f"/labs{encoded}/nodes")
    nodes   = data.get("data", {})
    host    = _cfg("EVE_NG_HOST")

    for _, node in nodes.items():
        if node.get("name", "").lower() == node_name.lower():
            node_url = node.get("url", "")
            if not node_url:
                raise ValueError(
                    f"Node '{node_name}' has no console URL — is it running?"
                )
            parts = node_url.rsplit(":", 1)
            if len(parts) != 2:
                raise ValueError(f"Cannot parse port from URL: {node_url}")
            return host, int(parts[-1])

    raise ValueError(f"Node '{node_name}' not found in '{lab_path}'.")


async def _telnet_exchange(
    host: str,
    port: int,
    commands: list[str],
    prompt_pattern: str = r"[>#\$]",
    connect_timeout: float = 15.0,
    command_timeout: float = 10.0,
    inter_command_delay: float = 0.3,
    encoding: str = "utf-8",
) -> str:
    """
    Core telnet engine using telnetlib3.

    telnetlib3 handles all IAC option negotiation automatically, so the
    output we receive is already clean device text.

    Returns the full session transcript.
    """
    import re
    import telnetlib3

    prompt_re  = re.compile(prompt_pattern)
    transcript = []

    async def read_until_prompt(reader, timeout: float) -> str:
        """Accumulate output until prompt regex matches or timeout expires."""
        buf = ""
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=min(remaining, 0.5))
                if not chunk:   # EOF
                    break
                buf += chunk if isinstance(chunk, str) else chunk.decode(encoding, errors="replace")
                if prompt_re.search(buf):
                    break
            except asyncio.TimeoutError:
                if prompt_re.search(buf):
                    break
                if asyncio.get_event_loop().time() >= deadline:
                    break
        return buf

    reader, writer = await asyncio.wait_for(
        telnetlib3.open_connection(host, port, encoding=encoding),
        timeout=connect_timeout,
    )
    log.info("telnetlib3 connected to %s:%d", host, port)

    try:
        # Drain initial banner / login prompt
        banner = await read_until_prompt(reader, connect_timeout)
        transcript.append(f"[connected]\n{banner}")
        log.debug("Banner: %r", banner)

        for cmd in commands:
            await asyncio.sleep(inter_command_delay)
            writer.write(cmd + "\r\n")
            log.debug("Sent: %r", cmd)

            output = await read_until_prompt(reader, command_timeout)
            transcript.append(f"# {cmd}\n{output}")
            log.debug("Output for %r: %r", cmd, output)

    finally:
        writer.close()

    return "\n".join(transcript)


@mcp.tool(
    description=(
        "Send one or more CLI commands to a node's console via telnet and return the output. "
        "The node must be running. Commands are sent in order; output from each is captured "
        "and returned in a single transcript. Ideal for initial device configuration."
    )
)
async def eveng_send_commands(
    lab_path: str,
    node_name: str,
    commands: list[str],
    prompt_pattern: str = r"[>#\$%]",
    connect_timeout: float = 15.0,
    command_timeout: float = 10.0,
) -> str:
    """
    Send CLI commands to a node via telnet console and return output.

    Args:
        lab_path:        Full lab path, e.g. '/test_lab.unl'
        node_name:       Node name as shown in EVE-NG (case-insensitive)
        commands:        Ordered list of CLI commands to send
        prompt_pattern:  Regex that matches the device prompt (default covers >, #, $, %)
        connect_timeout: Seconds to wait for initial connection and banner
        command_timeout: Seconds to wait for output after each command
    """
    log.debug(f"Executing tool: eveng_send_commands with args: lab_path={lab_path}, node_name={node_name}, commands={commands}, prompt_pattern={prompt_pattern}, connect_timeout={connect_timeout}, command_timeout={command_timeout}")
    try:
        host, port = await _resolve_console_port(lab_path, node_name)
    except ValueError as e:
        log.error(f"Value Error in eveng_send_commands: {e}\n{traceback.format_exc()}")
        result = f"Error: {e}"
        log.error(f"Tool eveng_send_commands output: {result}")
        return result

    log.info("Connecting to %s console at %s:%d", node_name, host, port)
    try:
        transcript = await _telnet_exchange(
            host=host,
            port=port,
            commands=commands,
            prompt_pattern=prompt_pattern,
            connect_timeout=connect_timeout,
            command_timeout=command_timeout,
        )
        log.debug(f"Tool eveng_send_commands output: {transcript}")
        return transcript
    except asyncio.TimeoutError:
        result = (
            f"Timeout connecting to {node_name} at {host}:{port}. "
            f"Verify the node is running and fully booted."
        )
        log.error(f"Tool eveng_send_commands output: {result}")
        return result
    except OSError as e:
        result = f"Connection error to {node_name} at {host}:{port} — {e}"
        log.error(f"Tool eveng_send_commands output: {result}")
        return result


@mcp.tool(
    description=(
        "Push an initial configuration to a node via telnet console. "
        "Handles the enable → configure terminal sequence automatically for Cisco-style devices. "
        "Specify config_lines as the list of configuration commands to apply."
    )
)
async def eveng_push_initial_config(
    lab_path: str,
    node_name: str,
    config_lines: list[str],
    enable_password: str = "",
    device_type: str = "cisco",
    connect_timeout: float = 20.0,
    command_timeout: float = 10.0,
) -> str:
    """
    Push initial configuration to a node via telnet.

    Builds the full command sequence automatically:
      - Cisco/Juniper: handles enable, configure terminal, config lines, end/commit
      - Generic: just sends config_lines as-is

    Args:
        lab_path:         Full lab path, e.g. '/test_lab.unl'
        node_name:        Node name (case-insensitive)
        config_lines:     Configuration commands to apply (without enable/conf t wrapper)
        enable_password:  Enable password if required (leave empty if not needed)
        device_type:      'cisco' | 'juniper' | 'generic'
        connect_timeout:  Seconds to wait for connection
        command_timeout:  Seconds to wait per command
    """
    log.debug(f"Executing tool: eveng_push_initial_config with args: lab_path={lab_path}, node_name={node_name}, config_lines={len(config_lines)} lines, device_type={device_type}")
    try:
        host, port = await _resolve_console_port(lab_path, node_name)
    except ValueError as e:
        log.error(f"Value Error in eveng_push_initial_config: {e}\n{traceback.format_exc()}")
        result = f"Error: {e}"
        log.debug(f"Tool eveng_push_initial_config output: {result}")
        return result

    dtype = device_type.lower()

    if dtype == "cisco":
        commands = []
        commands.append("\r")                     # wake the console
        commands.append("enable")
        if enable_password:
            commands.append(enable_password)
        commands.append("configure terminal")
        commands.extend(config_lines)
        commands.append("end")
        commands.append("write memory")
        prompt = r"[>#]"

    elif dtype == "juniper":
        commands = []
        commands.append("\r")
        commands.append("configure")
        commands.extend(config_lines)
        commands.append("commit and-quit")
        prompt = r"[>%#]"

    else:  # generic
        commands = list(config_lines)
        prompt = r"[>#\$%]"

    log.info(
        "Pushing %d config lines to %s (%s) at %s:%d",
        len(config_lines), node_name, device_type, host, port,
    )
    try:
        transcript = await _telnet_exchange(
            host=host,
            port=port,
            commands=commands,
            prompt_pattern=prompt,
            connect_timeout=connect_timeout,
            command_timeout=command_timeout,
            inter_command_delay=0.5,   # slower for config mode
        )
        log.debug(f"Tool eveng_push_initial_config output: {transcript}")
        return transcript
    except asyncio.TimeoutError:
        result = (
            f"Timeout connecting to {node_name} at {host}:{port}. "
            f"Verify the node is running and fully booted."
        )
        log.debug(f"Tool eveng_push_initial_config output: {result}")
        return result
    except OSError as e:
        result = f"Connection error to {node_name} at {host}:{port} — {e}"
        log.debug(f"Tool eveng_push_initial_config output: {result}")
        return result


# ============================================================
# Entrypoint
# ============================================================
if __name__ == "__main__":
    try:
        mcp.run(transport="streamable-http")
    except KeyboardInterrupt:
        logging.info("Interrupted by user, Exiting...")
    except Exception as e:
        logging.error(f"MCP server error: {e}\n{traceback.format_exc()}")