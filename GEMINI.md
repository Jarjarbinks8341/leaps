# GEMINI.md - Project Context: leaps

This file provides foundational context for AI-assisted development within the `leaps` workspace.

## Project Overview
The `leaps` project is currently in its initial setup phase. It is an empty Git repository cloned from `https://github.com/Jarjarbinks8341/leaps.git`.

- **Current State:** Active development for financial data tracking.
- **Project Structure:** A single subdirectory named `leaps` contains the Git metadata.
- **Goal:** To download and store daily stock prices (QQQ, VOO) in a local database for analysis.

## Building and Running
The project uses a Python 3 environment.

- **Setup:**
  - Create venv: `python3 -m venv venv`
  - Activate venv: `source venv/bin/activate`
  - Install dependencies: `pip install -r requirements.txt` (or manually install `yfinance`, `pandas`).
- **Commands:**
  - Fetch data: `python3 fetch_prices.py`
  - Query DB: `sqlite3 stock_prices.db`

## Development Conventions
*Conventions will be established as the codebase grows.*

- **Style:** Adhere to standard Python (PEP 8) conventions.
- **Testing:** [TODO] Implement and document testing procedures.
- **CI/CD:** [TODO] Define deployment and integration pipelines.

## Key Files
- `leaps/`: The primary directory (currently contains only Git metadata).
- `fetch_prices.py`: Script to download ticker data from yfinance to a local SQLite database.
- `stock_prices.db`: SQLite database containing the fetched price data.
- `venv/`: Python virtual environment directory.
- `GEMINI.md`: This file, providing context and guidance for future AI interactions.

---
*Last Updated: 2026-03-12*
