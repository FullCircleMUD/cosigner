#!/usr/bin/env python3
"""
Configure a SignerList on an XRPL account for multisig.

Submits a SignerListSet transaction signed by the account's current
master key. Once the signer list is active, all future transactions
from this account require signatures meeting the quorum.

Usage:
    # 2-of-2 setup (vault or issuer):
    python setup/configure_signerlist.py \\
        --network wss://s.altnet.rippletest.net:51233 \\
        --account-seed sEdMasterKey... \\
        --signers rKeyA:1,rKeyB:1 \\
        --quorum 2

    # Verify current signer list:
    python setup/configure_signerlist.py \\
        --network wss://s.altnet.rippletest.net:51233 \\
        --account-seed sEdMasterKey... \\
        --verify-only

    # Remove signer list (restore single-sig):
    python setup/configure_signerlist.py \\
        --network wss://s.altnet.rippletest.net:51233 \\
        --account-seed sEdMasterKey... \\
        --remove
"""

import argparse
import asyncio
import sys

from xrpl.asyncio.clients import AsyncWebsocketClient
from xrpl.asyncio.transaction import submit_and_wait
from xrpl.models.requests import AccountInfo
from xrpl.models.transactions import SignerListSet
from xrpl.models.transactions.signer_list_set import SignerEntry
from xrpl.wallet import Wallet


async def get_signer_list(network_url: str, address: str) -> dict:
    """Query current signer list for an account."""
    async with AsyncWebsocketClient(network_url) as client:
        response = await client.request(
            AccountInfo(account=address, signer_lists=True)
        )
    result = response.result
    account_data = result.get("account_data", {})
    signer_lists = account_data.get("signer_lists", [])
    return {
        "address": address,
        "signer_lists": signer_lists,
    }


async def set_signer_list(
    network_url: str,
    account_seed: str,
    signer_entries: list[SignerEntry],
    quorum: int,
) -> str:
    """Submit a SignerListSet transaction."""
    wallet = Wallet.from_seed(account_seed)

    tx = SignerListSet(
        account=wallet.address,
        signer_quorum=quorum,
        signer_entries=signer_entries,
    )

    async with AsyncWebsocketClient(network_url) as client:
        result = await submit_and_wait(tx, client, wallet)

    tx_result = result.result.get("meta", {}).get("TransactionResult")
    tx_hash = result.result.get("hash", "")

    if tx_result != "tesSUCCESS":
        print(f"\n  ERROR: SignerListSet failed: {tx_result} (tx: {tx_hash})")
        sys.exit(1)

    return tx_hash


async def remove_signer_list(network_url: str, account_seed: str) -> str:
    """Remove the signer list (restore single-sig)."""
    wallet = Wallet.from_seed(account_seed)

    tx = SignerListSet(
        account=wallet.address,
        signer_quorum=0,
    )

    async with AsyncWebsocketClient(network_url) as client:
        result = await submit_and_wait(tx, client, wallet)

    tx_result = result.result.get("meta", {}).get("TransactionResult")
    tx_hash = result.result.get("hash", "")

    if tx_result != "tesSUCCESS":
        print(f"\n  ERROR: SignerListSet removal failed: {tx_result} (tx: {tx_hash})")
        sys.exit(1)

    return tx_hash


def parse_signers(signer_str: str) -> list[SignerEntry]:
    """Parse 'rAddress:weight,rAddress:weight,...' into SignerEntry list."""
    entries = []
    for part in signer_str.split(","):
        part = part.strip()
        if ":" not in part:
            print(f"  ERROR: Invalid signer format '{part}' — expected 'rAddress:weight'")
            sys.exit(1)
        address, weight_str = part.rsplit(":", 1)
        try:
            weight = int(weight_str)
        except ValueError:
            print(f"  ERROR: Invalid weight '{weight_str}' for signer {address}")
            sys.exit(1)
        entries.append(SignerEntry(account=address.strip(), signer_weight=weight))
    return entries


def main():
    parser = argparse.ArgumentParser(
        description="Configure XRPL multisig SignerList",
    )
    parser.add_argument(
        "--network", required=True,
        help="XRPL websocket URL (e.g. wss://s.altnet.rippletest.net:51233)",
    )
    parser.add_argument(
        "--account-seed", required=True,
        help="Master key seed for the account to configure",
    )
    parser.add_argument(
        "--signers", type=str, default=None,
        help="Comma-separated signer entries: rAddr:weight,rAddr:weight,rAddr:weight",
    )
    parser.add_argument(
        "--quorum", type=int, default=None,
        help="Required signature weight quorum",
    )
    parser.add_argument(
        "--verify-only", action="store_true",
        help="Only query and display the current signer list",
    )
    parser.add_argument(
        "--remove", action="store_true",
        help="Remove the signer list (restore single-sig)",
    )
    args = parser.parse_args()

    wallet = Wallet.from_seed(args.account_seed)
    print(f"\nAccount: {wallet.address}")
    print(f"Network: {args.network}\n")

    # Verify-only mode
    if args.verify_only:
        info = asyncio.run(get_signer_list(args.network, wallet.address))
        signer_lists = info["signer_lists"]
        if not signer_lists:
            print("  No signer list configured (single-sig mode).")
        else:
            for sl in signer_lists:
                print(f"  Quorum: {sl.get('SignerQuorum')}")
                for entry in sl.get("SignerEntries", []):
                    se = entry.get("SignerEntry", {})
                    print(f"    {se.get('Account')} (weight: {se.get('SignerWeight')})")
        return

    # Remove mode
    if args.remove:
        print("Removing signer list...")
        tx_hash = asyncio.run(remove_signer_list(args.network, args.account_seed))
        print(f"  Signer list removed (tx: {tx_hash})")
        print("  Account is now single-sig.\n")
        return

    # Set mode — requires --signers and --quorum
    if not args.signers or args.quorum is None:
        parser.error("--signers and --quorum are required (unless --verify-only or --remove)")

    entries = parse_signers(args.signers)
    total_weight = sum(e.signer_weight for e in entries)

    print(f"Setting signer list (quorum: {args.quorum}, total weight: {total_weight}):")
    for e in entries:
        print(f"  {e.account} (weight: {e.signer_weight})")

    if args.quorum > total_weight:
        print(f"\n  ERROR: Quorum ({args.quorum}) exceeds total weight ({total_weight})")
        sys.exit(1)

    # Confirm
    confirm = input("\nProceed? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    tx_hash = asyncio.run(set_signer_list(
        args.network, args.account_seed, entries, args.quorum,
    ))
    print(f"\n  SignerListSet submitted (tx: {tx_hash})")

    # Verify
    print("\nVerifying...")
    info = asyncio.run(get_signer_list(args.network, wallet.address))
    signer_lists = info["signer_lists"]
    if signer_lists:
        sl = signer_lists[0]
        print(f"  Quorum: {sl.get('SignerQuorum')}")
        for entry in sl.get("SignerEntries", []):
            se = entry.get("SignerEntry", {})
            print(f"    {se.get('Account')} (weight: {se.get('SignerWeight')})")
        print("\n  SignerList configured successfully.\n")
    else:
        print("\n  WARNING: No signer list found after submission.\n")


if __name__ == "__main__":
    main()
