"""
eveng.py — Complete EVE-NG REST API client + CLI
Single-file, deps: requests, click, pyyaml, jinja2

API usage:
    from eveng import EvengClient

    c = EvengClient("10.0.0.1", username="admin", password="eve")
    c = EvengClient("10.0.0.1", username="admin", password="eve",
                    protocol="https", ssl_verify=False)

    labs    = c.list_folders()
    c.create_lab("mylab", path="/")
    lab     = "/mylab.unl"
    c.add_node(lab, template="veos", name="leaf01", image="veos-4.22.0F", left=50)
    c.add_lab_network(lab, network_type="pnet1", name="mgmt")
    c.connect_node_to_cloud(lab, src="leaf01", src_label="Mgmt1", dst="mgmt")
    c.start_all_nodes(lab)
    c.logout()

CLI usage:
    python eveng.py --host 10.0.0.1 --username admin --password eve show-status
    python eveng.py --host 10.0.0.1 --username admin --password eve lab list
    python eveng.py --host 10.0.0.1 --username admin --password eve node list --path /mylab.unl

    Or use env vars:
        export EVE_NG_HOST=10.0.0.1
        export EVE_NG_USERNAME=admin
        export EVE_NG_PASSWORD=eve

Dependencies:
    pip install requests click pyyaml jinja2
"""

from __future__ import annotations

import copy
import json
import logging
import os
import sys
from pathlib import Path
from random import randint
from typing import Dict, Literal, Optional, Tuple
from urllib.parse import quote_plus

import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning

# ---------------------------------------------------------------------------
# Optional CLI deps
# ---------------------------------------------------------------------------
try:
    import click
    _CLICK = True
except ImportError:
    _CLICK = False

try:
    import yaml
    _YAML = True
except ImportError:
    _YAML = False

try:
    from jinja2 import Environment, FileSystemLoader
    _JINJA = True
except ImportError:
    _JINJA = False

__version__ = "1.0.0"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class EvengError(Exception):
    """Base EVE-NG client error."""


class EvengLoginError(EvengError):
    """Raised when authentication fails."""


class EvengHTTPError(EvengError):
    """Raised for non-2xx API responses."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class EvengClient:
    """
    Flat EVE-NG REST API client. All API methods live directly on this class.

    Parameters
    ----------
    host        : EVE-NG hostname or IP address
    username    : login username (optional — call login() later if omitted)
    password    : login password
    protocol    : 'http' (default) or 'https'
    port        : custom TCP port (default: None -> uses protocol default)
    ssl_verify  : set False for self-signed certs; also silences urllib3 warnings
    log_level   : Python logging level string, default 'WARNING'
    """

    def __init__(
        self,
        host: str,
        username: str = "",
        password: str = "",
        protocol: str = "http",
        port: int = None,
        ssl_verify: bool = True,
        log_level: str = "WARNING",
    ):
        self.host = host
        self.protocol = protocol
        self.port = port
        self.ssl_verify = ssl_verify
        self._session: Optional[requests.Session] = None
        self.username = username
        self._is_community = True

        self.log = logging.getLogger("eveng")
        self.log.setLevel(getattr(logging, log_level.upper(), logging.WARNING))
        if not self.log.handlers:
            self.log.addHandler(logging.NullHandler())

        if not ssl_verify:
            requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

        if username and password:
            self.login(username, password)

    # ------------------------------------------------------------------
    # Session / auth
    # ------------------------------------------------------------------

    @property
    def base_url(self) -> str:
        if self.port:
            return f"{self.protocol}://{self.host}:{self.port}/api"
        return f"{self.protocol}://{self.host}/api"

    def login(self, username: str, password: str) -> None:
        """Authenticate and create a cookie session."""
        self._session = requests.Session()
        self._session.verify = self.ssl_verify
        self._session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        r = self._session.post(
            self.base_url + "/auth/login",
            data=json.dumps({"username": username, "password": password, "html5": -1}),
        )
        if not r.ok:
            raise EvengLoginError(f"Login failed [{r.status_code}]: {r.text}")
        self.username = username

        # detect community vs pro from server version string
        try:
            ver = (self._req("GET", "/status").get("data") or {}).get("version", "")
            self._is_community = "pro" not in ver.lower()
        except Exception:
            self._is_community = True

    def logout(self) -> None:
        if self._session:
            try:
                self._session.get(self.base_url + "/auth/logout")
            finally:
                self._session = None

    # ------------------------------------------------------------------
    # Core HTTP dispatcher
    # ------------------------------------------------------------------

    def _req(self, method: str, endpoint: str, full_url: bool = False, **kwargs) -> dict:
        if not self._session:
            raise EvengError("Not connected — call login() first.")
        url = endpoint if full_url else self.base_url + endpoint
        r = self._session.request(method, url, **kwargs)
        if r.ok:
            try:
                return r.json()
            except (json.JSONDecodeError, ValueError):
                return r   # raw response for binary downloads
        try:
            err = r.json()
            raise EvengHTTPError(
                f"{err.get('code', r.status_code)}: {err.get('message', r.text)}"
            )
        except (json.JSONDecodeError, AttributeError):
            r.raise_for_status()

    def _get(self, ep, **kw):    return self._req("GET",    ep, **kw)
    def _post(self, ep, **kw):   return self._req("POST",   ep, **kw)
    def _put(self, ep, **kw):    return self._req("PUT",    ep, **kw)
    def _delete(self, ep, **kw): return self._req("DELETE", ep, **kw)

    # ------------------------------------------------------------------
    # Path normalisation  (mirrors evengsdk's normalize_path exactly)
    # ------------------------------------------------------------------

    def _norm(self, path: str) -> str:
        """Normalise a lab path to a URL-safe /encoded/path.unl string."""
        if not path.startswith("/"):
            path = "/" + path
        p = Path(path).resolve().with_suffix(".unl")
        parts = [quote_plus(x) for x in p.parts[1:]]
        return "/" + "/".join(parts)

    # ------------------------------------------------------------------
    # System / server
    # ------------------------------------------------------------------

    def get_server_status(self) -> Dict:
        """Return EVE-NG server status."""
        return self._get("/status")

    def list_node_templates(self) -> Dict:
        """List all available node templates."""
        return self._get("/list/templates/")

    def node_template_detail(self, node_type: str) -> Dict:
        """Full details and available images for a single node template."""
        return self._get(f"/list/templates/{node_type}")

    def list_networks(self) -> Dict:
        """List available network types (pnet0-9, bridge, etc.)."""
        return self._get("/list/networks")

    def list_user_roles(self) -> Dict:
        """List available user roles."""
        return self._get("/list/roles")

    # ------------------------------------------------------------------
    # Folders
    # ------------------------------------------------------------------

    def list_folders(self) -> Dict:
        """List all folders and their contained labs."""
        return self._get("/folders/")

    def get_folder(self, folder: str) -> Dict:
        """Get details for a specific folder path."""
        return self._get(f"/folders/{folder}")

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def list_users(self) -> Dict:
        return self._get("/users/")

    def get_user(self, username: str) -> Dict:
        return self._get(f"/users/{username}")

    def add_user(
        self,
        username: str,
        password: str,
        role: str = "user",
        name: str = "",
        email: str = "",
        expiration: str = "-1",
    ) -> Dict:
        return self._post("/users", data=json.dumps({
            "username": username,
            "name": name,
            "email": email,
            "password": password,
            "role": role,
            "expiration": expiration,
        }))

    def edit_user(self, username: str, data: dict) -> Dict:
        """Merge *data* into the existing user record and PUT the result."""
        existing = self.get_user(username)
        if not existing:
            raise EvengError(f"User not found: {username}")
        updated = copy.deepcopy(existing)
        updated.update(data)
        return self._req(
            "PUT",
            self.base_url + f"/users/{username}",
            full_url=True,
            data=json.dumps(updated),
        )

    def delete_user(self, username: str) -> Dict:
        return self._delete(f"/users/{username}")

    # ------------------------------------------------------------------
    # Labs — CRUD
    # ------------------------------------------------------------------

    def get_lab(self, path: str) -> Dict:
        return self._get("/labs" + self._norm(path))

    def create_lab(
        self,
        name: str,
        path: str = "/",
        author: str = "",
        description: str = "",
        body: str = "",
        version: str = "1.0",
        scripttimeout: int = 600,
        lock: int = 0,
    ) -> Dict:
        return self._post("/labs", data=json.dumps({
            "name": name, "path": path, "author": author,
            "description": description, "body": body,
            "version": version, "scripttimeout": scripttimeout, "lock": lock,
        }))

    def edit_lab(self, path: str, param: dict) -> Dict:
        """
        Update one lab attribute. EVE-NG only accepts one field per request.
        Valid keys: name, version, author, description, body, lock, scripttimeout
        """
        valid = {"name", "version", "author", "description", "body", "lock", "scripttimeout"}
        if len(param) != 1:
            raise ValueError(
                f"Exactly one parameter per request, got {len(param)}: {list(param)}"
            )
        key = next(iter(param))
        if key not in valid:
            raise ValueError(f"Invalid parameter '{key}'. Valid: {valid}")
        return self._put("/labs" + self._norm(path), data=json.dumps(param))

    def delete_lab(self, path: str) -> Dict:
        return self._delete("/labs" + self._norm(path))

    def close_lab(self) -> Dict:
        """Close the currently-open lab session."""
        return self._delete("/labs/close")

    def lock_lab(self, path: str) -> Dict:
        """Lock lab to prevent edits."""
        return self._put("/labs" + self._norm(path) + "/Lock")

    def unlock_lab(self, path: str) -> Dict:
        """Unlock lab for editing."""
        return self._put("/labs" + self._norm(path) + "/Unlock")

    # ------------------------------------------------------------------
    # Labs — import / export
    # ------------------------------------------------------------------

    def export_lab(self, path: str, filename: str = None) -> Tuple[bool, Optional[str]]:
        """
        Export lab to a .zip archive and download it locally.
        Returns (True, saved_filename) on success, (False, None) on failure.
        """
        resp = self._post("/export", data=json.dumps({"0": path, "path": ""}))
        endpoint = resp.get("data", "")
        if not endpoint:
            return False, None
        zip_name = endpoint.split("/")[-1]
        download_url = f"{self.protocol}://{self.host}{endpoint}"
        r = self._req("GET", download_url, full_url=True)
        out = filename or zip_name
        with open(out, "wb") as fh:
            fh.write(r.content)
        return True, out

    def import_lab(self, path: str, folder: str = "/") -> Dict:
        """Upload a .zip lab archive to EVE-NG."""
        if not Path(path).exists():
            raise FileNotFoundError(path)
        cookies = self._session.cookies.get_dict()
        self._session.headers = {
            "Accept": "*/*",
            "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
        }
        with open(path, "rb") as fh:
            return self._post("/import", data={"path": folder}, files={"file": fh})

    # ------------------------------------------------------------------
    # Labs — info
    # ------------------------------------------------------------------

    def get_lab_topology(self, path: str) -> Dict:
        return self._get("/labs" + self._norm(path) + "/topology")

    def get_lab_pictures(self, path: str) -> Dict:
        return self._get(f"/labs{self._norm(path)}/pictures")

    def get_lab_picture_details(self, path: str, picture_id: int) -> Dict:
        return self._get("/labs" + self._norm(path) + f"/pictures/{picture_id}")

    # ------------------------------------------------------------------
    # Lab Networks
    # ------------------------------------------------------------------

    def list_lab_networks(self, path: str) -> Dict:
        return self._get(f"/labs{self._norm(path)}/networks")

    def get_lab_network(self, path: str, net_id: int) -> Dict:
        return self._get("/labs" + self._norm(path) + f"/networks/{net_id}")

    def get_lab_network_by_name(self, path: str, name: str) -> Optional[dict]:
        networks = self.list_lab_networks(path).get("data") or {}
        return next((v for v in networks.values() if v["name"] == name), None)

    def add_lab_network(
        self,
        path: str,
        network_type: str,
        name: str = "",
        visibility: int = 0,
        left: int = None,
        top: int = None,
    ) -> Dict:
        if name and self.get_lab_network_by_name(path, name):
            raise EvengError(f"Network '{name}' already exists in {path}")
        available = set(self.list_networks().get("data") or {})
        if network_type not in available:
            raise ValueError(
                f"Invalid network_type '{network_type}'. Available: {available}"
            )
        return self._post(
            "/labs" + self._norm(path) + "/networks",
            data=json.dumps({
                "type": network_type,
                "name": name,
                "visibility": visibility,
                "left": left if left is not None else randint(30, 70),
                "top":  top  if top  is not None else randint(30, 70),
            }),
        )

    def edit_lab_network(self, path: str, net_id: int, data: dict) -> Dict:
        if not data:
            raise ValueError("data is required")
        return self._put(
            "/labs" + self._norm(path) + f"/networks/{net_id}",
            data=json.dumps(data),
        )

    def delete_lab_network(self, path: str, net_id: int) -> Dict:
        return self._delete(f"/labs{self._norm(path)}/networks/{net_id}")

    def list_lab_links(self, path: str) -> Dict:
        """List all ethernet/serial remote endpoints in the lab."""
        return self._get("/labs" + self._norm(path) + "/links")

    # ------------------------------------------------------------------
    # Nodes — CRUD
    # ------------------------------------------------------------------

    def list_nodes(self, path: str) -> Dict:
        return self._get("/labs" + self._norm(path) + "/nodes")

    def get_node(self, path: str, node_id: str) -> Dict:
        return self._get("/labs" + self._norm(path) + f"/nodes/{node_id}")

    def get_node_by_name(self, path: str, name: str) -> Optional[dict]:
        data = self.list_nodes(path).get("data") or {}
        return next((v for v in data.values() if v["name"] == name), None)

    def node_exists(self, path: str, name: str) -> bool:
        node = self.get_node_by_name(path, name)
        return node is not None and node.get("name") == name.lower()

    def add_node(
        self,
        path: str,
        template: str,
        name: str = "",
        node_type: str = "qemu",
        image: str = None,
        icon: str = None,
        console: str = "telnet",
        config: str = "Unconfigured",
        ethernet: int = None,
        serial: int = None,
        ram: int = None,
        cpu: int = None,
        nvram: int = None,
        delay: int = 0,
        idlepc: str = None,
        slot: str = "",
        left: int = None,
        top: int = None,
    ) -> Dict:
        """
        Add a node to the lab.
        Unset hardware params are filled from the template defaults.
        Returns {} without API call if a node with that name already exists.
        """
        if name and self.node_exists(path, name):
            return {}
        opts = self.node_template_detail(template).get("data", {}).get("options", {})
        _d = lambda k: opts.get(k, {}).get("value")   # noqa: E731
        eth_val = ethernet if ethernet is not None else _d("ethernet")
        ser_val = serial   if serial   is not None else _d("serial")
        payload = {
            "type": node_type, "template": template, "name": name,
            "config": config, "delay": delay, "console": console,
            "image":  image  or _d("image"),
            "icon":   icon   or _d("icon"),
            "ram":    ram    or _d("ram"),
            "cpu":    cpu    or _d("cpu"),
            "nvram":  nvram  or _d("nvram"),
            "ethernet": int(eth_val) if eth_val else "",
            "serial":   int(ser_val) if ser_val else "",
            "left": left if left is not None else randint(30, 70),
            "top":  top  if top  is not None else randint(30, 70),
        }
        if node_type == "dynamips" and idlepc:
            payload["idlepc"] = idlepc
        if slot is not None:
            payload["slot"] = slot
        return self._post(f"/labs{self._norm(path)}/nodes", data=json.dumps(payload))

    def delete_node(self, path: str, node_id: str) -> Dict:
        return self._delete("/labs" + self._norm(path) + f"/nodes/{node_id}")

    # ------------------------------------------------------------------
    # Nodes — control
    # ------------------------------------------------------------------

    def start_node(self, path: str, node_id: str) -> Dict:
        return self._get("/labs" + self._norm(path) + f"/nodes/{node_id}/start")

    def stop_node(self, path: str, node_id: str) -> Dict:
        url = "/labs" + self._norm(path) + f"/nodes/{node_id}/stop"
        if not self._is_community:
            url += "/stopmode=3"
        return self._get(url)

    def wipe_node(self, path: str, node_id: str) -> Dict:
        """Wipe node — deletes startup config; next boot rebuilds from image."""
        return self._get("/labs" + self._norm(path) + f"/nodes/{node_id}/wipe")

    def export_node(self, path: str, node_id: str) -> Dict:
        """Save node's running config into the lab .unl file."""
        return self._put("/labs" + self._norm(path) + f"/nodes/{node_id}/export")

    def start_all_nodes(self, path: str) -> Dict:
        if self._is_community:
            return self._get(f"/labs{self._norm(path)}/nodes/start")
        return self._bulk_node_action(path, "start")

    def stop_all_nodes(self, path: str) -> Dict:
        if self._is_community:
            return self._get(f"/labs{self._norm(path)}/nodes/stop")
        return self._bulk_node_action(path, "stop")

    def wipe_all_nodes(self, path: str) -> Dict:
        if self._is_community:
            return self._get(f"/labs{self._norm(path)}/nodes/wipe")
        return self._bulk_node_action(path, "wipe")

    def export_all_nodes(self, path: str) -> Dict:
        """Save all running configs into the lab file."""
        return self._put("/labs" + self._norm(path) + "/nodes/export")

    def _bulk_node_action(self, path: str, action: str) -> dict:
        nodes = self.list_nodes(path).get("data") or {}
        fn = getattr(self, f"{action}_node")
        results = [fn(path, nid) for nid in nodes]
        ok = all(r.get("status") == "success" for r in results)
        return {"status": "success" if ok else "error", "data": results}

    # ------------------------------------------------------------------
    # Nodes — interfaces
    # ------------------------------------------------------------------

    def get_node_interfaces(self, path: str, node_id: str) -> Dict:
        return self._get("/labs" + self._norm(path) + f"/nodes/{node_id}/interfaces")

    def find_node_interface(
        self,
        path: str,
        node_id: str,
        interface_name: str,
        media: Literal["ethernet", "serial"] = "ethernet",
    ) -> Optional[Tuple[int, dict]]:
        """
        Return (index, interface_dict) for a named interface on a node.
        Returns None if not found.
        """
        ifaces = (
            (self.get_node_interfaces(path, node_id).get("data") or {})
            .get(media, [])
        )
        return next(
            ((idx, iface) for idx, iface in enumerate(ifaces)
             if iface["name"] == interface_name),
            None,
        )

    # ------------------------------------------------------------------
    # Nodes — configs
    # ------------------------------------------------------------------

    def get_node_configs(self, path: str, configset: str = "default") -> Dict:
        """List startup configs for all nodes in the lab."""
        url = "/labs" + self._norm(path) + "/configs"
        if not self._is_community:
            return self._post(url, data=json.dumps({"cfsid": configset}))
        return self._get(url)

    def get_node_config_by_id(
        self, path: str, node_id: int, configset: str = "default"
    ) -> Dict:
        """Get startup config for a single node by its numeric ID."""
        url = "/labs" + self._norm(path) + f"/configs/{node_id}"
        if not self._is_community:
            return self._post(url, data=json.dumps({"cfsid": configset}))
        return self._get(url)

    def upload_node_config(
        self,
        path: str,
        node_id: str,
        config: str,
        configset: str = "default",
    ) -> Dict:
        """Upload a startup config string to a node."""
        url = "/labs" + self._norm(path) + f"/configs/{node_id}"
        payload: dict = {"id": node_id, "data": config}
        if not self._is_community:
            payload["cfsid"] = configset
        return self._put(url, data=json.dumps(payload))

    def enable_node_config(self, path: str, node_id: str) -> Dict:
        """Tell EVE-NG to apply the uploaded startup config on next boot."""
        url = "/labs" + self._norm(path) + f"/nodes/{node_id}"
        return self._put(url, data=json.dumps({"id": node_id, "config": 1}))

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------

    def connect_node_to_cloud(
        self,
        path: str,
        src: str,
        src_label: str,
        dst: str,
        media: Literal["ethernet", "serial"] = "ethernet",
    ) -> Dict:
        """Connect a node interface to a cloud/network object, both by name."""
        node = self.get_node_by_name(path, src)
        if node is None:
            raise EvengError(f"Node not found: '{src}'")
        net = self.get_lab_network_by_name(path, dst)
        if net is None:
            raise EvengError(f"Network not found: '{dst}'")
        iface = self.find_node_interface(path, node["id"], src_label, media)
        if iface is None:
            raise EvengError(f"Interface '{src_label}' not found on node '{src}'")
        url = f"/labs{self._norm(path)}/nodes/{node['id']}/interfaces"
        return self._put(url, data=json.dumps({iface[0]: str(net["id"])}))

    def connect_node_to_node(
        self,
        path: str,
        src: str,
        src_label: str,
        dst: str,
        dst_label: str,
        media: Literal["ethernet", "serial"] = "ethernet",
    ) -> bool:
        """
        Create a hidden bridge between two node interfaces (p2p link).
        Returns True when both sides connect successfully.
        """
        s = self.get_node_by_name(path, src)
        d = self.get_node_by_name(path, dst)
        if not s or not d:
            raise EvengError(f"Node(s) not found: '{src}' / '{dst}'")
        si = self.find_node_interface(path, s["id"], src_label, media)
        di = self.find_node_interface(path, d["id"], dst_label, media)
        if not si:
            raise EvengError(f"Interface '{src_label}' not found on '{src}'")
        if not di:
            raise EvengError(f"Interface '{dst_label}' not found on '{dst}'")
        net_resp = self.add_lab_network(path, network_type="bridge", visibility=1)
        net_id = (net_resp.get("data") or {}).get("id")
        if not net_id:
            raise EvengError("Failed to create bridge network for p2p link")
        r1 = self._wire_iface(path, s["id"], si, net_id)
        r2 = self._wire_iface(path, d["id"], di, net_id)
        return r1.get("status") == "success" and r2.get("status") == "success"

    def connect_node(
        self,
        path: str,
        src: str,
        src_label: str,
        dst: str,
        dst_label: str = "",
        dst_type: Literal["network", "node"] = "network",
        media: Literal["ethernet", "serial"] = "ethernet",
    ) -> Dict:
        """
        Unified connect helper.
        dst_type='network'  -> connect_node_to_cloud
        dst_type='node'     -> connect_node_to_node
        """
        if dst_type not in ("network", "node"):
            raise ValueError(f"dst_type must be 'network' or 'node', got '{dst_type}'")
        if dst_type == "network":
            return self.connect_node_to_cloud(path, src, src_label, dst, media)
        return self.connect_node_to_node(path, src, src_label, dst, dst_label, media)

    def _wire_iface(self, path: str, node_id: str, iface: Tuple, net_id: str) -> Dict:
        """Attach one interface to a network and hide the bridge in the GUI."""
        url = "/labs" + self._norm(path) + f"/nodes/{node_id}/interfaces"
        self._put(url, data=json.dumps({iface[0]: str(net_id)}))
        return self.edit_lab_network(path, net_id, data={"visibility": "0"})


# ---------------------------------------------------------------------------
# Topology builder (YAML declarative lab builder)
# ---------------------------------------------------------------------------

class TopologyBuilder:
    """
    Builds an EVE-NG lab from a YAML topology file.

    Topology YAML keys
    ------------------
    name, path, description, author
    nodes[]  : name, template, image, node_type, left, top, cpu, ram,
               ethernet, serial, configuration{file|template+vars}
    networks[]: name, network_type, visibility, left, top
    links:
      network[]: {src, src_label, dst}
      node[]  : {src, src_label, dst, dst_label, media?}
    """

    def __init__(self, client: EvengClient, template_dir: str = "templates"):
        if not _YAML:
            raise ImportError("pyyaml required: pip install pyyaml")
        self.client = client
        self.template_dir = template_dir

    def _render_config(self, cfg: dict) -> Optional[str]:
        """Resolve a node configuration block to a plain config string."""
        if not cfg:
            return None
        if "file" in cfg:
            p = Path(cfg["file"])
            if not p.exists():
                raise FileNotFoundError(f"Config file not found: {cfg['file']}")
            return p.read_text()
        if "template" in cfg:
            if not _JINJA:
                raise ImportError("jinja2 required: pip install jinja2")
            env = Environment(loader=FileSystemLoader(self.template_dir))
            tmpl = env.get_template(cfg["template"])
            vars_val = cfg.get("vars", {})
            if isinstance(vars_val, str):          # path to a yaml vars file
                with open(vars_val) as fh:
                    vars_val = yaml.safe_load(fh)
            return tmpl.render(**(vars_val or {}))
        return None

    def build(self, topology_file: str) -> None:
        with open(topology_file) as fh:
            topo = yaml.safe_load(fh)

        name        = topo["name"]
        path_prefix = topo.get("path", "/").rstrip("/")
        lab_path    = f"{path_prefix}/{name}.unl"

        # 1. create lab
        self.client.create_lab(
            name=name, path=path_prefix or "/",
            description=topo.get("description", ""),
            author=topo.get("author", ""),
        )
        print(f"[+] Lab created: {lab_path}")

        # 2. networks
        for net in (topo.get("networks") or []):
            self.client.add_lab_network(
                lab_path,
                network_type=net["network_type"],
                name=net.get("name", ""),
                visibility=net.get("visibility", 0),
                left=net.get("left"),
                top=net.get("top"),
            )
            print(f"    network '{net.get('name')}' done.")

        # 3. nodes
        for node in (topo.get("nodes") or []):
            node = dict(node)                      # shallow copy
            cfg_block = node.pop("configuration", None)
            self.client.add_node(lab_path, **node)
            print(f"    node '{node['name']}' done.")
            if cfg_block:
                config_text = self._render_config(cfg_block)
                if config_text:
                    node_obj = self.client.get_node_by_name(lab_path, node["name"])
                    if node_obj:
                        nid = node_obj["id"]
                        self.client.upload_node_config(lab_path, nid, config_text)
                        self.client.enable_node_config(lab_path, nid)
                        print(f"      config applied to '{node['name']}'")

        # 4. links
        links = topo.get("links") or {}
        for link in (links.get("network") or []):
            self.client.connect_node_to_cloud(
                lab_path, link["src"], link["src_label"], link["dst"]
            )
        for link in (links.get("node") or []):
            self.client.connect_node_to_node(
                lab_path,
                link["src"], link["src_label"],
                link["dst"], link["dst_label"],
                media=link.get("media", "ethernet"),
            )
        print("[+] Topology build complete.")


# ---------------------------------------------------------------------------
# CLI (requires: pip install click)
# ---------------------------------------------------------------------------

if _CLICK:

    def _echo_json(data) -> None:
        click.echo(json.dumps(data, indent=2))

    def _client(ctx) -> EvengClient:
        o = ctx.obj
        return EvengClient(
            host=o["host"],
            username=o["username"],
            password=o["password"],
            protocol=o.get("protocol", "http"),
            port=o.get("port"),
            ssl_verify=o.get("verify", True),
            log_level="DEBUG" if o.get("debug") else "WARNING",
        )

    # ── root ──────────────────────────────────────────────────────────────

    @click.group()
    @click.version_option(__version__)
    @click.option("--host",     envvar="EVE_NG_HOST",      required=True)
    @click.option("--username", envvar="EVE_NG_USERNAME",  required=True,
                  default=lambda: os.environ.get("USER", "admin"),
                  show_default="current user")
    @click.option("--password", envvar="EVE_NG_PASSWORD",  required=True)
    @click.option("--port",     envvar="EVE_NG_PORT",      default=None,   type=int)
    @click.option("--protocol", envvar="EVE_NG_PROTOCOL",  default="http", show_default=True)
    @click.option("--insecure", envvar="EVE_NG_INSECURE",  is_flag=True,   default=False,
                  help="Disable SSL certificate verification")
    @click.option("--verify",   envvar="EVE_NG_SSL_VERIFY", default=True,  type=bool)
    @click.option("--debug/--no-debug", default=False)
    @click.pass_context
    def cli(ctx, host, username, password, port, protocol, insecure, verify, debug):
        """CLI application to manage EVE-NG objects."""
        ctx.ensure_object(dict)
        ctx.obj.update({
            "host": host, "username": username, "password": password,
            "port": port, "protocol": protocol,
            "verify": (not insecure) and verify,
            "debug": debug,
        })

    # ── system ────────────────────────────────────────────────────────────

    @cli.command("show-status")
    @click.option("--output", type=click.Choice(["json", "text", "table"]), default="text")
    @click.pass_context
    def show_status(ctx, output):
        """View EVE-NG server status.

        \b
        Examples:
          eve-ng show-status
        """
        c = _client(ctx)
        data = c.get_server_status().get("data") or {}
        if output == "json":
            _echo_json(data)
        else:
            for k, v in data.items():
                click.echo(f"{k:<20} {v}")

    @cli.command("show-template")
    @click.argument("template_name")
    @click.pass_context
    def show_template(ctx, template_name):
        """Get EVE-NG node template details.

        \b
        Examples:
          eve-ng show-template veos
        """
        _echo_json(_client(ctx).node_template_detail(template_name))

    @cli.command("list-node-templates")
    @click.option("--output", type=click.Choice(["json", "text", "table"]), default="text")
    @click.pass_context
    def list_node_templates(ctx, output):
        """List available EVE-NG node templates.

        \b
        Examples:
          eve-ng list-node-templates
        """
        data = _client(ctx).list_node_templates().get("data") or {}
        if output == "json":
            _echo_json(data)
        else:
            for k, v in data.items():
                click.echo(f"{k:<20} {v}")

    @cli.command("list-network-types")
    @click.option("--output", type=click.Choice(["json", "text", "table"]), default="text")
    @click.pass_context
    def list_network_types(ctx, output):
        """List EVE-NG network types.

        \b
        Examples:
          eve-ng list-network-types
        """
        data = _client(ctx).list_networks().get("data") or {}
        if output == "json":
            _echo_json(data)
        else:
            for k, v in data.items():
                click.echo(f"{k:<20} {v}")

    @cli.command("list-user-roles")
    @click.option("--output", type=click.Choice(["json", "text", "table"]), default="text")
    @click.pass_context
    def list_user_roles(ctx, output):
        """List EVE-NG user roles.

        \b
        Examples:
          eve-ng list-user-roles
        """
        data = _client(ctx).list_user_roles().get("data") or {}
        if output == "json":
            _echo_json(data)
        else:
            for k, v in data.items():
                click.echo(f"{k:<20} {v}")

    # ── folder ────────────────────────────────────────────────────────────

    @cli.group()
    def folder():
        """Manage EVE-NG folders."""

    @folder.command("list")
    @click.option("--output", type=click.Choice(["json", "text", "table"]), default="text")
    @click.pass_context
    def folder_list(ctx, output):
        """List folders on EVE-NG host.

        \b
        Examples:
          eve-ng folder list
        """
        data = _client(ctx).list_folders().get("data") or {}
        if output == "json":
            _echo_json(data)
        else:
            folders = data.get("folders", []) if isinstance(data, dict) else []
            for f in folders:
                click.echo(f.get("name", str(f)))

    @folder.command("read")
    @click.argument("folder")
    @click.pass_context
    def folder_read(ctx, folder):
        """Get folder details.

        \b
        Examples:
          eve-ng folder read /path/to/folder
        """
        _echo_json(_client(ctx).get_folder(folder))

    # ── lab ───────────────────────────────────────────────────────────────

    @cli.group()
    def lab():
        """Manage EVE-NG labs."""

    @lab.command("list")
    @click.option("--output", type=click.Choice(["json", "text", "table"]), default="text")
    @click.pass_context
    def lab_list(ctx, output):
        """List available labs.

        \b
        Examples:
          eve-ng lab list
        """
        data = _client(ctx).list_folders().get("data") or {}
        if output == "json":
            _echo_json(data)
        else:
            labs = data.get("labs", []) if isinstance(data, dict) else []
            for entry in labs:
                click.echo(entry.get("path", str(entry)))

    @lab.command("read")
    @click.option("--path",   envvar="EVE_NG_LAB_PATH")
    @click.option("--output", type=click.Choice(["json", "text"]), default="json")
    @click.pass_context
    def lab_read(ctx, path, output):
        """Get lab details.

        \b
        Examples:
          eve-ng lab read
          eve-ng lab read --path /folder/lab.unl
        """
        data = _client(ctx).get_lab(path)
        if output == "json":
            _echo_json(data)
        else:
            for k, v in (data.get("data") or {}).items():
                click.echo(f"{k:<20} {v}")

    @lab.command("create")
    @click.option("--name",        required=True,  help="Lab name")
    @click.option("--path",        default="/",    help="Parent folder")
    @click.option("--author",      default="")
    @click.option("--description", default="")
    @click.option("--version",     default="1.0")
    @click.pass_context
    def lab_create(ctx, name, path, author, description, version):
        """Create a new lab.

        \b
        Examples:
          eve-ng lab create --name lab1 --author "John Doe" --description "My lab"
        """
        resp = _client(ctx).create_lab(
            name=name, path=path, author=author,
            description=description, version=version,
        )
        click.echo(resp.get("message", json.dumps(resp)))

    @lab.command("edit")
    @click.option("--path",        envvar="EVE_NG_LAB_PATH")
    @click.option("--author",      default=None,
                  help="NOTE: mutually exclusive with description/version/body")
    @click.option("--description", default=None,
                  help="NOTE: mutually exclusive with author/version/body")
    @click.option("--version",     default=None,
                  help="NOTE: mutually exclusive with author/description/body")
    @click.option("--body",        default=None,
                  help="NOTE: mutually exclusive with author/description/version")
    @click.pass_context
    def lab_edit(ctx, path, author, description, version, body):
        """Edit a lab attribute. EVE-NG allows only one field per call.

        \b
        Examples:
          eve-ng lab edit --author "Tafsir Thiam"
          eve-ng lab edit --body "Lab to demonstrate VXLAN/BGP-EVPN on vEOS"
        """
        params = {k: v for k, v in
                  [("author", author), ("description", description),
                   ("version", version), ("body", body)] if v is not None}
        if not params:
            raise click.UsageError(
                "Provide exactly one of: --author, --description, --version, --body"
            )
        if len(params) > 1:
            raise click.UsageError("EVE-NG API supports editing one field at a time.")
        resp = _client(ctx).edit_lab(path, params)
        click.echo(resp.get("message", json.dumps(resp)))

    @lab.command("delete")
    @click.option("--path", envvar="EVE_NG_LAB_PATH", required=True)
    @click.pass_context
    def lab_delete(ctx, path):
        """Delete a lab.

        \b
        Examples:
          eve-ng lab delete --path /lab1
        """
        resp = _client(ctx).delete_lab(path)
        click.echo(resp.get("message", json.dumps(resp)))

    @lab.command("start")
    @click.option("--path", envvar="EVE_NG_LAB_PATH", required=True)
    @click.pass_context
    def lab_start(ctx, path):
        """Start all nodes in a lab.

        \b
        Examples:
          eve-ng lab start
          eve-ng lab start --path /lab1
        """
        resp = _client(ctx).start_all_nodes(path)
        click.echo(resp.get("message", json.dumps(resp)))

    @lab.command("stop")
    @click.option("--path", envvar="EVE_NG_LAB_PATH", required=True)
    @click.pass_context
    def lab_stop(ctx, path):
        """Stop all nodes in a lab.

        \b
        Examples:
          eve-ng lab stop
          eve-ng lab stop --path /lab1
        """
        resp = _client(ctx).stop_all_nodes(path)
        click.echo(resp.get("message", json.dumps(resp)))

    @lab.command("topology")
    @click.option("--path",   envvar="EVE_NG_LAB_PATH", required=True)
    @click.option("--output", type=click.Choice(["json", "text", "table"]), default="json")
    @click.pass_context
    def lab_topology(ctx, path, output):
        """Retrieve lab topology.

        \b
        Examples:
          eve-ng lab topology
        """
        _echo_json(_client(ctx).get_lab_topology(path))

    @lab.command("export")
    @click.option("--path", envvar="EVE_NG_LAB_PATH", required=True)
    @click.option("--dest", type=click.Path(), default=None)
    @click.pass_context
    def lab_export(ctx, path, dest):
        """Export and download lab file as ZIP archive.

        \b
        Examples:
          eve-ng lab export
          eve-ng lab export --dest /tmp/mylab.zip
        """
        ok, fname = _client(ctx).export_lab(path, dest)
        click.echo(f"Exported to: {fname}" if ok else "Export failed.")

    @lab.command("import")
    @click.option("--src",    type=click.Path(exists=True), required=True,
                  help="Source ZIP lab file")
    @click.option("--folder", default="/", help="Destination folder on EVE-NG")
    @click.pass_context
    def lab_import(ctx, src, folder):
        """Import lab into EVE-NG from ZIP archive."""
        resp = _client(ctx).import_lab(src, folder)
        click.echo(resp.get("message", json.dumps(resp)))

    @lab.command("set-active")
    @click.option("--path", required=True, help="Path to lab to activate")
    @click.pass_context
    def lab_set_active(ctx, path):
        """Set current lab path (prints export command for your shell).

        \b
        Examples:
          eval $(eve-ng lab set-active --path /mylab.unl)
        """
        click.echo(f"export EVE_NG_LAB_PATH={path}")

    @lab.command("show-active")
    @click.pass_context
    def lab_show_active(ctx):
        """Show the currently active lab path from environment.

        \b
        Examples:
          eve-ng lab show-active
        """
        active = os.environ.get("EVE_NG_LAB_PATH", "(not set)")
        click.echo(f"Active lab: {active}")

    @lab.command("create-from-topology")
    @click.option("-t", "--topology",     type=click.Path(exists=True), required=True,
                  help="Topology YAML file to import")
    @click.option("-d", "--template-dir", type=click.Path(), default="templates",
                  show_default=True, help="Jinja2 template directory")
    @click.pass_context
    def lab_create_from_topology(ctx, topology, template_dir):
        """Build a lab from a YAML topology declaration.

        \b
        Examples:
          eve-ng lab create-from-topology -t examples/test_topology.yml
          eve-ng lab create-from-topology -t topo.yml --template-dir ./templates
        """
        builder = TopologyBuilder(_client(ctx), template_dir=template_dir)
        builder.build(topology)

    # ── node ──────────────────────────────────────────────────────────────

    @cli.group()
    def node():
        """Manage EVE-NG lab nodes."""

    @node.command("list")
    @click.option("--path",   envvar="EVE_NG_LAB_PATH", required=True)
    @click.option("--output", type=click.Choice(["json", "text", "table"]), default="text")
    @click.pass_context
    def node_list(ctx, path, output):
        """List all nodes in a lab.

        \b
        Example:
          eve-ng node list
        """
        data = _client(ctx).list_nodes(path).get("data") or {}
        if output == "json":
            _echo_json(data)
        else:
            for nid, n in data.items():
                click.echo(
                    f"  [{nid}] {n['name']:<20} "
                    f"template={n.get('template','?'):>10}  "
                    f"status={n.get('status','?')}"
                )

    @node.command("read")
    @click.option("--path",          envvar="EVE_NG_LAB_PATH", required=True)
    @click.option("-n", "--node-id", required=True)
    @click.option("--output",        type=click.Choice(["json", "text"]), default="json")
    @click.pass_context
    def node_read(ctx, path, node_id, output):
        """Retrieve lab node details.

        \b
        Example:
          eve-ng node read -n 4
        """
        data = _client(ctx).get_node(path, node_id).get("data") or {}
        if output == "json":
            _echo_json(data)
        else:
            for k, v in data.items():
                click.echo(f"{k:<20} {v}")

    @node.command("create")
    @click.option("--path",         envvar="EVE_NG_LAB_PATH", required=True)
    @click.option("--name",         default="")
    @click.option("--template",     required=True)
    @click.option("--image",        default=None)
    @click.option("--node-type",    "node_type",
                  type=click.Choice(["iol", "qemu", "dynamips"]), default="qemu")
    @click.option("--ethernet",     type=int, default=None)
    @click.option("--serial",       type=int, default=None)
    @click.option("--console-type", "console",
                  type=click.Choice(["telnet", "vnc"]), default="telnet")
    @click.option("--ram",          type=int, default=None)
    @click.option("--cpu",          type=int, default=None)
    @click.pass_context
    def node_create(ctx, path, name, template, image, node_type,
                    ethernet, serial, console, ram, cpu):
        """Create a lab node.

        \b
        Example:
          eve-ng node create --name leaf05 --template veos --image veos-4.22.0F
        """
        resp = _client(ctx).add_node(
            path, template=template, name=name, image=image,
            node_type=node_type, ethernet=ethernet, serial=serial,
            console=console, ram=ram, cpu=cpu,
        )
        click.echo(resp.get("message", json.dumps(resp)))

    @node.command("delete")
    @click.option("--path",          envvar="EVE_NG_LAB_PATH", required=True)
    @click.option("-n", "--node-id", required=True)
    @click.pass_context
    def node_delete(ctx, path, node_id):
        """Delete lab node with specified id.

        \b
        Example:
          eve-ng node delete -n 4
        """
        resp = _client(ctx).delete_node(path, node_id)
        click.echo(resp.get("message", json.dumps(resp)))

    @node.command("start")
    @click.option("--path",          envvar="EVE_NG_LAB_PATH", required=True)
    @click.option("-n", "--node-id", required=True)
    @click.pass_context
    def node_start(ctx, path, node_id):
        """Start a node in lab.

        \b
        Example:
          eve-ng node start -n 4
        """
        resp = _client(ctx).start_node(path, node_id)
        click.echo(resp.get("message", json.dumps(resp)))

    @node.command("stop")
    @click.option("--path",          envvar="EVE_NG_LAB_PATH", required=True)
    @click.option("-n", "--node-id", required=True)
    @click.pass_context
    def node_stop(ctx, path, node_id):
        """Stop a node in lab.

        \b
        Example:
          eve-ng node stop -n 1
        """
        resp = _client(ctx).stop_node(path, node_id)
        click.echo(resp.get("message", json.dumps(resp)))

    @node.command("wipe")
    @click.option("--path",          envvar="EVE_NG_LAB_PATH", required=True)
    @click.option("-n", "--node-id", default=None,
                  help="Node ID to wipe (omit to wipe all nodes)")
    @click.pass_context
    def node_wipe(ctx, path, node_id):
        """Wipe node(s) — removes startup config, rebuilds from image on next boot.

        \b
        Examples:
          eve-ng node wipe -n 4    # wipe node 4
          eve-ng node wipe          # wipe all nodes
        """
        c = _client(ctx)
        resp = c.wipe_node(path, node_id) if node_id else c.wipe_all_nodes(path)
        click.echo(resp.get("message", json.dumps(resp)))

    @node.command("export")
    @click.option("--path",          envvar="EVE_NG_LAB_PATH", required=True)
    @click.option("-n", "--node-id", default=None,
                  help="Node ID to export (omit to export all nodes)")
    @click.pass_context
    def node_export(ctx, path, node_id):
        """Save node running config into the lab file.

        \b
        Examples:
          eve-ng node export -n 4   # export node 4
          eve-ng node export         # export all nodes
        """
        c = _client(ctx)
        resp = c.export_node(path, node_id) if node_id else c.export_all_nodes(path)
        click.echo(resp.get("message", json.dumps(resp)))

    @node.command("config")
    @click.option("--path",          envvar="EVE_NG_LAB_PATH", required=True)
    @click.option("-n", "--node-id", required=True)
    @click.option("-s", "--src",     type=click.Path(exists=True), default=None,
                  help="Config file to upload")
    @click.option("-c", "--config",  default=None,
                  help="Config string to upload inline")
    @click.pass_context
    def node_config(ctx, path, node_id, src, config):
        """View or upload a node startup config.

        \b
        Examples:
          eve-ng node config -n 1                         # view stored config
          eve-ng node config -n 4 --config "hostname r1"  # upload string
          eve-ng node config -n 4 -s config.txt           # upload from file
        """
        c = _client(ctx)
        if src:
            resp = c.upload_node_config(path, node_id, Path(src).read_text())
            click.echo(resp.get("message", json.dumps(resp)))
        elif config:
            resp = c.upload_node_config(path, node_id, config)
            click.echo(resp.get("message", json.dumps(resp)))
        else:
            data = c.get_node_config_by_id(path, int(node_id)).get("data") or {}
            click.echo(data.get("data", "(no config stored)"))

    # ── user ──────────────────────────────────────────────────────────────

    @cli.group()
    def user():
        """Manage EVE-NG users."""

    @user.command("list")
    @click.option("--output", type=click.Choice(["json", "text", "table"]), default="text")
    @click.pass_context
    def user_list(ctx, output):
        """List all EVE-NG users.

        \b
        Examples:
          eve-ng user list
        """
        data = _client(ctx).list_users().get("data") or {}
        if output == "json":
            _echo_json(data)
        else:
            for uname, info in data.items():
                role = info.get("role", "?") if isinstance(info, dict) else "?"
                click.echo(f"{uname:<20} role={role}")

    @user.command("read")
    @click.option("-u", "--username", required=True)
    @click.pass_context
    def user_read(ctx, username):
        """Retrieve EVE-NG user details."""
        _echo_json(_client(ctx).get_user(username))

    @user.command("create")
    @click.option("-u", "--username",   required=True, help="Login username")
    @click.option("-p", "--password",   default=None,  help="Login password")
    @click.option("-n", "--name",       default="",    help="User's full name")
    @click.option("-r", "--role",       default="user")
    @click.option("-e", "--expiration", type=int, default=-1,
                  help="Expiry UNIX timestamp; -1 = never")
    @click.option("--email",            default="")
    @click.pass_context
    def user_create(ctx, username, password, name, role, expiration, email):
        """Create an EVE-NG user.

        \b
        Examples:
          eve-ng user create -u user1 -p pass1 -e -1 --role user --name "John Doe"
        """
        resp = _client(ctx).add_user(
            username=username, password=password or "",
            name=name, role=role, expiration=str(expiration), email=email,
        )
        click.echo(resp.get("message", json.dumps(resp)))

    @user.command("edit")
    @click.option("-u", "--username",   required=True)
    @click.option("-p", "--password",   default=None)
    @click.option("-n", "--name",       default=None)
    @click.option("-r", "--role",       default=None)
    @click.option("-e", "--expiration", type=int, default=None)
    @click.option("--email",            default=None)
    @click.pass_context
    def user_edit(ctx, username, password, name, role, expiration, email):
        """Update an EVE-NG user.

        \b
        Examples:
          eve-ng user edit -u user1 --name "Jane Doe"
        """
        updates = {k: v for k, v in {
            "password": password, "name": name, "role": role,
            "expiration": str(expiration) if expiration is not None else None,
            "email": email,
        }.items() if v is not None}
        if not updates:
            raise click.UsageError("Provide at least one field to update.")
        resp = _client(ctx).edit_user(username, updates)
        click.echo((resp or {}).get("message", json.dumps(resp or {})))

    @user.command("delete")
    @click.option("-u", "--username", required=True)
    @click.pass_context
    def user_delete(ctx, username):
        """Delete an EVE-NG user."""
        resp = _client(ctx).delete_user(username)
        click.echo(resp.get("message", json.dumps(resp)))

    # ── entry-point ───────────────────────────────────────────────────────

    def main():
        cli(auto_envvar_prefix="EVE_NG")

else:
    def main():
        print("click is required for the CLI:  pip install click", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
'''
from eveng_helper import EvengClient

c = EvengClient("10.0.0.1", username="admin", password="eve")
c.create_lab("mylab")
c.add_node("/mylab.unl", template="veos", name="leaf01")
c.connect_node_to_cloud("/mylab.unl", src="leaf01", src_label="Mgmt1", dst="mgmt")
c.start_all_nodes("/mylab.unl")
c.logout()
'''