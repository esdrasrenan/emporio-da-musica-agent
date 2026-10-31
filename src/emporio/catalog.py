"""Product search over the catalog, with structured filters and typo tolerance.

The LLM extracts intent (category, budget, keywords); this module does the
matching and filtering deterministically. Keyword matching is accent- and
plural-insensitive, tolerates typos and model codes typed with spaces or
hyphens ("gd 20", "GD-20" -> "GD20").
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from decimal import Decimal

from emporio.data.models import Product, StoreData
from emporio.money import format_brl
from emporio.pricing import describe_installments, price_info
from emporio.text import singular, tokens

STOPWORDS = frozenset(
    """a o as os um uma uns umas de da do das dos e ou em no na nos nas para pra pro por com
    sem que qual quais quanto quantos quanta custa custam custando preco precos valor valores
    tem tens teria ter voce voces vc vcs me meu minha quero queria gostaria procuro procurando
    algum alguma alguns algumas opcao opcoes modelo modelos produto produtos loja ate r reais
    mais menos barato barata baratos baratas bom boa bons boas melhor melhores disponivel
    disponiveis estoque venda vende vendem""".split()
)

NAME, CATEGORY, TAG, SPEC, DESCRIPTION = 3.0, 2.0, 2.0, 1.0, 1.0

SPEC_LABELS = {
    "top": "tampo",
    "back_sides": "fundo e laterais",
    "neck": "braço",
    "strings": "cordas",
    "scale": "escala",
    "electronics": "eletrônica/captação",
    "color": "cor",
    "body": "corpo",
    "shells": "cascos",
    "pieces": "peças",
    "hardware": "ferragens",
    "keys": "teclas",
    "type": "tipo",
    "polyphony": "polifonia",
}
SPEC_VALUES = {"steel": "aço", "yes": "sim", "no": "não", "included": "inclusas"}


def _tags(product: Product) -> list[str]:
    """Search tags derived from specs, so "eletroacústico" or "aço" find the right items."""
    tags: list[str] = []
    if product.specs.get("electronics") == "yes":
        tags += ["eletrico", "eletroacustico", "captacao", "amplificado"]
    if product.specs.get("strings") == "steel":
        tags += ["aco", "folk"]
    elif product.specs.get("strings") == "nylon":
        tags += ["nylon", "classico"]
    return tags


@dataclass
class SearchResult:
    products: list[Product]
    total: int
    notes: list[str] = field(default_factory=list)


class Catalog:
    def __init__(self, data: StoreData) -> None:
        self.data = data
        self._index = {pid: self._index_product(p) for pid, p in data.products.items()}
        self._vocabulary = sorted({token for index in self._index.values() for token in index})
        self._vocabulary_set = set(self._vocabulary)
        self._compact_names = {pid: "".join(tokens(p.name)) for pid, p in data.products.items()}
        self._category_terms = {
            cid: {singular(t) for t in tokens(f"{c.name} {c.description}")}
            for cid, c in data.categories.items()
        }

    # ------------------------------------------------------------------ search
    def search(
        self,
        query: str | None = None,
        category: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        include_unavailable: bool | None = None,
        on_sale_only: bool = False,
        sort: str = "relevance",
        limit: int = 10,
    ) -> SearchResult:
        notes: list[str] = []
        candidates = list(self.data.products.values())

        if category:
            cat = self.data.category_by_slug(category)
            if cat is None:
                return SearchResult([], 0, [f"Categoria desconhecida: {category}."])
            candidates = [p for p in candidates if p.category_id == cat.id]
            if not candidates:
                notes.append(f"Não há produtos cadastrados na categoria {cat.name} no momento.")

        # Browsing shows what can be bought; naming a product also shows it when
        # it is out of stock or discontinued, so the agent can say so (policy 7.3).
        if include_unavailable is None:
            include_unavailable = bool(query)
        if not include_unavailable:
            candidates = [p for p in candidates if p.purchasable]

        def final_price(product: Product) -> Decimal:
            return price_info(product, self.data.active_promotion(product.id)).final_price

        if min_price is not None:
            candidates = [p for p in candidates if final_price(p) >= Decimal(str(min_price))]
        if max_price is not None:
            candidates = [p for p in candidates if final_price(p) <= Decimal(str(max_price))]
        if on_sale_only:
            candidates = [p for p in candidates if self.data.active_promotion(p.id)]

        scores: dict[int, tuple[int, float]] = {}
        if query:
            terms = self._query_terms(query)
            if terms:
                scored = {p.id: self._score(p.id, terms) for p in candidates}
                best = max((matched for matched, _ in scored.values()), default=0)
                threshold = best if len(terms) <= 2 else best - 1
                scores = {pid: s for pid, s in scored.items() if s[0] >= max(1, threshold)}
                candidates = [p for p in candidates if p.id in scores]
                unmatched = [t for t in terms if not any(self._term_weight(pid, t) for pid in scores)]
                if unmatched:
                    notes.append(f"Termos sem correspondência no catálogo: {', '.join(unmatched)}.")
                if not candidates:
                    notes += self._empty_category_notes(terms)

        if sort == "price_desc":
            candidates.sort(key=lambda p: (-final_price(p), p.id))
        elif sort == "price_asc" or not scores:
            candidates.sort(key=lambda p: (final_price(p), p.id))
        else:
            candidates.sort(
                key=lambda p: (-scores[p.id][0], not p.purchasable, -scores[p.id][1], final_price(p), p.id)
            )
        return SearchResult(candidates[: max(1, min(limit, 20))], len(candidates), notes)

    def alternatives(self, product: Product, limit: int = 3) -> list[Product]:
        """Available products of the same category: same kind first (string type,
        pickup), then closest in price."""

        def differences(other: Product) -> int:
            return sum(other.specs.get(k) != product.specs.get(k) for k in ("strings", "electronics"))

        pool = [
            p
            for p in self.data.products.values()
            if p.category_id == product.category_id and p.purchasable and p.id != product.id
        ]
        return sorted(pool, key=lambda p: (differences(p), abs(p.price - product.price), p.id))[:limit]

    # ------------------------------------------------------------------ views
    def view(self, product: Product, with_alternatives: bool = False) -> dict:
        """Customer-facing JSON view with everything the agent may quote."""
        category = self.data.categories[product.category_id]
        out: dict = {
            "product_id": product.id,
            "name": product.name,
            "category": category.name,
            "availability": product.availability.value,
        }
        if product.purchasable:
            out["stock"] = "últimas unidades" if product.low_stock else "em estoque"
        if product.status.value == "active":  # discontinued / coming soon have no valid price
            price = price_info(product, self.data.active_promotion(product.id))
            out["list_price"] = format_brl(price.list_price)
            if price.promotion:
                out["promotion"] = {
                    "name": price.promotion.description,
                    "discount": f"{price.promotion.discount_percent.normalize()}%",
                    "promotional_price": format_brl(price.final_price),
                    "rule": "o desconto PIX não se aplica sobre o preço promocional",
                }
            else:
                out["pix_price"] = format_brl(price.pix_price)
            out["credit_card"] = describe_installments(price.final_price)
        out["specs"] = {SPEC_LABELS.get(k, k): SPEC_VALUES.get(v, v) for k, v in product.specs.items()}
        if product.description:
            out["description"] = product.description
        if with_alternatives and not product.purchasable:
            out["available_alternatives"] = [
                {
                    "product_id": alt.id,
                    "name": alt.name,
                    "price": format_brl(price_info(alt, self.data.active_promotion(alt.id)).final_price),
                }
                for alt in self.alternatives(product)
            ]
        return out

    # ------------------------------------------------------------------ internals
    def _index_product(self, product: Product) -> dict[str, float]:
        category = self.data.categories[product.category_id]
        fields = [
            (product.name, NAME),
            (category.name, CATEGORY),
            (category.description, DESCRIPTION),
            (" ".join(_tags(product)), TAG),
            (" ".join(product.specs.values()), SPEC),
            (product.description or "", DESCRIPTION),
        ]
        index: dict[str, float] = {}
        for text, weight in fields:
            for token in tokens(text):
                term = singular(token)
                index[term] = max(index.get(term, 0.0), weight)
        return index

    def _query_terms(self, query: str) -> list[str]:
        raw = [singular(t) for t in tokens(query) if t not in STOPWORDS]
        merged: list[str] = []
        i = 0
        while i < len(raw):  # "gd 20" -> "gd20" when that is a real model code
            if i + 1 < len(raw):
                joined = raw[i] + raw[i + 1]
                if joined in self._vocabulary_set and not (
                    raw[i] in self._vocabulary_set and raw[i + 1] in self._vocabulary_set
                ):
                    merged.append(joined)
                    i += 2
                    continue
            merged.append(raw[i])
            i += 1
        terms = []
        for term in merged:
            if term.isdigit() and term not in self._vocabulary_set:
                continue  # prices typed in the query ("até 1000") are not keywords
            terms.append(self._correct(term))
        return terms

    def _correct(self, term: str) -> str:
        """Fix typos ("takamime" -> "takamine") against the catalog vocabulary."""
        if term in self._vocabulary_set or len(term) < 4:
            return term
        if any(v.startswith(term) for v in self._vocabulary):
            return term
        match = difflib.get_close_matches(term, self._vocabulary, n=1, cutoff=0.8)
        return match[0] if match else term

    def _term_weight(self, product_id: int, term: str) -> float:
        index = self._index[product_id]
        if term in index:
            return index[term]
        if len(term) >= 4:
            prefixed = [w for t, w in index.items() if t.startswith(term)]
            if prefixed:
                return max(prefixed) * 0.8
        return 0.0

    def _score(self, product_id: int, terms: list[str]) -> tuple[int, float]:
        weights = [self._term_weight(product_id, t) for t in terms]
        matched, total = sum(1 for w in weights if w), sum(weights)
        compact = "".join(terms)
        if len(terms) > 1 and len(compact) >= 4 and compact in self._compact_names[product_id]:
            matched, total = len(terms), total + NAME
        return matched, total

    def _empty_category_notes(self, terms: list[str]) -> list[str]:
        """Explain an empty result when the customer asked for a category we have no items in."""
        generic = STOPWORDS | {"instrumento", "outro", "acustico"}
        notes = []
        for cid, category_terms in self._category_terms.items():
            has_products = any(p.category_id == cid for p in self.data.products.values())
            if not has_products and set(terms) & (category_terms - generic):
                notes.append(
                    f"A categoria {self.data.categories[cid].name} existe, mas não há "
                    "produtos cadastrados nela no momento."
                )
        return notes
