#!/usr/bin/env python3
"""
Generate XRPL keypairs for multisig setup.

Creates new wallets (seed + derived address) for use as signer keys.
Does NOT fund accounts — use the XRPL testnet faucet or testnet_reinit
to fund them after generation.

Usage:
    python setup/generate_keys.py
    python setup/generate_keys.py --count 3 --labels vault_a,cosigner_b,recovery_c
"""

import argparse

from xrpl.wallet import Wallet


def main():
    parser = argparse.ArgumentParser(
        description="Generate XRPL keypairs for multisig setup",
    )
    parser.add_argument(
        "--count", type=int, default=3,
        help="Number of keypairs to generate (default: 3)",
    )
    parser.add_argument(
        "--labels", type=str, default=None,
        help="Comma-separated labels for each key (e.g. vault_a,cosigner_b,recovery_c)",
    )
    args = parser.parse_args()

    labels = None
    if args.labels:
        labels = [l.strip() for l in args.labels.split(",")]
        if len(labels) != args.count:
            parser.error(
                f"Number of labels ({len(labels)}) must match --count ({args.count})"
            )

    print(f"\nGenerating {args.count} XRPL keypair(s)...\n")
    print("=" * 70)

    for i in range(args.count):
        wallet = Wallet.create()
        label = labels[i] if labels else f"key_{i + 1}"

        print(f"\n  [{label}]")
        print(f"  Address: {wallet.address}")
        print(f"  Seed:    {wallet.seed}")
        print(f"  Pub key: {wallet.public_key}")

    print("\n" + "=" * 70)
    print("\nStore seeds securely. They cannot be recovered if lost.")
    print("Fund accounts via testnet faucet before use:")
    print("  POST https://faucet.altnet.rippletest.net/accounts")
    print('  Body: {"destination": "<address>"}\n')


if __name__ == "__main__":
    main()
