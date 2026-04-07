import abc
from datetime import datetime
from typing import Any, Dict, Optional

class AgentBase(abc.ABC):
    """Base class for all agents in the market intelligence system."""

    def __init__(self, name: str):
        self.name = name
        self.running = False
        self.last_activity: Optional[datetime] = None

    @abc.abstractmethod
    async def initialize(self) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    async def start(self) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    async def stop(self) -> None:
        raise NotImplementedError

    async def health_check(self) -> Dict[str, Any]:
        return {
            "agent": self.name,
            "running": self.running,
            "last_activity": self.last_activity.isoformat() if self.last_activity else None,
        }


class WatchlistManager:
    """Manages the set of coins and markets the system should follow."""

    def __init__(self, initial_symbols: Optional[list[str]] = None):
        self.symbols = list(initial_symbols or [])

    def add_symbol(self, symbol: str) -> None:
        symbol = symbol.upper()
        if symbol not in self.symbols:
            self.symbols.append(symbol)

    def remove_symbol(self, symbol: str) -> None:
        symbol = symbol.upper()
        if symbol in self.symbols:
            self.symbols.remove(symbol)

    def get_symbols(self) -> list[str]:
        return list(self.symbols)


class DataSink:
    """Abstracts the persistence layer and supports future replacement with BigQuery."""

    def __init__(self, db: Any):
        self.db = db

    async def persist(self, record: Dict[str, Any]) -> Any:
        raise NotImplementedError
