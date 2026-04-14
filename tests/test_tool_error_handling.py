import pytest
import asyncio
from unittest.mock import patch, MagicMock

# Import the tools we want to test from mcp_servers
from mcp_servers import (
    net_find_network_interfaces,
    net_get_routing_table,
    net_capture_network_traffic,
    net_run_commands_on_device
)

@pytest.mark.asyncio
@patch('mcp_servers.DeviceSSHSession')
async def test_mcp_tool_catches_ssh_exception(mock_ssh_session_class):
    """Test that networking tools catch exceptions and return error strings rather than raising."""
    
    # Configure the mock to raise an exception when execute_command is called
    mock_session_instance = MagicMock()
    mock_session_instance.execute_command.side_effect = ConnectionError("Connection refused by host")
    mock_ssh_session_class.return_value = mock_session_instance
    
    # 1. Test net_find_network_interfaces
    result_1 = await net_find_network_interfaces("192.168.1.1", "test_intention")
    assert isinstance(result_1, str)
    assert "Error finding network interfaces" in result_1
    assert "ConnectionError" in result_1
    
    # 2. Test net_get_routing_table
    result_2 = await net_get_routing_table("192.168.1.1", "test_intention")
    assert isinstance(result_2, str)
    assert "Error getting routing table" in result_2
    assert "ConnectionError" in result_2

    # 3. Test net_run_commands_on_device
    result_3 = await net_run_commands_on_device("192.168.1.1", ["show ip route"], "test_intention")
    assert isinstance(result_3, str)
    assert "Error running commands" in result_3
    assert "ConnectionError" in result_3

@pytest.mark.asyncio
@patch('mcp_servers.DeviceSSHSession')
async def test_mcp_tool_catches_init_exception(mock_ssh_session_class):
    """Test that networking tools catch exceptions during session initialization."""
    
    # Configure the mock to raise an exception when the session is instantiated
    mock_ssh_session_class.side_effect = ValueError("Invalid IP address format")
    
    result = await net_capture_network_traffic("invalid_ip", "eth0", 10, "test_intention")
    assert isinstance(result, str)
    assert "Error capturing network traffic" in result
    assert "ValueError" in result
