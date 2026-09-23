# EKT AI Engineer — HackAlem MVP

AI Sales Engineer for ekt.kz: search real products, verify live details, compare alternatives, assemble a solution and add items to a prototype cart only after explicit confirmation.

## Run

1. Install Python 3.11+ and `pip install -r requirements.txt`.
2. Copy `.env.example` to `.env`, set `EKT_PASSWORD` and optional model API keys.
3. Run `uvicorn app.main:app --reload` and open `http://127.0.0.1:8000`.

The catalog sync fetches the first `CATALOG_PAGES` pages (20 products each) and caches them locally. This bounded cache is a demo limitation: an uncached product ID still uses live detail lookup. Price and stock displayed for shortlisted products come from the live detail endpoint. The EKT API exposes no verified delivery ETA or cart mutation endpoint in the supplied contract, so the cart is session-local and links to the official EKT basket for manual checkout. It does not claim to modify the remote basket.

## Architecture

`Browser → FastAPI → EKT client → catalog cache/search → live details → solution/compatibility → confirmation gate → local cart`

Model routing uses no model for exact lookups and product facts. Optional models are configured through environment variables; the deterministic path works without provider keys.

## Demo

- Search for product ID `515291`.
- Ask for `автомат Schneider 3P 32A`.
- Upload a `.csv` or `.xlsx` specification.
- Compare Cheapest / Available / Best fit.
- Add the proposed set to the local cart with a separate confirmation.

The source API fields verified on 2026-09-23: list `page`, `per_page`, `count`, `items`; item `id`, `name`, `article`, `price`, `image`, `url`, `offers`; detail adds `description`, `quantity`, `stores`, `properties`. Some source fields conflict: product 515291 says 160 A in its name/description while `NOMINALNYY_TOK` says 250 A. The agent must surface this uncertainty.
