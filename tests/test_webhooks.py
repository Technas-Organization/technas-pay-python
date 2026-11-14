import json
import time

import pytest

from technas_pay import AsyncTechnasPay, TechnasPay, TechnasPayError, WebhookHandlers, WebhookVerificationError, sign

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
    ("balance.converted", {"id": "c", "from_currency": "GHS", "from_amount": 10, "to_currency": "XOF", "gross_amount": 450,
                           "cost": 9, "credited_amount": 441, "effective_rate": 44.1}, "on_balance_converted",
     lambda e: e.conversion.credited_amount == 441),
    ("fx_mode.updated", {"mode": "local", "previous_mode": "none", "markup_pct": 0, "home_currency": "XOF"}, "on_fx_mode_updated",
     lambda e: e.fx.previous_mode == "none"),
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
