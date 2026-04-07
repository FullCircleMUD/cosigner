"""
Configuration loader for the XRPL co-signing service.

Loads environment variables and wallet configuration from a JSON file.
Wallet seeds are referenced by env var name in the JSON — actual secrets
come from the environment, never from the config file.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WalletRules:
    """Business rules for a single wallet."""

    allowed_tx_types: frozenset[str] = field(default_factory=frozenset)
    blocked_tx_types: frozenset[str] = field(default_factory=frozenset)
    require_issuer: str | None = None
    max_per_minute: int = 30


@dataclass(frozen=True)
class WalletConfig:
    """Configuration for a single managed wallet."""

    address: str
    name: str
    seed: str  # resolved from env var
    rules: WalletRules


@dataclass(frozen=True)
class AppConfig:
    """Application-wide configuration."""

    xrpl_network_url: str
    api_key: str
    log_level: str
    wallets: dict[str, WalletConfig]  # keyed by XRPL r-address


def load_config() -> AppConfig:
    """
    Load configuration from environment variables and wallets.json.

    Raises:
        ValueError: If required env vars are missing or wallet config is invalid.
    """
    xrpl_network_url = os.environ.get("XRPL_NETWORK_URL", "")
    if not xrpl_network_url:
        raise ValueError("XRPL_NETWORK_URL environment variable is required")

    api_key = os.environ.get("API_KEY", "")
    if not api_key:
        raise ValueError("API_KEY environment variable is required")

    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()

    wallets_path = Path(os.environ.get("WALLETS_CONFIG", "./wallets.json"))
    if not wallets_path.exists():
        raise ValueError(f"Wallet config not found: {wallets_path}")

    with open(wallets_path) as f:
        raw = json.load(f)

    wallets = {}
    for address, wdata in raw.get("wallets", {}).items():
        seed_env = wdata.get("seed_env", "")
        seed = os.environ.get(seed_env, "")
        if not seed:
            raise ValueError(
                f"Wallet '{wdata.get('name', address)}': "
                f"env var {seed_env} is not set"
            )

        rules_data = wdata.get("rules", {})
        rules = WalletRules(
            allowed_tx_types=frozenset(rules_data.get("allowed_tx_types", [])),
            blocked_tx_types=frozenset(rules_data.get("blocked_tx_types", [])),
            require_issuer=rules_data.get("require_issuer"),
            max_per_minute=rules_data.get("max_per_minute", 30),
        )

        wallets[address] = WalletConfig(
            address=address,
            name=wdata.get("name", address),
            seed=seed,
            rules=rules,
        )

    if not wallets:
        raise ValueError("No wallets configured in wallet config file")

    logger.info(
        "Loaded %d wallet(s): %s",
        len(wallets),
        ", ".join(f"{w.name} ({w.address[:8]}...)" for w in wallets.values()),
    )

    return AppConfig(
        xrpl_network_url=xrpl_network_url,
        api_key=api_key,
        log_level=log_level,
        wallets=wallets,
    )
