import json
import time

import pytest

from technas_pay import AsyncTechnasPay, KycRequired, TechnasPay, TechnasPayError, WebhookHandlers, WebhookVerificationError, sign

from .fake_gateway import SECRET, WEBHOOK_SECRET

EVENT = json.dumps({
    "id": "evt_1",
    "type": "payment.completed",
    "created_at": "2026-11-06T10:00:00Z",
    "data": {"id": "pay_1", "reference": "cmd_1-1", "status": "completed", "amount": 5000, "currency": "XOF",
             "commission": 100, "net_amount": 4900, "metadata": {"a": 1}},
})


def test_webhook_valide_appelle_la_bonne_fonction():
    pay = TechnasPay(SECRET)
    seen = []
    result = pay.handle_webhook(EVENT.encode(), sign(EVENT, WEBHOOK_SECRET), WebhookHandlers(on_completed=seen.append))
    assert result.body == {"received": True}
    assert seen[0].payment.net_amount == 4900 and seen[0].payment.reference == "cmd_1-1"


@pytest.mark.parametrize("signature", [
    None,
    "n'importe quoi",
    sign(EVENT, "autre-secret"),
    sign(EVENT, WEBHOOK_SECRET, int(time.time()) - 3600),
])
def test_signature_refusee_aucune_fonction_appelee(signature):
    pay = TechnasPay(SECRET)
    handlers = WebhookHandlers(on_completed=lambda e: pytest.fail("ne doit pas être appelée"))
    with pytest.raises(WebhookVerificationError):
        pay.handle_webhook(EVENT, signature, handlers)


def test_corps_modifie_apres_signature_refuse():
    with pytest.raises(WebhookVerificationError):
        TechnasPay(SECRET).verify_webhook(EVENT.replace("5000", "9"), sign(EVENT, WEBHOOK_SECRET))


def test_fonction_qui_echoue_remonte_pour_que_technas_relivre():
    def boom(event):
        raise RuntimeError("base en panne")

    with pytest.raises(RuntimeError):
        TechnasPay(SECRET).handle_webhook(EVENT, sign(EVENT, WEBHOOK_SECRET), WebhookHandlers(on_completed=boom))


def test_type_inconnu_acquitte():
    body = json.dumps({"id": "e", "type": "nouveau.type", "created_at": "x", "data": {"k": 1}})
    result = TechnasPay(SECRET).handle_webhook(body, sign(body, WEBHOOK_SECRET), WebhookHandlers())
    assert result.event.type == "nouveau.type" and result.event.data == {"k": 1}


@pytest.mark.parametrize("type_, data, attr, check", [
    ("refund.failed", {"id": "re_1", "payment_id": "p", "payment_reference": "r", "amount": 1, "sent_amount": 1, "fee": 0,
                       "fees": "partner", "balance_debit": 1, "currency": "XOF", "status": "failed", "failure": "x"}, "on_refund",
     lambda e: e.refund.failure == "x" and e.refund.kind == "payout"),
    ("payout_destination.change_scheduled", {"currency": "XOF", "country": "TG", "network": "moov_tg", "phone": "+228",
                                             "beneficiary_name": "B", "status": "scheduled"}, "on_payout_destination",
     lambda e: e.destination.status == "scheduled"),
    ("balance.topped_up", {"id": "t", "amount": 5, "currency": "XOF"}, "on_top_up", lambda e: e.top_up.amount == 5),
    ("settlement.completed", {"id": "s", "amount": 392, "fee": 61, "net_amount": 331, "currency": "XOF",
                              "status": "completed", "created_at": "2026-11-19T21:15:59+00:00"}, "on_settlement",
     lambda e: (e.settlement.id, e.settlement.amount, e.settlement.fee, e.settlement.net_amount, e.settlement.currency,
                e.settlement.status, e.settlement.created_at)
     == ("s", 392, 61, 331, "XOF", "completed", "2026-11-19T21:15:59+00:00")),
    ("settlement.failed", {"id": "s", "amount": 392, "fee": 61, "net_amount": 331, "currency": "XOF", "status": "failed"},
     "on_settlement", lambda e: e.settlement.status == "failed" and e.settlement.amount == 392 and e.settlement.created_at is None),
    ("balance.converted", {"id": "c", "from_currency": "GHS", "from_amount": 10, "to_currency": "XOF", "gross_amount": 450,
                           "cost": 9, "credited_amount": 441, "effective_rate": 44.1}, "on_balance_converted",
     lambda e: e.conversion.credited_amount == 441),
    ("fx_mode.updated", {"mode": "local", "previous_mode": "none", "markup_pct": 0, "home_currency": "XOF"}, "on_fx_mode_updated",
     lambda e: e.fx.previous_mode == "none"),
    ("card_account.updated", {"status": "enabled", "can_accept_payments": True}, "on_card_account_updated",
     lambda e: e.card_account.can_accept_payments),
    ("account.kyc_required", {"reason": "deadline_7d", "collected_xof": 400000, "threshold_xof": 1000000,
                              "deadline": "2026-11-22T09:00:00Z", "blocked": False, "console_url": "https://c"}, "on_kyc_required",
     lambda e: e.kyc_required.reason == "deadline_7d" and e.kyc_required.console_url == "https://c"),
    ("payment.expired", {"id": "p", "reference": "r-1", "status": "expired", "amount": 1, "currency": "XOF"}, "on_expired",
     lambda e: e.payment.status == "expired"),
    ("payment.failed", {"id": "p", "reference": "r-1", "status": "failed", "amount": 1, "currency": "XOF"}, "on_failed",
     lambda e: e.payment.status == "failed"),
])
def test_chaque_famille_d_evenements(type_, data, attr, check):
    body = json.dumps({"id": "e", "type": type_, "created_at": "x", "data": data})
    seen = []
    TechnasPay(SECRET).handle_webhook(body, sign(body, WEBHOOK_SECRET), WebhookHandlers(**{attr: seen.append}))
    assert len(seen) == 1 and check(seen[0])


@pytest.mark.parametrize("type_", ["settlement.completed", "settlement.failed"])
def test_reversement_arrive_dans_on_settlement_jamais_dans_on_completed(type_):
    body = json.dumps({"id": "e", "type": type_, "created_at": "x", "data": {}})
    seen, payes = [], []
    result = TechnasPay(SECRET).handle_webhook(
        body, sign(body, WEBHOOK_SECRET), WebhookHandlers(on_settlement=seen.append, on_completed=payes.append))
    assert len(seen) == 1 and payes == [] and result.event.payment is None
    s = seen[0].settlement  # champs absents : None, jamais une exception
    assert (s.id, s.amount, s.fee, s.net_amount, s.currency, s.status, s.created_at) == (None,) * 7


def test_create_test_webhook_passe_la_verification_et_refuse_le_reel():
    pay = TechnasPay(SECRET)
    hook = pay.create_test_webhook("payment.completed", reference="cmd_9-1", amount=5000, currency="XOF", commission=100)
    seen = []
    pay.handle_webhook(hook.body, hook.headers["X-Technas-Payment-Signature"], WebhookHandlers(on_completed=seen.append))
    assert seen[0].payment.net_amount == 4900
    live = TechnasPay(SECRET.replace("tps_test_", "tps_live_"))
    with pytest.raises(TechnasPayError) as e:
        live.create_test_webhook("payment.failed", reference="r", amount=1, currency="XOF")
    assert e.value.code == "test_only"


def test_un_secret_test_ne_verifie_pas_un_webhook_signe_en_reel():
    live_secret = SECRET.replace("tps_test_", "tps_live_").replace(WEBHOOK_SECRET, "ws_live_autre")
    with pytest.raises(WebhookVerificationError):
        TechnasPay(SECRET).verify_webhook(EVENT, sign(EVENT, live_secret.split(".")[1]))


def test_fonction_async_dans_le_client_sync_signalee():
    async def handler(event):
        return None

    with pytest.raises(TypeError, match="AsyncTechnasPay"):
        TechnasPay(SECRET).handle_webhook(EVENT, sign(EVENT, WEBHOOK_SECRET), WebhookHandlers(on_completed=handler))


async def test_client_async_accepte_fonctions_sync_et_async():
    seen = []

    async def on_completed(event):
        seen.append(event.id)

    pay = AsyncTechnasPay(SECRET)
    await pay.handle_webhook(EVENT, sign(EVENT, WEBHOOK_SECRET), WebhookHandlers(on_completed=on_completed))
    await pay.handle_webhook(EVENT, sign(EVENT, WEBHOOK_SECRET), WebhookHandlers(on_completed=lambda e: seen.append("sync")))
    assert seen == ["evt_1", "sync"]
    await pay.aclose()


def test_avis_kyc_required_signe_contenu_type():
    data = {"reason": "amount_80", "collected_xof": 800000, "threshold_xof": 1000000,
            "deadline": "2026-11-22T09:00:00Z", "blocked": False, "console_url": "https://console.technas.test/identite"}
    body = json.dumps({"id": "evt_kyc", "type": "account.kyc_required", "created_at": "2026-11-15T10:00:00Z", "data": data})
    seen, other = [], []
    TechnasPay(SECRET).handle_webhook(body, sign(body, WEBHOOK_SECRET), WebhookHandlers(on_kyc_required=seen.append,
                                                                                       on_completed=other.append))
    assert not other and len(seen) == 1
    assert seen[0].kyc_required == KycRequired(**data) and seen[0].payment is None
    blocked = json.dumps({"id": "e2", "type": "account.kyc_required", "created_at": "x",
                          "data": dict(data, reason="blocked", blocked=True, deadline=None)})
    ev = TechnasPay(SECRET).verify_webhook(blocked, sign(blocked, WEBHOOK_SECRET))
    assert ev.kyc_required.blocked and ev.kyc_required.reason == "blocked" and ev.kyc_required.deadline is None
    # Avis ancien (sans l'état du compte) : les nouveaux champs valent None.
    assert seen[0].kyc_required.stage is None and seen[0].kyc_required.block_reason is None
    assert seen[0].kyc_required.days_elapsed is None and seen[0].kyc_required.grace_days is None
    assert seen[0].kyc_required.first_collected_at is None


def test_avis_kyc_required_porte_l_etat_du_compte():
    data = {"reason": "blocked", "collected_xof": 1000000, "threshold_xof": 1000000, "deadline": None,
            "blocked": True, "console_url": "https://console.technas.test/identite",
            "stage": "blocked", "block_reason": "duration", "days_elapsed": 31, "grace_days": 30,
            "first_collected_at": "2026-10-15T09:00:00Z"}
    body = json.dumps({"id": "evt_kyc2", "type": "account.kyc_required", "created_at": "x", "data": data})
    k = TechnasPay(SECRET).verify_webhook(body, sign(body, WEBHOOK_SECRET)).kyc_required
    assert k == KycRequired(**data)
    assert (k.stage, k.block_reason, k.days_elapsed, k.grace_days, k.first_collected_at) == (
        "blocked", "duration", 31, 30, "2026-10-15T09:00:00Z")
    nuls = json.dumps({"id": "evt_kyc3", "type": "account.kyc_required", "created_at": "x",
                       "data": dict(data, stage="ok", block_reason=None, days_elapsed=None,
                                    grace_days=None, first_collected_at=None)})
    k = TechnasPay(SECRET).verify_webhook(nuls, sign(nuls, WEBHOOK_SECRET)).kyc_required
    assert k.stage == "ok" and k.block_reason is None and k.days_elapsed is None and k.first_collected_at is None


def test_avis_expire_dit_pourquoi():
    seen = []
    handlers = WebhookHandlers(on_expired=lambda e: seen.append((e.payment.reason, e.payment.canceled_at)))
    base = {"id": "p", "reference": "F-1", "status": "expired", "amount": 1, "currency": "XOF"}
    for data in (
        {**base, "reason": "canceled", "canceled_at": "2026-11-16T09:00:00Z"},
        {**base, "reason": "timeout"},
        base,
    ):
        body = json.dumps({"id": "e", "type": "payment.expired", "created_at": "x", "data": data})
        TechnasPay(SECRET).handle_webhook(body, sign(body, WEBHOOK_SECRET), handlers)
    assert seen == [("canceled", "2026-11-16T09:00:00Z"), ("timeout", None), (None, None)]


def test_avis_paiement_porte_qui_paie_les_frais_et_le_total_du_client():
    pay = TechnasPay(SECRET)
    data = {**json.loads(EVENT)["data"], "amount": 200, "commission": 5, "net_amount": 200,
            "customer_amount": 205, "fees_paid_by": "customer", "customer_total": 214, "processing_fee": 9}
    seen = []
    for d in (data, json.loads(EVENT)["data"]):
        body = json.dumps({"id": "e", "type": "payment.completed", "created_at": "x", "data": d})
        pay.handle_webhook(body, sign(body, WEBHOOK_SECRET), WebhookHandlers(on_completed=seen.append))
    p = seen[0].payment
    assert (p.amount, p.customer_amount, p.fees_paid_by, p.customer_total, p.processing_fee) == (200, 205, "customer", 214, 9)
    # Avis d'un serveur ancien : customer_amount = amount, fees_paid_by = partner, le reste None.
    ancien = seen[1].payment
    assert (ancien.customer_amount, ancien.fees_paid_by, ancien.customer_total, ancien.processing_fee) == (5000, "partner", None, None)


def test_avis_paiement_porte_le_reseau_paye():
    pay = TechnasPay(SECRET)
    seen = []
    for d in ({**json.loads(EVENT)["data"], "network": "mixx"}, json.loads(EVENT)["data"]):
        body = json.dumps({"id": "e", "type": "payment.completed", "created_at": "x", "data": d})
        pay.handle_webhook(body, sign(body, WEBHOOK_SECRET), WebhookHandlers(on_completed=seen.append))
    assert [e.payment.network for e in seen] == ["mixx", None]
