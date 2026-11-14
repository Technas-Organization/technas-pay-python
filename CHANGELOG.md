# Changelog

## 0.4.0 — 2026-11-15

Paiement par carte : `ask_for_payment(method="card", currency=...)` (décimales permises), `start_card_onboarding`, `get_card_account`, `get_card_dashboard_link` (https://dashboard.stripe.com/), `card_account_not_ready` (`details.account_status`), `on_card_account_updated`, `Payment.method`, remboursement `kind == "stripe"`. Routes de l'app : `card=True`, `authorize_admin` (`GET /card/account`, `POST /card/onboarding`), `POST /payments {method: "card"}` (références `orderId-card-N`). `to_app_json` porte `method`. Contrat v1 : 23 cas.

## 0.3.0 — 2026-11-15

`pay.app_routes(authorize=, resolve_order=, return_url=, webhooks=)` : les routes toutes faites que lit `technas_pay_flutter`, avec `.fastapi_router()`, `.flask_blueprint()`, `.django_view()` et `.handle()`. Client sync ou async, crochets sync ou async. Essais expirés sautés automatiquement, propriété de la commande vérifiée à chaque lecture. Contrat partagé `contract/app-contract.v1.json` (13 cas), livré dans la roue.

## 0.2.0 — 2026-11-14

`to_app_json(payment)` : la vue sans secret qu'un backend renvoie à son app (contrat de `technas_pay_flutter`). Exemple FastAPI : routes `POST /technas-pay/payments` et `GET /technas-pay/payments/{id}`, testées.

## 0.1.0 — 2026-11-14

Première version, alignée sur `@technas/pay-next` 0.8.0 : paiements, statut, liste, solde,
remboursements (Technas et hors plateforme), recharges, numéros de reversement, change hors
zone franc CFA (`get_fx_mode` / `set_fx_mode`), règles de pays, webhooks signés, outils de test.
Clients sync et async (httpx). Exemples FastAPI, Flask, Django.
