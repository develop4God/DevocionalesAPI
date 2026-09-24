#!/usr/bin/env python3
"""Retry-launch a VM.Standard.A1.Flex instance across Ashburn ADs until OCI has capacity.

Usage:
    python3 scripts/oci_a1_retry_launch.py

Requires the OCI CLI configured with a valid session (oci session authenticate),
using --auth security_token like the rest of this session's commands.
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

ADS = [
    "YOyk:US-ASHBURN-AD-1",
    "YOyk:US-ASHBURN-AD-2",
    "YOyk:US-ASHBURN-AD-3",
]

RETRY_INTERVAL_SECONDS = 20


def launch(ad: str) -> tuple[bool, str]:
    cmd = [
        "oci", "compute", "instance", "launch",
        "--auth", "security_token",
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
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0, (result.stdout if result.returncode == 0 else result.stderr)


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
