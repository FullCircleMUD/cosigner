"""
Unit tests for the co-signing logic.

Tests app/signer.py:cosign_and_submit() with mocked xrpl-py internals.
No XRPL connection required.
"""

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.config import AppConfig, WalletConfig, WalletRules
from app.rules import RuleViolation
from app.signer import CosignError, cosign_and_submit


VAULT_ADDRESS = "rVAULT111111111111111111111111111"
ISSUER_ADDRESS = "rISSUER33333333333333333333333333"


def _make_config():
    """Create a test AppConfig with one vault wallet."""
    return AppConfig(
        api_key="test-key",
        log_level="INFO",
        wallets={
            VAULT_ADDRESS: WalletConfig(
                address=VAULT_ADDRESS,
                name="vault",
                seed="sEdFAKESEED",
                network_url="wss://s.altnet.rippletest.net:51233",
                rules=WalletRules(
                    allowed_tx_types=frozenset(["Payment", "OfferCreate"]),
                    blocked_tx_types=frozenset(["AccountDelete"]),
                    require_issuer=ISSUER_ADDRESS,
                    max_per_minute=30,
                ),
            )
        },
    )


def _make_tx_dict(account=VAULT_ADDRESS, tx_type="Payment", signers=None):
    """Create a mock transaction dict."""
    d = {
        "TransactionType": tx_type,
        "Account": account,
        "Destination": "rPLAYER22222222222222222222222222",
        "Amount": {
            "currency": "FCMGold",
            "value": "100",
            "issuer": ISSUER_ADDRESS,
        },
    }
    if signers is not None:
        d["Signers"] = signers
    else:
        # Default: one existing signer (key A)
        d["Signers"] = [{"Signer": {"Account": "rKEYA000000000000000000000000000"}}]
    return d


def _mock_submit_result(engine_result="tesSUCCESS", tx_hash="AABB1234"):
    """Create a mock submit_and_wait result."""
    return SimpleNamespace(
        result={
            "hash": tx_hash,
            "meta": {
                "TransactionResult": engine_result,
                "AffectedNodes": [],
            },
        }
    )


@pytest.fixture
def config():
    return _make_config()


class TestCosignSuccess:
    @pytest.mark.asyncio
    async def test_cosign_success(self, config):
        tx_dict = _make_tx_dict()
        mock_tx = MagicMock()
        mock_tx.to_xrpl.return_value = tx_dict

        mock_signed_b = MagicMock()
        mock_combined = MagicMock()

        mock_client = AsyncMock()
        mock_ws_ctx = AsyncMock()
        mock_ws_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ws_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.signer.Transaction.from_blob", return_value=mock_tx),
            patch("app.signer.Wallet.from_seed", return_value=MagicMock()),
            patch("app.signer.sign", return_value=mock_signed_b),
            patch("app.signer.multisign", return_value=mock_combined),
            patch("app.signer.AsyncWebsocketClient", return_value=mock_ws_ctx),
            patch("app.signer.submit_and_wait", new_callable=AsyncMock,
                  return_value=_mock_submit_result()),
            patch("app.signer.validate_transaction", return_value=[]),
        ):
            result = await cosign_and_submit("deadbeef", config)

        assert result["tx_hash"] == "AABB1234"
        assert result["engine_result"] == "tesSUCCESS"
        assert result["wallet_name"] == "vault"
        assert "meta" in result


    @pytest.mark.asyncio
    async def test_uses_wallet_network_url(self, config):
        """When wallet has network_url, signer uses it instead of global."""
        # Add a wallet with per-wallet network_url
        wallet_with_url = WalletConfig(
            address=VAULT_ADDRESS,
            name="vault",
            seed="sEdFAKESEED",
            rules=WalletRules(
                allowed_tx_types=frozenset(["Payment"]),
                blocked_tx_types=frozenset(),
                max_per_minute=30,
            ),
            network_url="wss://custom.xrpl.net:51233",
        )
        custom_config = AppConfig(
            api_key="test-key",
            log_level="INFO",
            wallets={VAULT_ADDRESS: wallet_with_url},
        )

        tx_dict = _make_tx_dict()
        mock_tx = MagicMock()
        mock_tx.to_xrpl.return_value = tx_dict

        mock_ws_ctx = AsyncMock()
        mock_ws_ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
        mock_ws_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.signer.Transaction.from_blob", return_value=mock_tx),
            patch("app.signer.validate_transaction", return_value=[]),
            patch("app.signer.Wallet.from_seed", return_value=MagicMock()),
            patch("app.signer.sign", return_value=MagicMock()),
            patch("app.signer.multisign", return_value=MagicMock()),
            patch("app.signer.AsyncWebsocketClient", return_value=mock_ws_ctx) as mock_ws_class,
            patch("app.signer.submit_and_wait", new_callable=AsyncMock,
                  return_value=_mock_submit_result()),
        ):
            await cosign_and_submit("deadbeef", custom_config)
            # Verify the per-wallet URL was used, not the global one
            mock_ws_class.assert_called_once_with("wss://custom.xrpl.net:51233")


class TestDevMode:
    @pytest.mark.asyncio
    async def test_dev_mode_skips_submission(self, config):
        """Dev mode runs full pipeline but skips XRPL submission."""
        tx_dict = _make_tx_dict()
        mock_tx = MagicMock()
        mock_tx.to_xrpl.return_value = tx_dict

        mock_combined = MagicMock()
        mock_combined.to_xrpl.return_value = tx_dict

        with (
            patch("app.signer.Transaction.from_blob", return_value=mock_tx),
            patch("app.signer.Wallet.from_seed", return_value=MagicMock()),
            patch("app.signer.sign", return_value=MagicMock()),
            patch("app.signer.multisign", return_value=mock_combined),
            patch("app.signer.validate_transaction", return_value=[]),
            patch("xrpl.core.binarycodec.encode", return_value="AABBCCDD") as mock_encode,
            patch("app.signer.AsyncWebsocketClient") as mock_ws,
            patch("app.signer.submit_and_wait") as mock_submit,
        ):
            result = await cosign_and_submit("deadbeef", config, dev_mode=True)

        # Submission should NOT be called
        mock_ws.assert_not_called()
        mock_submit.assert_not_called()

        # Should return mock success
        assert result["engine_result"] == "tesSUCCESS"
        assert result["wallet_name"] == "vault"
        assert result["meta"] == {"TransactionResult": "tesSUCCESS"}
        assert len(result["tx_hash"]) == 64  # SHA-256 hex

    @pytest.mark.asyncio
    async def test_dev_mode_still_validates_rules(self, config):
        """Dev mode should still catch rule violations."""
        tx_dict = _make_tx_dict()
        mock_tx = MagicMock()
        mock_tx.to_xrpl.return_value = tx_dict

        violations = [RuleViolation(rule="blocked_tx_types", detail="blocked")]

        with (
            patch("app.signer.Transaction.from_blob", return_value=mock_tx),
            patch("app.signer.validate_transaction", return_value=violations),
        ):
            with pytest.raises(CosignError) as exc_info:
                await cosign_and_submit("deadbeef", config, dev_mode=True)
            assert exc_info.value.error_type == "rule_violation"

    @pytest.mark.asyncio
    async def test_dev_mode_still_checks_missing_signature(self, config):
        """Dev mode should still catch missing signatures."""
        tx_dict = _make_tx_dict(signers=[])
        mock_tx = MagicMock()
        mock_tx.to_xrpl.return_value = tx_dict

        with (
            patch("app.signer.Transaction.from_blob", return_value=mock_tx),
            patch("app.signer.validate_transaction", return_value=[]),
        ):
            with pytest.raises(CosignError) as exc_info:
                await cosign_and_submit("deadbeef", config, dev_mode=True)
            assert exc_info.value.error_type == "missing_signature"

    @pytest.mark.asyncio
    async def test_dev_mode_deterministic_hash(self, config):
        """Same input should produce same fake tx_hash."""
        tx_dict = _make_tx_dict()
        mock_tx = MagicMock()
        mock_tx.to_xrpl.return_value = tx_dict

        mock_combined = MagicMock()
        mock_combined.to_xrpl.return_value = tx_dict

        with (
            patch("app.signer.Transaction.from_blob", return_value=mock_tx),
            patch("app.signer.Wallet.from_seed", return_value=MagicMock()),
            patch("app.signer.sign", return_value=MagicMock()),
            patch("app.signer.multisign", return_value=mock_combined),
            patch("app.signer.validate_transaction", return_value=[]),
            patch("xrpl.core.binarycodec.encode", return_value="AABBCCDD"),
        ):
            result1 = await cosign_and_submit("deadbeef", config, dev_mode=True)
            result2 = await cosign_and_submit("deadbeef", config, dev_mode=True)

        assert result1["tx_hash"] == result2["tx_hash"]


class TestCosignErrors:
    @pytest.mark.asyncio
    async def test_invalid_blob(self, config):
        with patch("app.signer.Transaction.from_blob", side_effect=Exception("bad hex")):
            with pytest.raises(CosignError) as exc_info:
                await cosign_and_submit("not-valid-hex", config)
            assert exc_info.value.error_type == "invalid_transaction"

    @pytest.mark.asyncio
    async def test_unknown_wallet(self, config):
        tx_dict = _make_tx_dict(account="rUNKNOWN99999999999999999999999")
        mock_tx = MagicMock()
        mock_tx.to_xrpl.return_value = tx_dict

        with patch("app.signer.Transaction.from_blob", return_value=mock_tx):
            with pytest.raises(CosignError) as exc_info:
                await cosign_and_submit("deadbeef", config)
            assert exc_info.value.error_type == "unknown_wallet"

    @pytest.mark.asyncio
    async def test_rule_violation(self, config):
        tx_dict = _make_tx_dict()
        mock_tx = MagicMock()
        mock_tx.to_xrpl.return_value = tx_dict

        violations = [RuleViolation(rule="blocked_tx_types", detail="blocked")]

        with (
            patch("app.signer.Transaction.from_blob", return_value=mock_tx),
            patch("app.signer.validate_transaction", return_value=violations),
        ):
            with pytest.raises(CosignError) as exc_info:
                await cosign_and_submit("deadbeef", config)
            assert exc_info.value.error_type == "rule_violation"
            assert "blocked" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_missing_signatures(self, config):
        tx_dict = _make_tx_dict(signers=[])  # empty Signers
        mock_tx = MagicMock()
        mock_tx.to_xrpl.return_value = tx_dict

        with (
            patch("app.signer.Transaction.from_blob", return_value=mock_tx),
            patch("app.signer.validate_transaction", return_value=[]),
        ):
            with pytest.raises(CosignError) as exc_info:
                await cosign_and_submit("deadbeef", config)
            assert exc_info.value.error_type == "missing_signature"

    @pytest.mark.asyncio
    async def test_signing_fails(self, config):
        tx_dict = _make_tx_dict()
        mock_tx = MagicMock()
        mock_tx.to_xrpl.return_value = tx_dict

        with (
            patch("app.signer.Transaction.from_blob", return_value=mock_tx),
            patch("app.signer.validate_transaction", return_value=[]),
            patch("app.signer.Wallet.from_seed", return_value=MagicMock()),
            patch("app.signer.sign", side_effect=Exception("signing error")),
        ):
            with pytest.raises(CosignError) as exc_info:
                await cosign_and_submit("deadbeef", config)
            assert exc_info.value.error_type == "signing_failed"

    @pytest.mark.asyncio
    async def test_combine_fails(self, config):
        tx_dict = _make_tx_dict()
        mock_tx = MagicMock()
        mock_tx.to_xrpl.return_value = tx_dict

        with (
            patch("app.signer.Transaction.from_blob", return_value=mock_tx),
            patch("app.signer.validate_transaction", return_value=[]),
            patch("app.signer.Wallet.from_seed", return_value=MagicMock()),
            patch("app.signer.sign", return_value=MagicMock()),
            patch("app.signer.multisign", side_effect=Exception("combine error")),
        ):
            with pytest.raises(CosignError) as exc_info:
                await cosign_and_submit("deadbeef", config)
            assert exc_info.value.error_type == "combine_failed"

    @pytest.mark.asyncio
    async def test_submission_fails(self, config):
        tx_dict = _make_tx_dict()
        mock_tx = MagicMock()
        mock_tx.to_xrpl.return_value = tx_dict

        mock_ws_ctx = AsyncMock()
        mock_ws_ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
        mock_ws_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.signer.Transaction.from_blob", return_value=mock_tx),
            patch("app.signer.validate_transaction", return_value=[]),
            patch("app.signer.Wallet.from_seed", return_value=MagicMock()),
            patch("app.signer.sign", return_value=MagicMock()),
            patch("app.signer.multisign", return_value=MagicMock()),
            patch("app.signer.AsyncWebsocketClient", return_value=mock_ws_ctx),
            patch("app.signer.submit_and_wait", new_callable=AsyncMock,
                  side_effect=Exception("network timeout")),
        ):
            with pytest.raises(CosignError) as exc_info:
                await cosign_and_submit("deadbeef", config)
            assert exc_info.value.error_type == "submission_failed"

    @pytest.mark.asyncio
    async def test_xrpl_non_success(self, config):
        tx_dict = _make_tx_dict()
        mock_tx = MagicMock()
        mock_tx.to_xrpl.return_value = tx_dict

        mock_ws_ctx = AsyncMock()
        mock_ws_ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
        mock_ws_ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.signer.Transaction.from_blob", return_value=mock_tx),
            patch("app.signer.validate_transaction", return_value=[]),
            patch("app.signer.Wallet.from_seed", return_value=MagicMock()),
            patch("app.signer.sign", return_value=MagicMock()),
            patch("app.signer.multisign", return_value=MagicMock()),
            patch("app.signer.AsyncWebsocketClient", return_value=mock_ws_ctx),
            patch("app.signer.submit_and_wait", new_callable=AsyncMock,
                  return_value=_mock_submit_result(engine_result="tecPATH_DRY")),
        ):
            with pytest.raises(CosignError) as exc_info:
                await cosign_and_submit("deadbeef", config)
            assert exc_info.value.error_type == "xrpl_error"
            assert "tecPATH_DRY" in exc_info.value.detail
