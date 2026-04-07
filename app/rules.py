"""
Business rule validation engine for the co-signing service.

Each configured wallet has its own rule set. Transactions are validated
against the rules for the wallet identified by the transaction's `account`
field before the co-signer adds its signature.
"""

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

from app.config import WalletConfig

logger = logging.getLogger(__name__)


@dataclass
class RuleViolation:
    """A single rule violation."""

    rule: str
    detail: str


class RateLimiter:
    """Simple sliding-window rate limiter per wallet address."""

    def __init__(self):
        self._timestamps: dict[str, list[float]] = defaultdict(list)

    def check(self, address: str, max_per_minute: int) -> bool:
        """Return True if request is within rate limit."""
        now = time.monotonic()
        window_start = now - 60.0

        # Prune old timestamps
        timestamps = self._timestamps[address]
        self._timestamps[address] = [
            ts for ts in timestamps if ts > window_start
        ]

        if len(self._timestamps[address]) >= max_per_minute:
            return False

        self._timestamps[address].append(now)
        return True


# Module-level rate limiter instance (persists across requests).
_rate_limiter = RateLimiter()


def validate_transaction(
    tx_dict: dict,
    wallet: WalletConfig,
) -> list[RuleViolation]:
    """
    Validate a deserialised XRPL transaction against a wallet's rules.

    Args:
        tx_dict: The transaction as a dict (from Transaction.to_xrpl()).
        wallet: The wallet configuration with rules to check against.

    Returns:
        List of RuleViolation objects. Empty list = all rules passed.
    """
    violations = []
    rules = wallet.rules

    # 1. Transaction type — allowed list
    tx_type = tx_dict.get("TransactionType", "")
    if rules.allowed_tx_types and tx_type not in rules.allowed_tx_types:
        violations.append(RuleViolation(
            rule="allowed_tx_types",
            detail=f"Transaction type '{tx_type}' is not allowed for "
                   f"wallet '{wallet.name}'",
        ))

    # 2. Transaction type — blocked list (defence in depth)
    if tx_type in rules.blocked_tx_types:
        violations.append(RuleViolation(
            rule="blocked_tx_types",
            detail=f"Transaction type '{tx_type}' is explicitly blocked for "
                   f"wallet '{wallet.name}'",
        ))

    # 3. Currency issuer validation
    if rules.require_issuer:
        issuer_violations = _check_currency_issuers(
            tx_dict, rules.require_issuer, wallet.name,
        )
        violations.extend(issuer_violations)

    # 4. Rate limit
    if not _rate_limiter.check(wallet.address, rules.max_per_minute):
        violations.append(RuleViolation(
            rule="rate_limit",
            detail=f"Rate limit exceeded for wallet '{wallet.name}': "
                   f"max {rules.max_per_minute}/minute",
        ))

    if violations:
        logger.warning(
            "Transaction rejected for %s (%s): %s",
            wallet.name,
            wallet.address[:8],
            "; ".join(v.detail for v in violations),
        )
    else:
        logger.info(
            "Transaction validated for %s (%s): type=%s",
            wallet.name,
            wallet.address[:8],
            tx_type,
        )

    return violations


def _check_currency_issuers(
    tx_dict: dict,
    required_issuer: str,
    wallet_name: str,
) -> list[RuleViolation]:
    """Check that all IssuedCurrencyAmount fields reference the required issuer."""
    violations = []

    # Fields that can contain IssuedCurrencyAmount
    amount_fields = ["Amount", "DeliverMax", "SendMax", "TakerGets", "TakerPays"]

    for field_name in amount_fields:
        amount = tx_dict.get(field_name)
        if not isinstance(amount, dict):
            continue
        issuer = amount.get("issuer", "")
        if issuer and issuer != required_issuer:
            violations.append(RuleViolation(
                rule="require_issuer",
                detail=f"Field '{field_name}' references issuer '{issuer}', "
                       f"expected '{required_issuer}' for wallet '{wallet_name}'",
            ))

    return violations
