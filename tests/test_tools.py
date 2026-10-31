import json

from emporio.llm.base import ToolCall


def run(toolbox, ctx, name, **arguments):
    result = toolbox.execute(ToolCall("call-1", name, arguments), ctx)
    return (json.loads(result.content) if not result.is_error else result.content), result.is_error


def walk(node):
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def test_schemas_are_compact_and_strict(toolbox):
    assert [s.name for s in toolbox.specs] == [
        "search_products",
        "quote_purchase",
        "consult_policy",
        "get_order_status",
        "create_support_ticket",
    ]
    for spec in toolbox.specs:
        for node in walk(spec.input_schema):
            if isinstance(node, dict):
                assert "title" not in node and "anyOf" not in node
                if node.get("type") == "object":
                    assert node["additionalProperties"] is False
    search = toolbox.specs[0].input_schema["properties"]
    assert "violoes" in search["category"]["enum"]
    sections = toolbox.specs[2].input_schema["properties"]["sections"]["items"]["enum"]
    assert "politica-de-trocas-e-devolucoes" in sections
    assert "atendimento-via-whatsapp" not in sections  # staff playbook stays internal


def test_invalid_arguments_come_back_as_errors(toolbox, ctx):
    output, is_error = run(toolbox, ctx, "search_products", max_price="barato")
    assert is_error and "max_price" in output
    output, is_error = run(toolbox, ctx, "search_products", unknown_field=1)
    assert is_error
    _, is_error = run(toolbox, ctx, "does_not_exist")
    assert is_error


def test_search_tool_output(toolbox, ctx):
    output, is_error = run(toolbox, ctx, "search_products", query="Takamine GD20")
    assert not is_error
    product = output["products"][0]
    assert (product["name"], product["list_price"], product["pix_price"]) == (
        "Takamine GD20 Dreadnought Natural",
        "R$ 2.199,00",
        "R$ 2.089,05",
    )


def test_consult_policy_returns_sections_and_rejects_internal_ones(toolbox, ctx):
    output, is_error = run(toolbox, ctx, "consult_policy", sections=["formas-de-pagamento"])
    assert not is_error and output["sections"][0]["section"] == "3. Formas de Pagamento"
    _, is_error = run(toolbox, ctx, "consult_policy", sections=["atendimento-via-whatsapp"])
    assert is_error


def test_order_lookup_counts_failed_verifications(toolbox, ctx):
    for _ in range(3):
        output, _ = run(toolbox, ctx, "get_order_status", order_id=8, contact="x@y.com")
        assert not output["found"]
    output, _ = run(toolbox, ctx, "get_order_status", order_id=8, contact="anacarol.ferreira@coldmail.com")
    assert not output["found"] and "Limite" in output["message"]


def test_ticket_needs_a_contact_unless_identified(toolbox, ctx, storage):
    output, is_error = run(
        toolbox, ctx, "create_support_ticket", category="reclamacao", summary="Pedido atrasado"
    )
    assert is_error and "telefone ou e-mail" in output
    output, is_error = run(
        toolbox,
        ctx,
        "create_support_ticket",
        category="reclamacao",
        summary="Pedido 8 atrasado",
        order_id=8,
        contact="(67) 99654-7821",
    )
    assert not is_error
    assert output["protocol"] == "EM-20260324-0001"
    assert "24 horas úteis" in output["next_steps"]
    assert storage.tickets()[0]["order_id"] == 8
