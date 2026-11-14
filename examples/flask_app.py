"""Webhook Technas Pay avec Flask (client sync).

    pip install technas-pay flask
    TECHNAS_PAY_SECRET=tps_test_... flask --app examples.flask_app run
"""

from flask import Flask, abort, redirect, request

from technas_pay import Event, TechnasPay, WebhookHandlers, WebhookVerificationError, retry_reference

app = Flask(__name__)
pay = TechnasPay()  # lit TECHNAS_PAY_SECRET au premier appel ; côté serveur uniquement

ORDERS = {"42": {"amount": 5000, "attempt": 1, "paid": False, "payment_id": None}}


@app.post("/orders/<order_id>/pay")
def pay_order(order_id: str):
    order = ORDERS[order_id]
    payment = pay.ask_for_payment(
        amount=order["amount"],
        reference=retry_reference(order_id, order["attempt"]),
        customer={"email": "awa@exemple.tg", "name": "Awa Mensah"},
        return_url="https://votre-site.example/merci",
    )
    order["payment_id"] = payment.id
    return redirect(payment.checkout_url, code=303)


def on_completed(event: Event) -> None:
    ORDERS[event.payment.reference.rsplit("-", 1)[0]]["paid"] = True


def on_expired(event: Event) -> None:
    ORDERS[event.payment.reference.rsplit("-", 1)[0]]["attempt"] += 1


HANDLERS = WebhookHandlers(on_completed=on_completed, on_expired=on_expired)


@app.post("/api/technas-pay")
def technas_webhook():
    raw = request.get_data()  # corps BRUT
    try:
        result = pay.handle_webhook(raw, request.headers.get("X-Technas-Payment-Signature"), HANDLERS)
    except WebhookVerificationError:
        abort(400)
    return result.body
