# EKT AI Engineer — HackAlem MVP

AI Sales Engineer for ekt.kz: search real products, verify live details, compare alternatives, assemble a solution and add items to a prototype cart only after explicit confirmation.

## Run

1. Install Python 3.11+ and `pip install -r requirements.txt`.
2. Copy `.env.example` to `.env`, set `EKT_PASSWORD` and optional OpenAI/NVIDIA API keys with model IDs. Keep `.env` private; it is ignored by Git.
3. Run `uvicorn app.main:app --reload` and open `http://127.0.0.1:8000`.

The catalog sync fetches up to `CATALOG_PAGES` pages (20 products each), stops when EKT repeats page one, and caches them locally. On 2026-09-23 a full scan found 14,835 unique products. Product ID lookup still uses live detail lookup. Price and stock displayed for shortlisted products come from the live detail endpoint. The EKT API exposes no individual delivery ETA or verified cart mutation endpoint in the supplied contract, so the cart is session-local and links to the official EKT basket for manual checkout. It does not claim to modify the remote basket. EKT's internal database is not accessible through the supplied credentials; integration into EKT can replace the isolated catalog and cart providers with internal services.

## Architecture

`Browser → FastAPI → intent/model router → catalog cache/search → live EKT details → solution/compatibility → confirmation gate → local cart`

Model routing uses no model for exact lookups and product facts. Short requests route to `CHEAP`, comparisons to `MEDIUM`, complex solution requests to `STRONG`, and images to `VISION`. Set each `*_MODEL` and optional `*_PROVIDER` (`openai` or `nvidia`) in `.env`. Requests fall back to deterministic lookup when keys/models are unavailable. The chosen route appears in the UI. The OpenAI cheap, medium, strong and vision routes have been live-tested. NVIDIA requires a separate key and has not been tested. A local usage ledger and configurable call/spend caps limit model usage; its dollar value is an estimate, not provider billing.

CSV, XLS/XLSX, DOCX and text PDFs are parsed locally. JPG/PNG and scanned PDFs use a configured vision model; for scanned PDFs the first page is rendered for the vision route. File content is treated as untrusted data. The parser processes up to 12 specification rows per upload. Complex solution requests produce several component roles and explicitly mark compatibility uncertain when critical ratings are absent. Modes include Cheapest, Available, Best fit and Preferred brand; follow-up requests can rerank a recently verified shortlist without another model call.

Official purchase conditions are shown from [EKT's information page](https://ekt.kz/about/information/); product-specific ETA and minimum order remain unknown.

## Demo

- Search for product ID `515291`.
- Ask for `автомат Schneider 3P 32A`.
- Upload a `.csv` or `.xlsx` specification.
- Compare Cheapest / Available / Best fit.
- Add the proposed set to the local cart with a separate confirmation.
- Ask for `Нужно собрать защиту двигателя 11 кВт, 380 В, желательно Schneider`; the result should request the motor's nameplate current before claiming compatibility.

The source API fields verified on 2026-09-23: list `page`, `per_page`, `count`, `items`; item `id`, `name`, `article`, `price`, `image`, `url`, `offers`; detail adds `description`, `quantity`, `stores`, `properties`. Some source fields conflict: product 515291 says 160 A in its name/description while `NOMINALNYY_TOK` says 250 A. The agent must surface this uncertainty.

## Limits

- The browser's cart is a local session prototype. The official EKT checkout link opens the real site but does not transfer local items.
- The supplied EKT API provides catalog and detail endpoints, not its entire internal database or an order-writing contract.
- Compatibility checks cover identifiable ratings in product names and exposed properties; they cannot certify electrical design.
- If EKT omits a certificate link, minimum order or item-specific ETA, the UI reports that information as unavailable.

