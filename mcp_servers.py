# Author: XTofTech
# Date: 2025-06-19

#1. mocks network operations
#2. runs a MCP server exposing those operations as tools
from typing import List
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("network_tools_server")

@mcp.tool()
async def get_switch_management_ip() -> str:
    """Get management IP of a switch"""
    return "Access switch: 10.10.10.254"

@mcp.tool()
async def find_network_interface(device_ip: str) -> str:
    """Find network interfaces"""
    return "interface Gi1/0/1, interface Gi1/0/2, interface Gi1/0/3"

@mcp.tool()
async def ping_device_from_switch(target_ip: str, count: int =5) -> str:
    """Ping a device from a switch"""
    return f"Pinged {target_ip} {count} times failed."

@mcp.tool()
async def get_switch_arp_table(device_ip: str) -> List[str]:
    """Get ARP table from a device"""
    return ["aaaa.bb12.3459 - 10.10.10.2", "cccc.dd34.5678 - 10.10.10.3"]

@mcp.tool()
async def get_switch_mac_table(device_ip: str) -> List[str]:
    """Get MAC address table from a device"""
    return ["aaaa.bb12.3456 - Gi1/0/1", "bbbb.cc23.4567 - Gi1/0/2"]



if __name__ == "__main__":
    mcp.run(transport="streamable-http")