#!/usr/bin/env python3
"""
Prisma SD-WAN – Scheduled Software Download & Upgrade
======================================================
This script:
  1. Authenticates via OAuth2 client credentials to obtain an access token.
  2. Offers a dry-run (cold run) that shows exactly what will happen — no
     changes are made until you confirm.
  3. Waits until DOWNLOAD_TIME, then triggers a software download on every
     machine in the tenant.
  4. Waits until UPGRADE_TIME, then triggers the upgrade on every machine.

Configuration
-------------
Edit the block marked "USER CONFIGURATION" below before running.

Dependencies
------------
    pip install requests

Usage
-----
    python sdwan_scheduled_upgrade.py
"""

import time
import logging
from datetime import datetime

import requests

# ---------------------------------------------------------------------------
# USER CONFIGURATION – edit everything in this block
# ---------------------------------------------------------------------------

# OAuth2 client credentials (from requestAccessToken.py)
CLIENT_ID     = "scl_api@1416375183.iam.panserviceaccount.com"
CLIENT_SECRET = "66b5b9a6-2fc9-42a9-ad2b-f189cbdcc42e"
TSG_ID        = "1416375183"  # Tenant Service Group ID — also used as tenant_id

# Region of your controller, e.g. "us-west1", "eu-west1"
REGION = "us-west1"

# Software version to download and install (e.g. "6.4.1-b4")
TARGET_VERSION = "6.4.1-b4"

# --- Scheduled times (local system time) ---
# Format: "YYYY-MM-DD HH:MM"  (24-hour clock)
DOWNLOAD_TIME = "2025-05-01 02:00"   # When to start the download
UPGRADE_TIME  = "2025-05-02 03:00"   # When to start the upgrade

# ---------------------------------------------------------------------------
# END OF USER CONFIGURATION
# ---------------------------------------------------------------------------

AUTH_URL = "https://auth.apps.paloaltonetworks.com/oauth2/access_token"
BASE_URL = f"https://api.{REGION}.cloudgenix.com"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

_access_token: str = ""


# ---------------------------------------------------------------------------
# Authentication  (logic from requestAccessToken.py)
# ---------------------------------------------------------------------------

def fetch_access_token() -> str:
    """Request a fresh OAuth2 access token using client credentials."""
    log.info("Requesting access token from %s …", AUTH_URL)
    response = requests.post(
        AUTH_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=f"grant_type=client_credentials&scope=tsg_id:{TSG_ID}",
        auth=(CLIENT_ID, CLIENT_SECRET),
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    access_token = data["access_token"]
    log.info("Access token obtained successfully.")
    return access_token


def refresh_token() -> None:
    global _access_token
    _access_token = fetch_access_token()


def auth_headers() -> dict:
    return {
        "x-auth-token": _access_token,
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------

def wait_until(target_str: str) -> None:
    """
    Block until local clock reaches *target_str* ('YYYY-MM-DD HH:MM').
    Refreshes the token every 10 minutes so it stays valid.
    """
    target_dt = datetime.strptime(target_str, "%Y-%m-%d %H:%M")
    log.info("Waiting until %s …", target_dt.strftime("%Y-%m-%d %H:%M"))
    while True:
        now = datetime.now()
        remaining = (target_dt - now).total_seconds()
        if remaining <= 0:
            log.info("Scheduled time reached: %s", target_str)
            return
        sleep_sec = min(remaining, 600)
        log.info(
            "Time remaining until %s: %.0f minute(s) – sleeping %.0f s",
            target_str, remaining / 60, sleep_sec,
        )
        time.sleep(sleep_sec)
        refresh_token()


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def get_all_machines() -> list[dict]:
    url  = f"{BASE_URL}/v2.0/api/tenants/{TSG_ID}/machines"
    resp = requests.get(url, headers=auth_headers(), timeout=30)
    resp.raise_for_status()
    machines = resp.json().get("items", [])
    log.info("Found %d machine(s) in tenant %s", len(machines), TSG_ID)
    return machines


def get_machine_software(machine_id: str) -> list[dict]:
    url  = f"{BASE_URL}/v2.0/api/tenants/{TSG_ID}/machines/{machine_id}/software"
    resp = requests.get(url, headers=auth_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json().get("items", [])


def get_element_images() -> list[dict]:
    url  = f"{BASE_URL}/v2.4/api/tenants/{TSG_ID}/element_images"
    resp = requests.get(url, headers=auth_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json().get("items", [])


def find_image_id(images: list[dict], version: str) -> str | None:
    for img in images:
        if img.get("version") == version or img.get("name") == version:
            return img.get("id")
    return None


def update_machine_software(machine_id: str, software_id: str, payload: dict) -> dict:
    url = (
        f"{BASE_URL}/v2.0/api/tenants/{TSG_ID}"
        f"/machines/{machine_id}/software/{software_id}"
    )
    resp = requests.put(url, headers=auth_headers(), json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

def dry_run(machines: list[dict], image_id: str) -> bool:
    """
    Simulate the download and upgrade steps, printing a summary of every
    action that WOULD be taken without making any API changes.

    Returns True if the user confirms they want to proceed, False to abort.
    """
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    CYAN   = "\033[96m"
    YELLOW = "\033[93m"
    GREEN  = "\033[92m"
    RED    = "\033[91m"

    print()
    print(f"{BOLD}{'='*62}{RESET}")
    print(f"{BOLD}  DRY RUN (COLD RUN) — No changes will be made yet{RESET}")
    print(f"{BOLD}{'='*62}{RESET}")
    print(f"  Target version : {CYAN}{TARGET_VERSION}{RESET}  (image ID: {image_id})")
    print(f"  Download at    : {CYAN}{DOWNLOAD_TIME}{RESET}")
    print(f"  Upgrade at     : {CYAN}{UPGRADE_TIME}{RESET}")
    print(f"  Machines found : {CYAN}{len(machines)}{RESET}")
    print()

    will_download = []
    will_upgrade  = []
    will_skip     = []

    for machine in machines:
        machine_id   = machine["id"]
        machine_name = machine.get("name", machine_id)

        try:
            sw_records   = get_machine_software(machine_id)
            current_vers = None

            # Try to find what version is currently active
            active = next((r for r in sw_records if r.get("active")), None)
            if active:
                current_vers = active.get("version") or active.get("image_id", "unknown")

            record = next(
                (r for r in sw_records if r.get("image_id") == image_id), None
            )

            if record is None:
                if sw_records:
                    will_download.append((machine_name, current_vers, "new record based on existing"))
                    will_upgrade.append((machine_name, current_vers, TARGET_VERSION))
                else:
                    will_skip.append((machine_name, "no existing software record found"))
            else:
                current_state = record.get("upgrade_state", "unknown")
                will_download.append((machine_name, current_vers, f"state '{current_state}' → 'download'"))
                will_upgrade.append((machine_name, current_vers, TARGET_VERSION))

        except requests.HTTPError as exc:
            will_skip.append((machine_name, f"API error: {exc}"))
        except Exception as exc:
            will_skip.append((machine_name, f"Unexpected error: {exc}"))

    # --- Download phase summary ---
    print(f"{BOLD}Phase 1 — DOWNLOAD  (scheduled: {DOWNLOAD_TIME}){RESET}")
    print(f"  {'Machine':<30} {'Current Version':<20} {'Action'}")
    print(f"  {'-'*28} {'-'*18} {'-'*30}")
    for name, cur, action in will_download:
        cur_str = cur or "unknown"
        print(f"  {GREEN}{name:<30}{RESET} {cur_str:<20} Set upgrade_state → download")
    for name, reason in will_skip:
        print(f"  {RED}{name:<30}{RESET} {'—':<20} SKIP — {reason}")
    print()

    # --- Upgrade phase summary ---
    print(f"{BOLD}Phase 2 — UPGRADE   (scheduled: {UPGRADE_TIME}){RESET}")
    print(f"  {'Machine':<30} {'From Version':<20} {'To Version'}")
    print(f"  {'-'*28} {'-'*18} {'-'*20}")
    for name, cur, target in will_upgrade:
        cur_str = cur or "unknown"
        print(f"  {GREEN}{name:<30}{RESET} {cur_str:<20} {YELLOW}{target}{RESET}")
    for name, reason in will_skip:
        print(f"  {RED}{name:<30}{RESET} {'—':<20} SKIP — {reason}")
    print()

    # --- Totals ---
    print(f"{BOLD}Summary:{RESET}")
    print(f"  Machines to download : {len(will_download)}")
    print(f"  Machines to upgrade  : {len(will_upgrade)}")
    print(f"  Machines to skip     : {len(will_skip)}")
    print()
    print(f"{BOLD}{'='*62}{RESET}")
    print()

    # --- Confirmation prompt ---
    while True:
        answer = input(
            "Do you want to proceed with the scheduled download and upgrade? [yes/no]: "
        ).strip().lower()
        if answer in ("yes", "y"):
            print()
            log.info("User confirmed. Proceeding with scheduled operations.")
            return True
        elif answer in ("no", "n"):
            print()
            log.info("User aborted. No changes were made.")
            return False
        else:
            print("  Please type 'yes' or 'no'.")


# ---------------------------------------------------------------------------
# Core actions
# ---------------------------------------------------------------------------

def trigger_download_all(machines: list[dict], image_id: str) -> None:
    """Set upgrade_state to 'download' on every machine."""
    log.info("=== Starting DOWNLOAD phase for %d machine(s) ===", len(machines))
    for machine in machines:
        machine_id   = machine["id"]
        machine_name = machine.get("name", machine_id)
        try:
            sw_records = get_machine_software(machine_id)
            record = next(
                (r for r in sw_records if r.get("image_id") == image_id), None
            )
            if record is None:
                if sw_records:
                    record = {**sw_records[0], "image_id": image_id, "upgrade_state": "download"}
                else:
                    log.warning("[%s] No existing software record – skipping download.", machine_name)
                    continue
            else:
                record["upgrade_state"] = "download"

            result = update_machine_software(machine_id, record["id"], record)
            log.info(
                "[%s] Download triggered – state: %s",
                machine_name,
                result.get("upgrade_state", "unknown"),
            )
        except requests.HTTPError as exc:
            log.error("[%s] Download trigger failed: %s", machine_name, exc)
        except Exception as exc:
            log.error("[%s] Unexpected error during download: %s", machine_name, exc)


def trigger_upgrade_all(machines: list[dict], image_id: str) -> None:
    """Set upgrade_state to 'upgrade' on every machine."""
    log.info("=== Starting UPGRADE phase for %d machine(s) ===", len(machines))
    for machine in machines:
        machine_id   = machine["id"]
        machine_name = machine.get("name", machine_id)
        try:
            sw_records = get_machine_software(machine_id)
            record = next(
                (r for r in sw_records if r.get("image_id") == image_id), None
            )
            if record is None:
                log.warning(
                    "[%s] No software record for image %s – skipping upgrade.",
                    machine_name, image_id,
                )
                continue

            record["upgrade_state"] = "upgrade"
            result = update_machine_software(machine_id, record["id"], record)
            log.info(
                "[%s] Upgrade triggered – state: %s",
                machine_name,
                result.get("upgrade_state", "unknown"),
            )
        except requests.HTTPError as exc:
            log.error("[%s] Upgrade trigger failed: %s", machine_name, exc)
        except Exception as exc:
            log.error("[%s] Unexpected error during upgrade: %s", machine_name, exc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    log.info("Prisma SD-WAN Scheduled Upgrade Script")
    log.info("  Target version : %s", TARGET_VERSION)
    log.info("  Download time  : %s", DOWNLOAD_TIME)
    log.info("  Upgrade time   : %s", UPGRADE_TIME)
    log.info("  Tenant (TSG)   : %s", TSG_ID)
    log.info("  Region         : %s", REGION)

    # Validate scheduling order
    dl_dt = datetime.strptime(DOWNLOAD_TIME, "%Y-%m-%d %H:%M")
    up_dt = datetime.strptime(UPGRADE_TIME,  "%Y-%m-%d %H:%M")
    if up_dt <= dl_dt:
        raise ValueError("UPGRADE_TIME must be later than DOWNLOAD_TIME.")

    # Authenticate
    refresh_token()

    # Validate the target image exists before sleeping
    log.info("Fetching available element images …")
    images   = get_element_images()
    image_id = find_image_id(images, TARGET_VERSION)
    if not image_id:
        available = [img.get("version") or img.get("name") for img in images]
        raise ValueError(
            f"Version '{TARGET_VERSION}' not found in controller. "
            f"Available versions: {available}"
        )
    log.info("Resolved image ID for %s: %s", TARGET_VERSION, image_id)

    # Fetch machines
    machines = get_all_machines()
    if not machines:
        log.warning("No machines found – nothing to do.")
        return

    # --- Dry run / confirmation ---
    confirmed = dry_run(machines, image_id)
    if not confirmed:
        return

    # --- Phase 1: Download ---
    wait_until(DOWNLOAD_TIME)
    refresh_token()
    trigger_download_all(machines, image_id)
    log.info("Download phase complete. Waiting for upgrade window …")

    # --- Phase 2: Upgrade ---
    wait_until(UPGRADE_TIME)
    refresh_token()
    trigger_upgrade_all(machines, image_id)
    log.info("Upgrade phase complete. Script finished.")


if __name__ == "__main__":
    main()
