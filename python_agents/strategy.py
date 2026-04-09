import json
from typing import List, Tuple, Dict, Any

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity


class StrategyHelper:
    """Helper class for strategy operations"""
    
    @staticmethod
    def normalize_prices(prices: np.ndarray) -> np.ndarray:
        if prices.size == 0:
            return prices
        std = np.std(prices)
        mean = np.mean(prices)
        if std == 0:
            return prices - mean
        return (prices - mean) / std
    
    @staticmethod
    def build_movement_vector(prices: np.ndarray) -> np.ndarray:
        if prices.size == 0:
            return prices
        base = prices[0]
        vector = (prices - base) / max(base, 1e-8)
        return vector
    
    @staticmethod
    def compare_similarity(current_vector: np.ndarray, historical_vectors: List[np.ndarray]) -> List[float]:
        if current_vector.size == 0 or not historical_vectors:
            return []
        target_len = current_vector.size
        resized = []
        for v in historical_vectors:
            if v.size == 0:
                continue
            if v.size == target_len:
                resized.append(v)
            else:
                resized.append(np.interp(
                    np.linspace(0, 1, target_len),
                    np.linspace(0, 1, v.size),
                    v
                ))
        if not resized:
            return []
        matrix = np.vstack([current_vector] + resized)
        similarity_matrix = cosine_similarity(matrix)
        current_similarities = similarity_matrix[0, 1:]
        return current_similarities.tolist()
    
    @staticmethod
    def strategy(prices: np.ndarray, params: List[float]) -> Tuple[float, float, float]:
        """Üst/alt eşik stratejisi ile ortalama kar, risk ve skoru hesapla."""
        upper_threshold = params[0]
        lower_threshold = params[1]
        if upper_threshold <= lower_threshold:
            return -float('inf'), float('inf'), -float('inf')
        position = 0
        entry_price = 0.0
        profits: List[float] = []
        for price in prices:
            if price < lower_threshold and position == 0:
                position = 1
                entry_price = price
            elif price > upper_threshold and position == 0:
                position = -1
                entry_price = price
            elif position == 1 and price >= upper_threshold:
                profits.append(price - entry_price)
                position = 0
            elif position == -1 and price <= lower_threshold:
                profits.append(entry_price - price)
                position = 0
        if position == 1:
            profits.append(prices[-1] - entry_price)
        elif position == -1:
            profits.append(entry_price - prices[-1])
        if not profits:
            return -float('inf'), float('inf'), -float('inf')
        mean_profit = float(np.mean(profits))
        risk = float(np.std(profits))
        score = mean_profit / (risk + 1e-6)
        return mean_profit, risk, score


def normalize_prices(prices: np.ndarray) -> np.ndarray:
    return StrategyHelper.normalize_prices(prices)


def build_movement_vector(prices: np.ndarray) -> np.ndarray:
    return StrategyHelper.build_movement_vector(prices)


def compare_similarity(current_vector: np.ndarray, historical_vectors: List[np.ndarray]) -> List[float]:
    return StrategyHelper.compare_similarity(current_vector, historical_vectors)


def strategy(prices: np.ndarray, params: List[float]) -> Tuple[float, float, float]:
    return StrategyHelper.strategy(prices, params)


def evolutionary_algorithm(prices: np.ndarray, population_size: int = 50, generations: int = 50) -> Dict[str, Any]:
    population = [[np.random.uniform(-2.0, 2.0), np.random.uniform(-2.0, 2.0)] for _ in range(population_size)]
    best_result = {'params': [0.5, -0.5], 'mean': -float('inf'), 'risk': float('inf'), 'score': -float('inf')}

    for generation in range(generations):
        fitness_results = []
        for params in population:
            if params[0] <= params[1]:
                fitness_results.append((-float('inf'), float('inf'), -float('inf'), params))
                continue

            mean_p, std_p, score_p = strategy(prices, params)
            fitness_results.append((score_p, mean_p, std_p, params))

            if score_p > best_result['score']:
                best_result = {
                    'params': params,
                    'mean': mean_p,
                    'risk': std_p,
                    'score': score_p
                }

        sorted_population = [params for score, mean, std, params in sorted(fitness_results, key=lambda x: x[0], reverse=True)]
        selected = sorted_population[: max(1, int(population_size * 0.2))]

        top_half = sorted_population[: max(2, int(population_size * 0.5))]
        while len(selected) < population_size:
            idxs = np.random.choice(len(top_half), 2, replace=False)
            parent1, parent2 = top_half[idxs[0]], top_half[idxs[1]]
            child = [(parent1[i] + parent2[i]) / 2 for i in range(2)]
            if np.random.random() < 0.2:
                child[np.random.randint(0, 2)] += np.random.uniform(-0.1, 0.1)
            selected.append(child)

        population = selected

    return best_result
