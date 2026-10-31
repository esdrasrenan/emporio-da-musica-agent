from fastapi.testclient import TestClient

from emporio.agent import Agent
from emporio.api import create_app
from emporio.cli import main
from tests.fakes import FakeChatModel


def test_api_chat_roundtrip(toolbox, storage, data, clock):
    model = FakeChatModel(
        [
            FakeChatModel.calls("search_products", query="Takamine GD20"),
            FakeChatModel.says("Custa R$ 2.199,00."),
            FakeChatModel.says("De nada!"),
        ]
    )
    with TestClient(create_app(Agent(model, toolbox, storage, data, clock))) as client:
        assert client.get("/healthz").json() == {"status": "ok", "model": "fake:fake-model"}

        first = client.post("/v1/chat", json={"message": "Quanto custa o Takamine GD20?"}).json()
        assert first["reply"] == "Custa R$ 2.199,00."
        assert first["tool_calls"][0]["name"] == "search_products"

        second = client.post("/v1/chat", json={"message": "Valeu", "session_id": first["session_id"]})
        assert second.json()["reply"] == "De nada!"

        assert client.post("/v1/chat", json={"message": "Oi", "session_id": "nope"}).status_code == 404
        assert client.post("/v1/chat", json={"message": ""}).status_code == 422


def test_cli_data_report(capsys):
    main(["data-report"])
    out = capsys.readouterr().out
    assert out.startswith("| Table | Record | Issue |")
    assert "Music Man Bass 1X" in out
