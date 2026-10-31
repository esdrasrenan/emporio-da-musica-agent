"""The agent's tools: argument models (validated), JSON schemas and handlers.

Five tools cover the store's recurring questions. Each schema is generated from
a Pydantic model, so the description the model reads and the validation the
handler runs cannot drift apart. Handlers return JSON; errors come back as
``is_error`` results the model can recover from.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from emporio.catalog import Catalog
from emporio.config import Clock
from emporio.data.models import StoreData
from emporio.llm.base import ToolCall, ToolResult, ToolSpec
from emporio.orders import OrderDesk
from emporio.policies import INTERNAL_SECTIONS, PolicyManual
from emporio.pricing import QuoteError, build_quote
from emporio.storage import Storage
from emporio.store import describe_day, is_open, next_opening

log = logging.getLogger(__name__)


@dataclass
class SessionContext:
    """Per-conversation facts that tools trust (they never come from the model)."""

    session_id: str
    customer_id: int | None = None  # set only when the channel identified the customer
    customer_name: str | None = None
    failed_verifications: int = 0


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchProductsArgs(_Args):
    query: str | None = Field(
        None,
        description=(
            "Brand, model or keywords in Portuguese, as products are described in Portuguese "
            "(e.g. 'Takamine GD20', 'nylon', '7 cordas', 'eletroacústico', 'tenor'). "
            "Do not put prices or the category name here: use the filters."
        ),
    )
    category: str | None = Field(None, description="Category, when the instrument type is known.")
    min_price: float | None = Field(None, description="Minimum final price in BRL (after promotion).")
    max_price: float | None = Field(
        None, description=("Maximum final price in BRL (after promotion), e.g. 1000 for 'até R$ 1.000'.")
    )
    on_sale_only: bool = Field(False, description="Only products with an active promotion.")
    include_unavailable: bool | None = Field(
        None,
        description=(
            "Also return out-of-stock, discontinued and coming-soon products. Defaults to true when "
            "'query' is set (so a named product is found even if unavailable), false otherwise."
        ),
    )
    sort: Literal["relevance", "price_asc", "price_desc"] = Field(
        "relevance", description="Result order. Relevance applies when 'query' is set."
    )
    limit: int = Field(12, description="Maximum number of products to return (1-20).")


class CartItem(_Args):
    product_id: int = Field(description="product_id returned by search_products.")
    quantity: int = Field(1, description="Number of units.")


class QuotePurchaseArgs(_Args):
    items: list[CartItem] = Field(description="Products the customer wants to buy.")
    city: str | None = Field(None, description="Delivery city, only if the customer said it.")


class ConsultPolicyArgs(_Args):
    sections: list[str] = Field(description="One or more manual sections to read.")


class GetOrderStatusArgs(_Args):
    order_id: int | None = Field(
        None,
        description=("Order number. Omit only to list the orders of a customer identified by the channel."),
    )
    contact: str | None = Field(
        None,
        description=(
            "E-mail or phone the customer says was used in the purchase, exactly as typed. "
            "Not needed when the customer is identified by the channel."
        ),
    )


class CreateSupportTicketArgs(_Args):
    category: Literal[
        "reclamacao", "troca_ou_devolucao", "cancelamento", "falar_com_atendente", "cotacao_de_frete", "outro"
    ]
    summary: str = Field(
        description=(
            "What the customer needs, in 1-3 sentences, with the facts already checked "
            "(order, product, dates)."
        )
    )
    order_id: int | None = Field(None, description="Related order number, if any.")
    customer_name: str | None = Field(None, description="Customer name, if known.")
    contact: str | None = Field(
        None,
        description=(
            "Phone or e-mail for the team to reply. Required unless the customer was identified "
            "by the channel."
        ),
    )


def _clean_schema(node: object, in_properties: bool = False) -> object:
    """Pydantic JSON schema -> compact schema for tool use (no titles/defaults,
    optional fields as plain types, additionalProperties false everywhere)."""
    if isinstance(node, list):
        return [_clean_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    if in_properties:  # keys are field names, not schema keywords
        return {key: _clean_schema(value) for key, value in node.items()}
    cleaned = {
        key: _clean_schema(value, in_properties=(key in ("properties", "$defs")))
        for key, value in node.items()
        if key not in ("title", "default")
    }
    any_of = cleaned.get("anyOf")
    if isinstance(any_of, list) and len(any_of) == 2 and {"type": "null"} in any_of:
        cleaned.pop("anyOf")
        cleaned.update(next(option for option in any_of if option != {"type": "null"}))
    if cleaned.get("type") == "object":
        cleaned["additionalProperties"] = False
    return cleaned


def _schema(model: type[BaseModel]) -> dict:
    return _clean_schema(model.model_json_schema())  # type: ignore[return-value]


class Toolbox:
    def __init__(
        self,
        data: StoreData,
        catalog: Catalog,
        policies: PolicyManual,
        orders: OrderDesk,
        storage: Storage,
        clock: Clock,
    ) -> None:
        self.data, self.catalog, self.policies = data, catalog, policies
        self.orders, self.storage, self.clock = orders, storage, clock
        self._handlers = {
            "search_products": (SearchProductsArgs, self._search_products),
            "quote_purchase": (QuotePurchaseArgs, self._quote_purchase),
            "consult_policy": (ConsultPolicyArgs, self._consult_policy),
            "get_order_status": (GetOrderStatusArgs, self._get_order_status),
            "create_support_ticket": (CreateSupportTicketArgs, self._create_support_ticket),
        }
        self.specs = self._build_specs()

    # ------------------------------------------------------------------ specs
    def _build_specs(self) -> list[ToolSpec]:
        search = _schema(SearchProductsArgs)
        search["properties"]["category"]["enum"] = [c.slug for c in self.data.categories.values()]
        policy = _schema(ConsultPolicyArgs)
        sections = self.policies.customer_sections
        policy["properties"]["sections"]["items"]["enum"] = [s.slug for s in sections]
        toc = "; ".join(f"{s.slug} = {s.heading}" for s in sections)
        stocked = {p.category_id for p in self.data.products.values()}
        empty = ", ".join(c.name for c in self.data.categories.values() if c.id not in stocked)
        return [
            ToolSpec(
                "search_products",
                (
                    "Search the store catalog (musical instruments only). Returns current price, "
                    "active promotion, PIX price, credit-card installments, availability and specs. "
                    "Call it before mentioning ANY price, stock, availability, promotion or product "
                    "detail - never answer those from memory - and to suggest alternatives. "
                    "Combine 'category' and price filters for browsing ('violões até R$ 1.000'); use "
                    "'query' for a named product or feature. "
                    f"Categories with no products at the moment: {empty or 'none'}."
                ),
                search,
            ),
            ToolSpec(
                "quote_purchase",
                (
                    "Compute the total for one or more products with every payment option (PIX, "
                    "credit-card installments, debit, boleto) and the delivery fee and time for a "
                    "city. Call it when the customer asks how much something costs in installments "
                    "or via PIX, the total of several items, or shipping cost/time. Needs product_id "
                    "values from search_products."
                ),
                _schema(QuotePurchaseArgs),
            ),
            ToolSpec(
                "consult_policy",
                (
                    "Read sections of the store's policy manual. Call it before answering about "
                    "opening hours, address and contacts, payment methods and installment rules, "
                    "returns/exchanges/regret, shipping and delivery, promotion rules, warranty or "
                    "privacy. Answer only with what the text says. Sections: " + toc + "."
                ),
                policy,
            ),
            ToolSpec(
                "get_order_status",
                (
                    "Look up an order: status, items, total, tracking code, delivery estimate and, "
                    "for delivered orders, the computed return/exchange/warranty deadlines. Requires "
                    "the order number plus the e-mail or phone used in the purchase (identity check "
                    "done by the system), unless the customer was identified by the channel."
                ),
                _schema(GetOrderStatusArgs),
            ),
            ToolSpec(
                "create_support_ticket",
                (
                    "Register a request for the human team and get a protocol number. Use it for "
                    "complaints, return/exchange or cancellation requests, special shipping quotes, "
                    "questions the policy does not answer, or when the customer asks for a person. "
                    "Collect a short summary and a contact for the reply before calling. Register "
                    "each request only once."
                ),
                _schema(CreateSupportTicketArgs),
            ),
        ]

    # ------------------------------------------------------------------ dispatch
    def execute(self, call: ToolCall, ctx: SessionContext) -> ToolResult:
        handler = self._handlers.get(call.name)
        if handler is None:
            return ToolResult(call.id, f"Ferramenta desconhecida: {call.name}", is_error=True)
        model, function = handler
        try:
            args = model.model_validate(call.arguments)
            output = function(args, ctx)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}" for e in exc.errors()
            )
            return ToolResult(call.id, f"Argumentos inválidos: {problems}", is_error=True)
        except (QuoteError, ValueError) as exc:
            return ToolResult(call.id, str(exc), is_error=True)
        except Exception:  # never let a tool crash the conversation
            log.exception("tool %s failed", call.name)
            return ToolResult(call.id, "Erro interno ao executar a ferramenta.", is_error=True)
        return ToolResult(call.id, json.dumps(output, ensure_ascii=False))

    # ------------------------------------------------------------------ handlers
    def _search_products(self, args: SearchProductsArgs, ctx: SessionContext) -> dict:
        result = self.catalog.search(
            query=args.query,
            category=args.category,
            min_price=args.min_price,
            max_price=args.max_price,
            include_unavailable=args.include_unavailable,
            on_sale_only=args.on_sale_only,
            sort=args.sort,
            limit=args.limit,
        )
        out: dict = {
            "total_matches": result.total,
            "returned": len(result.products),
            "products": [self.catalog.view(p, with_alternatives=bool(args.query)) for p in result.products],
        }
        notes = list(result.notes)
        if result.total > len(result.products):
            notes.append(
                f"Mostrando {len(result.products)} de {result.total} produtos; todos os {result.total} "
                "atendem aos filtros. Informe o total ao cliente ou busque de novo com 'limit' maior."
            )
        if notes:
            out["notes"] = notes
        return out

    def _quote_purchase(self, args: QuotePurchaseArgs, ctx: SessionContext) -> dict:
        return build_quote(self.data, [(i.product_id, i.quantity) for i in args.items], args.city)

    def _consult_policy(self, args: ConsultPolicyArgs, ctx: SessionContext) -> dict:
        found, unknown = [], []
        for slug in dict.fromkeys(args.sections):
            section = self.policies.get(slug)
            if section and section.number not in INTERNAL_SECTIONS:
                found.append({"section": section.heading, "text": section.text})
            else:
                unknown.append(slug)
        if unknown:
            raise ValueError(f"Seções inexistentes: {', '.join(unknown)}.")
        return {
            "source": "Manual de Políticas e Procedimentos, versão 2.1 (junho de 2025)",
            "sections": found,
        }

    def _get_order_status(self, args: GetOrderStatusArgs, ctx: SessionContext) -> dict:
        today = self.clock.now().date()
        if args.order_id is None:
            if ctx.customer_id is None:
                raise ValueError("Peça o número do pedido e o e-mail ou telefone usado na compra.")
            return {"orders": self.orders.orders_of(ctx.customer_id)}
        result, failed = self.orders.lookup(
            args.order_id, args.contact, ctx.customer_id, ctx.failed_verifications, today
        )
        ctx.failed_verifications += failed
        return result

    def _create_support_ticket(self, args: CreateSupportTicketArgs, ctx: SessionContext) -> dict:
        if ctx.customer_id is None and not args.contact:
            raise ValueError("Peça um telefone ou e-mail para a equipe retornar antes de registrar.")
        now = self.clock.now()
        # Idempotent per conversation and category: a second call (models sometimes
        # re-register after the customer confirms) returns the ticket already open.
        existing = self.storage.open_ticket(ctx.session_id, args.category)
        protocol = existing or self.storage.create_ticket(
            session_id=ctx.session_id,
            category=args.category,
            summary=args.summary,
            order_id=args.order_id,
            customer_id=ctx.customer_id,
            contact=args.contact or (self.data.customers[ctx.customer_id].phone if ctx.customer_id else None),
            now=now,
        )
        # Only complaints have a deadline in the manual (7.3); nothing else is promised.
        if args.category == "reclamacao":
            reply = "a equipe retorna em até 24 horas úteis (política 7.3)"
        else:
            reply = "a equipe vai analisar e retornar pelo contato informado (o manual não define prazo)"
        if not is_open(now):
            opening = next_opening(now)
            reply += (
                f"; a loja está fechada agora e a equipe volta {describe_day(opening)}, às {opening:%H:%M}"
            )
        return {
            "protocol": protocol,
            "already_registered": existing is not None,
            "category": args.category,
            "next_steps": reply,
            "context": "o chamado leva o histórico completo desta conversa, inclusive o que o cliente "
            "disser depois de registrado",
        }
