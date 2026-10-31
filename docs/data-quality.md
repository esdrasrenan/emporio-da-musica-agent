# Data quality report

The store's files are used as they were delivered: nothing under `data/` is edited. Every
cleaning rule lives in code ([`data/loader.py`](../src/emporio/data/loader.py) and
[`policies.py`](../src/emporio/policies.py)) and is listed here, so the business can fix the
source instead of relying on the agent's workarounds.

Regenerate the table with `emporio data-report`.

## Findings detected in code

| Table | Record | Issue | How the agent handles it |
|---|---|---|---|
| products | 130 - Kala KA-ABP-CTG Barítono Cedar | status 'coming_soon' but stock_quantity=2 | treated as not purchasable; status wins over stock |
| products | 135 - Music Man Bass 1X Electric Bass | description mentions fender but the product is music man | description hidden from the agent; name, specs and price are still used |
| products | 136 - Ibanez Bass 2X Electric Bass | description mentions fender but the product is ibanez | description hidden from the agent; name, specs and price are still used |
| products | 136 - Ibanez Bass 2X Electric Bass | status 'discontinued' but stock_quantity=13 | treated as not purchasable; status wins over stock |
| products | 137 - Yamaha Bass 3X Electric Bass | description mentions ibanez but the product is yamaha | description hidden from the agent; name, specs and price are still used |
| products | 139 - Bateria Acústica Yamaha Kit 1 Studio | description mentions pearl but the product is yamaha | description hidden from the agent; name, specs and price are still used |
| products | 140 - Bateria Acústica Pearl Kit 2 Studio | description mentions tama but the product is pearl | description hidden from the agent; name, specs and price are still used |
| products | 142 - Teclado Sintetizador Korg Synth 1 Pro | description mentions roland but the product is korg | description hidden from the agent; name, specs and price are still used |
| products | 144 - Teclado Sintetizador Roland Synth 3 Pro | description mentions yamaha but the product is roland | description hidden from the agent; name, specs and price are still used |
| categories | 6 - Instrumentos de Sopro (Madeiras) | category has no products | searches in it return an explicit 'no items in this category' note |
| categories | 7 - Instrumentos de Sopro (Metais) | category has no products | searches in it return an explicit 'no items in this category' note |
| categories | 8 - Cordas Orquestrais | category has no products | searches in it return an explicit 'no items in this category' note |
| promotions | product 93 | 2 promotions registered (0 active) | only active promotions count; if several are active the best one applies |
| promotions | product 96 | 2 promotions registered (0 active) | only active promotions count; if several are active the best one applies |
| promotions | product 99 | 2 promotions registered (0 active) | only active promotions count; if several are active the best one applies |
| customers | ids 11, 43 | e-mail bruno.martins@jmail.com is shared | identity checks compare the contact with the order's own customer, never resolve a customer from the e-mail alone |
| orders | order 3 | total_brl 3450.00 differs from current list prices (3498.00) | the recorded total is reported as is (it is what the customer was charged) |
| orders | order 3 | contains product 130, which is still 'coming_soon' | informational: the order is reported normally |
| orders | order 9 | 6x installments of 58.32 are below the R$ 80 minimum of policy 3.1 | informational: new quotes always follow policy 3.1 |
| orders | order 14 | contains product 130, which is still 'coming_soon' | informational: the order is reported normally |
| orders | order 16 | 6x installments of 71.65 are below the R$ 80 minimum of policy 3.1 | informational: new quotes always follow policy 3.1 |
| orders | order 18 | 12x installments of 38.33 are below the R$ 100 minimum of policy 3.1 | informational: new quotes always follow policy 3.1 |
| orders | order 20 | total_brl 1400.00 differs from current list prices (1488.00) | the recorded total is reported as is (it is what the customer was charged) |

Why hide a contradictory description instead of showing it: policy 7.1 says incorrect
information "can constitute misleading advertising". When a description sells a Fender Jazz
Bass under the name "Music Man Bass 1X", the agent cannot know which one is on the shelf, so it
quotes only what is consistent (name, specs, price). The rule is generic: any description that
names a brand (or a model that implies a brand, such as "Precision" or "StingRay") other than
the product's own is hidden.

## Findings in the policy manual (`políticas_da_loja.pdf`)

| Where | Issue | Decision |
|---|---|---|
| Cover vs. section 1.2 | The e-mail is `contato@emporiodamusica.com.br` on the cover and `contato@empóriodamusica.com.br` (with an accent) in the company table | The cover's version is used; the parser applies this as a documented erratum |
| Section 1.2 vs. section 7 | "Telefone / WhatsApp (67) 3341-4444" in the company table, "O canal de WhatsApp (67) 3321-4500" in section 7 | The agent gives only (67) 3341-4444, the number in the customer-facing section; the conflict needs confirmation from the store |
| Section 3 vs. section 3.1 | The table says "Parcela mínima de R$ 100,00" for credit cards; 3.1 sets tiers (R$ 50 up to 3x, R$ 80 for 4x-6x, R$ 100 for 7x-12x) | The more specific tiers of 3.1 are used |
| Section 3.1 | "sem valor mínimo de parcela (exceto abaixo de R$ 50,00)" is ambiguous | Read as "each installment must be at least R$ 50". For every product in the catalog both readings give the same result |
| Section 5.1 | "acima de R$ 500,00" is free and "abaixo de R$ 500,00" pays R$ 35; exactly R$ 500,00 is undefined | Exactly R$ 500,00 ships free (benefit of the doubt to the customer) |
| Section 4.3 | The 7 days for exchanges by preference have no starting point | Counted from receipt, like section 4.1 |
| Section 7.1 | "Sugerir lojas parceiras quando possível", but no partner store is listed anywhere | The agent never names a store |

## Dataset-level observations

- **The data is a snapshot from March 2026.** The latest order is from 2026-03-22, there are
  pending and confirmed orders and one shipment in transit. Answering with today's date would
  make every pending order look months late. Examples and evals freeze the clock at
  2026-03-24 10:30 (`EMPORIO_NOW`); in production the clock is the real time in Campo Grande.
- **Delivery dates are estimates.** Orders have `estimated_delivery` but no actual receipt date,
  while regret (4.1) and the legal warranty (8.1) count from receipt. The agent uses the estimate
  and says so to the customer.
- **Sales channel is not recorded.** Section 4.1 covers online purchases only. All orders have a
  delivery estimate or tracking code, so they are treated as remote purchases.
- **The catalog is a sample.** The manual mentions "more than 300 instruments"; the file has 65.
  The agent only offers what is in the file.
