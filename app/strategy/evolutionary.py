"""Evolutionary trading strategy adapted for real market data."""

import random

import numpy as np


def normalize(prices: list[float]) -> np.ndarray:
    """Normalize price array."""
    arr = np.array(prices, dtype=float)
    std = np.std(arr)
    if std == 0:
        return arr - np.mean(arr)
    return (arr - np.mean(arr)) / std


def evaluate_strategy(
    prices: np.ndarray, buy_threshold: float, sell_threshold: float
) -> tuple[float, float]:
    """Run a simple threshold strategy and return mean profit and std."""
    position = 0
    entry_price = 0.0
    profits: list[float] = []

    for price in prices:
        if price < buy_threshold and position == 0:
            position = 1
            entry_price = price
        elif price > sell_threshold and position == 1:
            position = 0
            profits.append(price - entry_price)

    if not profits:
        return 0.0, 0.0
    return float(np.mean(profits)), float(np.std(profits))


def evolutionary_algorithm(
    prices: list[float],
    population_size: int = 50,
    generations: int = 50,
) -> dict:
    """Optimize buy/sell thresholds using an evolutionary algorithm."""
    norm_prices = normalize(prices)

    population = [
        [random.uniform(-2.0, 0.0), random.uniform(0.0, 2.0)]
        for _ in range(population_size)
    ]

    for _gen in range(generations):
        fitness = []
        for params in population:
            mean_p, _std_p = evaluate_strategy(
                norm_prices, params[0], params[1]
            )
            fitness.append(mean_p)

        ranked = sorted(
            zip(fitness, population), key=lambda x: -x[0]
        )
        sorted_pop = [p for _, p in ranked]

        elite_count = max(2, int(population_size * 0.2))
        new_population = sorted_pop[:elite_count]

        while len(new_population) < population_size:
            parent1, parent2 = random.sample(
                sorted_pop[: max(2, int(population_size * 0.5))], 2
            )
            child = [(parent1[i] + parent2[i]) / 2 for i in range(2)]
            if random.random() < 0.1:
                idx = random.randint(0, 1)
                child[idx] += random.gauss(0, 0.2)
            new_population.append(child)

        population = new_population

    best_params = sorted_pop[0]
    mean_profit, std_profit = evaluate_strategy(
        norm_prices, best_params[0], best_params[1]
    )

    return {
        "buy_threshold": round(best_params[0], 6),
        "sell_threshold": round(best_params[1], 6),
        "mean_profit": round(mean_profit, 6),
        "std_profit": round(std_profit, 6),
        "generations": generations,
        "population_size": population_size,
    }
