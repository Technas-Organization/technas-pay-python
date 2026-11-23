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


def test_le_client_change_d_offre_ou_revient_plus_tard(pay, gw):
    """Le client choisit une offre, ne paie pas, revient plus tard choisir une AUTRE offre : un
    nouvel essai, jamais un refus. Réutiliser la référence avec un autre montant est un conflit."""
    first = ask(pay, ref=retry_reference("F", 1), amount=5000)
    pay.simulate_test_payment(first.id, "expired")
    assert pay.get_payment(first.id).status == "expired"
    second = ask(pay, ref=retry_reference("F", 2), amount=7500)
    assert second.id != first.id and second.status == "pending"
    sent = [json.loads(r.content) for r in gw.requests if r.method == "POST" and r.url.path == "/v1/payments"]
    assert [(b["reference"], b["amount"]) for b in sent] == [("F-1", 5000), ("F-2", 7500)]
    with pytest.raises(TechnasPayError) as e:
        ask(pay, ref="F-1", amount=7500)
    assert e.value.code == "reference_conflict" and e.value.status == 409 and not e.value.retryable


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
    state = pay.set_payout_destination(PayoutDestination("TG", "moov_tg", "+22890000000", "Boutique Awa"))
    assert state.destinations[0].pending.status == "awaiting_confirmation"
    assert json.loads(gw.requests[-1].content)["beneficiary_name"] == "Boutique Awa"
    assert pay.get_payout_destination().destinations[0].current is None
    top = pay.top_up_balance(amount=20000, currency="XOF")
    assert top.checkout_url.endswith("/top_1")
    rules = pay.set_country_rules(excluded=["NG"])
    assert rules.allowed is None and rules.excluded == ["NG"]


IDENTIFIANTS_INVALIDES = ["../balance", "..", ".", "a/b", "a%2Fb", "", "pay 1", "pay_1\n", "é", "x" * 129, None, 42]

# Toutes les méthodes qui mettent un identifiant dans un chemin d'URL.
APPELS_AVEC_IDENTIFIANT = {
    "get_payment": lambda c, i: c.get_payment(i),
    "cancel_payment": lambda c, i: c.cancel_payment(i),
    "refund": lambda c, i: c.refund(i, amount=100),
    "record_external_refund": lambda c, i: c.record_external_refund(i, amount=100, method="Espèces"),
    "get_refund": lambda c, i: c.get_refund(i),
    "simulate_test_payment": lambda c, i: c.simulate_test_payment(i, "completed"),
}


@pytest.mark.parametrize("appel", APPELS_AVEC_IDENTIFIANT, ids=list(APPELS_AVEC_IDENTIFIANT))
@pytest.mark.parametrize("identifiant", IDENTIFIANTS_INVALIDES, ids=repr)
def test_identifiant_mal_forme_refuse_avant_tout_appel(pay, gw, appel, identifiant):
    with pytest.raises(TechnasPayError) as e:
        APPELS_AVEC_IDENTIFIANT[appel](pay, identifiant)
    assert e.value.code == "invalid_request" and e.value.status == 0 and not e.value.retryable
    if isinstance(identifiant, str) and identifiant:
        assert identifiant not in e.value.message
    assert gw.requests == []


@pytest.mark.parametrize("appel", APPELS_AVEC_IDENTIFIANT, ids=list(APPELS_AVEC_IDENTIFIANT))
async def test_identifiant_mal_forme_refuse_par_le_client_async(appel):
    calls = []
    transport = httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(500))
    async with AsyncTechnasPay(SECRET, base_url=BASE, http_client=httpx.AsyncClient(transport=transport)) as client:
        for identifiant in IDENTIFIANTS_INVALIDES:
            with pytest.raises(TechnasPayError) as e:
                await APPELS_AVEC_IDENTIFIANT[appel](client, identifiant)
            assert e.value.code == "invalid_request" and e.value.status == 0
    assert calls == []


@pytest.mark.parametrize("identifiant", ["pay_1", "3f2b8c1e-9a4d-4e2b-8f61-0c7d5e9a1b23", "x" * 128])
def test_identifiant_valide_part_tel_quel(identifiant):
    seen = []

    def handler(request):
        seen.append(request.url.raw_path)
        return httpx.Response(404, json={"code": "payment_not_found", "message": "introuvable"})

    with TechnasPay(SECRET, base_url=BASE, http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
        for appel in (client.get_payment, client.cancel_payment, client.get_refund):
            with pytest.raises(TechnasPayError) as e:
                appel(identifiant)
            assert e.value.code == "payment_not_found"
    assert seen == [f"/v1/payments/{identifiant}".encode(), f"/v1/payments/{identifiant}/cancel".encode(),
                    f"/v1/refunds/{identifiant}".encode()]


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


ANNULE = {
    "id": "pay_1", "reference": "F-1", "amount": 5000, "currency": None, "commission": 100, "net_amount": 4900,
    "status": "expired", "checkout_url": None, "metadata": {}, "test_mode": True,
    "created_at": "2026-11-15T10:00:00Z", "expires_at": "2026-11-15T11:00:00Z",
    "canceled_at": "2026-11-16T09:00:00Z", "cancel_reason": "canceled_by_partner",
}


def _sequence(*replies):
    calls = []
    queue = list(replies)

    def handler(request):
        calls.append(request)
        status, body = queue.pop(0)
        return httpx.Response(status, json=body)

    return calls, handler


def test_annuler_l_ancien_essai_quand_le_client_change_d_offre():
    calls, handler = _sequence(
        (200, ANNULE), (200, ANNULE),
        (409, {"code": "payment_in_progress", "message": "m"}),
        (409, {"code": "payment_already_completed", "message": "m"}),
    )
    pay = TechnasPay(SECRET, base_url=BASE, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    p = pay.cancel_payment("pay_1")
    assert calls[0].method == "POST" and str(calls[0].url) == f"{BASE}/v1/payments/pay_1/cancel"
    assert calls[0].headers["authorization"] == f"Bearer {API_KEY}"
    assert p.status == "expired" and p.checkout_url is None
    assert p.canceled_at == "2026-11-16T09:00:00Z" and p.cancel_reason == "canceled_by_partner"
    assert can_retry(p)  # un paiement annulé reste expired
    assert pay.cancel_payment("pay_1").canceled_at == "2026-11-16T09:00:00Z"  # rejouer rend le même résultat
    with pytest.raises(TechnasPayError) as en_cours:
        pay.cancel_payment("pay_1")
    assert en_cours.value.code == "payment_in_progress" and en_cours.value.status == 409 and en_cours.value.retryable
    with pytest.raises(TechnasPayError) as paye:
        pay.cancel_payment("pay_1")
    assert paye.value.code == "payment_already_completed" and paye.value.status == 409 and not paye.value.retryable
    for invalide in ("", None):
        with pytest.raises(TechnasPayError) as e:
            pay.cancel_payment(invalide)
        assert e.value.code == "invalid_request" and e.value.status == 0
    assert len(calls) == 4


async def test_annuler_avec_le_client_async():
    calls, handler = _sequence((200, ANNULE))
    async with AsyncTechnasPay(SECRET, base_url=BASE, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))) as pay:
        p = await pay.cancel_payment("pay_1")
    assert calls[0].method == "POST" and calls[0].url.path == "/v1/payments/pay_1/cancel"
    assert p.cancel_reason == "canceled_by_partner"


PAYE = {**ANNULE, "status": "completed", "checkout_url": None, "canceled_at": None, "cancel_reason": None}


def test_frais_au_client_fees_envoye_seulement_s_il_est_donne_et_lu_avec_ses_defauts():
    calls, handler = _sequence(
        (201, {**PAYE, "id": "pay_2", "status": "pending"}),
        (201, {**PAYE, "id": "pay_3", "status": "pending"}),
        (200, {**PAYE, "amount": 200, "customer_amount": 205, "net_amount": 200, "commission": 5,
               "fees_paid_by": "customer", "customer_total": 214, "processing_fee": 9}),
    )
    pay = TechnasPay(SECRET, base_url=BASE, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    p = ask(pay, fees="customer")
    assert json.loads(calls[0].content)["fees"] == "customer"
    # Serveur plus ancien : customer_amount absent → amount, fees_paid_by absent → partner, le reste None.
    assert (p.customer_amount, p.fees_paid_by, p.customer_total, p.processing_fee) == (p.amount, "partner", None, None)
    ask(pay, ref="cmd_1-2")
    assert "fees" not in json.loads(calls[1].content)
    lu = pay.get_payment("pay_1")
    assert (lu.amount, lu.customer_amount, lu.net_amount, lu.fees_paid_by, lu.customer_total, lu.processing_fee) == (
        200, 205, 200, "customer", 214, 9)
    with pytest.raises(TechnasPayError) as e:
        ask(pay, fees="client")
    assert e.value.code == "invalid_request" and e.value.status == 0
    assert len(calls) == 3


DEVIS = {
    "method": "mobile_money", "currency": "XOF", "country": "TG", "fees_paid_by": "customer",
    "amount": 200, "customer_amount": 205, "customer_total": 214, "processing_fee": 9, "commission": 5, "net_amount": 200,
    "networks": [{"network": "togocel", "network_name": "Mixx by Yas", "customer_total": 214, "processing_fee": 9,
                  "commission": 5, "net_amount": 200, "operator_fee_estimate": 10, "otp_required": False,
                  "otp_instructions": None}],
}


def test_devis_quote_et_get_fees_envoient_le_montant_le_pays_et_le_mode_et_lisent_chaque_reseau():
    calls, handler = _sequence((200, DEVIS), (200, DEVIS), (200, {
        "method": "card", "currency": "EUR", "country": None, "fees_paid_by": "partner", "amount": 20,
        "customer_amount": 20, "customer_total": 20, "processing_fee": None, "commission": 0.2, "net_amount": None,
        "networks": [],
    }))
    pay = TechnasPay(SECRET, base_url=BASE, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    q = pay.quote(amount=200, country="tg", fees="customer")
    assert calls[0].method == "POST" and str(calls[0].url) == f"{BASE}/v1/quotes"
    assert calls[0].headers["authorization"] == f"Bearer {API_KEY}"
    assert json.loads(calls[0].content) == {"amount": 200, "country": "TG", "fees": "customer"}
    assert (q.customer_total, q.processing_fee, q.commission, q.net_amount, q.fees_paid_by, q.customer_amount) == (
        214, 9, 5, 200, "customer", 205)
    n = q.networks[0]
    assert (n.network, n.network_name, n.customer_total, n.processing_fee, n.commission, n.net_amount,
            n.operator_fee_estimate, n.otp_required, n.otp_instructions) == (
        "togocel", "Mixx by Yas", 214, 9, 5, 200, 10, False, None)
    pay.get_fees(amount=1000, country="TG", network="togocel")
    assert json.loads(calls[1].content) == {"amount": 1000, "country": "TG", "network": "togocel"}
    carte = pay.quote(amount=20, method="card", currency="eur")
    assert json.loads(calls[2].content) == {"amount": 20, "method": "card", "currency": "EUR"}
    assert (carte.processing_fee, carte.net_amount, carte.networks, carte.customer_total) == (None, None, [], 20)
    for bad in ({"amount": 0}, {"amount": 200, "fees": "client"}, {"amount": 200, "method": "cash"}):
        with pytest.raises(TechnasPayError) as e:
            pay.quote(**bad)
        assert e.value.status == 0
    assert len(calls) == 3


def test_devis_amount_too_low_rend_le_minimum():
    calls, handler = _sequence((422, {"code": "amount_too_low", "message": "trop petit", "minimum_amount": 209}))
    pay = TechnasPay(SECRET, base_url=BASE, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(TechnasPayError) as e:
        pay.quote(amount=200, country="TG", fees="partner")
    assert e.value.code == "amount_too_low" and e.value.status == 422 and e.value.details.minimum_amount == 209


async def test_devis_avec_le_client_async():
    calls, handler = _sequence((200, DEVIS), (200, DEVIS))
    async with AsyncTechnasPay(SECRET, base_url=BASE, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))) as pay:
        q = await pay.quote(amount=200, country="TG", fees="customer")
        await pay.get_fees(amount=200, country="TG")
    assert calls[0].url.path == "/v1/quotes" and q.customer_total == 214 and q.networks[0].network_name == "Mixx by Yas"
    assert json.loads(calls[1].content) == {"amount": 200, "country": "TG"}
