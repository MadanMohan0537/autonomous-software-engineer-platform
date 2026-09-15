"""List prices per million tokens, used to keep the dollar budget honest.

Prices are the published API list prices at the time of writing. Unknown models fall
back to the most expensive known tier so an unpriced model can never under-report cost.
"""

from __future__ import annotations

from dataclasses import dataclass

from ase.llm.client import Usage


@dataclass(frozen=True)
class Price:
    input_per_million: float
    output_per_million: float

    @property
    def cache_read_per_million(self) -> float:
        return self.input_per_million * 0.1

    @property
    def cache_write_per_million(self) -> float:
        return self.input_per_million * 1.25


PRICES: dict[str, Price] = {
    "claude-fable-5-1": Price(10.0, 50.0),
    "claude-opus-5": Price(5.0, 25.0),
    "claude-sonnet-5": Price(2.0, 10.0),
    "claude-haiku-4-5": Price(1.0, 5.0),
}
FALLBACK = Price(10.0, 50.0)


def price_for(model: str) -> Price:
    for prefix, price in PRICES.items():
        if model.startswith(prefix):
            return price
    return FALLBACK


def estimate_cost(model: str, usage: Usage) -> float:
    price = price_for(model)
    total = (
        usage.input_tokens * price.input_per_million
        + usage.output_tokens * price.output_per_million
        + usage.cache_read_tokens * price.cache_read_per_million
        + usage.cache_write_tokens * price.cache_write_per_million
    ) / 1_000_000
    return round(total, 6)
