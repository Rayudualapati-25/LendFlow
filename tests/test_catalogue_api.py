from fastapi.testclient import TestClient


def test_products_are_public_and_list_their_terms(client: TestClient) -> None:
    response = client.get("/products")

    assert response.status_code == 200
    personal = next(item for item in response.json() if item["product"] == "personal")
    assert personal == {
        "product": "personal",
        "annual_rate_pct": "14.00",
        "min_amount": "10000.00",
        "max_amount": "2500000.00",
        "min_tenure_months": 6,
        "max_tenure_months": 60,
    }
    assert [item["product"] for item in response.json()] == ["personal", "vehicle", "home"]
