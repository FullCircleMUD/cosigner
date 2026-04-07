"""
Unit tests for the co-signing service business rule validation.

No XRPL connection required — these test pure validation logic.
"""

import time
import unittest

from app.config import WalletConfig, WalletRules
from app.rules import RateLimiter, validate_transaction


def _make_wallet(
    address="rVAULT111111111111111111111111111",
    name="test_vault",
    seed="sEdFAKESEED",
    allowed_tx_types=None,
    blocked_tx_types=None,
    require_issuer=None,
    max_per_minute=30,
):
    """Helper to create a WalletConfig for testing."""
    return WalletConfig(
        address=address,
        name=name,
        seed=seed,
        rules=WalletRules(
            allowed_tx_types=frozenset(allowed_tx_types or []),
            blocked_tx_types=frozenset(blocked_tx_types or []),
            require_issuer=require_issuer,
            max_per_minute=max_per_minute,
        ),
    )


def _make_payment_tx(
    account="rVAULT111111111111111111111111111",
    destination="rPLAYER22222222222222222222222222",
    issuer="rISSUER33333333333333333333333333",
):
    """Helper to create a Payment transaction dict."""
    return {
        "TransactionType": "Payment",
        "Account": account,
        "Destination": destination,
        "Amount": {
            "currency": "FCMGold",
            "value": "100",
            "issuer": issuer,
        },
    }


class TestAllowedTxTypes(unittest.TestCase):
    """Test the allowed_tx_types rule."""

    def test_allowed_type_passes(self):
        wallet = _make_wallet(allowed_tx_types=["Payment", "OfferCreate"])
        tx = _make_payment_tx()
        violations = validate_transaction(tx, wallet)
        self.assertEqual(violations, [])

    def test_disallowed_type_fails(self):
        wallet = _make_wallet(allowed_tx_types=["Payment"])
        tx = {"TransactionType": "AccountDelete", "Account": wallet.address}
        violations = validate_transaction(tx, wallet)
        self.assertTrue(any(v.rule == "allowed_tx_types" for v in violations))

    def test_empty_allowed_list_allows_all(self):
        wallet = _make_wallet(allowed_tx_types=[])
        tx = {"TransactionType": "AccountDelete", "Account": wallet.address}
        violations = validate_transaction(tx, wallet)
        # Empty allowed list means no restriction on this rule
        self.assertFalse(any(v.rule == "allowed_tx_types" for v in violations))


class TestBlockedTxTypes(unittest.TestCase):
    """Test the blocked_tx_types rule."""

    def test_blocked_type_fails(self):
        wallet = _make_wallet(
            allowed_tx_types=["Payment", "AccountDelete"],
            blocked_tx_types=["AccountDelete", "SignerListSet"],
        )
        tx = {"TransactionType": "AccountDelete", "Account": wallet.address}
        violations = validate_transaction(tx, wallet)
        self.assertTrue(any(v.rule == "blocked_tx_types" for v in violations))

    def test_non_blocked_type_passes(self):
        wallet = _make_wallet(
            allowed_tx_types=["Payment"],
            blocked_tx_types=["AccountDelete"],
        )
        tx = _make_payment_tx()
        violations = validate_transaction(tx, wallet)
        self.assertFalse(any(v.rule == "blocked_tx_types" for v in violations))


class TestRequireIssuer(unittest.TestCase):
    """Test the require_issuer rule."""

    def test_correct_issuer_passes(self):
        wallet = _make_wallet(
            allowed_tx_types=["Payment"],
            require_issuer="rISSUER33333333333333333333333333",
        )
        tx = _make_payment_tx(issuer="rISSUER33333333333333333333333333")
        violations = validate_transaction(tx, wallet)
        self.assertEqual(violations, [])

    def test_wrong_issuer_fails(self):
        wallet = _make_wallet(
            allowed_tx_types=["Payment"],
            require_issuer="rISSUER33333333333333333333333333",
        )
        tx = _make_payment_tx(issuer="rATTACKER4444444444444444444444444")
        violations = validate_transaction(tx, wallet)
        self.assertTrue(any(v.rule == "require_issuer" for v in violations))

    def test_no_issuer_rule_allows_any(self):
        wallet = _make_wallet(
            allowed_tx_types=["Payment"],
            require_issuer=None,
        )
        tx = _make_payment_tx(issuer="rANYBODY55555555555555555555555555")
        violations = validate_transaction(tx, wallet)
        self.assertFalse(any(v.rule == "require_issuer" for v in violations))

    def test_offer_create_checks_both_amounts(self):
        wallet = _make_wallet(
            allowed_tx_types=["OfferCreate"],
            require_issuer="rISSUER33333333333333333333333333",
        )
        tx = {
            "TransactionType": "OfferCreate",
            "Account": wallet.address,
            "TakerGets": {
                "currency": "FCMGold",
                "value": "100",
                "issuer": "rISSUER33333333333333333333333333",
            },
            "TakerPays": {
                "currency": "FCMWheat",
                "value": "50",
                "issuer": "rATTACKER4444444444444444444444444",
            },
        }
        violations = validate_transaction(tx, wallet)
        self.assertTrue(any(v.rule == "require_issuer" for v in violations))

    def test_xrp_amount_string_skipped(self):
        """XRP amounts are strings, not dicts — should not trigger issuer check."""
        wallet = _make_wallet(
            allowed_tx_types=["NFTokenCreateOffer"],
            require_issuer="rISSUER33333333333333333333333333",
        )
        tx = {
            "TransactionType": "NFTokenCreateOffer",
            "Account": wallet.address,
            "Amount": "0",  # XRP drops as string — no issuer field
        }
        violations = validate_transaction(tx, wallet)
        self.assertFalse(any(v.rule == "require_issuer" for v in violations))


class TestRateLimiter(unittest.TestCase):
    """Test the rate limiter."""

    def test_under_limit_passes(self):
        limiter = RateLimiter()
        for _ in range(5):
            self.assertTrue(limiter.check("rTEST", 10))

    def test_over_limit_fails(self):
        limiter = RateLimiter()
        for _ in range(10):
            limiter.check("rTEST", 10)
        self.assertFalse(limiter.check("rTEST", 10))

    def test_different_wallets_independent(self):
        limiter = RateLimiter()
        for _ in range(10):
            limiter.check("rWALLET_A", 10)
        # Wallet B should still be under limit
        self.assertTrue(limiter.check("rWALLET_B", 10))


class TestMultipleViolations(unittest.TestCase):
    """Test that multiple violations are reported at once."""

    def test_multiple_violations(self):
        wallet = _make_wallet(
            allowed_tx_types=["Payment"],
            blocked_tx_types=["AccountDelete"],
            require_issuer="rISSUER33333333333333333333333333",
        )
        tx = {
            "TransactionType": "AccountDelete",
            "Account": wallet.address,
            "Amount": {
                "currency": "SCAM",
                "value": "999",
                "issuer": "rATTACKER4444444444444444444444444",
            },
        }
        violations = validate_transaction(tx, wallet)
        rules_violated = {v.rule for v in violations}
        self.assertIn("allowed_tx_types", rules_violated)
        self.assertIn("blocked_tx_types", rules_violated)
        self.assertIn("require_issuer", rules_violated)


if __name__ == "__main__":
    unittest.main()
