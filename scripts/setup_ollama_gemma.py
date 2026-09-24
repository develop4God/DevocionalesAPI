#!/usr/bin/env python3
"""Wait for gemma4-12b-server to be RUNNING and reachable, then install
Ollama and pull gemma4:12b over SSH.

Usage:
    python3 scripts/setup_ollama_gemma.py

Designed to be started any time (before or after the launch retry script
finishes) — it polls OCI directly for the instance rather than depending
on the retry script's output, so the two scripts don't need to be
coordinated by hand.
"""
import json
import subprocess
import sys
import time
from datetime import datetime

COMPARTMENT_ID = "ocid1.tenancy.oc1..aaaaaaaar4akt2yj6opp7jkpzc7dzkog2kki2swroy5ee5c3shvephg27tea"
DISPLAY_NAME = "gemma4-12b-server"
SSH_KEY_PRIVATE = "/home/develop4god/.ssh/oracle_devocional"
MODEL = "gemma4:12b"
POLL_INTERVAL_SECONDS = 15
SSH_RETRY_INTERVAL_SECONDS = 10


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def find_running_instance() -> dict | None:
    result = run([
        "oci", "compute", "instance", "list",
        "--auth", "security_token",
        "--compartment-id", COMPARTMENT_ID,
        "--display-name", DISPLAY_NAME,
        "--lifecycle-state", "RUNNING",
    ])
    if result.returncode != 0 or not result.stdout.strip():
        if result.stderr.strip():
            log(f"oci instance list error: {result.stderr.strip()}")
        return None
    try:
        data = json.loads(result.stdout)["data"]
    except json.JSONDecodeError:
        log(f"Unexpected non-JSON output from oci instance list: {result.stdout[:200]!r}")
        return None
    return data[0] if data else None


def get_public_ip(instance_id: str) -> str | None:
    result = run([
        "oci", "compute", "instance", "list-vnics",
        "--auth", "security_token",
        "--instance-id", instance_id,
    ])
    if result.returncode != 0 or not result.stdout.strip():
        if result.stderr.strip():
            log(f"oci list-vnics error: {result.stderr.strip()}")
        return None
    try:
        data = json.loads(result.stdout)["data"]
    except json.JSONDecodeError:
        log(f"Unexpected non-JSON output from oci list-vnics: {result.stdout[:200]!r}")
        return None
    if not data:
        return None
    return data[0].get("public-ip")


def wait_for_instance() -> dict:
    log(f"Polling for '{DISPLAY_NAME}' in RUNNING state...")
    while True:
        instance = find_running_instance()
        if instance:
            log(f"Found instance {instance['id']} — RUNNING.")
            return instance
        time.sleep(POLL_INTERVAL_SECONDS)


def wait_for_ssh(ip: str) -> None:
    log(f"Waiting for SSH on {ip}...")
    while True:
        result = run([
            "ssh", "-i", SSH_KEY_PRIVATE,
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=5",
            f"ubuntu@{ip}", "echo ok",
        ])
        if result.returncode == 0:
            log("SSH is up.")
            return
        time.sleep(SSH_RETRY_INTERVAL_SECONDS)


def install_ollama_and_pull_model(ip: str) -> None:
    log("Installing Ollama...")
    install_cmd = "curl -fsSL https://ollama.com/install.sh | sh"
    result = run([
        "ssh", "-i", SSH_KEY_PRIVATE,
        "-o", "StrictHostKeyChecking=accept-new",
        f"ubuntu@{ip}", install_cmd,
    ])
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        log("Ollama install failed.")
        sys.exit(1)

    log(f"Pulling model {MODEL} (this can take a while)...")
    pull_cmd = f"ollama pull {MODEL}"
    result = run([
        "ssh", "-i", SSH_KEY_PRIVATE,
        "-o", "StrictHostKeyChecking=accept-new",
        f"ubuntu@{ip}", pull_cmd,
    ])
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        log("Model pull failed.")
        sys.exit(1)

    log(f"Done. {MODEL} is ready on {ip}.")
    log(f"Connect with: ssh -i {SSH_KEY_PRIVATE} ubuntu@{ip}")
    log(f"Run with: ollama run {MODEL}")


def main() -> None:
    instance = wait_for_instance()
    ip = None
    while not ip:
        ip = get_public_ip(instance["id"])
        if not ip:
            time.sleep(5)
    log(f"Public IP: {ip}")
    wait_for_ssh(ip)
    install_ollama_and_pull_model(ip)


if __name__ == "__main__":
    main()
