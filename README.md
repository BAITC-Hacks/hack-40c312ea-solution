# EKT AI Engineer — HackAlem MVP

AI Sales Engineer for ekt.kz: search real products, verify live details, compare alternatives, assemble a solution and add items to a prototype cart only after explicit confirmation.

## Run

1. Install Python 3.11+ and `pip install -r requirements.txt`.
2. Copy `.env.example` to `.env`, set `EKT_PASSWORD` and optional OpenAI/NVIDIA API keys with model IDs.
3. Run `uvicorn app.main:app --reload` and open `http://127.0.0.1:8000`.

The catalog sync fetches up to `CATALOG_PAGES` pages (20 products each), stops when EKT repeats page one, and caches them locally. Product ID lookup still uses live detail lookup. Price and stock displayed for shortlisted products come from the live detail endpoint. The EKT API exposes no individual delivery ETA or verified cart mutation endpoint in the supplied contract, so the cart is session-local and links to the official EKT basket for manual checkout. It does not claim to modify the remote basket.

## Architecture

`Browser → FastAPI → EKT client → catalog cache/search → live details → solution/compatibility → confirmation gate → local cart`

Model routing uses no model for exact lookups and product facts. Short requests route to `CHEAP`, comparisons to `MEDIUM`, complex solution requests to `STRONG`, and images to `VISION`. Set each `*_MODEL` and optional `*_PROVIDER` (`openai` or `nvidia`) in `.env`. Requests fall back to deterministic lookup when keys/models are unavailable. The chosen route appears in the UI. No provider keys were available in the development environment, so paid routes have not been live-tested.

CSV, XLS/XLSX, DOCX and text PDFs are parsed locally. JPG/PNG and scanned PDFs use a configured vision model; for scanned PDFs the first page is rendered for the vision route.

Official purchase conditions are shown from [EKT's information page](https://ekt.kz/about/information/); product-specific ETA and minimum order remain unknown.

## Demo

- Search for product ID `515291`.
- Ask for `автомат Schneider 3P 32A`.
- Upload a `.csv` or `.xlsx` specification.
- Compare Cheapest / Available / Best fit.
- Add the proposed set to the local cart with a separate confirmation.

The source API fields verified on 2026-09-23: list `page`, `per_page`, `count`, `items`; item `id`, `name`, `article`, `price`, `image`, `url`, `offers`; detail adds `description`, `quantity`, `stores`, `properties`. Some source fields conflict: product 515291 says 160 A in its name/description while `NOMINALNYY_TOK` says 250 A. The agent must surface this uncertainty.
