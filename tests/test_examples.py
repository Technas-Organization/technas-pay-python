"""Les exemples de frameworks tournent vraiment : un webhook de test signé arrive sur la route."""

import importlib
import sys

import pytest

from .fake_gateway import SECRET


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setenv("TECHNAS_PAY_SECRET", SECRET)


def _fresh(name):
    sys.modules.pop(name, None)
    return importlib.import_module(name)


def test_fastapi():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    mod = _fresh("examples.fastapi_app")
    client = TestClient(mod.app)
    hook = mod.pay.create_test_webhook("payment.completed", reference="42-1", amount=5000, currency="XOF")
    assert client.post("/api/technas-pay", content=hook.body, headers=hook.headers).status_code == 200
    assert mod.ORDERS["42"]["paid"] is True
    expired = mod.pay.create_test_webhook("payment.expired", reference="42-1", amount=5000, currency="XOF")
    client.post("/api/technas-pay", content=expired.body, headers=expired.headers)
    assert mod.ORDERS["42"]["attempt"] == 2
    bad = client.post("/api/technas-pay", content=hook.body, headers={"X-Technas-Payment-Signature": "t=1,s=00"})
    assert bad.status_code == 400


def test_flask():
    pytest.importorskip("flask")
    mod = _fresh("examples.flask_app")
    client = mod.app.test_client()
    hook = mod.pay.create_test_webhook("payment.completed", reference="42-1", amount=5000, currency="XOF")
    assert client.post("/api/technas-pay", data=hook.body, headers=hook.headers).status_code == 200
    assert mod.ORDERS["42"]["paid"] is True
    assert client.post("/api/technas-pay", data=hook.body).status_code == 400


def test_django():
    pytest.importorskip("django")
    from django.conf import settings

    if not settings.configured:
        settings.configure(DEBUG=True, SECRET_KEY="test", ROOT_URLCONF=__name__, ALLOWED_HOSTS=["*"])
        import django

        django.setup()
    from django.test import RequestFactory

    mod = _fresh("examples.django_app.views")
    hook = mod.pay.create_test_webhook("payment.completed", reference="42-1", amount=5000, currency="XOF")
    factory = RequestFactory()
    req = factory.post("/api/technas-pay", data=hook.body, content_type="application/json",
                       headers={"X-Technas-Payment-Signature": hook.headers["X-Technas-Payment-Signature"]})
    assert mod.technas_webhook(req).status_code == 200 and "42-1" in mod.PAID
    bad = factory.post("/api/technas-pay", data=hook.body, content_type="application/json")
    assert mod.technas_webhook(bad).status_code == 400


urlpatterns = []
