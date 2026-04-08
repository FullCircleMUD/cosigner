"""
XRPL multisign logic for the co-signing service.

Deserialises a partially-signed transaction blob, validates it,
co-signs with the configured wallet key, combines signatures,
and submits to the XRPL network.
"""

import logging

from xrpl.asyncio.clients import AsyncWebsocketClient
from xrpl.asyncio.transaction import submit_and_wait
from xrpl.models import Transaction
from xrpl.transaction import sign
from xrpl.transaction.multisign import multisign
from xrpl.wallet import Wallet

from app.config import AppConfig, WalletConfig
from app.rules import RuleViolation, validate_transaction

logger = logging.getLogger(__name__)


class CosignError(Exception):
    """Raised when co-signing fails."""

    def __init__(self, error_type: str, detail: str):
        super().__init__(detail)
        self.error_type = error_type
        self.detail = detail


async def cosign_and_submit(
    tx_blob: str,
    config: AppConfig,
) -> dict:
    """
    Co-sign a partially-signed transaction and submit to XRPL.

    Args:
        tx_blob: Hex-encoded transaction blob, already signed with key A
                 (multisign=True). Contains a Signers array with one entry.
        config: Application configuration with wallet configs and network URL.

    Returns:
        dict with tx_hash, engine_result, wallet_name.

    Raises:
        CosignError: If validation fails or submission fails.
    """
    # 1. Deserialise the transaction from blob
    try:
        tx = Transaction.from_blob(tx_blob)
    except Exception as e:
        raise CosignError("invalid_transaction", f"Failed to decode tx blob: {e}")

    tx_dict = tx.to_xrpl()

    # 2. Look up the wallet by account address
    account = tx_dict.get("Account", "")
    wallet_config = config.wallets.get(account)
    if wallet_config is None:
        raise CosignError(
            "unknown_wallet",
            f"No configuration for account {account}",
        )

    # 3. Validate business rules
    violations = validate_transaction(tx_dict, wallet_config)
    if violations:
        detail = "; ".join(v.detail for v in violations)
        raise CosignError("rule_violation", detail)

    # 4. Verify the transaction already has at least one signer (key A)
    signers = tx_dict.get("Signers", [])
    if not signers:
        raise CosignError(
            "missing_signature",
            "Transaction has no existing signatures — "
            "it must be partially signed with key A before sending to co-signer",
        )

    # 5. Co-sign with key B
    cosigner_wallet = Wallet.from_seed(wallet_config.seed)
    try:
        signed_b = sign(tx, cosigner_wallet, multisign=True)
    except Exception as e:
        raise CosignError("signing_failed", f"Failed to co-sign: {e}")

    # 6. Combine signatures (key A from blob + key B from our signing)
    try:
        combined = multisign(tx, [tx, signed_b])
    except Exception as e:
        raise CosignError("combine_failed", f"Failed to combine signatures: {e}")

    # 7. Submit to XRPL
    logger.info(
        "Submitting multisigned tx for %s (%s): type=%s",
        wallet_config.name,
        account[:8],
        tx_dict.get("TransactionType"),
    )

    network_url = wallet_config.network_url
    try:
        async with AsyncWebsocketClient(network_url) as client:
            result = await submit_and_wait(combined, client)
    except Exception as e:
        raise CosignError("submission_failed", f"XRPL submission failed: {e}")

    meta = result.result.get("meta", {})
    engine_result = meta.get("TransactionResult", "unknown")
    tx_hash = result.result.get("hash", "")

    if engine_result != "tesSUCCESS":
        raise CosignError(
            "xrpl_error",
            f"Transaction failed on-chain: {engine_result} (tx: {tx_hash})",
        )

    logger.info(
        "Transaction submitted for %s: %s (tx: %s)",
        wallet_config.name,
        engine_result,
        tx_hash,
    )

    return {
        "tx_hash": tx_hash,
        "engine_result": engine_result,
        "wallet_name": wallet_config.name,
        "meta": meta,
    }
