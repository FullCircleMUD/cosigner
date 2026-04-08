"""
Unit tests for configuration loading.

Tests app/config.py:load_config() — env vars, wallets.json parsing,
error handling for missing/invalid config.
"""

import json
import os
import pytest

from app.config import load_config, AppConfig, WalletConfig


def _write_wallets_json(path, wallets_dict):
    """Helper to write a wallets.json file."""
    path.write_text(json.dumps(wallets_dict))


def _base_env(monkeypatch, wallets_path):
    """Set the minimum required env vars for a valid config."""
    monkeypatch.setenv("XRPL_NETWORK_URL", "wss://s.altnet.rippletest.net:51233")
    monkeypatch.setenv("API_KEY", "test-api-key")
    monkeypatch.setenv("WALLETS_CONFIG", str(wallets_path))
    monkeypatch.setenv("WALLET_SEED_VAULT", "sEdFAKESEED_VAULT")


SINGLE_WALLET_JSON = {
    "wallets": {
        "rVAULT111111111111111111111111111": {
            "name": "vault",
            "seed_env": "WALLET_SEED_VAULT",
            "rules": {
                "allowed_tx_types": ["Payment", "OfferCreate"],
                "blocked_tx_types": ["AccountDelete"],
                "require_issuer": "rISSUER33333333333333333333333333",
                "max_per_minute": 20,
            },
        }
    }
}


class TestLoadConfigSuccess:
    def test_load_config_success(self, tmp_path, monkeypatch):
        wallets_path = tmp_path / "wallets.json"
        _write_wallets_json(wallets_path, SINGLE_WALLET_JSON)
        _base_env(monkeypatch, wallets_path)

        config = load_config()

        assert isinstance(config, AppConfig)
        assert config.xrpl_network_url == "wss://s.altnet.rippletest.net:51233"
        assert config.api_key == "test-api-key"
        assert config.log_level == "INFO"
        assert "rVAULT111111111111111111111111111" in config.wallets

        wallet = config.wallets["rVAULT111111111111111111111111111"]
        assert wallet.name == "vault"
        assert wallet.seed == "sEdFAKESEED_VAULT"
        assert "Payment" in wallet.rules.allowed_tx_types
        assert "AccountDelete" in wallet.rules.blocked_tx_types
        assert wallet.rules.require_issuer == "rISSUER33333333333333333333333333"
        assert wallet.rules.max_per_minute == 20

    def test_default_log_level(self, tmp_path, monkeypatch):
        wallets_path = tmp_path / "wallets.json"
        _write_wallets_json(wallets_path, SINGLE_WALLET_JSON)
        _base_env(monkeypatch, wallets_path)
        monkeypatch.delenv("LOG_LEVEL", raising=False)

        config = load_config()
        assert config.log_level == "INFO"

    def test_default_max_per_minute(self, tmp_path, monkeypatch):
        wallets_json = {
            "wallets": {
                "rVAULT111111111111111111111111111": {
                    "name": "vault",
                    "seed_env": "WALLET_SEED_VAULT",
                    "rules": {
                        "allowed_tx_types": ["Payment"],
                        # no max_per_minute — should default to 30
                    },
                }
            }
        }
        wallets_path = tmp_path / "wallets.json"
        _write_wallets_json(wallets_path, wallets_json)
        _base_env(monkeypatch, wallets_path)

        config = load_config()
        wallet = config.wallets["rVAULT111111111111111111111111111"]
        assert wallet.rules.max_per_minute == 30

    def test_multiple_wallets(self, tmp_path, monkeypatch):
        wallets_json = {
            "wallets": {
                "rVAULT111111111111111111111111111": {
                    "name": "vault",
                    "seed_env": "WALLET_SEED_VAULT",
                    "rules": {"allowed_tx_types": ["Payment"]},
                },
                "rISSUER22222222222222222222222222": {
                    "name": "issuer",
                    "seed_env": "WALLET_SEED_ISSUER",
                    "rules": {"allowed_tx_types": ["Payment", "NFTokenMint"]},
                },
            }
        }
        wallets_path = tmp_path / "wallets.json"
        _write_wallets_json(wallets_path, wallets_json)
        _base_env(monkeypatch, wallets_path)
        monkeypatch.setenv("WALLET_SEED_ISSUER", "sEdFAKESEED_ISSUER")

        config = load_config()
        assert len(config.wallets) == 2
        assert config.wallets["rVAULT111111111111111111111111111"].name == "vault"
        assert config.wallets["rISSUER22222222222222222222222222"].name == "issuer"


    def test_per_wallet_network_url(self, tmp_path, monkeypatch):
        wallets_json = {
            "wallets": {
                "rVAULT111111111111111111111111111": {
                    "name": "vault",
                    "seed_env": "WALLET_SEED_VAULT",
                    "network_url": "wss://custom.xrpl.net:51233",
                    "rules": {"allowed_tx_types": ["Payment"]},
                }
            }
        }
        wallets_path = tmp_path / "wallets.json"
        _write_wallets_json(wallets_path, wallets_json)
        _base_env(monkeypatch, wallets_path)

        config = load_config()
        wallet = config.wallets["rVAULT111111111111111111111111111"]
        assert wallet.network_url == "wss://custom.xrpl.net:51233"

    def test_no_wallet_network_url_defaults_none(self, tmp_path, monkeypatch):
        wallets_path = tmp_path / "wallets.json"
        _write_wallets_json(wallets_path, SINGLE_WALLET_JSON)
        _base_env(monkeypatch, wallets_path)

        config = load_config()
        wallet = config.wallets["rVAULT111111111111111111111111111"]
        assert wallet.network_url is None


class TestLoadConfigErrors:
    def test_missing_network_url(self, tmp_path, monkeypatch):
        wallets_path = tmp_path / "wallets.json"
        _write_wallets_json(wallets_path, SINGLE_WALLET_JSON)
        _base_env(monkeypatch, wallets_path)
        monkeypatch.delenv("XRPL_NETWORK_URL")

        with pytest.raises(ValueError, match="XRPL_NETWORK_URL"):
            load_config()

    def test_missing_api_key(self, tmp_path, monkeypatch):
        wallets_path = tmp_path / "wallets.json"
        _write_wallets_json(wallets_path, SINGLE_WALLET_JSON)
        _base_env(monkeypatch, wallets_path)
        monkeypatch.delenv("API_KEY")

        with pytest.raises(ValueError, match="API_KEY"):
            load_config()

    def test_missing_wallets_file(self, tmp_path, monkeypatch):
        wallets_path = tmp_path / "nonexistent.json"
        _base_env(monkeypatch, wallets_path)

        with pytest.raises(ValueError, match="not found"):
            load_config()

    def test_missing_wallet_seed_env(self, tmp_path, monkeypatch):
        wallets_path = tmp_path / "wallets.json"
        _write_wallets_json(wallets_path, SINGLE_WALLET_JSON)
        _base_env(monkeypatch, wallets_path)
        monkeypatch.delenv("WALLET_SEED_VAULT")

        with pytest.raises(ValueError, match="WALLET_SEED_VAULT"):
            load_config()

    def test_empty_wallets_dict(self, tmp_path, monkeypatch):
        wallets_path = tmp_path / "wallets.json"
        _write_wallets_json(wallets_path, {"wallets": {}})
        _base_env(monkeypatch, wallets_path)

        with pytest.raises(ValueError, match="No wallets configured"):
            load_config()
