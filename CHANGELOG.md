# Changelog

## 0.2.0 — 2026-11-14

`to_app_json(payment)` : la vue sans secret qu'un backend renvoie à son app (contrat de `technas_pay_flutter`). Exemple FastAPI : routes `POST /technas-pay/payments` et `GET /technas-pay/payments/{id}`, testées.

## 0.1.0 — 2026-11-14

Première version, alignée sur `@technas/pay-next` 0.8.0 : paiements, statut, liste, solde,
remboursements (Technas et hors plateforme), recharges, numéros de reversement, change hors
zone franc CFA (`get_fx_mode` / `set_fx_mode`), règles de pays, webhooks signés, outils de test.
Clients sync et async (httpx). Exemples FastAPI, Flask, Django.
