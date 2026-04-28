import os
import sys
import json
import subprocess
import time
import getpass
from pathlib import Path
from datetime import date
from utils.credentials_helper import CredentialsHelper

CREDS_FILE = Path.home() / ".net-deepagent" / "creds.json"
PREFS_FILE = Path.home() / ".net-deepagent" / "preferences.json"
SKILL_DIR = Path.home() / ".net-deepagent" / "net-agent" / "skills" / "network-facts-and-procedures"
SKILL_FILE = SKILL_DIR / "SKILL.md"

def ensure_config_dir():
    CREDS_FILE.parent.mkdir(parents=True, exist_ok=True)

def gather_features():
    print("--- 🚀 WorkerXY Initializer ---")
    features = {
        "mcp": True,
        "headless": False,
        "discord": False,
        "webui": False
    }
    
    print("\nSelect features to activate (y/N for optional features):")
    print("[*] MCP Server (Required) - Activated")
    
    ans = input("[?] Enable Headless Worker (requires RabbitMQ)? [y/N]: ").strip().lower()
    if ans == 'y': features["headless"] = True
    
    ans = input("[?] Enable Discord Bot bridge (requires RabbitMQ)? [y/N]: ").strip().lower()
    if ans == 'y': features["discord"] = True
    
    ans = input("[?] Enable Streamlit Web UI? [y/N]: ").strip().lower()
    if ans == 'y': features["webui"] = True
    
    return features

def gather_credentials():
    print("\n--- 🔐 Credentials Configuration ---")
    ensure_config_dir()
    
    creds = {}
    if CREDS_FILE.exists():
        try:
            with open(CREDS_FILE, 'r') as f:
                creds = json.load(f)
        except json.JSONDecodeError:
            print("[!] Invalid JSON in creds.json, starting fresh.")
            
    keys_needed = [
        "OPENAI_KEY",
        "GROQ_KEY",
        "ANTHROPIC_KEY",
        "GEMINI_KEY",
        "GROK_KEY",
        "DISCORDID",
        "DISCORD_API_KEY",
        "DISCORD_URI",
        "RABBITMQUSER",
        "RABBITMQ_PASSWORD",
        "DEVICES_SSH_USERNAME",
        "DEVICES_SSH_PASSWORD",
        "SERVER_USERNAME",
        "SERVER_PASSWORD",
        "CLOUD_DESKTOP_USER",
        "CLOUD_DESKTOP_PASSWORD",
        "API_AC5_MIST_COM_ORGID",
        "API_AC5_MIST_COM_TOKEN",
        "API_AC5_MIST_COM_TOKEN_SCHEME",
        "SERVICENOW_INSTANCE_URL",
        "SERVICENOW_ACCESS_TOKEN",
        "SERVICENOW_SERVER_NAME",
        "JIRA_API_TOKEN",
        "JIRA_API_TOKEN_SCOPED",
        "JIRA_BASE_URL",
        "JIRA_USER",
        "DISCORD_PERMISSION_CHANNEL",
        "DISCORD_PERMISSION_WEBHOOK",
        "SLACK_PERMISSION_WEBHOOK",
        "SLACK_BOT_AUTH_TOKEN",
        "SLACK_BOT_SOCKET_TOKEN"
    ]

    hints = {
        "DISCORDID": "Note: This is the Discord Channel ID. Discord or Slack channels must be available for headless mode."
    }

    for key in keys_needed:
        if key not in creds or not str(creds[key]).strip():
            hint = hints.get(key, "")
            if hint:
                print(f"\n{hint}")
            val = input(f"[?] Enter {key} (leave blank to skip): ").strip()
            if val:
                creds[key] = val
            elif key not in creds:
                creds[key] = ""
        else:
            print(f"[*] {key} is already configured.")
            
    with open(CREDS_FILE, 'w') as f:
        json.dump(creds, f, indent=4)
        print(f"[*] Credentials saved to {CREDS_FILE}")

    # --- Secure Vault Option ---
    print("\n--- 🔒 Secure Vault Option ---")
    seal_choice = input("[?] Would you like to seal these credentials in an encrypted vault? [y/N]: ").strip().lower()
    if seal_choice == 'y':
        print("\nNote: The vault password is NOT stored and is unrecoverable if lost.")
        pwd = getpass.getpass("Set a strong vault password: ")
        confirm = getpass.getpass("Confirm vault password: ")
        
        if pwd == confirm:
            try:
                # Use the helper to create the vault
                CredentialsHelper.create_vault(creds, pwd)
                print(f"[*] Successfully created encrypted vault at {CredentialsHelper.VAULT_FILE}")
                
                # Optional cleanup
                rm_choice = input(f"[?] Remove plain-text JSON file ({CREDS_FILE}) for security? [y/N]: ").strip().lower()
                if rm_choice == 'y':
                    os.remove(CREDS_FILE)
                    print(f"[*] Plain-text credentials file removed.")
            except Exception as e:
                print(f"[!] Error creating local vault: {e}")
        else:
            print("[!] Passwords do not match. Skipping local vault creation.")

    # --- HashiCorp Vault Option ---
    print("\n--- ☁️ HashiCorp Vault Option ---")
    hc_choice = input("[?] Would you like to upload these credentials to HashiCorp Vault? [y/N]: ").strip().lower()
    if hc_choice == 'y':
        # Check for required env vars
        if not os.environ.get("VAULT_ADDR") or not os.environ.get("VAULT_TOKEN"):
            print("[!] Error: VAULT_ADDR and VAULT_TOKEN not found in environment.")
            print("    Please export them before using HashiCorp Vault.")
        else:
            if CredentialsHelper.seal_to_hashicorp(creds):
                # Optional cleanup if not already done
                if os.path.exists(CREDS_FILE):
                    rm_choice = input(f"[?] Remove plain-text JSON file ({CREDS_FILE}) for security? [y/N]: ").strip().lower()
                    if rm_choice == 'y':
                        os.remove(CREDS_FILE)
                        print(f"[*] Plain-text credentials file removed.")

def set_preferences():
    print("\n--- ⚙️ User Preferences ---")
    ensure_config_dir()
    
    prefs = {}
    if PREFS_FILE.exists():
        try:
            with open(PREFS_FILE, 'r') as f:
                prefs = json.load(f)
        except json.JSONDecodeError:
            pass
            
    print("Default Models:")
    print(" 1) gpt-5.1")
    print(" 2) claude-sonnet-4-5-20250929")
    print(" 3) gpt-5-mini")
    
    choice = input("[?] Select default model [1-3, or press Enter to keep current]: ").strip()
    if choice == '1':
        prefs["default_model"] = "gpt-5.1"
    elif choice == '2':
        prefs["default_model"] = "claude-sonnet-4-5-20250929"
    elif choice == '3':
        prefs["default_model"] = "gpt-5-mini"
        
    ch_choice = input("[?] Default Discord Channel ID (leave blank to skip/keep current): ").strip()
    if ch_choice:
        prefs["discord_channel"] = ch_choice
        
    with open(PREFS_FILE, 'w') as f:
        json.dump(prefs, f, indent=4)
        print(f"[*] Preferences saved to {PREFS_FILE}")

def provision_skills():
    print("\n--- 🧠 Skill Provisioning ---")
    if not SKILL_FILE.exists():
        print(f"[*] Creating foundational skill: network-facts-and-procedures...")
        SKILL_DIR.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()
        content = f"""---
description: 'Provides concise network facts, standard operating procedures, and step-by-step
  runbooks for common networking tasks. Use when an agent needs authoritative quick-reference
  for routing, switching, VLANs, subnetting, DHCP, NAT, firewall rules, access-control,
  cabling, diagnostics, troubleshooting, change procedures, or RFC references. Keywords:
  network facts, procedures, runbook, troubleshooting, VLAN, routing, subnetting,
  DHCP, NAT, firewall, RFC, configuration, diagnostics.'
metadata:
  created: '{today}'
  last-updated: '{today}'
  total-updates: 1
name: network-facts-and-procedures
---
"""
        with open(SKILL_FILE, 'w') as f:
            f.write(content)
        print(f"[*] Foundational skill created at {SKILL_FILE}")
        return True
    else:
        print(f"[*] Foundational skill already exists at {SKILL_FILE}")
        return False

def start_services(features):
    print("\n--- 🛠 Starting Background Services ---")
    
    processes = []
    
    if features.get("mcp"):
        print("[*] Starting MCP Server in background...")
        proc = subprocess.Popen(["workerxy", "mcp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        processes.append(("MCP Server", proc))
        
    if features.get("headless") or features.get("discord"):
        print("[*] Ensuring RabbitMQ is running...")
        print("    (Run `sudo systemctl start rabbitmq-server` manually if not already running)")
        
    if features.get("headless"):
        print("[*] Starting Headless Worker...")
        proc = subprocess.Popen(["workerxy", "headless"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        processes.append(("Headless Worker", proc))
        
    if features.get("discord"):
        print("[*] Starting Discord Bridge...")
        proc = subprocess.Popen(["workerxy", "discord"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        processes.append(("Discord Bridge", proc))
        
    if features.get("webui"):
        print("[*] Starting Web UI Dashboard...")
        proc = subprocess.Popen(["workerxy", "ui"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        processes.append(("Web UI", proc))
        
    time.sleep(2)
    print("\n--- ✅ Initialization Complete ---")
    for name, p in processes:
        if p.poll() is None:
            print(f" [OK] {name} is running (PID {p.pid})")
        else:
            print(f" [FAIL] {name} failed to start or exited immediately.")

def show_next_steps(skill_created=False):
    print("\n--- 🚀 Next Steps ---")
    
    if skill_created:
        print("\n--- 🧠 Skill Provisioning (Important) ---")
        print("A foundational skill file has been created at:")
        print(f"  {SKILL_FILE}")
        print("To enable the agent to interact autonomously, please update it with your specific network details.")
        print("You can edit the file manually, or run the agent and use:")
        print("  /skill update")
        print("in a conversation where your onboarding information is mentioned.\n")

    print("To start the WorkerXY Smart CLI (Interactive Terminal):")
    print("  Native: workerxy start-cli")
    print("  Docker: docker compose run --rm cli")
    print("\nTo start the Full Stack (MCPs, Headless, Discord, and UI):")
    print("  Native: workerxy start-all")
    print("  Docker: docker compose up all")
    print("\nFor more options, check the README.md or run `workerxy --help`.")

def main():
    try:
        features = gather_features()
        gather_credentials()
        set_preferences()
        skill_created = provision_skills()
        
        start = input("\n[?] Do you want to start the selected background services now? [Y/n]: ").strip().lower()
        if start != 'n':
            start_services(features)
        else:
            print("[*] Service startup skipped. You can run them manually via `workerxy <component>`.")
            
        show_next_steps(skill_created)
            
    except KeyboardInterrupt:
        print("\n[!] Initialization aborted by user.")
        show_next_steps()
        sys.exit(1)

if __name__ == "__main__":
    main()
