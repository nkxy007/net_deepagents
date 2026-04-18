# Author: XTofTech
# Date: 2025-06-19

#1. mocks network operations
#2. runs a MCP server exposing those operations as tools
from typing import List, Optional, Dict, Any
from mcp.server.fastmcp import FastMCP
import json
from utils.ssh_executor import DeviceSSHSession, DEVICE_PROFILES
import re
import logging
import sys
from tools_helpers.service_now_incidents_helper import ServiceNowIncident
from tools_helpers.service_now_changes_helper import ServiceNowChangeRequest
from creds import SERVICENOW_INSTANCE_URL, SERVICENOW_ACCESS_TOKEN
import subprocess
import tempfile
import os
import contextlib
import io
import asyncio
import traceback
import base64
import aiohttp
import urllib.parse
from urllib.parse import urlparse
from pathlib import Path
import xml.etree.ElementTree as ET
import csv
from datetime import datetime
from tools_helpers.retriever_archiver import ArchiverRetriever
from utils.credentials_helper import get_helper
from utils.atlassian.jira_helper import JiraClient, _load_client
import argparse
import getpass
import time
from enum import Enum
import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator


from net_deepagent_cli.communication.logger import setup_logger, set_log_level

# Initialize credentials and log settings based on CLI arguments
parser = argparse.ArgumentParser(add_help=False)
parser.add_argument('--vault', action='store_true', help='Use credentials from vault file')
parser.add_argument('--log-level', default='INFO', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], help='Set logging level')
args, remaining_argv = parser.parse_known_args()

# Update sys.argv to remove our custom flags so FastMCP doesn't error
sys.argv = [sys.argv[0]] + remaining_argv

# Set log level globally
set_log_level(args.log_level)

# Configure logging using centralized utility
logger = setup_logger("mcp_server")

if args.vault:
    # Prompt for password if we are using the vault
    logger.info("\n--- Vault Access Requested ---")
    password = getpass.getpass("Enter vault decryption password: ")
    get_helper(password=password, use_vault=True)
else:
    # Fallback to creds.py (no vault prompt)
    get_helper(use_vault=False)

mcp = FastMCP("network_tools_server")

# Initialize Retriever Archiver
try:
    archiver = ArchiverRetriever()
    logger.info("Retriever Archiver initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize Retriever Archiver: {e}")
    archiver = None

# Initialize Jira Client
try:
    jira_client = _load_client()
    logger.info("Jira Client initialized successfully")
except Exception as e:
    logger.warning(f"Failed to initialize Jira Client: {e}. Jira tools will not be available.")
    jira_client = None


def log_tool_call_to_csv(tool_name: str, intention: str, **kwargs):
    """Logs tool call details to a CSV file in ~/.net_deepagent/tool_calls_logger.csv"""
    try:
        log_dir = os.path.expanduser("~/.net_deepagent")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "tool_calls_logger.csv")
        
        file_exists = os.path.isfile(log_file)
        with open(log_file, mode='a', newline='') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(['timestamp', 'tool_name', 'arguments', 'intention'])
            
            timestamp = datetime.now().isoformat()
            # Serialize arguments to JSON string to keep it in one CSV cell
            arguments_json = json.dumps(kwargs)
            writer.writerow([timestamp, tool_name, arguments_json, intention])
    except Exception as e:
        logger.error(f"Error logging tool call to CSV: {e}")



@mcp.tool()
async def get_site_info(site_name: str, intention: str) -> str:
    """Get site information or site inventory. This is information about site details, such as devices
    name and IP address as well as site location and other details.
    args:
        site_name (str): name of the site
        intention (str): llm intention to call this tool
    returns:
        str: site information
    """
    logger.debug(f"Executing tool: get_site_info with args: site_name={site_name}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(get_site_info.__name__, intention, site_name=site_name)
    logger.info(f"Getting site info for site: {site_name}")
    try:
        with open("sites.json", "r") as f:
            data = json.load(f)
        for site in data["sites"]:
            if site_name.lower() in site["name"].lower():
                result = json.dumps(site, indent=2)
                logger.debug(f"Tool get_site_info output: {result}")
                return result
        result = f"Site {site_name} not found"
        logger.debug(f"Tool get_site_info output: {result}")
        return result
    except Exception as e:
        logger.error(f"Error retrieving site info: {e}\n{traceback.format_exc()}")
        result = f"Error retrieving site info: {e}"
        logger.debug(f"Tool get_site_info output: {result}")
        return result

@mcp.tool()
async def net_get_devices_management_ip(site_name: str, device_type: str, intention: str) -> str:
    """Get management IP of a network device from a site, uses infor from CMDB, IPAM and NMS to get the info
    args:
        site_name (str): name of the site stripped of anything like office, building, floor etc.
        device_type (str): type of the device (e.g., switch, router)
        intention (str): llm intention to call this tool
    returns:
        str: management IP address of the device
    """
    logger.debug(f"Executing tool: net_get_devices_management_ip with args: site_name={site_name}, device_type={device_type}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(net_get_devices_management_ip.__name__, intention, site_name=site_name, device_type=device_type)
    logger.info(f"Getting management IP for device type {device_type} in site {site_name}")
    try:
        with open("sites.json", "r") as f:
            data = json.load(f)
        for site in data["sites"]:
            if site_name in site["name"]:
                for device in site["devices"]:
                    if device["type"].lower() == device_type.lower():
                        result = device["management_ip"]
                        logger.debug(f"Tool net_get_devices_management_ip output: {result}")
                        return result
        # return only sites names so that AI checks if the site was not mispelled
        # filter sites names from data 
        logger.warning(f"{site_name} is not found in sites container.")
        sites_names = [site["name"] for site in data["sites"]]
        result = f"Device management IP cannot be found, you could have issues wrong site name, try again. Available sites are: {', '.join(sites_names)}"
        logger.debug(f"Tool net_get_devices_management_ip output: {result}")
        return result
    except Exception as e:
        logger.error(f"Error reading sites container: {e}\n{traceback.format_exc()}")
        result = f"Error reading sites container: {e}"
        logger.debug(f"Tool net_get_devices_management_ip output: {result}")
        return result
    result = "Device management IP cannot be found."
    logger.debug(f"Tool net_get_devices_management_ip output: {result}")
    return result

@mcp.tool()
async def net_find_network_interfaces(device_management_ip: str, intention: str) -> str:
    """connect to the device management IP and Find network interfaces
    args:
        device_management_ip (str): management IP of the device
        intention (str): llm intention to call this tool
    """
    logger.debug(f"Executing tool: net_find_network_interfaces with args: device_management_ip={device_management_ip}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(net_find_network_interfaces.__name__, intention, device_management_ip=device_management_ip)
    logger.info(f"Finding network interfaces for device: {device_management_ip}")
    try:
        device = DeviceSSHSession(device_management_ip)
        interfaces_with_ip = device.execute_command("show ip interfaces brief | exclude unassigned")
        interface_physical = device.execute_command("show interface status")
        result = interfaces_with_ip + "\n" + interface_physical
        logger.debug(f"Tool net_find_network_interfaces output: {result}")
        return result
    except Exception as e:
        logger.error(f"Error finding network interfaces: {e}\n{traceback.format_exc()}")
        result = f"Error finding network interfaces: {type(e).__name__}: {e}"
        logger.debug(f"Tool net_find_network_interfaces output: {result}")
        return result

#@mcp.tool()
#async def net_ping_device_from_gateway(device_ip: str, target_ip: str, intention: str, count: int = 5) -> str:
#    """Ping a device from a switch or router where the device is connected to.
#    Mainly used in environments with VPN or device is behind NAT.
#    This is used if the device IP address we want to reach is on different subnet than the current machinewe are on.
#    Run shell tool to know our current machine IP.
#    # NOTE: not implemented at the moment as it requires more scrutiny on how it works
#    args:
#        device_ip (str): IP of the device
#        target_ip (str): target IP to ping
#        intention (str): llm intention to call this tool
#        count (int): number of pings (default: 5)
#    """
#    logger.info(f"Intention: {intention}")
#    log_tool_call_to_csv("ping_device_from_gateway", intention, device_ip=device_ip, target_ip=target_ip, count=count)
#    logger.info(f"Pinging {target_ip} from {device_ip} (count={count})")
#    return f"Pinged {target_ip} {count} times failed."

@mcp.tool()
async def net_get_network_device_arp_table(device_management_ip: str, intention: str) -> List[str]:
    """Get ARP table from a Cisco device
    args:
        device_management_ip (str): management IP of the device
        intention (str): llm intention to call this tool
    """
    logger.debug(f"Executing tool: net_get_network_device_arp_table with args: device_management_ip={device_management_ip}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(net_get_network_device_arp_table.__name__, intention, device_management_ip=device_management_ip)
    logger.info(f"Getting ARP table for device: {device_management_ip}")
    try:
        device = DeviceSSHSession(device_management_ip)
        arp_table = device.execute_command("show ip arp")
        result = [line for line in arp_table.splitlines("\n") if line.strip()]
        logger.debug(f"Tool net_get_network_device_arp_table output: {result}")
        return result
    except Exception as e:
        logger.error(f"Error getting ARP table: {e}\n{traceback.format_exc()}")
        result = [f"Error getting ARP table: {type(e).__name__}: {e}"]
        logger.debug(f"Tool net_get_network_device_arp_table output: {result}")
        return result

@mcp.tool()
async def net_get_switch_mac_address_table(device_management_ip: str, intention: str) -> List[str]:
    """Get MAC address table from a cisco device
    args:
        device_management_ip (str): management IP of the device
        intention (str): llm intention to call this tool
    """
    logger.debug(f"Executing tool: net_get_switch_mac_address_table with args: device_management_ip={device_management_ip}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(net_get_switch_mac_address_table.__name__, intention, device_management_ip=device_management_ip)
    logger.info(f"Getting MAC address table for device: {device_management_ip}")
    try:
        device = DeviceSSHSession(device_management_ip)
        mac_table = device.execute_command("show mac address-table")
        result = [line for line in mac_table.splitlines("\n") if line.strip()]
        logger.debug(f"Tool net_get_switch_mac_address_table output: {result}")
        return result
    except Exception as e:
        logger.error(f"Error getting MAC address table: {e}\n{traceback.format_exc()}")
        result = [f"Error getting MAC address table: {type(e).__name__}: {e}"]
        logger.debug(f"Tool net_get_switch_mac_address_table output: {result}")
        return result

@mcp.tool()
async def net_get_l2_forwarding_information(device_management_ip: str, intention: str) -> str:
    """Get trunking status and spanning tree information from the Cisco switch
    args:
        device_management_ip (str): management IP of the switch
        intention (str): llm intention to call this tool
    """
    logger.debug(f"Executing tool: net_get_l2_forwarding_information with args: device_management_ip={device_management_ip}")
    try:
        logger.info(f"Intention: {intention}")
        log_tool_call_to_csv(net_get_l2_forwarding_information.__name__, intention, device_management_ip=device_management_ip)
        logger.info(f"Getting L2 forwarding info for device: {device_management_ip}")
        device = DeviceSSHSession(device_management_ip)
        # retrieve trunking and spanning tree info via ssh command
        trunking_info = device.execute_command("show interfaces trunk")
        spanning_tree_info = device.execute_command("show spanning-tree")
        result = f"Trunking Info:\n{trunking_info}\nSpanning Tree Info:\n{spanning_tree_info}"
        logger.debug(f"Tool net_get_l2_forwarding_information output: {result}")
        return result
    except Exception as e:
        logger.error(f"Error retrieving L2 information: {e}\n{traceback.format_exc()}")
        result = f"Error retrieving L2 information: {e}"
        logger.debug(f"Tool net_get_l2_forwarding_information output: {result}")
        return result
    

@mcp.tool()
async def net_get_nat_table(router_management_ip: str, intention: str) -> List[str]:
    """Get NAT table from a Cisco router
    args:
        router_management_ip (str): management IP of the router
        intention (str): llm intention to call this tool
    """
    logger.debug(f"Executing tool: net_get_nat_table with args: router_management_ip={router_management_ip}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(net_get_nat_table.__name__, intention, router_management_ip=router_management_ip)
    logger.info(f"Getting NAT table for router: {router_management_ip}")
    try:
        device = DeviceSSHSession(router_management_ip)
        nat_table = device.execute_command("show ip nat translations")
        result = [line for line in nat_table.splitlines("\n") if line.strip()]
        logger.debug(f"Tool net_get_nat_table output: {result}")
        return result
    except Exception as e:
        logger.error(f"Error getting NAT table: {e}\n{traceback.format_exc()}")
        result = [f"Error getting NAT table: {type(e).__name__}: {e}"]
        logger.debug(f"Tool net_get_nat_table output: {result}")
        return result

@mcp.tool()
async def net_get_routing_table(router_management_ip: str, intention: str) -> str:
    """Get routing table from a Cisco router
    args:
        router_management_ip (str): management IP of the router
        intention (str): llm intention to call this tool
    """
    logger.debug(f"Executing tool: net_get_routing_table with args: router_management_ip={router_management_ip}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(net_get_routing_table.__name__, intention, router_management_ip=router_management_ip)
    logger.info(f"Getting routing table for router: {router_management_ip}")
    try:
        device = DeviceSSHSession(router_management_ip)
        routing_table = device.execute_command("show ip route")
        result = f"routing table for router {router_management_ip}:\n{routing_table.splitlines('\n')}"
        logger.debug(f"Tool net_get_routing_table output: {result}")
        return result
    except Exception as e:
        logger.error(f"Error getting routing table: {e}\n{traceback.format_exc()}")
        result = f"Error getting routing table: {type(e).__name__}: {e}"
        logger.debug(f"Tool net_get_routing_table output: {result}")
        return result

@mcp.tool()
async def net_capture_network_traffic(device_management_ip: str, interface: str, duration_seconds: int, intention: str) -> str:
    """Capture network traffic on a given interface for a specified duration
    args:
        device_management_ip (str): management IP of the device
        interface (str): interface to capture traffic on
        duration_seconds (int): duration of capture in seconds
        intention (str): llm intention to call this tool
    """
    logger.debug(f"Executing tool: net_capture_network_traffic with args: device_management_ip={device_management_ip}, interface={interface}, duration_seconds={duration_seconds}")
    # returns a filtered pickup file
    captured_network_traffic = "No captured traffic"
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(net_capture_network_traffic.__name__, intention, device_management_ip=device_management_ip, interface=interface, duration_seconds=duration_seconds)
    logger.info(f"Capturing network traffic on {device_management_ip} interface {interface} for {duration_seconds}s")
    try:
        device = DeviceSSHSession(device_management_ip)
        if device_model := device.execute_command("show version | include Model number"):
            logger.info(f"Device model is {device_model}")
        if "cisco" in device_model.lower():

            commands = [
                "monitor capture buffer AI_CAPTURE size 10000",
                f"monitor capture AI_CAPTURE {interface} both",
                "monitor capture AI_CAPTURE match ipv4 protocol tcp any any limit pps 1000000",
                "monitor capture start AI_CAPTURE",
            ]
            for command in commands:
                device.execute_command(command)
            # wait for duration_seconds
            import time
            time.sleep(duration_seconds)
            # stop capture
            device.execute_command("monitor capture stop AI_CAPTURE")
            # show capture and export to tftp server (assuming tftp server is at TODO: to add this later)
            device.execute_command("term length 0")
            captured_network_traffic = device.execute_command("show monitor capture AI_CAPTURE buffer detailed")
            # if captured packets exist then sanitize the capture and filter what you look for using the tcpdump tool return the filtered capture
            # TODO: implement a cisco capture to pcap converter then apply it to the traffic and make it into text for LLM
        else:
            # TODO: implement for juniper, arista, palo alto, etc.
            logger.warning(f"Device model {device_model} not supported for traffic capture yet.")
        result = f"Captured traffic on {interface} for {duration_seconds} seconds. is {captured_network_traffic}"
        logger.debug(f"Tool net_capture_network_traffic output: {result}")
        return result
    except Exception as e:
        logger.error(f"Error capturing network traffic: {e}\n{traceback.format_exc()}")
        result = f"Error capturing network traffic: {type(e).__name__}: {e}"
        logger.debug(f"Tool net_capture_network_traffic output: {result}")
        return result

@mcp.tool()
async def net_get_device_logs(device_management_ip: str, log_type: str, time_range: str, intention: str, filter_regex: str = "") -> List[str]:
    """Get Cisco device logs of a specific type within a time range
    args:
        device_management_ip (str): management IP of the device
        log_type (str): type of logs to retrieve (e.g., error, warning, info)
        time_range (str): time range for the logs (e.g., last 1 hour, last 24 hours)
        intention (str): llm intention to call this tool
        filter_regex Optional[str]: optional regex or keyword to filter logs
    """
    logger.debug(f"Executing tool: net_get_device_logs with args: device_management_ip={device_management_ip}, log_type={log_type}, time_range={time_range}, filter_regex={filter_regex}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(net_get_device_logs.__name__, intention, device_management_ip=device_management_ip, log_type=log_type, time_range=time_range, filter_regex=filter_regex)
    logger.info(f"Getting device logs for {device_management_ip} (type={log_type}, range={time_range})")
    try:
        device = DeviceSSHSession(device_management_ip)
        # retrieve logs via ssh command
        if log_type.lower() == "error":
            log_level = "-4-.*"
        elif log_type.lower() == "warning":
            log_level = "-5-.*"
        elif log_type.lower() == "info":
            log_level = "-6-.*"
        else:
            log_level = ".*"
        device_logs = device.execute_command(f"show logging | i {log_level}")
        # filter using regex the logs matching the time range 
        if time_range:
            time_range_expression = f"{time_range}.*"
            time_range_regexp = re.compile(time_range_expression)
            device_logs = [log for log in device_logs.splitlines("\n") if time_range_regexp.search(log)]
        else:
            device_logs = device_logs.splitlines("\n")
        result = device_logs[:10]  # return first 10 logs for brevity
        logger.debug(f"Tool net_get_device_logs output: {result}")
        return result
    except Exception as e:
        logger.error(f"Error getting device logs: {e}\n{traceback.format_exc()}")
        result = [f"Error getting device logs: {type(e).__name__}: {e}"]
        logger.debug(f"Tool net_get_device_logs output: {result}")
        return result

@mcp.tool()
async def net_run_commands_on_device(device_management_ip: str, commands: List[str], intention: str, confidence: float, privileged: bool = False, model: str = "cisco") -> str:
    """Run a command on a network device via SSH
    args:
        device_management_ip (str): management IP of the device
        commands (List[str]): list of commands to execute on the device
        intention (str): llm intention to call this tool
        confidence (float): confidence level of the model in the commands generated
        privileged (bool): whether to run the command in privileged mode
        model (str): model of the device default is cisco, juniper, nokia, arista, paloalto, huawei, hpe_comware, hpe_procurve, fortinet
    returns:
        str: output of the command execution
    """
    logger.debug(f"Executing tool: net_run_commands_on_device with args: device_management_ip={device_management_ip}, commands={commands}, privileged={privileged}")
    logger.info(f"Intention: {intention}")
    logger.info(f"Confidence: {confidence}")
    log_tool_call_to_csv(net_run_commands_on_device.__name__, intention, device_management_ip=device_management_ip, commands=commands)
    logger.info(f"Running commands on {device_management_ip}: {commands}")
    try:
        device = DeviceSSHSession(device_management_ip, model=model)
        output = ""
        if privileged:
            for command in commands:
                _output = device.execute_privileged_command(command)
                output += f"Command: {command}\nOutput: {_output}\n"
        else:
            for command in commands:
                _output = device.execute_command(command)
                output += f"Command: {command}\nOutput: {_output}\n"
        logger.debug(f"Tool net_run_commands_on_device output: {output}")
        return output
    except Exception as e:
        logger.error(f"Error running commands: {e}\n{traceback.format_exc()}")
        result = f"Error running commands: {type(e).__name__}: {e}"
        logger.debug(f"Tool net_run_commands_on_device output: {result}")
        return result

@mcp.tool()
async def servicenow_get_incidents_by_priority(priority: int, intention: str) -> str:
    """Get active ServiceNow incidents by priority
    args:
        priority (int): priority of the incidents to retrieve (1=Critical, 2=High, 3=Moderate, 4=Low, 5=Planning)
        intention (str): llm intention to call this tool
    returns:
        str: summary of active incidents with the specified priority
    """
    logger.debug(f"Executing tool: servicenow_get_incidents_by_priority with args: priority={priority}")
    SERVICENOW_INSTANCE = SERVICENOW_INSTANCE_URL
    ACCESS_TOKEN = SERVICENOW_ACCESS_TOKEN
    
    # Create ServiceNow client
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(servicenow_get_incidents_by_priority.__name__, intention, priority=priority)
    logger.info(f"Getting active ServiceNow incidents with priority {priority}")
    sn_client = ServiceNowIncident(SERVICENOW_INSTANCE, ACCESS_TOKEN)
    
    result_raw = sn_client.get_active_incidents(priority=priority)
    
    if result_raw['success']:
        incidents = result_raw['data']['result']
        summary = f"Retrieved {result_raw['count']} active incidents with priority {priority}:\n"
        for inc in incidents:
            summary += f"- Number: {inc.get('number')}, Short Description: {inc.get('short_description')}\n"
        logger.debug(f"Tool servicenow_get_incidents_by_priority output: {summary}")
        return summary
    else:
        result = f"Error retrieving incidents: {result_raw['error']}"
        logger.debug(f"Tool servicenow_get_incidents_by_priority output: {result}")
        return result

@mcp.tool()
async def servicenow_get_incidents_by_incident_id(incident_id: str, intention: str) -> str:
    """Get ServiceNow incident details by incident ID
    args:
        incident_id (str): incident number to retrieve
        intention (str): llm intention to call this tool
    returns:
        str: details of the specified incident
    """
    logger.debug(f"Executing tool: servicenow_get_incidents_by_incident_id with args: incident_id={incident_id}")
    SERVICENOW_INSTANCE = SERVICENOW_INSTANCE_URL
    ACCESS_TOKEN = SERVICENOW_ACCESS_TOKEN
    
    # Create ServiceNow client
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(servicenow_get_incidents_by_incident_id.__name__, intention, incident_id=incident_id)
    logger.info(f"Getting ServiceNow incident details for ID: {incident_id}")
    sn_client = ServiceNowIncident(SERVICENOW_INSTANCE, ACCESS_TOKEN)
    # get all incidents and filter incidents where 
    result_raw = sn_client.get_incident_by_id(incident_id)
    
    if result_raw['success']:
        # get_incident_by_id returns {'result': {...}} so result['data']['result'] is the object
        incident = result_raw['data'].get('result', {})
        details = f"Incident Details:\nNumber: {incident.get('number')}\nShort Description: {incident.get('short_description')}\nState: {incident.get('state')}\nPriority: {incident.get('priority')}\n"
        logger.debug(f"Tool servicenow_get_incidents_by_incident_id output: {details}")
        return details
    else:
        result = f"Error retrieving incident: {result_raw['error']}"
        logger.debug(f"Tool servicenow_get_incidents_by_incident_id output: {result}")
        return result
    
@mcp.tool()
async def servicenow_get_incidents_by_user(user: str, intention: str) -> str:
    """Get ServiceNow incidents assigned to a specific user
    args:
        user (str): a user like peter torch ...
        intention (str): llm intention to call this tool
    returns:
        str: summary of incidents assigned to the user
    """
    logger.debug(f"Executing tool: servicenow_get_incidents_by_user with args: user={user}")
    SERVICENOW_INSTANCE = SERVICENOW_INSTANCE_URL
    ACCESS_TOKEN = SERVICENOW_ACCESS_TOKEN
    
    # Create ServiceNow client
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(servicenow_get_incidents_by_user.__name__, intention, user=user)
    logger.info(f"Getting ServiceNow incidents for user: {user}")
    sn_client = ServiceNowIncident(SERVICENOW_INSTANCE, ACCESS_TOKEN)
    
    result_raw = sn_client.get_my_incidents(user)
    
    if result_raw['success']:
        incidents = result_raw['data']['result']
        summary = f"Retrieved {result_raw['count']} incidents assigned to user {user}:\n"
        for inc in incidents:
            summary += f"- Number: {inc.get('number')}, Short Description: {inc.get('short_description')}\n"
        logger.debug(f"Tool servicenow_get_incidents_by_user output: {summary}")
        return summary
    else:
        result = f"Error retrieving incidents: {result_raw['error']}"
        logger.debug(f"Tool servicenow_get_incidents_by_user output: {result}")
        return result

@mcp.tool()
async def servicenow_get_unassigned_incidents_for_group(group_name: str, intention: str) -> str:
    """Get unassigned ServiceNow incidents for a specific group
    args:
        group_name (str): The name of the group (e.g., 'Software')
        intention (str): llm intention to call this tool
    returns:
        str: summary of unassigned incidents for the group
    """
    logger.debug(f"Executing tool: servicenow_get_unassigned_incidents_for_group with args: group_name={group_name}")
    SERVICENOW_INSTANCE = SERVICENOW_INSTANCE_URL
    ACCESS_TOKEN = SERVICENOW_ACCESS_TOKEN
    
    # Create ServiceNow client
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(servicenow_get_unassigned_incidents_for_group.__name__, intention, group_name=group_name)
    logger.info(f"Getting unassigned incidents for group: {group_name}")
    sn_client = ServiceNowIncident(SERVICENOW_INSTANCE, ACCESS_TOKEN)
    
    result_raw = sn_client.get_unassigned_group_incidents(group_name)
    
    if result_raw['success']:
        incidents = result_raw['data']['result']
        count = result_raw['count']
        if count == 0:
            result = f"No unassigned incidents found for group '{group_name}'."
            logger.debug(f"Tool servicenow_get_unassigned_incidents_for_group output: {result}")
            return result
            
        summary = f"Retrieved {count} unassigned incidents for group '{group_name}':\n"
        for inc in incidents:
            summary += f"- Number: {inc.get('number')}, Short Description: {inc.get('short_description')}\n"
        logger.debug(f"Tool servicenow_get_unassigned_incidents_for_group output: {summary}")
        return summary
    else:
        result = f"Error retrieving unassigned incidents: {result_raw.get('error', 'Unknown error')}"
        logger.debug(f"Tool servicenow_get_unassigned_incidents_for_group output: {result}")
        return result

@mcp.tool()
async def servicenow_create_incident(short_description: str, intention: str, description: str = '', caller_id: str = '', urgency: int = 3, impact: int = 3, assignment_group: str = '') -> str:
    """Create a new  incident in ServiceNow
    args:
        short_description (str): Brief summary of the issue
        intention (str): llm intention to call this tool
        description (str): Detailed description (optional)
        caller_id (str): Name or sys_id of the person reporting (optional)
        urgency (int): 1 (Critical), 2 (High), 3 (Moderate), 4 (Low) (default: 3)
        impact (int): 1 (High), 2 (Medium), 3 (Low) (default: 3)
        assignment_group (str): Name or sys_id of the group to assign (optional)
    returns:
        str: summary of the created incident
    """
    logger.debug(f"Executing tool: servicenow_create_incident with args: short_description={short_description}, caller_id={caller_id}, urgency={urgency}, impact={impact}, assignment_group={assignment_group}")
    SERVICENOW_INSTANCE = SERVICENOW_INSTANCE_URL
    ACCESS_TOKEN = SERVICENOW_ACCESS_TOKEN
    
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(servicenow_create_incident.__name__, intention, short_description=short_description, caller_id=caller_id, urgency=urgency, impact=impact, assignment_group=assignment_group)
    logger.info(f"Creating ServiceNow incident: {short_description}")
    
    sn_client = ServiceNowIncident(SERVICENOW_INSTANCE, ACCESS_TOKEN)
    
    result_raw = sn_client.create_incident(
        short_description=short_description,
        description=description,
        caller_id=caller_id,
        urgency=urgency,
        impact=impact,
        assignment_group=assignment_group
    )
    
    if result_raw['success']:
        incident_data = result_raw['data']['result']
        result = f"✅ Incident Created Successfully!\nNumber: {incident_data.get('number')}\nShort Description: {incident_data.get('short_description')}\nPriority: {incident_data.get('priority')}\nSys ID: {incident_data.get('sys_id')}"
        logger.debug(f"Tool servicenow_create_incident output: {result}")
        return result
    else:
        result = f"❌ Failed to create incident: {result_raw.get('error', 'Unknown error')}"
        logger.debug(f"Tool servicenow_create_incident output: {result}")
        return result

@mcp.tool()
async def servicenow_create_change_request(short_description: str, description: str, intention: str, priority: str = '4', risk: str = '3', impact: str = '3', ci_name: str = '') -> str:
    """Create a ServiceNow change request
    args:
        short_description (str): Short summary of the change
        description (str): Detailed description
        intention (str): llm intention to call this tool
        priority (str): Priority 1-5 (default: 4)
        risk (str): Risk 1-3 (default: 3)
        impact (str): Impact 1-3 (default: 3)
        ci_name (str): specific Config Item name (server, app, etc)
    returns:
        str: summary of the created change request
    """
    logger.debug(f"Executing tool: servicenow_create_change_request with args: short_description={short_description}, description={description}, priority={priority}, risk={risk}, impact={impact}, ci_name={ci_name}")
    SERVICENOW_INSTANCE = SERVICENOW_INSTANCE_URL
    ACCESS_TOKEN = SERVICENOW_ACCESS_TOKEN
    
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(servicenow_create_change_request.__name__, intention, short_description=short_description, description=description, priority=priority, risk=risk, impact=impact, ci_name=ci_name)
    logger.info(f"Creating change request: {short_description}")
    sn_client = ServiceNowChangeRequest(SERVICENOW_INSTANCE, ACCESS_TOKEN)
    
    result_raw = sn_client.create_change_request(
        short_description=short_description,
        description=description,
        priority=priority,
        risk=risk,
        impact=impact,
        cmdb_ci=ci_name
    )
    
    if result_raw['success']:
        change_data = result_raw['data']['result']
        result = f"✅ Change Request Created Successfully!\nNumber: {change_data.get('number')}\nSys ID: {change_data.get('sys_id')}\nLink: {result_raw['data']['result'].get('link', 'N/A')}"
        logger.debug(f"Tool servicenow_create_change_request output: {result}")
        return result
    else:
        result = f"❌ Failed to create change request: {result_raw.get('error', 'Unknown error')}"
        logger.debug(f"Tool servicenow_create_change_request output: {result}")
        return result

# ============================================================
# JIRA TOOLS
# ============================================================

@mcp.tool()
async def jira_get_ticket(issue_key: str, intention: str) -> str:
    """Fetch a Jira issue by key (e.g. \"PROJ-123\").
    Args:
        issue_key (str): Jira issue key
        intention (str): llm intention to call this tool
    """
    logger.debug(f"Executing tool: jira_get_ticket with args: issue_key={issue_key}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(jira_get_ticket.__name__, intention, issue_key=issue_key)
    if not jira_client:
        result = "Jira Client is not initialized. Please check credentials."
        logger.debug(f"Tool jira_get_ticket output: {result}")
        return result
    try:
        result_raw = jira_client.get_ticket(issue_key)
        fields = result_raw.get("fields", {})
        summary = (
            f"Ticket: {result_raw.get('key')} - {fields.get('summary')}\n"
            f"Status: {fields.get('status', {}).get('name')}\n"
            f"Priority: {(fields.get('priority') or {}).get('name')}\n"
            f"Assignee: {(fields.get('assignee') or {}).get('displayName', 'Unassigned')}\n"
            f"Reporter: {(fields.get('reporter') or {}).get('displayName')}\n"
            f"Created: {fields.get('created')}\n"
            f"Updated: {fields.get('updated')}"
        )
        logger.debug(f"Tool jira_get_ticket output: {summary}")
        return summary
    except Exception as e:
        logger.error(f"Error fetching Jira ticket {issue_key}: {e}\n{traceback.format_exc()}")
        result = f"Error fetching Jira ticket: {e}"
        logger.debug(f"Tool jira_get_ticket output: {result}")
        return result

@mcp.tool()
async def jira_get_ticket_details(issue_key: str, intention: str) -> str:
    """Fetch a Jira issue with full field expansion and comments.
    Args:
        issue_key (str): Jira issue key
        intention (str): llm intention to call this tool
    """
    logger.debug(f"Executing tool: jira_get_ticket_details with args: issue_key={issue_key}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(jira_get_ticket_details.__name__, intention, issue_key=issue_key)
    if not jira_client:
        result = "Jira Client is not initialized. Please check credentials."
        logger.debug(f"Tool jira_get_ticket_details output: {result}")
        return result
    try:
        details = jira_client.get_ticket_details(issue_key)
        result = json.dumps(details, indent=2)
        logger.debug(f"Tool jira_get_ticket_details output: {result}")
        return result
    except Exception as e:
        logger.error(f"Error fetching Jira ticket details {issue_key}: {e}\n{traceback.format_exc()}")
        result = f"Error fetching Jira ticket details: {e}"
        logger.debug(f"Tool jira_get_ticket_details output: {result}")
        return result

@mcp.tool()
async def jira_update_ticket(issue_key: str, intention: str, summary: Optional[str] = None, assignee_id: Optional[str] = None, priority: Optional[str] = None, labels: Optional[str] = None, due_date: Optional[str] = None) -> str:
    """Update one or more fields on a Jira ticket.
    Args:
        issue_key (str): Jira issue key
        intention (str): llm intention to call this tool
        summary (str): New summary/title text (optional)
        assignee_id (str): Atlassian accountId of the new assignee (optional)
        priority (str): Priority name, e.g. High, Medium, Low (optional)
        labels (str): Comma-separated labels, e.g. backend,urgent (optional)
        due_date (str): Due date in YYYY-MM-DD format (optional)
    """
    logger.debug(f"Executing tool: jira_update_ticket with args: issue_key={issue_key}, summary={summary}, assignee_id={assignee_id}, priority={priority}, labels={labels}, due_date={due_date}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(jira_update_ticket.__name__, intention, issue_key=issue_key, summary=summary, assignee_id=assignee_id, priority=priority, labels=labels, due_date=due_date)
    if not jira_client:
        result = "Jira Client is not initialized. Please check credentials."
        logger.debug(f"Tool jira_update_ticket output: {result}")
        return result
    
    fields = {}
    if summary: fields["summary"] = summary
    if assignee_id: fields["assignee"] = {"accountId": assignee_id}
    if priority: fields["priority"] = {"name": priority}
    if labels: fields["labels"] = [l.strip() for l in labels.split(",")]
    if due_date: fields["duedate"] = due_date

    if not fields:
        result = "No update fields provided."
        logger.debug(f"Tool jira_update_ticket output: {result}")
        return result

    try:
        jira_client.update_ticket(issue_key, fields)
        result = f"✓ Ticket {issue_key} updated successfully."
        logger.debug(f"Tool jira_update_ticket output: {result}")
        return result
    except Exception as e:
        logger.error(f"Error updating Jira ticket {issue_key}: {e}\n{traceback.format_exc()}")
        result = f"Error updating Jira ticket: {e}"
        logger.debug(f"Tool jira_update_ticket output: {result}")
        return result

@mcp.tool()
async def jira_add_comment(issue_key: str, comment_text: str, intention: str) -> str:
    """Add a comment to a Jira issue.
    Args:
        issue_key (str): Jira issue key
        comment_text (str): Comment text
        intention (str): llm intention to call this tool
    """
    logger.debug(f"Executing tool: jira_add_comment with args: issue_key={issue_key}, comment_text={comment_text}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(jira_add_comment.__name__, intention, issue_key=issue_key, comment_text=comment_text)
    if not jira_client:
        result = "Jira Client is not initialized. Please check credentials."
        logger.debug(f"Tool jira_add_comment output: {result}")
        return result
    try:
        result_raw = jira_client.add_comment(issue_key, comment_text)
        result = f"✓ Comment added (id={result_raw.get('id')}) to {issue_key}."
        logger.debug(f"Tool jira_add_comment output: {result}")
        return result
    except Exception as e:
        logger.error(f"Error adding comment to Jira ticket {issue_key}: {e}\n{traceback.format_exc()}")
        result = f"Error adding comment: {e}"
        logger.debug(f"Tool jira_add_comment output: {result}")
        return result

@mcp.tool()
async def jira_transition_ticket(issue_key: str, transition_name: str, intention: str) -> str:
    """Move a ticket to a new status by transition name (e.g. \"In Progress\", \"Done\").
    Args:
        issue_key (str): Jira issue key
        transition_name (str): Target transition name
        intention (str): llm intention to call this tool
    """
    logger.debug(f"Executing tool: jira_transition_ticket with args: issue_key={issue_key}, transition_name={transition_name}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(jira_transition_ticket.__name__, intention, issue_key=issue_key, transition_name=transition_name)
    if not jira_client:
        result = "Jira Client is not initialized. Please check credentials."
        logger.debug(f"Tool jira_transition_ticket output: {result}")
        return result
    try:
        jira_client.transition_ticket(issue_key, transition_name)
        result = f"✓ Ticket {issue_key} transitioned to '{transition_name}'."
        logger.debug(f"Tool jira_transition_ticket output: {result}")
        return result
    except Exception as e:
        logger.error(f"Error transitioning Jira ticket {issue_key}: {e}\n{traceback.format_exc()}")
        result = f"Error transitioning ticket: {e}"
        logger.debug(f"Tool jira_transition_ticket output: {result}")
        return result

@mcp.tool()
async def jira_list_transitions(issue_key: str, intention: str) -> str:
    """List available transitions for a ticket.
    Args:
        issue_key (str): Jira issue key
        intention (str): llm intention to call this tool
    """
    logger.debug(f"Executing tool: jira_list_transitions with args: issue_key={issue_key}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(jira_list_transitions.__name__, intention, issue_key=issue_key)
    if not jira_client:
        result = "Jira Client is not initialized. Please check credentials."
        logger.debug(f"Tool jira_list_transitions output: {result}")
        return result
    try:
        transitions = jira_client.list_transitions(issue_key)
        if not transitions:
            result = f"No transitions available for {issue_key}."
            logger.debug(f"Tool jira_list_transitions output: {result}")
            return result
        lines = [f"Available transitions for {issue_key}:"]
        for t in transitions:
            lines.append(f"  [{t['id']}] {t['name']}")
        result = "\n".join(lines)
        logger.debug(f"Tool jira_list_transitions output: {result}")
        return result
    except Exception as e:
        logger.error(f"Error listing transitions for Jira ticket {issue_key}: {e}\n{traceback.format_exc()}")
        result = f"Error listing transitions: {e}"
        logger.debug(f"Tool jira_list_transitions output: {result}")
        return result

@mcp.tool()
async def jira_get_recent_tickets(intention: str, project: Optional[str] = None, limit: int = 50, status: Optional[str] = None, days: int = 30) -> str:
    """List tickets ordered by most recently created.
    Args:
        intention (str): llm intention to call this tool
        project (str): Scope to a project key, e.g. PROJ (optional)
        limit (int): Max results (default 50)
        status (str): Filter by status, e.g. \"In Progress\" (optional)
        days (int): How many days back to search when no project given (default 30)
    """
    logger.debug(f"Executing tool: jira_get_recent_tickets with args: project={project}, limit={limit}, status={status}, days={days}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(jira_get_recent_tickets.__name__, intention, project=project, limit=limit, status=status, days=days)
    if not jira_client:
        result = "Jira Client is not initialized. Please check credentials."
        logger.debug(f"Tool jira_get_recent_tickets output: {result}")
        return result
    try:
        issues = jira_client.get_recent_tickets(project=project, max_results=limit, status=status, days=days)
        if not issues:
            result = "No tickets found."
            logger.debug(f"Tool jira_get_recent_tickets output: {result}")
            return result
        lines = [f"Recent tickets:"]
        for issue in issues:
            f = issue.get("fields", {})
            lines.append(f"- {issue.get('key')}: {f.get('summary')} (Status: {f.get('status', {}).get('name')})")
        result = "\n".join(lines)
        logger.debug(f"Tool jira_get_recent_tickets output: {result}")
        return result
    except Exception as e:
        logger.error(f"Error fetching recent Jira tickets: {e}\n{traceback.format_exc()}")
        result = f"Error fetching recent tickets: {e}"
        logger.debug(f"Tool jira_get_recent_tickets output: {result}")
        return result

@mcp.tool()
async def jira_get_tickets_by_assignee(intention: str, assignee: Optional[str] = None, project: Optional[str] = None, limit: int = 50, status: Optional[str] = None, order_by: str = "created", order_dir: str = "DESC") -> str:
    """List tickets assigned to a user.
    Args:
        intention (str): llm intention to call this tool
        assignee (str): Display name or accountId. Omit to use currentUser() (optional)
        project (str): Scope to a project key, e.g. PROJ (optional)
        limit (int): Max results (default 50)
        status (str): Filter by status, e.g. \"To Do\" (optional)
        order_by (str): Sort field: created | updated | priority | status (default: created)
        order_dir (str): Sort direction: ASC | DESC (default: DESC)
    """
    logger.debug(f"Executing tool: jira_get_tickets_by_assignee with args: assignee={assignee}, project={project}, limit={limit}, status={status}, order_by={order_by}, order_dir={order_dir}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(jira_get_tickets_by_assignee.__name__, intention, assignee=assignee, project=project, limit=limit, status=status, order_by=order_by, order_dir=order_dir)
    if not jira_client:
        result = "Jira Client is not initialized. Please check credentials."
        logger.debug(f"Tool jira_get_tickets_by_assignee output: {result}")
        return result
    try:
        issues = jira_client.get_tickets_by_assignee(assignee=assignee, project=project, max_results=limit, status=status, order_by=order_by, order_dir=order_dir)
        if not issues:
            result = f"No tickets found for assignee {assignee or 'currentUser()'}."
            logger.debug(f"Tool jira_get_tickets_by_assignee output: {result}")
            return result
        lines = [f"Tickets assigned to {assignee or 'currentUser()'}:"]
        for issue in issues:
            f = issue.get("fields", {})
            lines.append(f"- {issue.get('key')}: {f.get('summary')} (Status: {f.get('status', {}).get('name')})")
        result = "\n".join(lines)
        logger.debug(f"Tool jira_get_tickets_by_assignee output: {result}")
        return result
    except Exception as e:
        logger.error(f"Error fetching Jira tickets by assignee: {e}\n{traceback.format_exc()}")
        result = f"Error fetching tickets by assignee: {e}"
        logger.debug(f"Tool jira_get_tickets_by_assignee output: {result}")
        return result

@mcp.tool()
async def jira_search_tickets(jql: str, intention: str, limit: int = 50) -> str:
    """Search tickets with a JQL query.
    Args:
        jql (str): JQL query string
        intention (str): llm intention to call this tool
        limit (int): Max results (default 50)
    """
    logger.debug(f"Executing tool: jira_search_tickets with args: jql={jql}, limit={limit}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(jira_search_tickets.__name__, intention, jql=jql, limit=limit)
    if not jira_client:
        result = "Jira Client is not initialized. Please check credentials."
        logger.debug(f"Tool jira_search_tickets output: {result}")
        return result
    try:
        issues = jira_client.search_tickets(jql, max_results=limit)
        if not issues:
            result = f"No issues found for JQL: {jql}"
            logger.debug(f"Tool jira_search_tickets output: {result}")
            return result
        lines = [f"Found {len(issues)} issues:"]
        for issue in issues:
            f = issue.get("fields", {})
            lines.append(f"- {issue.get('key')}: {f.get('summary')} (Status: {f.get('status', {}).get('name')})")
        result = "\n".join(lines)
        logger.debug(f"Tool jira_search_tickets output: {result}")
        return result
    except Exception as e:
        logger.error(f"Error searching Jira tickets with JQL {jql}: {e}\n{traceback.format_exc()}")
        result = f"Error searching tickets: {e}"
        logger.debug(f"Tool jira_search_tickets output: {result}")
        return result

@mcp.tool()
async def cloud_ssh_tool(management_ip: str, cloud_provider: str, command: List[str], intention: str) -> str:
    """SSH into a cloud VM and run a CLI command such as AWS CLI, or AZure CLI, or GCP CLI commands.
    args:
        management_ip (str): management IP of the machine used to access the cloud provider(if not provided use localhost)
        cloud_provider (str): the cloud provider (AWS, Azure, GCP)
        command (List[str]): commands to execute to get information from the cloud
        intention (str): llm intention to call this tool
    returns:
        str: output of the command execution
    """
    logger.debug(f"Executing tool: cloud_ssh_tool with args: management_ip={management_ip}, cloud_provider={cloud_provider}, command={command}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(cloud_ssh_tool.__name__, intention, management_ip=management_ip, cloud_provider=cloud_provider, command=command)
    try:
        username = os.environ.get("CLOUD_DESKTOP_USER", "admin")
        device = DeviceSSHSession(
            management_ip,
            model="linux",
            username=username,
            password=os.environ.get("CLOUD_DESKTOP_PASSWORD", "password"),
        )
        logger.info(f"Connecting to {cloud_provider} VM at {management_ip} as {username}")
        output = ""
        for cmd in command:
            logger.info(f"Executing command: {cmd}")
            _output = device.execute_command(cmd)
            output += f"Command: {cmd}\nOutput: {_output}\n"
        logger.debug(f"Tool cloud_ssh_tool output: {output}")
        return output
    except Exception as e:
        logger.error(f"Error connecting/executing on cloud VM: {e}\n{traceback.format_exc()}")
        result = f"Error connecting/executing on cloud VM: {type(e).__name__}: {e}"
        logger.debug(f"Tool cloud_ssh_tool output: {result}")
        return result

@mcp.tool()
async def linux_server_ssh_tool(management_ip: str, command: List[str], intention: str) -> str:
    """SSH into a Linux server and run a command
    args:
        management_ip (str): management IP of the VM
        command (List[str]): commands to execute
        intention (str): llm intention to call this tool
    returns:
        str: output of the command execution
    """
    logger.debug(f"Executing tool: linux_server_ssh_tool with args: management_ip={management_ip}, command={command}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(linux_server_ssh_tool.__name__, intention, management_ip=management_ip, command=command)
    try:
        username = os.environ.get("SERVER_USERNAME", "admin")
        device = DeviceSSHSession(
            management_ip,
            model="linux",
            username=username,
            password=os.environ.get("SERVER_PASSWORD", "password"),
        )
        logger.info(f"Connecting to Linux server at {management_ip} as {username}")
        output = ""
        for cmd in command:
            logger.info(f"Executing command: {cmd}")
            _output = device.execute_command(cmd)
            output += f"Command: {cmd}\nOutput: {_output}\n"
        logger.debug(f"Tool linux_server_ssh_tool output: {output}")
        return output
    except Exception as e:
        logger.error(f"Error connecting/executing on Linux server: {e}\n{traceback.format_exc()}")
        result = f"Error connecting/executing on Linux server: {type(e).__name__}: {e}"
        logger.debug(f"Tool linux_server_ssh_tool output: {result}")
        return result

@mcp.tool()
async def execute_shell_command(command: str, intention: str, timeout: int = 60) -> str:
    """
    Execute a shell or bash command on the host machine in a safe manner.
    This can safely be used with AI agent with no risk of damaging the local machine.
    Use this for network diagnostics (ping, traceroute, dig, curl, tshark, netstatetc.) or other CLI tasks.
    sometime when tools are missing you may install them using apt-get or yum or dnf etc.
    
    Args:
        command (str): The shell command to execute.
        intention (str): llm intention to call this tool
        timeout (int): Timeout in seconds (default: 60).
        
    Returns:
        str: Combined stdout and stderr output.
    """
    logger.debug(f"Executing tool: execute_shell_command with args: command={command}, timeout={timeout}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(execute_shell_command.__name__, intention, command=command, timeout=timeout)
    logger.info(f"Executing shell command: {command}")
    
    try:
        # Use asyncio.create_subprocess_shell for non-blocking execution
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            
            output = ""
            if stdout:
                output += stdout.decode().strip()
            if stderr:
                if output:
                    output += "\nSTDERR:\n"
                output += stderr.decode().strip()
                
            return_code = process.returncode
            if return_code != 0:
                output += f"\nCommand failed with return code {return_code}"
                
            logger.debug(f"Tool execute_shell_command output: {output}")
            return output
            
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            result = f"Error: Command timed out after {timeout} seconds"
            logger.debug(f"Tool execute_shell_command output: {result}")
            return result
            
    except Exception as e:
        logger.error(f"Error executing shell command: {e}\n{traceback.format_exc()}")
        result = f"Error executing command: {str(e)}"
        logger.debug(f"Tool execute_shell_command output: {result}")
        return result

@mcp.tool()
async def execute_generated_code(code: str, intention: str, mode: str = "docker", dependencies: List[str] = []) -> str:
    """
    Safely executes Python code generated by an LLM with options for local, sandboxed, or internal execution.

    Args:
        code (str): The Python code to execute.
        intention (str): llm intention to call this tool
        mode (str): The execution mode. options:
            - 'docker': (Default) Runs the code inside a 'python:3-slim' Docker container. (Recommended for safety).
            - 'local_process': Runs the code directly on the host system as a subprocess. (WARNING: Risky, uses system privileges).
        dependencies (List[str]): A list of pip packages to install before execution (Docker mode only currently).

    Returns:
        str: The combined stdout/stderr output of the executed code, or error details.
    """
    logger.debug(f"Executing tool: execute_generated_code with args: code={code}, mode={mode}, dependencies={dependencies}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(execute_generated_code.__name__, intention, code=code, mode=mode, dependencies=dependencies)
    logger.info(f"Executing generated code. Mode: {mode}")
    
    # Clean up code string (remove markdown code blocks if present)
    code = code.strip()
    if code.startswith("```python"):
        code = code[9:]
    elif code.startswith("```"):
        code = code[3:]
    if code.endswith("```"):
        code = code[:-3]
    
    output = ""
    
    try:
        if mode == "docker":
            # Safest mode
            # Create a localized temp file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as temp_script:
                temp_script.write(code)
                temp_script_path = temp_script.name
            
            try:
                # Prepare docker command
                # We mount the temp file to /app/script.py
                # Note: This assumes docker is installed and user has permission
                volume_mount = f"{os.path.abspath(temp_script_path)}:/app/script.py"
                
                # Ensure the container can read the file
                os.chmod(temp_script_path, 0o644)
 
                docker_cmd = ["docker", "run", "--rm", "-v", volume_mount, "python:3-slim", "bash", "-c"]
                
                # Construct the shell command inside docker
                internal_cmds = []
                if dependencies:
                    internal_cmds.append(f"pip install {' '.join(dependencies)}")
                internal_cmds.append("python /app/script.py")
                
                full_shell_cmd = " && ".join(internal_cmds)
                docker_cmd.append(full_shell_cmd)
                
                logger.info(f"Running docker command: {docker_cmd}")
                
                result = subprocess.run(
                    docker_cmd, 
                    capture_output=True, 
                    text=True, 
                    timeout=120 
                )
                output = result.stdout + result.stderr
                
            finally:
                if os.path.exists(temp_script_path):
                    os.remove(temp_script_path)
                    
        elif mode == "local_process":
            # RISKY: Runs on host
            logger.warning("Executing code in 'local_process' mode. This is potentially unsafe.")
            if dependencies:
                logger.warning("Dependencies argument is ignored in local_process mode to avoid polluting system environment.")

            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as temp_script:
                temp_script.write(code)
                temp_script_path = temp_script.name
            
            try:
                # Run the script
                result = subprocess.run(
                    [sys.executable, temp_script_path],
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                output = result.stdout + result.stderr
            finally:
                if os.path.exists(temp_script_path):
                    os.remove(temp_script_path)

        
 

        else:
            result = f"Error: Unknown execution mode: {mode}, supported modes are: docker, local_process"
            logger.debug(f"Tool execute_generated_code output: {result}")
            return result

    except subprocess.TimeoutExpired:
        output += "\nExecution timed out."
    except Exception as e:
        logger.error(f"Execution failed: {e}\n{traceback.format_exc()}")
        output += f"\nExecution failed: {str(e)} + {traceback.format_exc()}"

    logger.debug(f"Tool execute_generated_code output: {output}")
    return output


@mcp.tool()
async def net_execute_with_tool_modification(
    tool_name: str,
    tool_params: dict,
    modification_code: str,
    intention: str
) -> str:
    """
    Executes an existing MCP tool and then modifies its output using provided Python code.
    
    This allows the LLM to chain tool calls with custom post-processing logic.
    
    Args:
        tool_name (str): The name of the MCP tool to execute (e.g., "read_file", "search_database").
        tool_params (dict): A dictionary of parameters to pass to the tool.
        modification_code (str): Python code to modify the output. The original output is available 
                                as 'original_output' variable. Set 'modified_output' variable with the result.
                                Example: "modified_output = original_output.upper()"
        intention (str): llm intention to call this tool
    
    Returns:
        str: The modified output from the tool, or error details if execution fails.
    
    Example:
        tool_name = "read_file"
        tool_params = {"path": "/data/report.txt"}
        modification_code = '''
import json
lines = original_output.split('\\n')
modified_output = json.dumps({"line_count": len(lines), "preview": lines[0]})
'''
    """
    logger.debug(f"Executing tool: net_execute_with_tool_modification with args: tool_name={tool_name}, tool_params={tool_params}, modification_code={modification_code}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(net_execute_with_tool_modification.__name__, intention, tool_name=tool_name, tool_params=tool_params, modification_code=modification_code)
    logger.info(f"Executing tool '{tool_name}' with modification")
    
    try:
        # Get the tool function from the MCP server's registered tools
        tool_func = _get_tool_by_name(tool_name)
        
        if tool_func is None:
            return f"Error: Tool '{tool_name}' not found in registered MCP tools"
        
        # Execute the original tool
        logger.info(f"Calling tool '{tool_name}' with params: {tool_params}")
        original_output = await tool_func(**tool_params)
        
        # Convert to string if not already
        if not isinstance(original_output, str):
            original_output = str(original_output)
        
        # Clean up modification code (remove markdown if present)
        modification_code = modification_code.strip()
        if modification_code.startswith("```python"):
            modification_code = modification_code[9:]
        elif modification_code.startswith("```"):
            modification_code = modification_code[3:]
        if modification_code.endswith("```"):
            modification_code = modification_code[:-3]
        
        # Create execution context with original output
        local_vars = {
            "original_output": original_output,
            "modified_output": original_output  # Default to original if not set
        }
        
        # Execute modification code
        logger.info("Executing modification code")
        exec(modification_code, {"__builtins__": __builtins__}, local_vars)
        
        # Return the modified output
        result = local_vars.get("modified_output", original_output)
        
        # Ensure we return a string
        if not isinstance(result, str):
            result = str(result)
            
        logger.info("Tool modification completed successfully")
        logger.debug(f"Tool net_execute_with_tool_modification output: {result}")
        return result
        
    except Exception as e:
        error_msg = f"Error executing tool with modification: {str(e)}"
        logger.error(f"{error_msg}\n{traceback.format_exc()}")
        logger.debug(f"Tool net_execute_with_tool_modification output: {error_msg}")
        return error_msg


def _get_tool_by_name(tool_name: str):
    """
    Helper function to retrieve an MCP tool function by name.
    
    This implementation depends on how your MCP server registers tools.
    You may need to adjust this based on your specific MCP framework.
    
    Args:
        tool_name (str): The name of the tool to retrieve
        
    Returns:
        The tool function if found, None otherwise
    """
    # Option 1: If using mcp library with a registry
    # Adjust this based on your actual MCP implementation
    try:
        # Check if mcp has a tools registry
        if hasattr(mcp, 'tools'):
            return mcp.tools.get(tool_name)
        
        # Option 2: If tools are stored in a global registry
        if hasattr(mcp, '_tool_registry'):
            return mcp._tool_registry.get(tool_name)
        
        # Option 3: Search through globals for the decorated function
        # This assumes tools are defined in the same module
        for name, obj in globals().items():
            if callable(obj) and hasattr(obj, '__name__') and obj.__name__ == tool_name:
                return obj
        
        # Option 4: If you have a specific way tools are registered
        # Add your custom logic here
        
        return None
        
    except Exception as e:
        logger.error(f"Error retrieving tool '{tool_name}': {str(e)}")
        return None

#@mcp.tool()
async def get_skill_all_related_tools(skill_name: str, intention: str) -> str:
    """
    Get all related tools for a given skill
    args:
        skill_name (str): The name of the skill
        intention (str): llm intention to call this tool
    returns:
        str: a string with a tuple of (tool_name, tool_description) for each related tool
    """
    # get all current MCP tools then filter them according to the skill_name given
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(get_skill_all_related_tools.__name__, intention, skill_name=skill_name)
    # all skill related tools start with <skill-name>_name-of-the-tool
    # example: linux_server_ssh_tool for linux server skill
    try:
        all_tools = globals()
        skill_prefix = skill_name + "_"
        skill_tools_list = []
        
        for name, obj in all_tools.items():
            if name.startswith(skill_prefix) and callable(obj):
                doc = (obj.__doc__ or "No description").strip()
                skill_tools_list.append((name, doc))
        
        logger.info(f"Skill tools information: {skill_tools_list}")
        if not skill_tools_list:
            return f"No tools found related to skill '{skill_name}'"
            
        return f"{skill_name} skill tools: {skill_tools_list}"
    except Exception as e:
        logger.error(f"Error retrieving tools for skill '{skill_name}': {e}\n{traceback.format_exc()}")
        return f"Error retrieving tools for skill '{skill_name}': {e}"

@mcp.tool()
async def visualize_drawio_diagram(
    diagram_xml_code: str,
    intention: str,
    save_to_file: Optional[str] = None,
    width: int = 800,
    height: int = 600,
    scale: float = 1.0,
    border: int = 0
) -> str:
    """
    Visualize a drawio diagram using Diagrams.net export API.
    NOTE: Do not use until proven working
    
    Args:
        diagram_xml_code (str): The XML code of the diagram
        intention (str): llm intention to call this tool
        save_to_file (Optional[str]): Path to save the PNG image
        width (int): Width of the image (default: 800)
        height (int): Height of the image (default: 600)
        scale (float): Scale factor (default: 1.0)
        border (int): Border width (default: 0)
        
    Returns:
        str: base64 encoded png image of the diagram
    """
    logger.debug(f"Executing tool: visualize_drawio_diagram with args: diagram_xml_code={diagram_xml_code}, save_to_file={save_to_file}, width={width}, height={height}, scale={scale}, border={border}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(visualize_drawio_diagram.__name__, intention, diagram_xml_code=diagram_xml_code, save_to_file=save_to_file, width=width, height=height, scale=scale, border=border)
    logger.info("Visualizing draw.io diagram via export API")
    
    export_url = "https://convert.diagrams.net/node/export"
    
    async with aiohttp.ClientSession() as session:
        # We request binary data (base64=0) because we need it for saving to file
        params = {
            'format': 'png',
            'xml': diagram_xml_code,
            'bg': 'none',
            'base64': '0',
            'w': str(width),
            'h': str(height),
            'border': str(border),
            'scale': str(scale)
        }
        
        headers = {
            'Origin': 'https://app.diagrams.net',
            'Referer': 'https://app.diagrams.net/'
        }
        
        try:
            async with session.post(
                export_url,
                data=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status != 200:
                    error_body = await response.text()
                    logger.error(f"Draw.io export failed: {response.status} - {error_body}")
                    return f"Error: Export failed with status {response.status} - {error_body}"
                
                image_bytes = await response.read()
                
                if not image_bytes:
                    logger.error("Received empty response from Draw.io API")
                    return "Error: Received empty response from API"
                
                # Save to file if requested
                if save_to_file:
                    save_path = Path(save_to_file)
                    save_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(save_path, 'wb') as f:
                        f.write(image_bytes)
                    logger.info(f"✓ PNG saved to: {save_path.absolute()}")
                
                base64_result = base64.b64encode(image_bytes).decode('utf-8')
                logger.info(f"Successfully visualized diagram. Base64 length: {len(base64_result)}")
                logger.debug(f"Tool visualize_drawio_diagram output: [Base64 string, length {len(base64_result)}]")
                return base64_result
                
        except Exception as e:
            logger.error(f"Exception during draw.io visualization: {e}\n{traceback.format_exc()}")
            result = f"Error: {str(e)}"
            logger.debug(f"Tool visualize_drawio_diagram output: {result}")
            return result

@mcp.tool()
async def analyze_drawio_diagram(diagram_xml: str, intention: str, original_request: str = "") -> str:
    """
    Performs a deep audit of a Draw.io XML diagram to identify 'bizarre' errors.
    Checks for overlapping nodes, missing connections, orphans, and alignment with the request.
    
    Args:
        diagram_xml (str): The raw Draw.io XML code.
        intention (str): llm intention to call this tool
        original_request (str): The initial user instruction to compare against.
        
    Returns:
        str: A Markdown formatted audit report.
    """
    logger.debug(f"Executing tool: analyze_drawio_diagram with args: diagram_xml={diagram_xml}, original_request={original_request}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(analyze_drawio_diagram.__name__, intention, diagram_xml=diagram_xml, original_request=original_request)
    logger.info("Analyzing draw.io diagram logic")
    try:
        # 1. Parse XML
        # Handle cases where XML might be wrapped in <mxfile> or starts at <mxGraphModel>
        root = ET.fromstring(diagram_xml)
        
        nodes = {}
        edges = []
        
        # Find all mxCell elements
        for cell in root.iter('mxCell'):
            cell_id = cell.get('id')
            if not cell_id or cell_id in ('0', '1'):  # Skip root layers
                continue
                
            # Node (vertex)
            if cell.get('vertex') == '1':
                geometry = cell.find('mxGeometry')
                nodes[cell_id] = {
                    'id': cell_id,
                    'value': cell.get('value', '').replace('\n', ' '),
                    'x': float(geometry.get('x', 0)) if geometry is not None else 0,
                    'y': float(geometry.get('y', 0)) if geometry is not None else 0,
                    'w': float(geometry.get('width', 0)) if geometry is not None else 0,
                    'h': float(geometry.get('height', 0)) if geometry is not None else 0,
                    'connections': 0
                }
            
            # Edge (connection)
            elif cell.get('edge') == '1':
                edges.append({
                    'id': cell_id,
                    'source': cell.get('source'),
                    'target': cell.get('target'),
                    'value': cell.get('value', '')
                })
        
        # 2. Connection Analysis
        for edge in edges:
            if edge['source'] in nodes:
                nodes[edge['source']]['connections'] += 1
            if edge['target'] in nodes:
                nodes[edge['target']]['connections'] += 1
                
        # 3. Collision Detection (Overlaps)
        overlaps = []
        node_ids = list(nodes.keys())
        for i in range(len(node_ids)):
            for j in range(i + 1, len(node_ids)):
                n1 = nodes[node_ids[i]]
                n2 = nodes[node_ids[j]]
                
                # Check AABB collision
                if (n1['x'] < n2['x'] + n2['w'] and
                    n1['x'] + n1['w'] > n2['x'] and
                    n1['y'] < n2['y'] + n2['h'] and
                    n1['y'] + n1['h'] > n2['y']):
                    overlaps.append(f"• **Overlap**: '{n1['value']}' and '{n2['value']}' are physically colliding.")

        # 4. Logical Anomalies
        orphans = [n['value'] for n in nodes.values() if n['connections'] == 0 and n['value']]
        zero_size = [n['value'] for n in nodes.values() if (n['w'] <= 5 or n['h'] <= 5) and n['value']]

        # 5. Gap Analysis (Contextual Layer)
        gaps = []
        if original_request:
            req_lower = original_request.lower()
            
            # Categories of technology to check
            TECH_KEYWORDS = {
                "Routing Protocols": ["ospf", "bgp", "eigrp", "is-is", "rip", "static route"],
                "Overlay/Tunneling": ["mpls", "gre", "vxlan", "dmvpn", "ipsec", "vpn"],
                "Infrastructure": ["vlan", "subnet", "vrf", "stp", "lacp", "hsrp", "vrrp", "trunk"],
                "Security/Services": ["firewall", "load balancer", "f5", "nat", "acl"],
                "Device Types": ["router", "switch", "server", "pc", "laptop", "cloud", "aws", "azure"]
            }
            
            # Flatten diagram content for easy searching
            all_labels = " ".join([n['value'] for n in nodes.values()] + [e['value'] for e in edges]).lower()
            
            for category, keywords in TECH_KEYWORDS.items():
                for kw in keywords:
                    if kw in req_lower:
                        # Request mentioned this keyword, check if it exists in the diagram
                        if kw not in all_labels:
                            gaps.append(f"• **Missing {category}**: '{kw.upper()}' was requested but not found in any labels or links.")

        # 6. Generate Report
        report = ["### 📊 Diagram Audit Report"]
        
        if overlaps:
            report.append("\n#### ⚠️ Physical Issues (Bizarre Layout)")
            report.extend(overlaps)
        else:
            report.append("\n✅ **No physical overlaps detected.**")
            
        if orphans or zero_size:
            report.append("\n#### ❌ Logical Errors")
            if orphans:
                report.append(f"• **Orphan Nodes**: {', '.join(orphans)} have no connections.")
            if zero_size:
                report.append(f"• **Zero-Size Nodes**: {', '.join(zero_size)} are too small to see.")
                
        if gaps:
            report.append("\n#### 🔍 Instruction Alignment (Gap Analysis)")
            report.extend(gaps)

        # TODO: 6. LLM Visual Analysis (Future Placeholder)
        # This section is reserved for feeding the rendered PNG to a Vision LLM
        # to detect aesthetic issues or missing logical flow that XML doesn't catch.
        report.append("\n#### 👁️ Visual Semantic Analysis (Future Feature)")
        report.append("• *Note: Automated visual inspection is currently disabled. Feed the rendered image to a vision-capable LLM for final aesthetic and contextual verification.*")
            
        report.append("\n#### 📈 Graph Stats")
        result = "\n".join(report)
        logger.debug(f"Tool analyze_drawio_diagram output: {result}")
        return result

    except Exception as e:
        logger.error(f"Audit failed: {e}\n{traceback.format_exc()}")
        result = f"Error analyzing XML: {str(e)}"
        logger.debug(f"Tool analyze_drawio_diagram output: {result}")
        return result

@mcp.tool()
async def archive_current_conversation(messages: List[Dict[str, str]], intention: str, metadata: Optional[Dict] = None) -> str:
    """
    Save the current chat history to the long-term archive. Not used for skills update
    
    Args:
        messages (List[Dict]): List of message objects with 'role' and 'content'.
        intention (str): LLM intention to call this tool.
        metadata (Optional[Dict]): Additional tags or context for the archive.
        
    Returns:
        str: Outcome of the archival process.
    """
    logger.debug(f"Executing tool: archive_current_conversation with args: num_messages={len(messages)}, metadata={metadata}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(archive_current_conversation.__name__, intention, num_messages=len(messages))
    
    if not archiver:
        return "Error: Retriever Archiver is not initialized."
    
    try:
        doc_id = archiver.archive_conversation(messages, metadata=metadata)
        result = f"✅ Conversation archived successfully with document ID: {doc_id}"
        logger.debug(f"Tool archive_current_conversation output: {result}")
        return result
    except Exception as e:
        logger.error(f"Error archiving conversation: {e}\n{traceback.format_exc()}")
        result = f"❌ Failed to archive conversation: {str(e)}"
        logger.debug(f"Tool archive_current_conversation output: {result}")
        return result

@mcp.tool()
async def archive_local_document(file_path: str, intention: str, metadata: Optional[Dict] = None) -> str:
    """
    Ingest and embed a local file (Markdown, text, etc.) into the agent's knowledge base.
    
    Args:
        file_path (str): Absolute path to the file to ingest.
        intention (str): LLM intention to call this tool.
        metadata (Optional[Dict]): Additional metadata for the document.
        
    Returns:
        str: Outcome of the ingestion process.
    """
    logger.debug(f"Executing tool: archive_local_document with args: file_path={file_path}, metadata={metadata}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(archive_local_document.__name__, intention, file_path=file_path)
    
    if not archiver:
        return "Error: Retriever Archiver is not initialized."
    
    try:
        doc_id = archiver.archive_documentation(file_path, metadata=metadata)
        result = f"✅ Document '{file_path}' archived successfully with ID: {doc_id}"
        logger.debug(f"Tool archive_local_document output: {result}")
        return result
    except Exception as e:
        logger.error(f"Error archiving document: {e}\n{traceback.format_exc()}")
        result = f"❌ Failed to archive document: {str(e)}"
        logger.debug(f"Tool archive_local_document output: {result}")
        return result

@mcp.tool()
async def query_agent_archives(query: str, intention: str) -> str:
    """
    Perform a semantic RAG search across archived conversations and documentation.
    Use this to recall past solutions, technical details, or command syntax.
    
    Args:
        query (str): The question or search term.
        intention (str): LLM intention to call this tool.
        
    Returns:
        str: Augmented answer based on archived information.
    """
    logger.debug(f"Executing tool: query_agent_archives with args: query={query}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(query_agent_archives.__name__, intention, query=query)
    
    if not archiver:
        return "Error: Retriever Archiver is not initialized."
    
    try:
        result_raw = archiver.rag_query(query)
        answer = result_raw['answer']
        sources = ", ".join(result_raw['sources'])
        result = f"RECALLED INFORMATION:\n{answer}\n\nSOURCES: {sources}"
        logger.debug(f"Tool query_agent_archives output: {result}")
        return result
    except Exception as e:
        logger.error(f"Error querying archives: {e}\n{traceback.format_exc()}")
        result = f"❌ Failed to query archives: {str(e)}"
        logger.debug(f"Tool query_agent_archives output: {result}")
        return result

## API Calls adapter #####
# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT = 30.0          # seconds
MAX_RESPONSE_BYTES = 1_048_576  # 1 MB — truncate larger responses
OAUTH2_TOKEN_CACHE: dict[str, str] = {}  # simple in-process cache keyed by prefix

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class HttpMethod(str, Enum):
    GET    = "GET"
    POST   = "POST"
    PUT    = "PUT"
    PATCH  = "PATCH"
    DELETE = "DELETE"
    HEAD   = "HEAD"


class AuthMethod(str, Enum):
    NONE                    = "none"
    BASIC_AUTH              = "basic_auth"
    BEARER_TOKEN            = "bearer_token"
    API_KEY_HEADER          = "api_key_header"
    OAUTH2_CLIENT_CREDS     = "oauth2_client_credentials"


# ---------------------------------------------------------------------------
# Input model
# ---------------------------------------------------------------------------

class RestApiCallInput(BaseModel):
    """Input model for generic REST API call."""
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra="forbid",
    )

    base_url: str = Field(
        ...,
        description=(
            "Base URL of the API including scheme and host. "
            "Examples: 'https://nsxmgr.corp', 'https://api.github.com'. "
            "Used to derive the env var prefix for credential resolution."
        ),
        min_length=7,
        max_length=512,
    )

    endpoint: str = Field(
        ...,
        description=(
            "API endpoint path, starting with '/'. "
            "Examples: '/api/v1/logical-switches', '/repos/owner/repo/issues'."
        ),
        min_length=1,
        max_length=1024,
    )

    method: HttpMethod = Field(
        default=HttpMethod.GET,
        description="HTTP method: GET, POST, PUT, PATCH, DELETE, HEAD.",
    )

    auth_method: AuthMethod = Field(
        default=AuthMethod.NONE,
        description=(
            "Authentication scheme. Credentials are resolved from env vars "
            "derived from the base_url hostname — never passed as parameters. "
            "Options: none, basic_auth, bearer_token, api_key_header, "
            "oauth2_client_credentials."
        ),
    )

    body: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "Request body as a JSON object. Used for POST, PUT, PATCH. "
            "Ignored for GET, HEAD, DELETE."
        ),
    )

    query_params: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "URL query parameters as a key-value dict. "
            "Example: {'page': 1, 'per_page': 50}."
        ),
    )

    extra_headers: Optional[dict[str, str]] = Field(
        default=None,
        description=(
            "Additional HTTP headers to include in the request. "
            "Do NOT pass Authorization here — use auth_method instead."
        ),
    )

    use_proxy: bool = Field(
        default=False,
        description=(
            "If True, route the request through the proxy configured in "
            "HTTP_PROXY / HTTPS_PROXY environment variables."
        ),
    )

    verify_ssl: bool = Field(
        default=True,
        description=(
            "If False, skip TLS certificate verification. "
            "Useful for internal APIs with self-signed certs."
        ),
    )

    timeout: float = Field(
        default=DEFAULT_TIMEOUT,
        description="Request timeout in seconds.",
        ge=1.0,
        le=300.0,
    )

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("base_url must start with http:// or https://")
        if not parsed.netloc:
            raise ValueError("base_url must include a valid hostname")
        # Strip trailing slash for consistent joining
        return v.rstrip("/")

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError("endpoint must start with '/'")
        return v

    @field_validator("extra_headers")
    @classmethod
    def block_auth_headers(cls, v: Optional[dict[str, str]]) -> Optional[dict[str, str]]:
        if not v:
            return v
        forbidden = {"authorization", "x-api-key", "api-key", "x-auth-token"}
        lowered = {k.lower() for k in v}
        overlap = forbidden & lowered
        if overlap:
            raise ValueError(
                f"Do not pass auth headers directly ({overlap}). "
                "Use auth_method instead — credentials come from env vars."
            )
        return v


# ---------------------------------------------------------------------------
# Credential resolution helpers
# ---------------------------------------------------------------------------

def _env_prefix(base_url: str) -> str:
    """
    Derive an env var prefix from the base URL hostname.

    Examples:
      https://nsxmgr.corp         → NSXMGR_CORP
      https://api.github.com      → API_GITHUB_COM
      https://rabbitmq.internal:15672 → RABBITMQ_INTERNAL
    """
    parsed = urlparse(base_url)
    host = parsed.hostname or ""  # strips port
    # Replace dots/hyphens with underscores, uppercase
    prefix = re.sub(r"[.\-]", "_", host).upper()
    return prefix


def _require_env(var: str) -> str:
    """Read a required env var or raise a descriptive error."""
    val = os.environ.get(var)
    if not val:
        raise EnvironmentError(
            f"Required env var '{var}' is not set. "
            "Set it in your environment before using this auth method."
        )
    return val


def _build_auth_headers(auth_method: AuthMethod, prefix: str) -> dict[str, str]:
    """
    Resolve credentials from env vars and return the appropriate auth headers.
    Never returns raw credentials to the LLM — only injects them into headers.
    """
    logger.info(f"Building auth headers for prefix: {prefix}")
    if auth_method == AuthMethod.NONE:
        return {}

    if auth_method == AuthMethod.BASIC_AUTH:
        logger.info("Building headers for basic authentication")
        import base64
        user = _require_env(f"{prefix}_USER")
        passwd = _require_env(f"{prefix}_PASS")
        token = base64.b64encode(f"{user}:{passwd}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    if auth_method == AuthMethod.BEARER_TOKEN:
        token = _require_env(f"{prefix}_TOKEN")
        scheme = os.environ.get(f"{prefix}_TOKEN_SCHEME", "Bearer")  # default Bearer, override per API
        return {"Authorization": f"{scheme} {token}"}

    if auth_method == AuthMethod.API_KEY_HEADER:
        logger.info("Building headers for api key authentication")
        api_key = _require_env(f"{prefix}_API_KEY")
        header_name = os.environ.get(f"{prefix}_API_KEY_HEADER", "X-API-Key")
        return {header_name: api_key}

    if auth_method == AuthMethod.OAUTH2_CLIENT_CREDS:
        logger.info("Building headers for oauth2 client credentials")
        return _oauth2_client_credentials(prefix)
    logger.warning("Unknown auth method: %s", auth_method)

    return {}


def _oauth2_client_credentials(prefix: str) -> dict[str, str]:
    """
    Obtain an OAuth2 bearer token via client_credentials grant.
    Token is cached in-process to avoid redundant token requests.
    """
    if prefix in OAUTH2_TOKEN_CACHE:
        return {"Authorization": f"Bearer {OAUTH2_TOKEN_CACHE[prefix]}"}

    client_id     = _require_env(f"{prefix}_CLIENT_ID")
    client_secret = _require_env(f"{prefix}_CLIENT_SECRET")
    token_url     = _require_env(f"{prefix}_TOKEN_URL")

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        resp.raise_for_status()
        token = resp.json()["access_token"]

    OAUTH2_TOKEN_CACHE[prefix] = token
    logger.info("OAuth2 token obtained and cached for prefix %s", prefix)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Proxy helper
# ---------------------------------------------------------------------------

def _proxy_settings() -> Optional[str]:
    """Return proxy URL from env, preferring HTTPS_PROXY over HTTP_PROXY."""
    return os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")


# ---------------------------------------------------------------------------
# HTTP execution
# ---------------------------------------------------------------------------

async def _execute_request(params: RestApiCallInput) -> dict[str, Any]:
    """
    Build and execute the HTTP request. Returns a structured result dict.
    """
    url = params.base_url + params.endpoint
    prefix = _env_prefix(params.base_url)

    # Resolve auth headers
    try:
        auth_headers = _build_auth_headers(params.auth_method, prefix)
    except EnvironmentError as e:
        return {
            "success": False,
            "error": str(e),
            "hint": (
                f"Env var prefix derived from '{params.base_url}' is '{prefix}'. "
                f"Set the required vars (e.g. {prefix}_USER, {prefix}_PASS) "
                "in your environment."
            ),
        }

    # Merge headers
    headers: dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
    headers.update(auth_headers)
    if params.extra_headers:
        logger.info(f"Extra headers: {params.extra_headers}")
        headers.update(params.extra_headers)

    # Proxy
    proxy: Optional[str] = None
    if params.use_proxy:
        proxy = _proxy_settings()
        if not proxy:
            return {
                "success": False,
                "error": "use_proxy=True but neither HTTP_PROXY nor HTTPS_PROXY is set in environment.",
            }

    # Build httpx client kwargs
    client_kwargs: dict[str, Any] = {
        "timeout": params.timeout,
        "verify": params.verify_ssl,
    }
    if proxy:
        client_kwargs["proxy"] = proxy

    # Body — only attach for mutating methods
    body_bytes: Optional[bytes] = None
    if params.body and params.method in (HttpMethod.POST, HttpMethod.PUT, HttpMethod.PATCH):
        body_bytes = json.dumps(params.body).encode("utf-8")

    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            response = await client.request(
                method=params.method.value,
                url=url,
                headers=headers,
                params=params.query_params,
                content=body_bytes,
            )

        # Parse response
        status = response.status_code
        response_headers = dict(response.headers)
        content_type = response_headers.get("content-type", "")

        # Truncate large responses
        raw = response.content
        truncated = False
        if len(raw) > MAX_RESPONSE_BYTES:
            raw = raw[:MAX_RESPONSE_BYTES]
            truncated = True

        # Try JSON decode
        body_out: Any
        try:
            body_out = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            body_out = raw.decode("utf-8", errors="replace")

        return {
            "success": status < 400,
            "status_code": status,
            "url": str(response.url),
            "method": params.method.value,
            "auth_method": params.auth_method.value,
            "env_prefix": prefix,
            "response_body": body_out,
            "response_headers": response_headers,
            "truncated": truncated,
        }

    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "status_code": e.response.status_code,
            "error": f"HTTP error {e.response.status_code}: {e.response.text[:500]}",
        }
    except httpx.TimeoutException:
        return {
            "success": False,
            "error": f"Request timed out after {params.timeout}s. Consider increasing the timeout parameter.",
        }
    except httpx.ConnectError as e:
        return {
            "success": False,
            "error": f"Connection failed: {e}. Check base_url and network connectivity.",
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Unexpected error: {type(e).__name__}: {e}",
        }


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------

@mcp.tool(
    name="rest_api_call",
    annotations={
        "title": "Generic REST API Call",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def rest_api_call(params: RestApiCallInput, intention: str) -> str:
    """
    Execute an HTTP request against any REST API endpoint.

    Credentials are NEVER passed as parameters. The tool derives an env var
    prefix from the base_url hostname and reads credentials from the
    environment at call time. The LLM only specifies which auth scheme to use.

    Env var naming convention (examples):
      https://nsxmgr.corp          → prefix NSXMGR_CORP
        Basic auth:   NSXMGR_CORP_USER, NSXMGR_CORP_PASS
        Bearer:       NSXMGR_CORP_TOKEN
        API key:      NSXMGR_CORP_API_KEY (header name: NSXMGR_CORP_API_KEY_HEADER)
        OAuth2:       NSXMGR_CORP_CLIENT_ID, NSXMGR_CORP_CLIENT_SECRET, NSXMGR_CORP_TOKEN_URL

      https://api.github.com       → prefix API_GITHUB_COM
        Bearer:       API_GITHUB_COM_TOKEN

    Args:
        params (RestApiCallInput): Validated input containing:
            - base_url (str): API base URL (scheme + host)
            - endpoint (str): Endpoint path starting with '/'
            - method (HttpMethod): GET | POST | PUT | PATCH | DELETE | HEAD
            - auth_method (AuthMethod): Authentication scheme
            - body (dict | None): Request body for POST/PUT/PATCH
            - query_params (dict | None): URL query parameters
            - extra_headers (dict | None): Additional headers (no auth headers)
            - use_proxy (bool): Route through HTTP_PROXY / HTTPS_PROXY
            - verify_ssl (bool): Verify TLS certificates (default True)
            - timeout (float): Request timeout in seconds
        intention (str): The user's intention for this API call.

    Returns:
        str: JSON-formatted response containing:
            {
              "success": bool,
              "status_code": int,
              "url": str,
              "method": str,
              "auth_method": str,
              "env_prefix": str,        # derived prefix used for credential lookup
              "response_body": any,     # parsed JSON or raw text
              "response_headers": dict,
              "truncated": bool,        # True if response exceeded 1 MB
              "error": str              # only present on failure
            }
    """
    logger.debug(f"Executing tool: rest_api_call with args: params={params.model_dump(exclude={'body'}, exclude_none=True)}")
    logger.info(f"calling: {rest_api_call.__name__}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(rest_api_call.__name__,intention, query=params.model_dump(exclude={"body"}, exclude_none=True))
    result_raw = await _execute_request(params)
    result = json.dumps(result_raw, indent=2, default=str)
    logger.debug(f"Tool rest_api_call output: {result}")
    return result


@mcp.tool(
    name="rest_api_inspect_env",
    annotations={
        "title": "Inspect REST API Env Var Prefix",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def rest_api_inspect_env(base_url: str, intention: str) -> str:
    """
    Show what env var prefix and variable names will be used for a given base_url.

    Use this before calling rest_api_call to verify which environment variables
    need to be set for a given API endpoint and auth method.

    Args:
        base_url (str): The API base URL to inspect (e.g. 'https://nsxmgr.corp').

    Returns:
        str: JSON showing the derived prefix and all expected variable names per auth method.
    """
    logger.debug(f"Executing tool: rest_api_inspect_env with args: base_url={base_url}")
    logger.info(f"calling: {rest_api_inspect_env.__name__}")
    logger.info(f"Intention: {intention}")
    log_tool_call_to_csv(rest_api_inspect_env.__name__, intention)
    try:
        base_url = base_url.rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return json.dumps({"error": "Invalid base_url. Must start with http:// or https://"})

        prefix = _env_prefix(base_url)
        logger.info(f"searching for variables on prefix: {prefix}")

        expected_vars = {
            "basic_auth": [f"{prefix}_USER", f"{prefix}_PASS"],
            "bearer_token": [f"{prefix}_TOKEN"],
            "api_key_header": [f"{prefix}_API_KEY", f"{prefix}_API_KEY_HEADER (optional, default: X-API-Key)"],
            "oauth2_client_credentials": [
                f"{prefix}_CLIENT_ID",
                f"{prefix}_CLIENT_SECRET",
                f"{prefix}_TOKEN_URL",
            ],
        }
        logger.info(f"expected variables: {expected_vars}")

        # Check which vars are already set (presence only, not values)
        presence: dict[str, dict[str, bool]] = {}
        for method, vars_ in expected_vars.items():
            presence[method] = {}
            for var in vars_:
                clean_var = var.split(" ")[0]  # strip "(optional...)" notes
                presence[method][clean_var] = bool(os.environ.get(clean_var))

        global_proxy = {
            "HTTP_PROXY": bool(os.environ.get("HTTP_PROXY")),
            "HTTPS_PROXY": bool(os.environ.get("HTTPS_PROXY")),
        }

        result = json.dumps({
            "base_url": base_url,
            "hostname": parsed.hostname,
            "env_prefix": prefix,
            "expected_vars_by_auth_method": expected_vars,
            "vars_present_in_env": presence,
            "proxy_vars": global_proxy,
        }, indent=2)
        logger.debug(f"Tool rest_api_inspect_env output: {result}")
        return result

    except Exception as e:
        result = json.dumps({"error": str(e)})
        logger.debug(f"Tool rest_api_inspect_env output: {result}")
        return result


if __name__ == "__main__":
    try:
        mcp.run(transport="streamable-http")
    except KeyboardInterrupt:
        logger.info("Interrupted by user, Exiting...")
    except Exception as e:
        logger.error(f"Error running MCP server: {e}\n{traceback.format_exc()}")