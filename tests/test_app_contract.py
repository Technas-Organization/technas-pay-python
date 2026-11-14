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

    return pay.app_routes(authorize=authorize, resolve_order=resolve, return_url="https://app.test/retour", webhooks=webhooks)


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


def test_flask_blueprint():
    pytest.importorskip("flask")
    from flask import Flask

    app = Flask(__name__)
    routes = _sync_pay(FIRST).app_routes(authorize=lambda req: "u1", resolve_order=lambda oid, u: FIRST["orders"].get(oid))
    app.register_blueprint(routes.flask_blueprint())
    r = app.test_client().post("/api/technas-pay/payments", json={"orderId": "42"})
    assert r.status_code == 200 and r.get_json() == FIRST["expect"]["body"]
    assert app.test_client().post("/api/technas-pay/payments", json={}).status_code == 400


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


urlpatterns = []
assert sys  # garde l'import (django lit ROOT_URLCONF sur ce module)
