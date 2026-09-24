#!/usr/bin/env python3
"""Retry-launch a VM.Standard.A1.Flex instance across Ashburn ADs until OCI has capacity.

On success, automatically launches setup_ollama_gemma.py in the background
(detached, logs to setup_ollama_gemma.log next to this script) so Ollama
install + model pull start immediately without waiting for the user to run
it by hand.

Usage:
    python3 scripts/oci_a1_retry_launch.py

Requires the OCI CLI configured with a permanent API key profile in
~/.oci/config (no browser session token, so it never expires mid-run).
"""
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

COMPARTMENT_ID = "ocid1.tenancy.oc1..aaaaaaaar4akt2yj6opp7jkpzc7dzkog2kki2swroy5ee5c3shvephg27tea"
IMAGE_ID = "ocid1.image.oc1.iad.aaaaaaaacuygljashkvpqu5qqmlausq2vwrwasp3lxpbpitxjhvbhsktlhma"
SUBNET_ID = "ocid1.subnet.oc1.iad.aaaaaaaagokuawamgikuy2r5uf4gsnrei3ctnbjn4irvbvwvuavqg4ssnmza"
SSH_KEY_FILE = "/home/develop4god/.ssh/oracle_devocional.pub"
DISPLAY_NAME = "gemma4-12b-server"

RESULT_FILE = Path(__file__).parent / "gemma4_12b_server_instance.json"
SETUP_SCRIPT = Path(__file__).parent / "setup_ollama_gemma.py"
SETUP_LOG_FILE = Path(__file__).parent / "setup_ollama_gemma.log"

ADS = [
    "YOyk:US-ASHBURN-AD-1",
    "YOyk:US-ASHBURN-AD-2",
    "YOyk:US-ASHBURN-AD-3",
]

RETRY_INTERVAL_SECONDS = 20


LAUNCH_TIMEOUT_SECONDS = 60


def launch(ad: str) -> tuple[bool, str]:
    cmd = [
        "oci", "compute", "instance", "launch",
        "--availability-domain", ad,
        "--compartment-id", COMPARTMENT_ID,
        "--display-name", DISPLAY_NAME,
        "--shape", "VM.Standard.A1.Flex",
        "--shape-config", '{"ocpus": 2, "memoryInGBs": 12}',
        "--image-id", IMAGE_ID,
        "--subnet-id", SUBNET_ID,
        "--assign-public-ip", "true",
        "--ssh-authorized-keys-file", SSH_KEY_FILE,
    ]
    print(f"  -> running: {' '.join(cmd)}", flush=True)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=LAUNCH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print(f"  -> TIMED OUT after {LAUNCH_TIMEOUT_SECONDS}s (likely an expired session token"
              " blocking on an interactive re-auth prompt). Run 'oci session authenticate"
              " --region us-ashburn-1' and restart this script.", flush=True)
        sys.exit(1)
    print(f"  -> exit code: {result.returncode}", flush=True)
    if result.stderr.strip():
        print(f"  -> stderr: {result.stderr.strip()[:500]}", flush=True)
    return result.returncode == 0, (result.stdout if result.returncode == 0 else result.stderr)


def launch_setup_script() -> None:
    """Fire-and-forget: start setup_ollama_gemma.py detached so it keeps
    running (installing Ollama, pulling the model) after this process exits,
    without requiring the user to start it by hand."""
    print(f"Launching {SETUP_SCRIPT.name} in the background (log: {SETUP_LOG_FILE}) ...", flush=True)
    with open(SETUP_LOG_FILE, "w") as log_file:
        subprocess.Popen(
            [sys.executable, str(SETUP_SCRIPT)],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )


def main() -> None:
    attempt = 0
    while True:
        for ad in ADS:
            attempt += 1
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] Attempt {attempt} — trying {ad} ...", flush=True)
            ok, output = launch(ad)
            if ok:
                print(f"[{ts}] SUCCESS on {ad}!\n")
                print(output)
                data = json.loads(output)
                RESULT_FILE.write_text(json.dumps({
                    "instance_id": data["data"]["id"],
                    "availability_domain": ad,
                }, indent=2))
                print(f"Instance info written to {RESULT_FILE}")
                launch_setup_script()
                return
            if "Out of host capacity" in output:
                print(f"[{ts}] Out of host capacity on {ad}, moving on.")
            else:
                print(f"[{ts}] Unexpected error on {ad}:\n{output}")
                print("Stopping — not a capacity error, needs investigation.")
                sys.exit(1)
        print(f"All ADs exhausted this round, sleeping {RETRY_INTERVAL_SECONDS}s...\n", flush=True)
        time.sleep(RETRY_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
