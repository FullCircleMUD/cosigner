#!/usr/bin/env python3
"""
Integration test for the cosigner deployment.

Builds a real XRPL transaction, signs with key A (multisign=True),
POSTs to the cosigner service, and verifies the response.
Runs against testnet — no game server needed.

Usage:
    python setup/integration_test.py \
        --cosigner-url https://tx.fcmud.world \
        --api-key <COSIGNER_API_KEY> \
        --signer-seed <KEY_A_SEED> \
        --vault-address <VAULT_ADDRESS> \
        --issuer-address <ISSUER_ADDRESS> \
        --network wss://s.altnet.rippletest.net:51233

    # Or with defaults for FCM testnet:
    python setup/integration_test.py \
        --api-key <COSIGNER_API_KEY> \
        --signer-seed <KEY_A_SEED>
"""

import argparse
import asyncio
import sys

import httpx

from xrpl.asyncio.clients import AsyncWebsocketClient
from xrpl.asyncio.transaction import autofill
from xrpl.core.binarycodec import encode
from xrpl.models.amounts import IssuedCurrencyAmount
from xrpl.models.transactions import Payment
from xrpl.transaction import sign
from xrpl.wallet import Wallet


TESTNET_NETWORK = "wss://s.altnet.rippletest.net:51233"
TESTNET_VAULT = "rhYjpvpoU6FFjVSMvDRR1AUndgQx56TWaQ"
TESTNET_ISSUER = "rU3VtgY3LE63tmd7egjPUx37JqQXumokyJ"


def encode_currency_hex(code):
    """Encode currency codes >3 chars as hex (same as game server)."""
    if len(code) <= 3:
        return code
    return code.encode("ascii").hex().upper().ljust(40, "0")


async def build_and_sign(network_url, signer_seed, vault_address, issuer_address):
    """Build a small Payment tx, autofill, and sign with key A."""
    wallet_a = Wallet.from_seed(signer_seed)
    print(f"  Key A address: {wallet_a.address}")
    print(f"  Vault address: {vault_address}")

    # Send a tiny amount of FCMGold from vault back to the issuer.
    # Issuer can always receive its own currency, so this is safe.
    tx = Payment(
        account=vault_address,
        destination=issuer_address,
        amount=IssuedCurrencyAmount(
            currency=encode_currency_hex("FCMGold"),
            value="0.001",
            issuer=issuer_address,
        ),
    )

    print(f"  Connecting to {network_url} for autofill...")
    async with AsyncWebsocketClient(network_url) as client:
        tx_filled = await autofill(tx, client)

    # Multisig needs higher fee: base_fee * (1 + num_signers)
    # With 2 signers (A + B), multiply by 3
    base_fee = int(tx_filled.fee)
    multisig_fee = str(base_fee * 3)
    tx_filled = tx_filled.to_xrpl()
    tx_filled["Fee"] = multisig_fee
    from xrpl.models import Transaction
    tx_filled = Transaction.from_xrpl(tx_filled)

    print(f"  Autofilled: Sequence={tx_filled.sequence}, "
          f"LastLedgerSequence={tx_filled.last_ledger_sequence}, "
          f"Fee={tx_filled.fee} (multisig adjusted)")

    signed_a = sign(tx_filled, wallet_a, multisign=True)
    tx_blob = encode(signed_a.to_xrpl())
    print(f"  Signed with key A, blob length={len(tx_blob)}")

    return tx_blob


async def post_to_cosigner(cosigner_url, api_key, tx_blob):
    """POST the signed blob to the cosigner and return the response."""
    url = f"{cosigner_url.rstrip('/')}/cosign"
    print(f"  POSTing to {url}...")

    async with httpx.AsyncClient(timeout=120.0) as http:
        response = await http.post(
            url,
            json={"tx_blob": tx_blob},
            headers={"X-API-Key": api_key},
        )

    print(f"  Response: {response.status_code}")
    return response


def main():
    parser = argparse.ArgumentParser(
        description="Integration test for cosigner deployment",
    )
    parser.add_argument(
        "--cosigner-url", default="https://tx.fcmud.world",
        help="Cosigner service URL (default: https://tx.fcmud.world)",
    )
    parser.add_argument("--api-key", required=True, help="Cosigner API key")
    parser.add_argument("--signer-seed", required=True, help="Key A's seed")
    parser.add_argument(
        "--vault-address", default=TESTNET_VAULT,
        help=f"Vault account address (default: {TESTNET_VAULT})",
    )
    parser.add_argument(
        "--issuer-address", default=TESTNET_ISSUER,
        help=f"Issuer address (default: {TESTNET_ISSUER})",
    )
    parser.add_argument(
        "--network", default=TESTNET_NETWORK,
        help=f"XRPL network URL for autofill (default: {TESTNET_NETWORK})",
    )
    args = parser.parse_args()

    print("\n=== Cosigner Integration Test ===\n")

    # Step 1: Health check
    print("[1] Health check...")
    health_url = f"{args.cosigner_url.rstrip('/')}/health"
    try:
        resp = httpx.get(health_url, timeout=10.0)
        print(f"  {resp.status_code}: {resp.json()}")
        if resp.status_code != 200:
            print("  FAIL: Health check failed")
            sys.exit(1)
    except Exception as e:
        print(f"  FAIL: Could not reach cosigner: {e}")
        sys.exit(1)

    # Step 2: Build and sign tx
    print("\n[2] Building and signing transaction...")
    try:
        tx_blob = asyncio.run(build_and_sign(
            args.network, args.signer_seed, args.vault_address, args.issuer_address,
        ))
    except Exception as e:
        print(f"  FAIL: {e}")
        sys.exit(1)

    # Step 3: POST to cosigner
    print("\n[3] Sending to cosigner...")
    try:
        response = asyncio.run(post_to_cosigner(
            args.cosigner_url, args.api_key, tx_blob,
        ))
    except Exception as e:
        print(f"  FAIL: {e}")
        sys.exit(1)

    # Step 4: Verify response
    print("\n[4] Result:")
    data = response.json()
    if response.status_code == 200:
        print(f"  SUCCESS!")
        print(f"  tx_hash:       {data.get('tx_hash')}")
        print(f"  engine_result: {data.get('engine_result')}")
        print(f"  wallet_name:   {data.get('wallet_name')}")
    else:
        print(f"  FAILED: {response.status_code}")
        detail = data.get("detail", data)
        if isinstance(detail, dict):
            print(f"  error:  {detail.get('error')}")
            print(f"  detail: {detail.get('detail')}")
        else:
            print(f"  detail: {detail}")
        sys.exit(1)

    print("\n=== Test Complete ===\n")


if __name__ == "__main__":
    main()
