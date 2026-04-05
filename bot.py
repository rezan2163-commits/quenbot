import pandas as pd
import numpy as np

# Örnek olarak bir CSV dosyası yükleyelim
data = pd.read_csv('your_data.csv')

# Fiyatları alın ve normalizasyon yapın
prices = data['price'].values
normalized_prices = (prices - np.mean(prices)) / np.std(prices)
def strategy(prices, params):
    buy_threshold = params[0]
    sell_threshold = params[1]
    position = 0
    profits = []

    for price in prices:
        if price > buy_threshold and position == 0:
            position = -1
        elif price < sell_threshold and position == -1:
            position = 0
            profits.append(price - buy_threshold)
        elif price < buy_threshold and position == 1:
            position = 0
            profits.append(sell_threshold - price)

    return np.mean(profits), np.std(profits)

def evolutionary_algorithm(prices, population_size=50, generations=50):
    population = [[random.uniform(0.8, 1.2) for _ in range(2)] for _ in range(population_size)]

    for generation in range(generations):
        fitness = [strategy(normalized_prices * param[0] + param[1], params) for params in population]
        sorted_population = [params for _, params in sorted(zip(fitness, population), key=lambda x: -x[0][0])]

        new_population = sorted_population[:int(population_size * 0.2)]

        while len(new_population) < population_size:
            parent1, parent2 = random.sample(sorted_population[:int(population_size * 0.5)], 2)
            child = [(parent1[i] + parent2[i]) / 2 for i in range(len(parent1))]
            if random.random() < 0.1:  # Mutasyon
                child[random.randint(0, len(child) - 1)] = random.uniform(0.8, 1.2)
            new_population.append(child)

        population = new_population

    best_params = sorted_population[0]
    return best_params

best_params = evolutionary_algorithm(normalized_prices)
print(f"En iyi parametreler: {best_params}")
def execute_strategy(prices, params):
    buy_threshold = params[0]
    sell_threshold = params[1]
    position = 0
    profits = []

    for price in prices:
        if price > buy_threshold and position == 0:
            position = -1
        elif price < sell_threshold and position == -1:
            position = 0
            profits.append(price - buy_threshold)
        elif price < buy_threshold and position == 1:
            position = 0
            profits.append(sell_threshold - price)

    return np.mean(profits), np.std(profits)

mean_profit, std_profit = execute_strategy(normalized_prices * best_params[0] + best_params[1], best_params)
print(f"Ortalama kâr: {mean_profit}, Standart Sapma: {std_profit}")
data = pd.read_csv('your_data.csv')
