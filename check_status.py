#!/usr/bin/env python3

import subprocess
import time
import sys
import os

def run_cmd(cmd):
    """Run a command and return output"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
        return result.stdout + result.stderr
    except:
        return "ERROR"

def check_service(name, port=None, cmd=None):
    """Check if a service is running"""
    if port:
        result = os.system(f"curl -s http://localhost:{port}/api/health > /dev/null 2>&1")
        status = "✓ RUNNING" if result == 0 else "✗ NOT RUNNING"
    elif cmd:
        result = os.system(f"{cmd} > /dev/null 2>&1")
        status = "✓ OK" if result == 0 else "✗ ERROR"
    else:
        status = "?"
    
    print(f"  {name}: {status}")
    return status

print("🔍 QuenBot - System Status")
print("=" * 50)
print()

print("1️⃣  PostgreSQL Database:")
check_service("PostgreSQL", cmd="docker ps | grep quenbot-db")

print()
print("2️⃣  API Server (port 3001):")
check_service("API Server", port=3001)

print()
print("3️⃣  Dashboard (port 4173/5173):")
check_service("Dashboard", cmd="curl -s http://localhost:4173 > /dev/null || curl -s http://localhost:5173 > /dev/null")

print()
print("4️⃣  Python Agents:")
if os.path.exists("/workspaces/quenbot/python_agents/agents.log"):
    with open("/workspaces/quenbot/python_agents/agents.log", "r") as f:
        lines = f.readlines()[-3:]
    print("  ✓ Log file exists, last lines:")
    for line in lines:
        print(f"    {line.rstrip()}")
else:
    print("  ✗ Not started yet")

print()
print("=" * 50)
print()
print("🚀 TO START SERVICES (use separate terminals):")
print()
print("Terminal 1 - Python Agents:")
print("  cd /workspaces/quenbot/python_agents")
print("  python3 main.py")
print()
print("Terminal 2 - API Server:")
print("  cd /workspaces/quenbot")
print("  pnpm --dir artifacts/api-server run dev")
print()
print("Terminal 3 - Dashboard:")
print("  cd /workspaces/quenbot")
print("  pnpm --dir artifacts/market-intel run preview")
print()
print("Then access dashboard at http://localhost:4173 (or 5173)")
print()
