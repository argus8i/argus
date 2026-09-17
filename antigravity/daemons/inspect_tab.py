import requests, asyncio, websockets, json

async def inspect_depth():
    r = requests.get('http://localhost:9333/json').json()
    kite_tabs = [t for t in r if 'kite.zerodha.com' in t.get('url','')]
    if not kite_tabs:
        print("ERROR: No Kite Web tab found on port 9333.")
        return
    tab = kite_tabs[0]
    async with websockets.connect(tab['webSocketDebuggerUrl']) as ws:
        expr = """(() => {
            const out = {
                watchlist: [],
                stats: {}
            };
            
            // 1. Watchlist
            document.querySelectorAll('.item-wrapper').forEach(el => {
                const name = el.querySelector('.name');
                const tag = el.querySelector('.tag');
                const price = el.querySelector('.last-price');
                const pct = el.querySelector('.change-percentage');
                const chg = el.querySelector('.change-absolute');
                if (name && price) {
                    out.watchlist.push({
                        symbol: name.innerText.trim(),
                        exchange: tag ? tag.innerText.trim() : '',
                        ltp: price.innerText.trim(),
                        change_pct: pct ? pct.innerText.trim() : '',
                        change_abs: chg ? chg.innerText.trim() : ''
                    });
                }
            });

            // 2. Open stats / depth
            document.querySelectorAll('*').forEach(el => {
                if (el.innerText && el.innerText.includes('Volume') && el.innerText.includes('Avg. trade price') && el.children.length < 15) {
                    out.stats_text = el.innerText;
                }
            });

            return JSON.stringify(out);
        })()"""
        await ws.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate', 'params': {'expression': expr}}))
        res = await ws.recv()
        val = json.loads(json.loads(res)['result']['result']['value'])
        print("WATCHLIST:")
        for w in val['watchlist']:
            print(f"  {w['symbol']:<12} ({w['exchange']}): LTP {w['ltp']:<6} | Change: {w['change_pct']}")
        if 'stats_text' in val:
            print("\nSTATS / DEPTH PANE:")
            print(val['stats_text'])

if __name__ == "__main__":
    try:
        asyncio.run(inspect_depth())
    except Exception as e:
        print(f"CDP connection failed: {e}")
