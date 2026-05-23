# AI Trader — Project Memory

## User
- Name: Sayan (sbrahma0@gmail.com)
- Goal: Exponential portfolio growth (3x+) over 2-3 years, NO day trading
- Risk tolerance: Medium-high
- Strategy: Long-term positions, hold months to years, no short-selling

## Investment Preferences
- **Core sectors**: AI Infrastructure, Quantum Computing, Space, Clean/Nuclear Energy
- **Core holdings universe**: NVDA, AMD, INTC, MU, MRVL, TSMC, AVGO (AI infra);
  IONQ, RGTI, QUBT (Quantum); RKLB, ASTS (Space); NEE, FSLR, CEG, VST (Energy)
- Sayan recently expanded into energy, quantum, and space — newer positions, higher volatility accepted
- Prefers stocks with strong AI/data center tailwinds

## System Design Decisions
- No direct brokerage API access — Sayan manually logs trades via the Trade Journal
- Analysis runs twice daily: market open (9:30 AM ET) and market close (4:00 PM ET)
- Recommendations must include: action, confidence score, buy/sell price ranges, growth thesis, timeline, risks
- Portfolio state is maintained via SQLite; updated by PDF imports and manual action_log entries

## Tech Stack
- Python + Streamlit (mobile-first)
- SQLite at data/trader.db
- Claude API (claude-sonnet-4-6) for analysis synthesis
- GitHub Actions for scheduling (cron)
- Free APIs: yfinance, Finnhub, Alpha Vantage, NewsAPI, Reddit PRAW, FRED, pytrends, World Bank

## Project Location
- C:\Users\sbrah\AI_trader\
- Main app: app.py
- Core logic: core/
- DB: data/trader.db

## Phases
- [x] Phase 1: Foundation — DB schema, PDF parser, Streamlit app skeleton
- [ ] Phase 2: Intelligence — All data collectors, sentiment engine, Claude analyzer, GitHub Actions cron
- [ ] Phase 3: Polish — Charts, push notifications, recommendation history/accuracy tracking

## Learning Goals
Sayan wants to learn about Claude tools while building:
- Skills (pdf, xlsx, pptx — reusable task bundles)
- MCP (Model Context Protocol — how Claude connects to external tools)
- Connectors (pre-built MCP integrations like GitHub, Google Drive)
- Hooks (pre/post tool-call intercepts for logging/validation)
