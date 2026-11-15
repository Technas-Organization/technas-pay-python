import json
import logging

import httpx
import pytest

from technas_pay import (
    AccountKyc,
    AsyncTechnasPay,
    PayoutDestination,
    TechnasPay,
    TechnasPayError,
    can_retry,
    currency_for_country,
    is_final,
    retry_reference,
    to_app_json,
)

from .fake_gateway import API_KEY, BASE, SECRET, WEBHOOK_SECRET, FakeGateway

CUSTOMER = {"email": "awa@exemple.tg", "name": "Awa Mensah"}


@pytest.fixture
def gw():
    return FakeGateway()


@pytest.fixture
def pay(gw):
    with TechnasPay(SECRET, base_url=BASE + "/", http_client=httpx.Client(transport=gw.transport())) as client:
        yield client


def ask(pay, ref="cmd_1-1", amount=5000, **kw):
    return pay.ask_for_payment(amount=amount, reference=ref, customer=CUSTOMER, return_url="https://p.test/merci", **kw)


def test_seule_la_cle_api_part_et_dans_un_en_tete(pay, gw):
    payment = ask(pay)
    req = gw.requests[-1]
    assert str(req.url) == f"{BASE}/v1/payments"
    assert req.headers["authorization"] == f"Bearer {API_KEY}"
    everything = str(req.url) + json.dumps(dict(req.headers)) + req.content.decode()
    assert WEBHOOK_SECRET not in everything
    assert "tps_test_" not in everything
    body = json.loads(req.content)
    assert body["return_url"] == "https://p.test/merci"
    assert "currency" not in body  # le client choisit son pays
    assert payment.checkout_url and payment.net_amount == 4900


def test_le_secret_n_apparait_dans_aucun_repr(pay):
    assert "tps_" not in repr(pay) and API_KEY not in repr(pay)


def test_la_devise_suit_le_pays_cfa(pay, gw):
    pay.ask_for_payment(amount=5000, reference="r-cm", customer={**CUSTOMER, "country": "cm"}, return_url="https://p.test")
    assert json.loads(gw.requests[-1].content)["currency"] == "XAF"
    pay.ask_for_payment(amount=5000, reference="r-gh", customer={**CUSTOMER, "country": "GH"}, return_url="https://p.test")
    assert "currency" not in json.loads(gw.requests[-1].content)


@pytest.mark.parametrize("amount", [50.5, 0, -1, True, "5000"])
def test_montant_non_entier_refuse_avant_tout_appel(pay, gw, amount):
    with pytest.raises(TechnasPayError) as e:
        ask(pay, amount=amount)
    assert e.value.code == "invalid_amount" and e.value.status == 0
    assert gw.requests == []


def test_une_reference_un_essai(pay):
    first = ask(pay)
    assert ask(pay).id == first.id  # rejouer rend le même essai
    with pytest.raises(TechnasPayError) as e:
        ask(pay, amount=9000)
    assert e.value.code == "reference_conflict" and not e.value.retryable
    failed = pay.simulate_test_payment(first.id, "failed")
    assert failed.status == "failed" and not is_final(failed) and not can_retry(failed)
    expired = pay.simulate_test_payment(first.id, "expired")
    assert is_final(expired) and can_retry(expired) and expired.checkout_url is None
    with pytest.raises(TechnasPayError) as e:
        ask(pay)
    assert e.value.code == "payment_expired" and e.value.status == 409 and not e.value.retryable
    assert ask(pay, ref=retry_reference("cmd_1", 2)).id != first.id


def test_retry_reference():
    assert retry_reference("CMD-42", 2) == "CMD-42-2"
    with pytest.raises(ValueError):
        retry_reference("CMD-42", 0)


def test_paiement_complet_puis_remboursements(pay):
    p = ask(pay, amount=10000)
    with pytest.raises(TechnasPayError) as e:
        pay.refund(p.id)
    assert e.value.code == "payment_not_refundable"
    done = pay.simulate_test_payment(p.id, "completed")  # 303 vers returnUrl : non suivi
    assert done.status == "completed"
    assert pay.get_payment_by_reference(p.reference).id == p.id
    with pytest.raises(TechnasPayError) as e:
        pay.refund(p.id)  # 10 000 + 100 de frais > solde de 9 800
    assert e.value.code == "insufficient_balance" and e.value.details.missing_amount == 300
    part = pay.refund(p.id, amount=3000, fees="customer", reference="decision-1")
    assert part.sent_amount == 2900 and part.kind == "payout"
    ext = pay.record_external_refund(p.id, amount=2000, method=" Flooz ", note="au guichet")
    assert ext.kind == "external" and ext.method == "Flooz"
    with pytest.raises(TechnasPayError) as e:
        pay.refund(p.id, amount=6000)
    assert e.value.code == "refund_exceeds_payment" and e.value.details.refundable_amount == 5000
    assert pay.get_refund(part.id).id == part.id
    assert pay.get_payment(p.id).refunded_amount == 5000


def test_validations_locales(pay, gw):
    with pytest.raises(TechnasPayError, match="fees"):
        pay.refund("p", fees="moi")
    with pytest.raises(TechnasPayError) as e:
        pay.record_external_refund("p", amount=100, method="  ")
    assert e.value.code == "invalid_request"
    assert gw.requests == []


def test_change_hors_zone(pay):
    fx = pay.get_fx_mode()
    assert fx.mode == "none" and fx.available_modes == ["none", "local"]
    assert fx.countries[0].reason == "no_payout_number" and fx.risks["local"]
    with pytest.raises(TechnasPayError) as e:
        pay.set_fx_mode(mode="local")
    assert e.value.code == "risk_not_accepted" and e.value.details.risk == "Vous portez le change."
    with pytest.raises(TechnasPayError) as e:
        pay.set_fx_mode(mode="converted", accept_risk=True)
    assert e.value.code == "fx_mode_unavailable"
    assert pay.set_fx_mode(mode="local", accept_risk=True).mode == "local"


def test_le_reste_de_la_surface(pay, gw):
    ask(pay, ref="a-1")
    ask(pay, ref="b-1")
    page = pay.list_transactions(limit=1, status="pending")
    assert len(page.items) == 1 and page.next_cursor == "suite"
    assert gw.requests[-1].url.query.decode() == "limit=1&status=pending"
    assert pay.get_balance()[0].currency == "XOF"
    assert pay.get_settlements()[0].fee == 150
    assert pay.get_payout_networks("TG")[0].code == "moov_tg"
    state = pay.set_payout_destination(PayoutDestination("TG", "moov_tg", "+22890000000", "Blexima"))
    assert state.destinations[0].pending.status == "awaiting_confirmation"
    assert json.loads(gw.requests[-1].content)["beneficiary_name"] == "Blexima"
    assert pay.get_payout_destination().destinations[0].current is None
    top = pay.top_up_balance(amount=20000, currency="XOF")
    assert top.checkout_url.endswith("/top_1")
    rules = pay.set_country_rules(excluded=["NG"])
    assert rules.allowed is None and rules.excluded == ["NG"]


def test_identifiants_echappes_dans_le_chemin(pay, gw):
    with pytest.raises(TechnasPayError):
        pay.get_payment("../balance")
    assert gw.requests[-1].url.raw_path == b"/v1/payments/..%2Fbalance"


def test_erreurs_reseau_et_serveur_rejouables(gw):
    def boom(request):
        raise httpx.ConnectError("réseau")

    down = TechnasPay(SECRET, base_url=BASE, http_client=httpx.Client(transport=httpx.MockTransport(boom)))
    with pytest.raises(TechnasPayError) as e:
        down.get_balance()
    assert e.value.code == "network_error" and e.value.retryable
    gw.fail_next = httpx.Response(503, text="<html>panne</html>")
    pay = TechnasPay(SECRET, base_url=BASE, http_client=httpx.Client(transport=gw.transport()))
    with pytest.raises(TechnasPayError) as e:
        pay.get_balance()
    assert e.value.code == "request_failed" and e.value.status == 503 and e.value.retryable


def test_secret_mal_forme_ne_casse_pas_l_import_et_ne_fuit_pas(monkeypatch):
    monkeypatch.delenv("TECHNAS_PAY_SECRET", raising=False)
    pay = TechnasPay("mon-secret-en-clair")
    with pytest.raises(TechnasPayError) as e:
        pay.get_balance()
    assert e.value.code == "invalid_secret" and "mon-secret-en-clair" not in str(e.value)
    with pytest.raises(TechnasPayError):
        TechnasPay().get_balance()


def test_secret_lu_dans_l_environnement(monkeypatch, gw):
    monkeypatch.setenv("TECHNAS_PAY_SECRET", SECRET)
    pay = TechnasPay(base_url=BASE, http_client=httpx.Client(transport=gw.transport()))
    assert pay.test_mode
    pay.get_balance()


def test_simulation_refusee_en_reel(gw):
    live = TechnasPay(SECRET.replace("tps_test_", "tps_live_"), base_url=BASE, http_client=httpx.Client(transport=gw.transport()))
    with pytest.raises(TechnasPayError) as e:
        live.simulate_test_payment("p", "completed")
    assert e.value.code == "test_only"


def test_aucun_secret_dans_les_journaux(pay, caplog):
    caplog.set_level(logging.DEBUG)
    ask(pay)
    assert API_KEY not in caplog.text and WEBHOOK_SECRET not in caplog.text


def test_currency_for_country():
    assert currency_for_country(" tg ") == "XOF" and currency_for_country("GH") is None and currency_for_country(None) is None


async def test_client_async_meme_surface(gw):
    async with AsyncTechnasPay(SECRET, base_url=BASE, http_client=httpx.AsyncClient(transport=gw.async_transport())) as pay:
        p = await pay.ask_for_payment(amount=5000, reference="x-1", customer=CUSTOMER, return_url="https://p.test")
        assert (await pay.simulate_test_payment(p.id, "completed")).status == "completed"
        refund = await pay.refund(p.id, amount=1000)
        assert refund.balance_debit == 1100
        assert (await pay.get_fx_mode()).mode == "none"


def test_les_deux_clients_ont_la_meme_surface():
    public = lambda cls: {n for n in dir(cls) if not n.startswith("_")} - {"close", "aclose"}
    assert public(TechnasPay) == public(AsyncTechnasPay)


def test_to_app_json_ne_rend_que_le_necessaire(pay):
    j = to_app_json(ask(pay))
    assert set(j) == {"id", "reference", "status", "checkout_url", "amount", "currency", "expires_at", "method"}


def test_carte_decimales_devise_et_method(gw):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content) if request.content else None)
        if request.url.path == "/v1/payments":
            return httpx.Response(201, json={"id": "c1", "reference": "r-card-1", "amount": 12.5, "currency": "EUR", "commission": 0.25,
                                             "net_amount": 12.25, "status": "pending", "method": "card", "checkout_url": "https://x"})
        if request.url.path == "/v1/card/account":
            return httpx.Response(200, json={"status": "enabled", "can_accept_payments": True})
        if request.url.path == "/v1/card/dashboard-link":
            return httpx.Response(200, json={"url": "https://dashboard.stripe.com/"})
        return httpx.Response(409, json={"code": "card_account_not_ready", "message": "m", "account_status": "onboarding"})

    pay = TechnasPay(SECRET, base_url=BASE, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(TechnasPayError) as e:
        pay.ask_for_payment(amount=12.5, reference="r", customer=CUSTOMER, return_url="https://p.test", method="card")
    assert e.value.code == "currency_required"
    with pytest.raises(TechnasPayError) as e:
        ask(pay, amount=12.5)
    assert e.value.code == "invalid_amount"
    p = pay.ask_for_payment(amount=12.5, reference="r-card-1", customer=CUSTOMER, return_url="https://p.test", method="card", currency="eur")
    assert p.method == "card" and calls[-1]["method"] == "card" and calls[-1]["currency"] == "EUR"
    assert pay.get_card_account().can_accept_payments
    assert pay.get_card_dashboard_link() == "https://dashboard.stripe.com/"
    with pytest.raises(TechnasPayError) as e:
        pay.start_card_onboarding(return_url="https://a", refresh_url="https://b", country="fr")
    assert e.value.code == "card_account_not_ready" and e.value.details.account_status == "onboarding"
    assert calls[-1]["country"] == "FR"


@pytest.mark.parametrize("received, expected", [("stripe", "card"), ("card", "card"),
                                                ("payout", "payout"), ("external", "external"), (None, "payout")])
def test_refund_kind_stripe_received_is_read_as_card(received, expected):
    from technas_pay.models import Refund
    assert Refund.from_api({"id": "r1", "kind": received}).kind == expected


KYC_FULL = {
    "applies": True, "stage": "soon", "level": "none", "days_elapsed": 23, "warn_from_xof": 800000,
    "warn_from_date": "2026-11-20T00:00:00Z", "collected_xof": 812500, "threshold_xof": 1000000, "grace_days": 30,
    "first_collected_at": "2026-10-23T09:00:00Z", "deadline": "2026-11-22T09:00:00Z", "waiver_until": "2026-12-01T00:00:00Z",
    "blocked": False, "reason": None, "console_url": "https://console.technas.test/identite",
}
KYC_NULLS = {
    "applies": False, "stage": "not_applicable", "level": "none", "days_elapsed": None, "warn_from_xof": 800000,
    "warn_from_date": None, "collected_xof": 0, "threshold_xof": 1000000, "grace_days": 30, "first_collected_at": None,
    "deadline": None, "waiver_until": None, "blocked": False, "reason": None, "console_url": "https://console.technas.test/identite",
}


def _kyc_client(body, seen):
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=body)

    return TechnasPay(SECRET, base_url=BASE, http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_get_account_kyc_route_en_tete_et_tous_les_champs():
    seen = []
    kyc = _kyc_client(KYC_FULL, seen).get_account_kyc()
    assert isinstance(kyc, AccountKyc)
    assert seen[0].method == "GET" and seen[0].url.path == "/v1/account/kyc"
    assert seen[0].headers["authorization"] == f"Bearer {API_KEY}" and not seen[0].content
    assert kyc == AccountKyc(**KYC_FULL)


def test_get_account_kyc_champs_nuls():
    kyc = _kyc_client(KYC_NULLS, []).get_account_kyc()
    assert kyc == AccountKyc(**KYC_NULLS)
    assert kyc.days_elapsed is None and kyc.deadline is None and kyc.first_collected_at is None


def test_get_account_kyc_valeurs_inconnues_gardees():
    body = dict(KYC_FULL, stage="paused", level="enhanced", reason="volume", blocked=True)
    kyc = _kyc_client(body, []).get_account_kyc()
    assert (kyc.stage, kyc.level, kyc.reason, kyc.blocked) == ("paused", "enhanced", "volume", True)


async def test_get_account_kyc_async():
    async def handler(request):
        assert request.url.path == "/v1/account/kyc" and request.headers["authorization"] == f"Bearer {API_KEY}"
        return httpx.Response(200, json=KYC_FULL)

    async with AsyncTechnasPay(SECRET, base_url=BASE, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))) as pay:
        assert (await pay.get_account_kyc()).threshold_xof == 1000000


def test_erreur_kyc_required_et_contract_required():
    replies = [
        httpx.Response(409, json={"code": "kyc_required", "message": "m", "reason": "amount",
                                  "console_url": "https://console.technas.test/identite"}),
        httpx.Response(409, json={"code": "contract_required", "message": "m", "version": "2026-11",
                                  "console_url": "https://console.technas.test/contrat"}),
    ]
    pay = TechnasPay(SECRET, base_url=BASE, http_client=httpx.Client(transport=httpx.MockTransport(lambda r: replies.pop(0))))
    with pytest.raises(TechnasPayError) as e:
        ask(pay)
    assert e.value.code == "kyc_required" and e.value.status == 409 and not e.value.retryable
    assert e.value.details.console_url == "https://console.technas.test/identite" and e.value.details.reason == "amount"
    with pytest.raises(TechnasPayError) as e:
        ask(pay)
    assert e.value.code == "contract_required" and not e.value.retryable
    assert e.value.details.version == "2026-11" and e.value.details.console_url == "https://console.technas.test/contrat"
