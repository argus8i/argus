# Circuit Mechanics: Market Microstructure & Operator Playbook

## 1. Indian Circuit Execution Mechanics

### Price-Time Priority (FIFO Queue)
In the National Stock Exchange (NSE) and Bombay Stock Exchange (BSE), the order matching engine operates strictly on **Price-Time Priority**:
1. Highest bid price gets priority over lower bids; lowest offer price gets priority over higher offers.
2. When multiple orders share the same limit price (e.g. at the Upper Circuit ceiling), execution is strictly determined by **time of entry (FIFO)**.

### Why Selling on the First Red Day is Impossible in Penny Stocks
Retail traders often believe: *"I will buy the stock at Upper Circuit, hold it while it goes up, and the moment it turns red, I will sell."*
**Why this fails mathematically:**
1. In liquid large-caps, if a stock drops 2%, buyers exist at -2.1%, -2.2%, etc.
2. In operator-controlled micro-caps, there is no natural institutional market making.
3. The operator controls both the float and the bid order queue.
4. When the operator decides to exit, they pull their massive bid orders and place block sell orders.
5. At 09:15:00 AM, the stock opens directly locked at **Lower Circuit (-5%)**.
6. Because the stock is locked at Lower Circuit, the order book becomes:
   - **Bids: 0**
   - **Offers: 20,00,000+ shares**
7. If you place a sell order at 09:15:05 AM, you are order #600 in the queue behind 20 lakh shares. Because there are no buyers, zero shares are traded. Your order expires unfulfilled at 15:30 PM.
8. The next day, it happens again (-5%). And again (-5%).
9. This creates the **Lower Circuit Lock**, which in `CROPSTER` wiped out -49% over 12 sessions before a single bid appeared.

---

## 2. The Golden Rule of Circuit Riding: Sell into the Upper Circuit

To win consistently in circuit stocks without getting trapped:
> **YOU MUST SELL WHILE THE STOCK IS STILL AT UPPER CIRCUIT.**

### The Mechanics of Selling into UC:
- When a stock is locked at Upper Circuit with **50,00,000 shares on bid**, any sell order placed by you executes **INSTANTLY** at the maximum price because millions of buyers are waiting in line to absorb every available share.
- By sacrificing the potential extra 5% of the final day, you guarantee **100% liquidity and instant profit realization**.

### The 3-Day Rule (Target: 15% - 20%):
1. **Day 1 (Ignition):** Stock breaks out from flat base on huge volume (>5x SMA). Enter via AMO Limit order at UC price.
2. **Day 2 (Follow-through):** Stock gaps to UC (+5%). Hold. (Cumulative: ~+10.25%).
3. **Day 3 (Momentum Peak):** Stock opens at UC (+5%). Target achieved (+15.76%).
4. **Day 4 (Target Session):** Stock opens at UC (+5%). Cumulative profit = **+21.55%**. Place a Limit Sell order directly into the opening bid queue. Capture your 20% profit for the month and walk away.
