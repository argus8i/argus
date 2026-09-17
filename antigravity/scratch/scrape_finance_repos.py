import requests
import json

queries = [
    ("Chronos & Time Series Foundation Models", "chronos forecasting time series"),
    ("Quantitative Finance & Algorithmic Trading", "topic:quantitative-finance stars:>1000"),
    ("Financial AI & Sentiment / Filings LLMs", "FinGPT OR financial-analysis-llm OR financial-agent"),
    ("Limit Order Book & Market Microstructure Deep Learning", "limit-order-book OR DeepLOB OR microstructure-trading")
]

results = {}
headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/vnd.github.v3+json"}

for category, q in queries:
    url = f"https://api.github.com/search/repositories?q={q}&sort=stars&order=desc&per_page=5"
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            results[category] = [
                {
                    "name": item.get("full_name"),
                    "stars": item.get("stargazers_count"),
                    "url": item.get("html_url"),
                    "description": item.get("description"),
                    "language": item.get("language")
                }
                for item in items
            ]
    except Exception as e:
        results[category] = str(e)

print(json.dumps(results, indent=2))
