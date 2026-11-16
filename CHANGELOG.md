# Changelog

## 0.7.1 — 2026-11-16

Sécurité : identifiants validés avant tout appel. Un identifiant mis dans un chemin d'URL (`get_payment`, `cancel_payment`, `refund`, `record_external_refund`, `get_refund`, `simulate_test_payment`, clients sync et async) doit correspondre à `^[A-Za-z0-9_-]{1,128}$` ; sinon erreur `invalid_request` (statut 0, non rejouable), sans aucun appel et sans citer la valeur reçue. Avant, `get_payment("../balance")` partait encodé (`..%2Fbalance`) avec votre clé : un intermédiaire qui décode puis normalise le chemin pouvait viser une autre route. Routes toutes faites : un identifiant de paiement hors de ce motif rend 404 `payment_not_found`, un `orderId` hors de ce motif 404 `order_not_found`, sans appel.

## 0.7.0 — 2026-11-16

Annuler un paiement : `cancel_payment(id)` (`POST /v1/payments/{id}/cancel`, client sync et async) annule DÉFINITIVEMENT un paiement que personne n'a commencé à payer ; rend le paiement (`status` `expired`, `checkout_url` `None`). Idempotent. `Payment` : `canceled_at`, `cancel_reason` ; `Payment` et `EventPayment` : `reason` (avis `payment.expired` : `canceled` ou `timeout`, `None` dans un avis plus ancien). Nouvelle erreur `payment_already_completed` (409, non rejouable). Routes toutes faites : `POST /payments/{id}/cancel` (même contrôle de propriété que `GET /payments/{id}`), et sur `reference_conflict` l'ancien essai est relu par sa référence puis annulé avant l'essai suivant ; payé ou demande en cours chez l'opérateur : aucun second paiement, l'erreur est rendue à l'app. Si l'annulation répond 404 (serveur qui ne l'a pas encore), le démarrage passe à l'essai suivant comme avant. Contrat v1 : 31 cas (`query_includes` vérifié).

## 0.6.0 — 2026-11-15

Le client change d'offre ou revient plus tard. Routes toutes faites (`app_routes`) : un `reference_conflict` (la référence d'un essai existe avec un autre montant : le client a changé d'offre) fait passer à l'essai suivant, comme `payment_expired`, dans la même borne `max_attempts` : jamais un refus. Contrat v1 : 24 cas. Documentation : nouvelle section « Le client change d'offre ou revient plus tard » (AGENTS.md, README, llms.txt) avec un exemple ; test de scénario (essai expiré, puis nouvel essai d'un autre montant).

## 0.5.1 — 2026-11-15

Avis `account.kyc_required` : `KycRequired` porte aussi l'état du compte, comme `get_account_kyc()` : `stage`, `block_reason` (`amount` | `duration`), `days_elapsed`, `grace_days`, `first_collected_at`. Tous facultatifs (`None` sur un avis plus ancien). `reason` ne change pas de sens.

## 0.5.0 — 2026-11-15

Seuil d'identité : `get_account_kyc()` (`GET /v1/account/kyc`) rend un `AccountKyc` (`stage`, `level`, `collected_xof`, `threshold_xof`, `grace_days`, `deadline`, `console_url`…). Avis `account.kyc_required` : fonction `on_kyc_required`, contenu typé `event.kyc_required` (`KycRequired`). Erreurs `kyc_required` et `contract_required` (409, non rejouables) : `details.console_url` et `details.version`. Les valeurs inconnues de `stage`, `level` et `reason` sont gardées telles quelles.

## 0.4.2 — 2026-11-15

Remboursement par carte : `Refund.kind` vaut désormais `"card"` (valeurs : `"payout"` | `"external"` | `"card"`). Pendant la transition, un `"stripe"` encore renvoyé par l'API est lu comme `"card"`. Documentation : carte à 1 % (plus les frais de votre compte carte), mobile money à 2 % (les frais de l'opérateur sont payés par le client).

## 0.4.0 — 2026-11-15

Paiement par carte : `ask_for_payment(method="card", currency=...)` (décimales permises), `start_card_onboarding`, `get_card_account`, `get_card_dashboard_link` (tableau de bord du compte carte), `card_account_not_ready` (`details.account_status`), `on_card_account_updated`, `Payment.method`, remboursement par carte (`kind`, voir 0.4.2). Routes de l'app : `card=True`, `authorize_admin` (`GET /card/account`, `POST /card/onboarding`), `POST /payments {method: "card"}` (références `orderId-card-N`). `to_app_json` porte `method`. Contrat v1 : 23 cas.

## 0.3.0 — 2026-11-15

`pay.app_routes(authorize=, resolve_order=, return_url=, webhooks=)` : les routes toutes faites que lit `technas_pay_flutter`, avec `.fastapi_router()`, `.flask_blueprint()`, `.django_view()` et `.handle()`. Client sync ou async, crochets sync ou async. Essais expirés sautés automatiquement, propriété de la commande vérifiée à chaque lecture. Contrat partagé `contract/app-contract.v1.json` (13 cas), livré dans la roue.

## 0.2.0 — 2026-11-14

`to_app_json(payment)` : la vue sans secret qu'un backend renvoie à son app (contrat de `technas_pay_flutter`). Exemple FastAPI : routes `POST /technas-pay/payments` et `GET /technas-pay/payments/{id}`, testées.

## 0.1.0 — 2026-11-14

Première version, alignée sur `@technas/pay-next` 0.8.0 : paiements, statut, liste, solde,
remboursements (Technas et hors plateforme), recharges, numéros de reversement, change hors
zone franc CFA (`get_fx_mode` / `set_fx_mode`), règles de pays, webhooks signés, outils de test.
Clients sync et async (httpx). Exemples FastAPI, Flask, Django.
