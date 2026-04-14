"""
tests/test_ssh_executor.py
--------------------------
Unit tests for utils/ssh_executor.DeviceSSHSession.

Run with:
    conda run -n test_langchain_env pytest tests/test_ssh_executor.py -v
"""
import os
import re
import time
import pytest
from unittest.mock import MagicMock, patch, call, PropertyMock

from utils.ssh_executor import DeviceSSHSession, DEVICE_PROFILES, _VENDOR_HINTS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MockShell(MagicMock):
    def __init__(self, prompt_response: str = "Router#\n", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.closed = False
        self._prompt_response = prompt_response.encode()
        self._data_available = False
        self.exit_status_ready_val = False
        self.send = MagicMock(side_effect=self._mock_send)

    def recv_ready(self):
        return self._data_available

    def recv(self, n=65535):
        self._data_available = False
        return self._prompt_response

    def _mock_send(self, data):
        self._data_available = True
        return len(data)

    def exit_status_ready(self):
        return self.exit_status_ready_val

def _make_mock_shell(prompt_response: str = "Router#\n"):
    """Return a stateful MockShell that simulates a paramiko interactive shell channel."""
    return MockShell(prompt_response=prompt_response)


def _make_mock_ssh_client(shell):
    """Return a MagicMock SSHClient that yields `shell` on invoke_shell()."""
    client = MagicMock()
    client.invoke_shell.return_value = shell
    return client


# ---------------------------------------------------------------------------
# __init__ — credential resolution
# ---------------------------------------------------------------------------

class TestInit:
    def test_explicit_credentials_used(self):
        dev = DeviceSSHSession("10.0.0.1", username="alice", password="secret", model="cisco")
        assert dev.username == "alice"
        assert dev.password == "secret"
        assert dev.management_ip == "10.0.0.1"
        assert dev.model == "cisco"

    def test_env_var_fallback(self, monkeypatch):
        monkeypatch.setenv("DEVICES_SSH_USERNAME", "envuser")
        monkeypatch.setenv("DEVICES_SSH_PASSWORD", "envpass")
        dev = DeviceSSHSession("10.0.0.2")
        assert dev.username == "envuser"
        assert dev.password == "envpass"

    def test_defaults_when_no_env(self, monkeypatch):
        monkeypatch.delenv("DEVICES_SSH_USERNAME", raising=False)
        monkeypatch.delenv("DEVICES_SSH_PASSWORD", raising=False)
        dev = DeviceSSHSession("10.0.0.3")
        assert dev.username == "admin"
        assert dev.password == "password"

    def test_unknown_model_falls_back_to_cisco_profile(self):
        dev = DeviceSSHSession("10.0.0.4", model="completely_unknown_vendor")
        assert dev.model == "cisco"
        assert dev._profile == DEVICE_PROFILES["cisco"]

    def test_known_model_profile_loaded(self):
        for model_key in DEVICE_PROFILES:
            dev = DeviceSSHSession("10.0.0.5", model=model_key)
            assert dev.model == model_key
            assert dev._profile == DEVICE_PROFILES[model_key]

    def test_intelligent_model_resolution(self):
        # 'nokia sr os' matches 'nokia' hints
        dev = DeviceSSHSession("10.0.0.6", model="nokia sr os")
        assert dev.model == "nokia"
        assert dev._profile == DEVICE_PROFILES["nokia"]



# ---------------------------------------------------------------------------
# from_snmp_sysdescr factory
# ---------------------------------------------------------------------------

class TestFromSnmpSysdescr:
    @pytest.mark.parametrize("sysdescr, expected_model", [
        # _VENDOR_HINTS is checked in dict insertion order.
        # 'cisco' matches on 'cisco ios xe', 'cisco ios', or 'nx-os'.
        # 'cisco_xr' matches on 'cisco ios xr' or 'iosxr'.
        # Since 'cisco' is first, a string with 'cisco ios xr' also hits 'cisco ios' → matches cisco.
        # Use 'iosxr' (no space) to uniquely identify XR without the ambiguity.
        ("Cisco IOS XE Software",                    "cisco"),
        ("IOS XR Software (iosxr), Version 7.5",     "cisco_xr"),  # 'iosxr' matches, skips cisco entry
        ("Juniper Networks, Inc. EX Series",          "juniper"),
        ("Palo Alto Networks PAN-OS 11.0",            "paloalto"),
        ("Huawei VRP (R) software",                   "huawei"),
        ("Nokia TIMOS 22.9",                          "nokia"),
        ("Arista Networks EOS version 4.28",          "arista"),
        ("FortiGate-100F v7.2.4",                     "fortinet"),
        ("H3C Comware Platform Software",             "hpe_comware"),
        ("ProCurve Switch 2650",                      "hpe_procurve"),
    ])
    def test_auto_detect_known_vendors(self, sysdescr, expected_model):
        dev = DeviceSSHSession.from_snmp_sysdescr("1.2.3.4", sysdescr=sysdescr)
        assert dev.model == expected_model

    def test_unknown_vendor_defaults_to_cisco(self):
        dev = DeviceSSHSession.from_snmp_sysdescr("1.2.3.4", sysdescr="Some Unknown Box v1.0")
        assert dev.model == "cisco"

    def test_kwargs_passed_through(self):
        dev = DeviceSSHSession.from_snmp_sysdescr("1.2.3.4", sysdescr="Juniper", username="bob")
        assert dev.username == "bob"


# ---------------------------------------------------------------------------
# connect / disconnect lifecycle
# ---------------------------------------------------------------------------

class TestConnect:
    @patch.object(DeviceSSHSession, "_drain")
    @patch("utils.ssh_executor.paramiko.SSHClient")
    def test_connect_calls_paramiko(self, mock_client_cls, mock_drain):
        shell = _make_mock_shell()
        client = _make_mock_ssh_client(shell)
        mock_client_cls.return_value = client

        dev = DeviceSSHSession("10.0.0.1", username="u", password="p", model="cisco")
        dev.connect()

        client.connect.assert_called_once_with(
            "10.0.0.1",
            username="u",
            password="p",
            timeout=10,
            look_for_keys=False,
            allow_agent=False,
        )
        client.invoke_shell.assert_called_once()

    @patch.object(DeviceSSHSession, "_drain")
    @patch("utils.ssh_executor.paramiko.SSHClient")
    def test_idempotent_connect(self, mock_client_cls, mock_drain):
        """Calling connect() twice when already connected is a no-op."""
        shell = _make_mock_shell()
        client = _make_mock_ssh_client(shell)
        mock_client_cls.return_value = client

        dev = DeviceSSHSession("10.0.0.1", username="u", password="p", model="linux")
        dev.connect()
        dev.connect()  # second call should be ignored

        assert client.connect.call_count == 1

    @patch.object(DeviceSSHSession, "_drain")
    @patch("utils.ssh_executor.paramiko.SSHClient")
    def test_disconnect_clears_references(self, mock_client_cls, mock_drain):
        shell = _make_mock_shell()
        client = _make_mock_ssh_client(shell)
        mock_client_cls.return_value = client

        dev = DeviceSSHSession("10.0.0.1", username="u", password="p", model="linux")
        dev.connect()
        dev.disconnect()

        assert dev._shell is None
        assert dev._ssh is None

    @patch.object(DeviceSSHSession, "_drain")
    @patch("utils.ssh_executor.paramiko.SSHClient")
    def test_pagination_cmd_sent_on_connect(self, mock_client_cls, mock_drain):
        """Cisco profile should send 'terminal length 0' after connecting."""
        shell = _make_mock_shell()
        client = _make_mock_ssh_client(shell)
        mock_client_cls.return_value = client

        dev = DeviceSSHSession("10.0.0.1", username="u", password="p", model="cisco")
        dev.connect()

        sent_cmds = [c[0][0] for c in shell.send.call_args_list]
        assert any("terminal length 0" in cmd for cmd in sent_cmds)

    @patch.object(DeviceSSHSession, "_drain")
    @patch("utils.ssh_executor.paramiko.SSHClient")
    def test_linux_no_pagination_cmd(self, mock_client_cls, mock_drain):
        """Linux profile has no pagination_cmd — shell.send should NOT be called during connect."""
        shell = _make_mock_shell()  # recv_ready=False → _drain returns immediately
        client = _make_mock_ssh_client(shell)
        mock_client_cls.return_value = client

        dev = DeviceSSHSession("10.0.0.1", username="u", password="p", model="linux")
        dev.connect()

        # send should not have been called (no pagination for linux)
        shell.send.assert_not_called()


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------

class TestContextManager:
    @patch.object(DeviceSSHSession, "_drain")
    @patch("utils.ssh_executor.paramiko.SSHClient")
    def test_enter_connects_exit_disconnects(self, mock_client_cls, mock_drain):
        shell = _make_mock_shell()
        client = _make_mock_ssh_client(shell)
        mock_client_cls.return_value = client

        dev = DeviceSSHSession("10.0.0.1", username="u", password="p", model="linux")
        with dev as d:
            assert d is dev
            assert client.connect.call_count == 1

        shell.close.assert_called_once()
        client.close.assert_called_once()


# ---------------------------------------------------------------------------
# _wait_for_prompt timeout
# ---------------------------------------------------------------------------

class TestWaitForPromptTimeout:
    @patch.object(DeviceSSHSession, "_drain")
    @patch("utils.ssh_executor.paramiko.SSHClient")
    def test_timeout_raises(self, mock_client_cls, mock_drain):
        shell = _make_mock_shell()
        client = _make_mock_ssh_client(shell)
        mock_client_cls.return_value = client

        dev = DeviceSSHSession("10.0.0.1", username="u", password="p", model="cisco",
                               command_timeout=1)
        dev._ssh = client
        dev._shell = shell
        dev._profile = DEVICE_PROFILES["cisco"]

        with pytest.raises(TimeoutError, match="Prompt not seen"):
            dev._wait_for_prompt(timeout=1)


# ---------------------------------------------------------------------------
# execute_command — output stripping
# ---------------------------------------------------------------------------

class TestExecuteCommand:
    @patch.object(DeviceSSHSession, "_drain")
    @patch("utils.ssh_executor.paramiko.SSHClient")
    def test_echo_and_prompt_stripped(self, mock_client_cls, mock_drain):
        """Returns output without the command echo and trailing prompt."""
        raw = "show version\r\nCisco IOS XE 17.9.1\r\nRouter#\r\n"
        shell = _make_mock_shell()
        shell._prompt_response = raw.encode() # Override prompt response
        shell._data_available = True
        client = _make_mock_ssh_client(shell)
        mock_client_cls.return_value = client

        dev = DeviceSSHSession("10.0.0.1", username="u", password="p", model="cisco")
        dev._ssh = client
        dev._shell = shell

        result = dev.execute_command("show version")
        assert "Cisco IOS XE" in result
        # Echo line should be removed
        assert result.strip().startswith("Cisco")
        # Trailing prompt should be removed
        assert not result.strip().endswith("#")

    @patch.object(DeviceSSHSession, "_drain")
    @patch("utils.ssh_executor.paramiko.SSHClient")
    def test_execute_command_auto_connects(self, mock_client_cls, mock_drain):
        """execute_command should call connect() if not already connected."""
        shell = _make_mock_shell()
        shell._prompt_response = b"show clock\r\n15:00:00 UTC\r\nRouter#\r\n"
        client = _make_mock_ssh_client(shell)
        mock_client_cls.return_value = client

        dev = DeviceSSHSession("10.0.0.1", username="u", password="p", model="cisco")
        dev.execute_command("show clock")

        client.connect.assert_called_once()


# ---------------------------------------------------------------------------
# execute_privileged_command — enable escalation
# ---------------------------------------------------------------------------

class TestExecutePrivilegedCommand:
    @patch.object(DeviceSSHSession, "_drain")
    @patch("utils.ssh_executor.paramiko.SSHClient")
    def test_enable_cmd_sent_for_cisco(self, mock_client_cls, mock_drain):
        """Cisco profile should send 'enable' before the main command."""
        shell = _make_mock_shell()

        responses = [
            b"Router>",       # after enable
            b"Router#\n",     # privileged prompt
            b"show run\r\nBuilding configuration...\r\nRouter#\r\n",  # command output
        ]
        
        # We need a custom send to pop responses queue 
        # But for magicmock the responses are enough if we use simple side_effect for recv
        recv_iter = iter(responses)
        shell.recv_ready = MagicMock(return_value=True) # Always true so wait_for_prompt grabs
        shell.recv = MagicMock(side_effect=lambda *x: next(recv_iter))

        client = _make_mock_ssh_client(shell)
        mock_client_cls.return_value = client

        dev = DeviceSSHSession("10.0.0.1", username="u", password="p", model="cisco")
        dev._ssh = client
        dev._shell = shell

        dev.execute_privileged_command("show run")

        sent = [c[0][0] for c in shell.send.call_args_list]
        assert any("enable" in s for s in sent)

    @patch.object(DeviceSSHSession, "_drain")
    @patch("utils.ssh_executor.paramiko.SSHClient")
    def test_no_enable_for_juniper(self, mock_client_cls, mock_drain):
        """JunOS has no enable concept — enable_cmd is None for juniper profile."""
        shell = _make_mock_shell()
        shell._prompt_response = b"user@host> \n"
        shell._data_available = True
        client = _make_mock_ssh_client(shell)
        mock_client_cls.return_value = client

        dev = DeviceSSHSession("10.0.0.1", username="u", password="p", model="juniper")
        dev._ssh = client
        dev._shell = shell

        dev.execute_privileged_command("show route summary")
        sent = [c[0][0] for c in shell.send.call_args_list]
        # enable should NOT have been sent
        assert not any("enable" in s for s in sent)


# ---------------------------------------------------------------------------
# execute_config_commands — sequential execution
# ---------------------------------------------------------------------------

class TestExecuteConfigCommands:
    @patch.object(DeviceSSHSession, "execute_command", return_value="ok")
    def test_calls_execute_command_per_command(self, mock_exec):
        dev = DeviceSSHSession("10.0.0.1")
        results = dev.execute_config_commands(["cmd1", "cmd2", "cmd3"])

        assert mock_exec.call_count == 3
        assert results == ["ok", "ok", "ok"]

    @patch.object(DeviceSSHSession, "execute_command", return_value="output")
    def test_returns_list_of_outputs(self, mock_exec):
        dev = DeviceSSHSession("10.0.0.1")
        results = dev.execute_config_commands(["a", "b"])
        assert isinstance(results, list)
        assert len(results) == 2


# ---------------------------------------------------------------------------
# execute_long_running — delegates to execute_command with timeout
# ---------------------------------------------------------------------------

class TestExecuteLongRunning:
    @patch.object(DeviceSSHSession, "execute_command", return_value="capture output")
    def test_delegates_with_timeout(self, mock_exec):
        dev = DeviceSSHSession("10.0.0.1")
        result = dev.execute_long_running("tcpdump -i eth0", timeout=90)

        mock_exec.assert_called_once_with("tcpdump -i eth0", timeout=90)
        assert result == "capture output"


# ---------------------------------------------------------------------------
# reconnect
# ---------------------------------------------------------------------------

class TestReconnect:
    @patch.object(DeviceSSHSession, "_drain")
    @patch("utils.ssh_executor.paramiko.SSHClient")
    def test_reconnect_disconnects_then_connects(self, mock_client_cls, mock_drain):
        shell = _make_mock_shell()
        client = _make_mock_ssh_client(shell)
        mock_client_cls.return_value = client

        dev = DeviceSSHSession("10.0.0.1", username="u", password="p", model="linux")
        dev.connect()
        dev.reconnect()

        # connect() called twice (initial + after disconnect)
        assert client.connect.call_count == 2


# ---------------------------------------------------------------------------
# _strip_command_echo — static method
# ---------------------------------------------------------------------------

class TestStripCommandEcho:
    def test_removes_echo_line(self):
        raw = "show version\r\nCisco IOS XE\r\nRouter#"
        result = DeviceSSHSession._strip_command_echo("show version", raw)
        assert "show version" not in result
        assert "Cisco IOS XE" in result

    def test_removes_trailing_prompt(self):
        raw = "show ip route\r\n10.0.0.0/8 via 10.1.1.1\r\nRouter#"
        result = DeviceSSHSession._strip_command_echo("show ip route", raw)
        assert not result.strip().endswith("#")

    def test_passthrough_when_no_echo(self):
        raw = "line1\r\nline2\r\nline3"
        result = DeviceSSHSession._strip_command_echo("some_command", raw)
        assert "line1" in result


# ---------------------------------------------------------------------------
# __repr__
# ---------------------------------------------------------------------------

class TestRepr:
    def test_repr_shows_ip_and_model(self):
        dev = DeviceSSHSession("192.168.1.1", model="juniper")
        r = repr(dev)
        assert "192.168.1.1" in r
        assert "juniper" in r
        assert "connected=False" in r
