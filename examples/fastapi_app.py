"""Webhook Technas Pay avec FastAPI (client async).

    pip install technas-pay fastapi uvicorn
    TECHNAS_PAY_SECRET=tps_test_... uvicorn examples.fastapi_app:app
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse

from technas_pay import AsyncTechnasPay, Event, WebhookHandlers, WebhookVerificationError, retry_reference

app = FastAPI()
pay = AsyncTechnasPay()  # lit TECHNAS_PAY_SECRET au premier appel ; côté serveur uniquement

# Remplacez par votre base de données.
ORDERS = {"42": {"amount": 5000, "attempt": 1, "paid": False, "payment_id": None}}


@app.post("/orders/{order_id}/pay")
async def pay_order(order_id: str):
    order = ORDERS[order_id]  # le montant vient de VOTRE base, jamais du navigateur
    payment = await pay.ask_for_payment(
        amount=order["amount"],
        reference=retry_reference(order_id, order["attempt"]),  # une référence = un essai
        customer={"email": "awa@exemple.tg", "name": "Awa Mensah"},
        return_url="https://votre-site.example/merci",
    )
    order["payment_id"] = payment.id
    return RedirectResponse(payment.checkout_url, status_code=303)


async def on_completed(event: Event) -> None:
    order_id = event.payment.reference.rsplit("-", 1)[0]
    ORDERS[order_id]["paid"] = True  # idempotent : un webhook peut arriver deux fois


async def on_expired(event: Event) -> None:
    # Seul moment où relancer le client : NOUVELLE référence (tentative + 1).
    order_id = event.payment.reference.rsplit("-", 1)[0]
    ORDERS[order_id]["attempt"] += 1


HANDLERS = WebhookHandlers(on_completed=on_completed, on_expired=on_expired)


@app.post("/api/technas-pay")
async def technas_webhook(request: Request):
    raw = await request.body()  # corps BRUT : pas de request.json() avant la vérification
    try:
        result = await pay.handle_webhook(raw, request.headers.get("x-technas-payment-signature"), HANDLERS)
    except WebhookVerificationError:
        raise HTTPException(status_code=400, detail="invalid_signature")
    # Une erreur de vos fonctions remonte en 500 : Technas relivrera l'événement.
    return result.body
