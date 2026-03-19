"""
leaderboard.py
Data model for a single Polymarket leaderboard entry.
Pure data — no business logic, no API calls.
"""

from dataclasses import dataclass, field


@dataclass
class LeaderboardEntry:
    """
    Represents a single trader entry from the Polymarket leaderboard API.

    Attributes:
        rank: Position on the leaderboard (1-indexed).
        proxy_wallet: On-chain wallet address of the trader.
        user_name: Polymarket display name.
        x_username: Linked X (Twitter) handle, empty string if not set.
        vol: Total trading volume in USD.
        pnl: Profit and loss in USD for the selected time period.
        profile_image: URL to profile image, empty string if not set.
        verified_badge: Whether the trader has a verified badge.
    """

    rank: int
    proxy_wallet: str
    user_name: str
    x_username: str
    vol: float
    pnl: float
    profile_image: str
    verified_badge: bool
    lists: list = field(default_factory=list)

    @classmethod
    def from_api_response(cls, data: dict) -> "LeaderboardEntry":
        """
        Construct a LeaderboardEntry from a raw API response dictionary.

        Args:
            data: Single dict item from the Polymarket leaderboard JSON response.

        Returns:
            A populated LeaderboardEntry instance.

        Raises:
            KeyError: If a required field is missing from the response.
            ValueError: If rank cannot be converted to int.
        """
        return cls(
            rank=int(data["rank"]),
            proxy_wallet=data["proxyWallet"],
            user_name=data["userName"],
            x_username=data.get("xUsername", ""),
            vol=float(data.get("vol", 0.0)),
            pnl=float(data.get("pnl", 0.0)),
            profile_image=data.get("profileImage", ""),
            verified_badge=bool(data.get("verifiedBadge", False)),
        )


@dataclass
class BuilderLeaderboardEntry:
    """
    Represents a single builder entry from the Polymarket builder leaderboard API.

    Attributes:
        rank: Position on the leaderboard (1-indexed).
        builder: Builder identifier / address.
        volume: Total volume attributed to this builder in USD.
        active_users: Number of active users on this builder's platform.
        verified: Whether the builder is verified.
        builder_logo: URL to the builder's logo.
    """

    rank: int
    builder: str
    volume: float
    active_users: int
    verified: bool
    builder_logo: str

    @classmethod
    def from_api_response(cls, data: dict) -> "BuilderLeaderboardEntry":
        """
        Construct a BuilderLeaderboardEntry from a raw API response dictionary.

        Args:
            data: Single dict item from the Polymarket builder leaderboard JSON response.

        Returns:
            A populated BuilderLeaderboardEntry instance.
        """
        return cls(
            rank=int(data["rank"]),
            builder=data["builder"],
            volume=float(data.get("volume", 0.0)),
            active_users=int(data.get("activeUsers", 0)),
            verified=bool(data.get("verified", False)),
            builder_logo=data.get("builderLogo", ""),
        )
