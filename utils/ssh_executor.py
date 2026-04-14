"""
device_ssh_session.py
---------------------
Persistent SSH session manager for multi-vendor network devices.

Supported models:
    cisco, cisco_nxos, cisco_xr, juniper, paloalto, huawei,
    nokia, nokia_mdcli, arista, fortinet, hpe_comware, hpe_procurve, linux

Usage:
    # Context manager (auto connect/disconnect)
    with DeviceSSHSession("192.168.1.1", model="cisco") as dev:
        print(dev.execute_command("show ip interface brief"))
        print(dev.execute_privileged_command("show running-config"))

    # Persistent session (reuse connection across many commands)
    dev = DeviceSSHSession("192.168.1.1", model="cisco")
    dev.connect()
    dev.execute_command("show version")
    dev.execute_command("show ip bgp summary")
    dev.disconnect()

    # Long-running command (e.g. packet capture)
    with DeviceSSHSession("10.0.0.1", model="linux") as dev:
        output = dev.execute_long_running("timeout 60 tcpdump -i eth0 -n", timeout=75)

    # Auto-detect vendor from SNMP sysDescr
    dev = DeviceSSHSession.from_snmp_sysdescr("10.0.0.1", sysdescr="Cisco IOS XE")
"""

import os
import re
import time
import traceback
import logging
from typing import Optional

import paramiko

logger = logging.getLogger(__name__)


_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[mABCDEFGHJKLMPSTXZ]|\x1b\][^\x07]*\x07|\x1b[()][AB012]|\x1b[=>]|\x1b\[\?[0-9]+[hl]")

# ---------------------------------------------------------------------------
# Per-vendor profiles
# ---------------------------------------------------------------------------

DEVICE_PROFILES: dict[str, dict] = {
    # ---------------------------------------------------------------- Cisco IOS / IOS-XE
    "cisco": {
        "prompt": re.compile(r"[a-zA-Z0-9_\-]+[>#]\s*$"),
        "pagination_cmd": "terminal length 0",
        "enable_cmd": "enable",
    },
    # ---------------------------------------------------------------- Cisco NX-OS
    "cisco_nxos": {
        "prompt": re.compile(r"[a-zA-Z0-9_\-]+[>#]\s*$"),
        "pagination_cmd": "terminal length 0",
        "enable_cmd": "enable",
    },
    # ---------------------------------------------------------------- Cisco IOS-XR
    # XR drops you straight into privileged mode — no enable needed.
    "cisco_xr": {
        "prompt": re.compile(r"[a-zA-Z0-9_\-/]+#\s*$"),
        "pagination_cmd": "terminal length 0",
        "enable_cmd": None,
    },
    # ---------------------------------------------------------------- Juniper JunOS
    "juniper": {
        "prompt": re.compile(r"[a-zA-Z0-9_\-@]+[>%]\s*$"),
        "pagination_cmd": "set cli screen-length 0",
        "enable_cmd": None,  # JunOS CLI is already privileged
    },
    # ---------------------------------------------------------------- Palo Alto PAN-OS
    # SSH drops into a restricted CLI. Pagination must be disabled per-session.
    # Consider using the XML/REST API for heavy automation.
    "paloalto": {
        "prompt": re.compile(r"[a-zA-Z0-9_\-@]+[>#]\s*$"),
        "pagination_cmd": "set cli pager off",
        "enable_cmd": None,  # access controlled by RBAC, no enable concept
    },
    "paloalto_ion": {
        "prompt": re.compile(r"[a-zA-Z0-9_\-@]+[>#]\s*$"),
        "pagination_cmd": "set paging off",
        "enable_cmd": None,  # access controlled by RBAC, no enable concept
    },
    # ---------------------------------------------------------------- Huawei VRP
    # Prompt changes shape by view:
    #   <hostname>          = user view  (read-only)
    #   [hostname]          = system view (config mode, via 'system-view')
    #   [hostname-iface]    = interface submode
    "huawei": {
        "prompt": re.compile(r"[<\[][a-zA-Z0-9_\-]+[>\]]"),
        "pagination_cmd": "screen-length 0 temporary",
        "enable_cmd": None,  # use system-view for config, not enable
    },
    # ---------------------------------------------------------------- Nokia SR OS (classic CLI)
    "nokia": {
        # Matches: A:SYD_RTR#  *A:SYD_RTR#  A:SYD_RTR>config>router#
        "prompt": re.compile(r"[*]?[A-Za-z]:[A-Za-z0-9_\-]+(>[A-Za-z0-9_\-]+)*#\s*$", re.MULTILINE),
        "pagination_cmd": "environment no more",
        "enable_cmd": None,
    },
    # ---------------------------------------------------------------- Nokia SR OS (MD-CLI)
    # MD-CLI prompts look like: (ex)[/] A:hostname#
    "nokia_mdcli": {
        "prompt": re.compile(r"\(ex\)?\[.*?\]\s*[A-Za-z0-9_\-]+#\s*$"),
        "pagination_cmd": "environment console length 0",
        "enable_cmd": None,
    },
    # ---------------------------------------------------------------- Arista EOS
    "arista": {
        "prompt": re.compile(r"[a-zA-Z0-9_\-]+[>#]\s*$"),
        "pagination_cmd": "terminal length 0",
        "enable_cmd": "enable",
    },
    # ---------------------------------------------------------------- Fortinet FortiOS
    # No pagination toggle; FortiOS SSH has an aggressive idle timeout —
    # keep-alives or reconnect logic are recommended for long-lived sessions.
    "fortinet": {
        "prompt": re.compile(r"[a-zA-Z0-9_\-]+ [#$]\s*$"),
        "pagination_cmd": None,
        "enable_cmd": None,
    },
    # ---------------------------------------------------------------- HPE Comware (H3C / FlexFabric)
    "hpe_comware": {
        "prompt": re.compile(r"[<\[][a-zA-Z0-9_\-]+[>\]]"),
        "pagination_cmd": "screen-length disable",
        "enable_cmd": None,
    },
    # ---------------------------------------------------------------- HPE ProCurve / ArubaOS-Switch
    "hpe_procurve": {
        "prompt": re.compile(r"[a-zA-Z0-9_\-]+[#>]\s*$"),
        "pagination_cmd": "no page",
        "enable_cmd": None,
    },
    # ---------------------------------------------------------------- Linux
    "linux": {
        "prompt": re.compile(r"[#$]\s*$"),
        "pagination_cmd": None,
        "enable_cmd": "sudo -s",
    },
}

# Keyword hints for auto-detecting vendor from SNMP sysDescr
_VENDOR_HINTS: dict[str, list[str]] = {
    "cisco":       ["cisco ios xe", "cisco ios", "nx-os"],
    "cisco_xr":    ["cisco ios xr", "iosxr"],
    "juniper":     ["juniper", "junos"],
    "paloalto":    ["palo alto", "pan-os", "panorama"],
    "huawei":      ["huawei", "vrp"],
    "nokia":       ["nokia", "timos", "sros"],
    "arista":      ["arista", "eos", "veos"],
    "fortinet":    ["fortinet", "fortios", "fortigate"],
    "hpe_comware": ["comware", "h3c"],
    "hpe_procurve":["procurve", "arubaos-switch"],
}


# ---------------------------------------------------------------------------
# Session class
# ---------------------------------------------------------------------------

class DeviceSSHSession:
    """
    Persistent SSH session for multi-vendor network devices.

    Maintains a single SSH connection and shell channel across multiple
    command calls. Prompt-driven output capture eliminates blind sleeps.
    """

    def __init__(
        self,
        management_ip: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        model: str = "cisco",
        connect_timeout: int = 10,
        command_timeout: int = 30,
    ):
        """
        Args:
            management_ip:   Device IP or hostname.
            username:        SSH username. Falls back to DEVICES_SSH_USERNAME env var.
            password:        SSH password. Falls back to DEVICES_SSH_PASSWORD env var.
            model:           Device model key (see DEVICE_PROFILES).
            connect_timeout: TCP connect timeout in seconds.
            command_timeout: Default per-command timeout in seconds.
        """
        self.management_ip = management_ip
        self.username = username or os.environ.get("DEVICES_SSH_USERNAME", "admin")
        self.password = password or os.environ.get("DEVICES_SSH_PASSWORD", "password")
        self.model = self._resolve_model(model)
        self.connect_timeout = connect_timeout
        self.command_timeout = command_timeout

        self._profile = DEVICE_PROFILES[self.model]
        self._ssh: Optional[paramiko.SSHClient] = None
        self._shell: Optional[paramiko.Channel] = None

        logger.debug(
            f"DeviceSSHSession initialised — ip={management_ip} "
            f"model={model} user={self.username}"
        )

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Establish SSH connection and open a persistent interactive shell."""
        if self._shell and not self._shell.closed:
            return  # already connected

        self._ssh = paramiko.SSHClient()
        self._ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self._ssh.connect(
            self.management_ip,
            username=self.username,
            password=self.password,
            timeout=self.connect_timeout,
            look_for_keys=False,
            allow_agent=False,
        )

        self._shell = self._ssh.invoke_shell(width=220, height=50)
        self._drain(timeout=5)  # clear MOTD / banner

        # Disable pagination so we never hit --More-- prompts
        pagination_cmd = self._profile.get("pagination_cmd")
        if pagination_cmd:
            self._send_and_wait(pagination_cmd)

        logger.debug(f"Connected to {self.management_ip}")

    def disconnect(self) -> None:
        """Close shell channel and SSH connection."""
        if self._shell:
            try:
                self._shell.close()
            except Exception:
                pass
            self._shell = None
        if self._ssh:
            try:
                self._ssh.close()
            except Exception:
                pass
            self._ssh = None
        logger.debug(f"Disconnected from {self.management_ip}")

    def reconnect(self) -> None:
        """Force a fresh connection (useful after idle timeout)."""
        self.disconnect()
        self.connect()

    def __enter__(self) -> "DeviceSSHSession":
        self.connect()
        return self

    def __exit__(self, *_) -> None:
        self.disconnect()

    # ------------------------------------------------------------------
    # Core send / receive primitives
    # ------------------------------------------------------------------

    def _wait_for_prompt(self, timeout: Optional[int] = None) -> str:
        """
        Poll the shell until the device prompt appears or timeout expires.

        Uses a tight 50 ms poll loop instead of arbitrary sleeps — this
        means short commands return immediately while long-running ones
        wait as long as they need.

        Returns:
            Everything received from the shell (including the prompt line).

        Raises:
            TimeoutError: If no prompt is seen within `timeout` seconds.
        """
        timeout = timeout or self.command_timeout
        prompt_re = self._profile["prompt"]
        buf = ""
        deadline = time.time() + timeout

        while time.time() < deadline:
            if self._shell.recv_ready():
                chunk = self._shell.recv(65535).decode("utf-8", errors="ignore")
                buf += chunk
                # Normalise line endings + strip ANSI before prompt test
                clean = _ANSI_ESCAPE.sub("", buf)
                clean = clean.replace("\r\n", "\n").replace("\r", "\n")
                if prompt_re.search(clean):
                    return buf
            elif self._shell.exit_status_ready():
                break
            time.sleep(0.05)

        raise TimeoutError(
            f"Prompt not seen within {timeout}s on {self.management_ip}.\n"
        f"Buffer tail:\n{buf[-300:]!r}"
    )

    def _drain(self, timeout: int = 3) -> str:
        """
        Read all available data without waiting for a prompt.
        Used to consume banners and MOTD on connect.
        """
        buf = ""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._shell.recv_ready():
                buf += self._shell.recv(65535).decode("utf-8", errors="ignore")
                deadline = time.time() + 0.5  # reset window on new data
            time.sleep(0.05)
        if buf:
            logger.debug(f"[{self.management_ip}] Banner/MOTD drained ({len(buf)} bytes)")
        return buf

    def _send_and_wait(self, command: str, timeout: Optional[int] = None) -> str:
        """Send a command and block until the device prompt reappears."""
        if not command.endswith("\n"):
            command += "\n"
        self._shell.send(command)
        return self._wait_for_prompt(timeout=timeout)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute_command(self, command: str, timeout: Optional[int] = None) -> str:
        """
        Execute a command on the device.

        Connects automatically if not already connected.

        Args:
            command: Command string to send (newline appended if missing).
            timeout: Per-command timeout override (seconds).

        Returns:
            Command output with echo and trailing prompt stripped.
        """
        try:
            self.connect()
            raw = self._send_and_wait(command, timeout=timeout)
            output = self._strip_command_echo(command, raw)
            logger.debug(
                f"[{self.management_ip}] CMD: {command!r} -> {len(output)} bytes"
            )
            return output
        except Exception as e:
            logger.error(
                f"Command failed on {self.management_ip}: {e}\n"
                f"{traceback.format_exc()}"
            )
            return f"Error executing command: {e}"

    def execute_privileged_command(
        self,
        command: str,
        enable_password: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> str:
        """
        Escalate to privileged / enable mode then execute command.

        For devices with no enable concept (JunOS, XR, Huawei, etc.) this
        behaves identically to execute_command.

        Args:
            command:         Command to run after escalation.
            enable_password: Enable/privileged password. Falls back to login password.
            timeout:         Per-command timeout override (seconds).

        Returns:
            Command output with echo and trailing prompt stripped.
        """
        try:
            self.connect()

            enable_cmd = self._profile.get("enable_cmd")
            if enable_cmd:
                self._shell.send(f"{enable_cmd}\n")
                # Some devices prompt for an enable/sudo password
                response = self._wait_for_prompt(timeout=5)
                if re.search(r"[Pp]assword", response):
                    pw = enable_password or self.password
                    self._shell.send(f"{pw}\n")
                    self._wait_for_prompt(timeout=5)

            return self.execute_command(command, timeout=timeout)

        except Exception as e:
            logger.error(
                f"Privileged command failed on {self.management_ip}: {e}\n"
                f"{traceback.format_exc()}"
            )
            return f"Error executing privileged command: {e}"

    def execute_long_running(self, command: str, timeout: int = 120) -> str:
        """
        Execute a long-running command (packet captures, extended pings, etc.).

        Identical to execute_command but with an explicit generous timeout.
        The prompt-wait loop means output is captured completely regardless
        of how long the command takes, up to `timeout` seconds.

        Args:
            command: Command to run (e.g. "timeout 60 tcpdump -i eth0 -n").
            timeout: How long to wait for command completion (seconds).
                     Should be command_duration + a small buffer.

        Returns:
            Full command output.
        """
        return self.execute_command(command, timeout=timeout)

    def execute_config_commands(
        self,
        commands: list[str],
        timeout: Optional[int] = None,
    ) -> list[str]:
        """
        Send a sequence of configuration commands, capturing output for each.

        Useful for multi-line config blocks. Each command waits for prompt
        before the next is sent.

        Args:
            commands: Ordered list of commands to send.
            timeout:  Per-command timeout override.

        Returns:
            List of output strings, one per command.
        """
        results = []
        for cmd in commands:
            results.append(self.execute_command(cmd, timeout=timeout))
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_command_echo(command: str, output: str) -> str:
        """Remove the echoed command and trailing prompt line from shell output."""
        lines = output.splitlines()

        # Drop first line if it echoes the command
        if lines and command.strip() in lines[0]:
            lines = lines[1:]

        # Drop last line if it looks like a device prompt
        if lines and re.search(r"[>#$%\]>]\s*$", lines[-1]):
            lines = lines[:-1]

        return "\n".join(lines).strip()

    # ------------------------------------------------------------------
    # Factory / class methods
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_model(model_str: str) -> str:
        """Resolve a free-form model string to an exact profile key."""
        model_str_lower = model_str.lower().strip()
        
        # 1. First check if it's already an exact known key
        if model_str_lower in DEVICE_PROFILES:
            return model_str_lower
            
        # 2. Check against keyword hints
        for model_key, hints in _VENDOR_HINTS.items():
            if any(hint in model_str_lower for hint in hints):
                logger.debug(f"Auto-resolved model={model_key!r} from string: {model_str!r}")
                return model_key
                
        # 3. Fallback
        logger.warning(f"Unknown device model string: {model_str!r} — defaulting to cisco")
        return "cisco"

    @classmethod
    def from_snmp_sysdescr(
        cls,
        management_ip: str,
        sysdescr: str,
        **kwargs,
    ) -> "DeviceSSHSession":
        """
        Instantiate with model auto-detected from an SNMP sysDescr string.

        Useful when you already poll SNMP for device discovery in your NOC
        agent and want to avoid hardcoding model strings.

        Args:
            management_ip: Device IP or hostname.
            sysdescr:      SNMP sysDescr string from the device.
            **kwargs:      Passed through to __init__ (username, password, etc.)

        Returns:
            DeviceSSHSession with the best-matching model profile.
        """
        resolved_model = cls._resolve_model(sysdescr)
        return cls(management_ip, model=resolved_model, **kwargs)

    def __repr__(self) -> str:
        connected = self._shell is not None and not self._shell.closed
        return (
            f"DeviceSSHSession("
            f"ip={self.management_ip!r}, "
            f"model={self.model!r}, "
            f"connected={connected})"
        )
    def debug_raw(self, command: str, wait: float = 5.0) -> str:
        """Dump raw bytes from shell — use to diagnose prompt issues."""
        self.connect()
        self._shell.send(f"{command}\n")
        time.sleep(wait)
        buf = ""
        while self._shell.recv_ready():
            buf += self._shell.recv(65535).decode("utf-8", errors="ignore")
        print(repr(buf))  # repr() shows escape codes, spaces, newlines exactly
        return buf