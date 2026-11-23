"""Rejoue contract/app-contract.v1.json (le même fichier que le client Flutter et les SDK Node et
Dart), avec le client sync et le client async, puis les adaptateurs FastAPI, Flask et Django."""

import json
import pathlib
import sys

import httpx
import pytest

from technas_pay import AppRequest, AsyncTechnasPay, TechnasPay, WebhookHandlers, sign

CONTRACT = json.loads((pathlib.Path(__file__).parent.parent / "contract" / "app-contract.v1.json").read_text())
WEBHOOK_SECRET = "ws_secret"
SECRET = f"tps_test_pk_abc.{WEBHOOK_SECRET}"
BASE = "https://gw.test"


class Gateway:
    """Rend les réponses du cas, dans l'ordre, et note les appels."""

    def __init__(self, responses):
        self.queue = list(responses)
        self.calls = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append({"method": request.method, "path": request.url.path,
                           "query": dict(request.url.params),
                           "body": json.loads(request.content) if request.content else None})
        assert self.queue, "appel imprévu à la passerelle"
        nxt = self.queue.pop(0)
        if nxt["body"] is None:
            return httpx.Response(nxt["status"], text="<html>panne</html>")
        return httpx.Response(nxt["status"], json=nxt["body"])


def _routes(case, pay, webhooks=None):
    async def authorize(req):  # async exprès : les crochets peuvent être sync ou async
        return case["user"]

    def resolve(order_id, user):
        return case["orders"].get(order_id) if user == case["user"] else None

    extra = dict(case.get("options") or {})
    if "admin" in case:
        extra["authorize_admin"] = lambda req: case["admin"]
    return pay.app_routes(authorize=authorize, resolve_order=resolve, return_url="https://app.test/retour", webhooks=webhooks, **extra)


def _request(case):
    r = case["request"]
    return AppRequest(method=r["method"], path=r["path"], query=r.get("query") or {},
                      raw_body=b"" if "body" not in r else json.dumps(r["body"]).encode())


def _check(case, response, gw):
    assert response.status == case["expect"]["status"]
    body = dict(response.body) if isinstance(response.body, dict) else response.body
    if response.status >= 400:
        assert isinstance(body.pop("message"), str)
    assert body == case["expect"]["body"]
    assert len(gw.calls) == len(case["expect_gateway"])
    for call, exp in zip(gw.calls, case["expect_gateway"]):
        assert (call["method"], call["path"]) == (exp["method"], exp["path"])
        for k, v in (exp.get("body_includes") or {}).items():
            assert call["body"][k] == v, k
        for k, v in (exp.get("query_includes") or {}).items():
            assert call["query"].get(k) == v, k


def test_le_contrat_a_32_cas():
    assert len(CONTRACT["cases"]) == 32


@pytest.mark.parametrize("case", CONTRACT["cases"], ids=[c["name"] for c in CONTRACT["cases"]])
async def test_contrat_client_async(case):
    gw = Gateway(case["gateway"])
    pay = AsyncTechnasPay(SECRET, base_url=BASE, http_client=httpx.AsyncClient(transport=httpx.MockTransport(gw.handler)))
    _check(case, await _routes(case, pay).handle(_request(case)), gw)


@pytest.mark.parametrize("case", CONTRACT["cases"], ids=[c["name"] for c in CONTRACT["cases"]])
def test_contrat_client_sync(case):
    gw = Gateway(case["gateway"])
    pay = TechnasPay(SECRET, base_url=BASE, http_client=httpx.Client(transport=httpx.MockTransport(gw.handler)))
    _check(case, _routes(case, pay).handle_sync(_request(case)), gw)


def test_webhook_signe_et_refuse():
    paid = []
    routes = _routes({"user": "u1", "orders": {}}, TechnasPay(SECRET),
                     WebhookHandlers(on_completed=lambda e: paid.append(e.payment.reference)))
    body = json.dumps({"id": "e1", "type": "payment.completed", "created_at": "x",
                       "data": {"id": "p", "reference": "42-1", "status": "completed", "amount": 1, "currency": "XOF"}})
    ok = routes.handle_sync(AppRequest("POST", "/webhook", headers={"x-technas-payment-signature": sign(body, WEBHOOK_SECRET)}, raw_body=body.encode()))
    assert ok.status == 200 and paid == ["42-1"]
    bad = routes.handle_sync(AppRequest("POST", "/webhook", headers={"x-technas-payment-signature": "t=1,s=00"}, raw_body=body.encode()))
    assert bad.status == 400
    sans = _routes({"user": "u1", "orders": {}}, TechnasPay(SECRET)).handle_sync(AppRequest("POST", "/webhook", raw_body=body.encode()))
    assert sans.status == 404


FIRST = CONTRACT["cases"][0]
CANCEL = next(c for c in CONTRACT["cases"] if c["request"]["path"] == "/payments/pay_1/cancel" and c["user"])


def _async_pay(case):
    gw = Gateway(case["gateway"])
    return AsyncTechnasPay(SECRET, base_url=BASE, http_client=httpx.AsyncClient(transport=httpx.MockTransport(gw.handler)))


def _sync_pay(case):
    gw = Gateway(case["gateway"])
    return TechnasPay(SECRET, base_url=BASE, http_client=httpx.Client(transport=httpx.MockTransport(gw.handler)))


def test_fastapi_router():
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    seen = {}
    app = FastAPI()

    def authorize(req):
        seen["auth"] = req.headers.get("authorization")
        return "u1"

    routes = _async_pay(FIRST).app_routes(authorize=authorize, resolve_order=lambda oid, u: FIRST["orders"].get(oid))
    app.include_router(routes.fastapi_router())
    r = TestClient(app).post("/api/technas-pay/payments", json={"orderId": "42"}, headers={"Authorization": "Bearer jeton"})
    assert r.status_code == 200 and r.json() == FIRST["expect"]["body"]
    assert seen["auth"] == "Bearer jeton"
    methods = next(c for c in CONTRACT["cases"] if c["request"]["path"] == "/methods")
    r = TestClient(app).get("/api/technas-pay/methods", params={"orderId": "42"})
    assert r.json() == methods["expect"]["body"]


def _cancel_routes(pay):
    return pay.app_routes(authorize=lambda req: "u1", resolve_order=lambda oid, u: CANCEL["orders"].get(oid))


def test_fastapi_router_annulation():
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(_cancel_routes(_async_pay(CANCEL)).fastapi_router())
    r = TestClient(app).post("/api/technas-pay/payments/pay_1/cancel")
    assert r.status_code == 200 and r.json() == CANCEL["expect"]["body"]


def test_flask_blueprint():
    pytest.importorskip("flask")
    from flask import Flask

    app = Flask(__name__)
    routes = _sync_pay(FIRST).app_routes(authorize=lambda req: "u1", resolve_order=lambda oid, u: FIRST["orders"].get(oid))
    app.register_blueprint(routes.flask_blueprint())
    r = app.test_client().post("/api/technas-pay/payments", json={"orderId": "42"})
    assert r.status_code == 200 and r.get_json() == FIRST["expect"]["body"]
    assert app.test_client().post("/api/technas-pay/payments", json={}).status_code == 400


def test_flask_blueprint_annulation():
    pytest.importorskip("flask")
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(_cancel_routes(_sync_pay(CANCEL)).flask_blueprint())
    r = app.test_client().post("/api/technas-pay/payments/pay_1/cancel", json={})
    assert r.status_code == 200 and r.get_json() == CANCEL["expect"]["body"]


def test_django_view():
    pytest.importorskip("django")
    from django.conf import settings

    if not settings.configured:
        settings.configure(DEBUG=True, SECRET_KEY="test", ROOT_URLCONF=__name__, ALLOWED_HOSTS=["*"])
        import django

        django.setup()
    from django.test import RequestFactory

    view = _sync_pay(FIRST).app_routes(authorize=lambda req: "u1", resolve_order=lambda oid, u: FIRST["orders"].get(oid)).django_view()
    req = RequestFactory().post("/api/technas-pay/payments", data=json.dumps({"orderId": "42"}), content_type="application/json")
    res = view(req, rest="payments")
    assert res.status_code == 200 and json.loads(res.content) == FIRST["expect"]["body"]
    cancel = _cancel_routes(_sync_pay(CANCEL)).django_view()
    res = cancel(RequestFactory().post("/api/technas-pay/payments/pay_1/cancel"), rest="payments/pay_1/cancel")
    assert res.status_code == 200 and json.loads(res.content) == CANCEL["expect"]["body"]


urlpatterns = []
assert sys  # garde l'import (django lit ROOT_URLCONF sur ce module)


@pytest.mark.parametrize("path, query, body, code", [
    ("/payments/..%2Fbalance", None, None, "payment_not_found"),
    ("/payments/..%2Fbalance/cancel", None, None, "payment_not_found"),
    ("/payments/pay%201", None, None, "payment_not_found"),
    ("/payments/a%252Fb", None, None, "payment_not_found"),
    ("/payments/" + "x" * 129, None, None, "payment_not_found"),
    ("/methods", {"orderId": "../42"}, None, "order_not_found"),
    ("/methods", {"orderId": "42 1"}, None, "order_not_found"),
    ("/payments", None, {"orderId": "../balance"}, "order_not_found"),
    ("/payments", None, {"orderId": "a/b"}, "order_not_found"),
])
async def test_identifiant_mal_forme_introuvable_sans_appel(path, query, body, code):
    resolved = []
    gw = Gateway([])

    def resolve(order_id, user):
        resolved.append(order_id)
        return {"amount": 5000}

    method = "GET" if path.startswith("/methods") or (path.startswith("/payments/") and not path.endswith("/cancel")) else "POST"
    request = AppRequest(method=method, path=path, query=query or {},
                         raw_body=json.dumps(body).encode() if body is not None else b"")
    for pay in (AsyncTechnasPay(SECRET, base_url=BASE, http_client=httpx.AsyncClient(transport=httpx.MockTransport(gw.handler))),
                TechnasPay(SECRET, base_url=BASE, http_client=httpx.Client(transport=httpx.MockTransport(gw.handler)))):
        routes = pay.app_routes(authorize=lambda req: "u1", resolve_order=resolve, return_url="https://app.test/retour")
        response = await routes.handle(request)
        assert response.status == 404 and response.body["code"] == code
    assert gw.calls == [] and resolved == []
