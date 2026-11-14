# technas-pay (Python) : règles pour un agent de code

Consignes pour un assistant de code (Codex, Claude Code…). Appliquez-les dans l'application de l'utilisateur, pas dans ce dépôt. Le README détaille chaque fonction ; des exemples complets sont dans [`examples/`](examples) (FastAPI, Flask, Django). Ce fichier est aussi livré dans le paquet : `python -c "import technas_pay, pathlib; print(pathlib.Path(technas_pay.__file__).with_name('AGENTS.md'))"`.

Même API, mêmes règles que le SDK TypeScript `@technas/pay-next` : seuls les noms passent en snake_case (`askForPayment` → `ask_for_payment`, `checkoutUrl` → `checkout_url`).

## Avant de commencer

1. Python 3.9+. Application web côté serveur (FastAPI, Django, Flask, Starlette, Litestar…). **Jamais** dans une app mobile, un navigateur ou un script distribué : le secret y serait exposé.
2. `TECHNAS_PAY_SECRET` doit exister dans l'environnement serveur (ou `.env`, ignoré par git) et commencer par `tps_test_`. S'il manque, demandez-le à l'utilisateur. **N'en inventez pas, ne l'affichez pas, ne le commitez pas, ne le loggez pas.**
3. Repérez le modèle de commande : un identifiant, un montant calculé côté serveur, un état « payée », un numéro de tentative (entier, 1 par défaut) et une colonne pour garder `payment.id` (nécessaire pour rembourser).

## Installer

```bash
pip install technas-pay \
  --index-url https://nexus.technas.fr/repository/technas-pypi-hosted/simple/ \
  --extra-index-url https://pypi.org/simple/
```

(Depuis un pod du cluster Technas : index `http://nexus.technas-infra.svc.cluster.local:8081/repository/technas-pypi-hosted/simple/` avec `--trusted-host nexus.technas-infra.svc.cluster.local`.)

## Quel client

| Framework | Client |
|---|---|
| FastAPI, Starlette, Litestar, aiohttp, Django async | `AsyncTechnasPay()` (méthodes à `await`) |
| Django, Flask, scripts, Celery | `TechnasPay()` |

Créez UNE instance au niveau du module (`pay = TechnasPay()`) : elle lit `TECHNAS_PAY_SECRET` au premier appel.

## Quelle méthode pour quoi

| Besoin | Appel |
|---|---|
| Faire payer une commande | `pay.ask_for_payment(amount=..., reference=retry_reference(order_id, attempt), customer={"email", "name"}, return_url=...)`, puis rediriger (303) vers `payment.checkout_url` |
| Savoir qu'elle est payée | webhook, `WebhookHandlers(on_completed=...)` |
| Rattraper un webhook manqué | `pay.get_payment_by_reference(reference)` |
| Savoir si on peut relancer | `on_expired`, ou `can_retry(pay.get_payment(id))` |
| Rembourser | `pay.refund(payment_id, amount=?, fees=?, reference=?)` |
| Noter un remboursement fait hors Technas | `pay.record_external_refund(payment_id, amount=..., method="Flooz")` |
| Savoir que le remboursement est arrivé | `on_refund` (`refund.completed`, `refund.failed`, `refund.recorded`) |
| Recharger le solde | `pay.top_up_balance(amount=..., currency="XOF")`, ouvrir `checkout_url` ; `on_top_up` |
| Solde, reversements | `pay.get_balance()`, `pay.get_settlements()` |
| Historique | `pay.list_transactions(limit=, status=, cursor=)` |
| Pays hors zone CFA ouverts, risques | `pay.get_fx_mode()` |
| Activer une option hors zone (après accord explicite) | `pay.set_fx_mode(mode="local", accept_risk=True)` |
| Pays où vendre | `pay.set_country_rules(allowed=?, excluded=?)` ou `allowed_countries` / `excluded_countries` par paiement |
| Numéro de reversement (administrateur) | `pay.get_payout_networks("TG")`, `pay.get_payout_destination()`, `pay.set_payout_destination(PayoutDestination(...))` |
| Tester la route webhook | `pay.create_test_webhook("payment.completed", reference=..., amount=..., currency="XOF")` |
| Jouer le payeur en test | `pay.simulate_test_payment(id, "completed" \| "failed" \| "expired")` |

## Étapes

1. `pay.py` (ou `services/pay.py`) : `from technas_pay import TechnasPay` puis `pay = TechnasPay()`.
2. Dans la vue « payer » : relisez la commande en base, appelez `ask_for_payment`, gardez `payment.id`, redirigez vers `checkout_url`.
3. Route webhook `POST /api/technas-pay` : lisez le corps **brut**, appelez `pay.handle_webhook(raw, request.headers.get("X-Technas-Payment-Signature"), HANDLERS)`. `WebhookVerificationError` → réponse 400. Copiez l'exemple de votre framework dans `examples/`.
4. Ajoutez un test qui poste `pay.create_test_webhook(...)` sur cette route (voir `tests/test_examples.py`), puis lancez-le.

## App mobile (Flutter)

Ne réécrivez pas de routes : `pay.app_routes(authorize=..., resolve_order=..., return_url=..., webhooks=...)`, puis `.fastapi_router()`, `.flask_blueprint()` ou `.django_view()`. `resolve_order(order_id, user)` relit la commande EN BASE et rend `None` si elle n'est pas à cet utilisateur. N'y mettez jamais un montant reçu de l'app. Côté app : `TechnasPayClient(baseUrl: '.../api/technas-pay')`.

## Obligatoire

- **`amount`** : un `int` en unité principale (`5000` = 5 000 FCFA, jamais × 100), calculé côté serveur. Un `float` est refusé (`invalid_amount`) avant tout appel.
- **`reference`** : `retry_reference(order_id, attempt)`. Voir la règle de relance.
- **`customer`** : `{"email": ..., "name": ...}` de vrais champs du client, jamais inventés. Ajoutez `"country"` et `"phone"` si l'application les connaît.
- **`return_url`** : en https en production.

## La devise est automatique

- **Ne passez pas `currency`** si l'application vend en francs CFA : elle suit le pays du client (TG → XOF, CM → XAF, même montant) ; sans pays, le client choisit sur la page.
- **Ne fixez pas `default_currency`** : il empêcherait ce choix.
- **Hors zone, les montants payés peuvent avoir des centimes** (`121.96` GHS) : stockez-les en `Decimal`/`NUMERIC`, ne les arrondissez pas.
- Dans le webhook, `event.payment.original_amount` est le montant demandé en F CFA ; `amount` et `currency` sont ce qui a été payé.

## Clients hors zone franc CFA : une option à risque, décidée par l'utilisateur

- **Technas ne couvre aucun risque de change.** Avant `set_fx_mode(mode="local", accept_risk=True)` :
  1. lisez `pay.get_fx_mode().risks["local"]` (et `risks["markup"]` si un supplément est prévu) ;
  2. **montrez ce texte à l'utilisateur, en entier, et expliquez-le simplement** ;
  3. attendez son accord explicite, en toutes lettres ;
  4. seulement ensuite, appelez `set_fx_mode`.
- **N'activez jamais une option de vous-même**, et ne passez jamais `accept_risk=True` sans cet accord.
- **`local`** : solde tenu et reversé dans la monnaie du client ; il faut un numéro de reversement **confirmé** dans cette monnaie, sinon le pays reste fermé (`no_payout_number`).
- **`converted`** : PAS ENCORE OUVERT (`fx_mode_unavailable`). Seules les options de `get_fx_mode().available_modes` existent.
- **Ne promettez jamais** un pays que `get_fx_mode()` ne donne pas `available=True`.
- Branchez `on_fx_mode_updated` : un changement que l'utilisateur n'a pas fait signale une clé compromise.

## Paiement par carte

1. **Compte carte, une fois** : au clic du RESPONSABLE sur « Activer la carte », `link = pay.start_card_onboarding(country="TG", return_url=..., refresh_url=...)`, puis redirigez-le vers `link.url` (il y donne son identité et sa banque ; le lien expire en minutes). `refresh_url` : rappelez la méthode et redirigez de nouveau.
2. **Attendez `pay.get_card_account().can_accept_payments`** (ou le webhook `on_card_account_updated`). Avant, `method="card"` lève `card_account_not_ready` (`error.details.account_status`).
3. **Faire payer** : `pay.ask_for_payment(method="card", amount=12.5, currency="EUR", ...)`. Devise obligatoire, décimales permises (jamais × 100).
4. **L'argent va directement sur le compte carte**, commission Technas déjà retirée : il n'apparaît pas dans `get_balance()`. Un remboursement repart sur la carte depuis ce compte (`refund.kind == "stripe"`).
5. **Tableau de bord** (virements, litiges) : `pay.get_card_dashboard_link()` → https://dashboard.stripe.com/, où le responsable se connecte avec SES identifiants Stripe.
6. **App mobile** : `pay.app_routes(..., card=True, authorize_admin=...)` ; `/methods` n'ouvre la carte que si le compte est prêt et que `resolve_order` rend une `currency`.

## Sécurité

- **Le secret reste côté serveur** : ni template, ni réponse JSON, ni log, ni commit, ni URL.
- **Vérifiez la signature de chaque webhook** : `handle_webhook` / `verify_webhook` le font. Un secret `tps_test_` ne vérifie que les webhooks de test, `tps_live_` que ceux du réel.
- **Corps brut** : FastAPI `await request.body()`, Flask `request.get_data()`, Django `request.body`. Jamais `request.json()` puis `json.dumps` : la signature ne correspondrait plus.
- Django : la vue webhook est `@csrf_exempt` (la signature Technas la protège).
- **Seul le webhook prouve le paiement.** Ne marquez jamais une commande payée depuis la page `return_url`.
- **Réservez à un administrateur** les remboursements et le numéro de reversement.

## La règle de relance (à respecter strictement)

**Une référence = un essai. Relancez avec une NOUVELLE référence seulement après `payment.expired` (ou `get_payment` → `expired`), jamais sur `failed`.**

- Composez `retry_reference(order_id, attempt)` et gardez `attempt` en base.
- Rejouer la référence d'un paiement expiré lève `payment_expired` (409) : ne réessayez pas le même appel, incrémentez `attempt`.
- Après expiration, `checkout_url` vaut `None` : n'envoyez jamais l'ancien lien.
- `failed` n'est **pas** définitif : le client peut encore payer sur la même page pendant une heure. Dans `on_failed`, n'annulez rien, n'envoyez pas de nouveau lien.
- `expired` est définitif. `can_retry(payment)` n'est vrai que là.
- Expliquez cette règle à l'utilisateur : un client relancé sur `failed` pourrait payer deux fois.

## Webhooks

- **Ils peuvent arriver plusieurs fois** : vos fonctions doivent le supporter (vérifiez l'état en base ou gardez `event.id`).
- **Ne rattrapez pas leurs exceptions pour répondre 200** : elles doivent remonter (5xx) pour que Technas relivre.
- `on_refund` : prévenez le client (e-mail, SMS). Technas ne lui écrit pas. `refund.failed` : rien n'est parti, votre solde est recrédité.
- Avec `TechnasPay` (sync), les fonctions sont sync. Des fonctions `async def` → `AsyncTechnasPay`.

## Rembourser

- `pay.refund(payment_id)` rembourse tout ce qui reste ; `amount=` une partie. L'argent repart au numéro qui a payé.
- Plafond : jamais plus que le montant payé. La commission de 2 % n'est pas rendue.
- `fees="partner"` (défaut : le client reçoit tout) ou `"customer"` (frais retirés de `sent_amount`).
- Rejouer le même appel ne rembourse pas deux fois. Deux remboursements partiels identiques voulus : une `reference` chacun.
- `insufficient_balance` : `error.details.missing_amount` ; `top_up_balance` puis rappeler `refund`.
- Remboursement déjà fait à la main : `record_external_refund`, jamais `refund` (le client serait remboursé deux fois).

## Erreurs

Attrapez `TechnasPayError` et testez `error.code` (jamais le message). `error.retryable` vrai : refaites **le même appel**, mêmes valeurs. `error.details` : `missing_amount`, `refundable_amount`, `reason`, `risk`.

| `error.code` | Que faire |
|---|---|
| `invalid_secret` | Variable absente ou mal copiée : demandez à l'utilisateur de la vérifier |
| `invalid_api_key` | Secret refusé : contacter Technas |
| `invalid_request` | Corriger le champ indiqué dans `error.errors` |
| `invalid_amount`, `amount_too_low` | Montant `int` positif, assez grand pour couvrir les frais |
| `currency_not_enabled` | Devise non ouverte sur ce compte : contacter Technas |
| `currency_conversion_unavailable` | Client hors zone CFA en mode `none` : ne pas proposer ce pays ; voir `get_fx_mode()` |
| `country_not_allowed` | Le marchand ne vend pas dans ce pays : ne pas contourner |
| `country_not_available` | Pays pas prêt : `error.details.reason` |
| `risk_not_accepted` | Montrer `error.details.risk` à l'utilisateur ; n'activer qu'avec son accord explicite |
| `fx_mode_unavailable`, `fx_channel_unavailable` | `converted` pas ouvert : proposer `local` (avec accord) ou rester en `none` |
| `quote_expired` | Le client rechoisit son pays sur la page, rien à faire |
| `reference_conflict` | Même référence, autres valeurs : ne pas réutiliser la référence |
| `payment_in_progress` | Réessayer dans une minute, même appel |
| `payment_not_found`, `refund_not_found` | Mauvais identifiant ou autre compte |
| `payment_expired` | Essai expiré : NOUVELLE référence (`attempt + 1`), jamais le même appel |
| `payment_not_refundable` | Seul un paiement `completed` se rembourse |
| `refund_exceeds_payment` | Rembourser au plus `error.details.refundable_amount` |
| `refund_conflict` | Cette `reference` de remboursement a déjà servi avec d'autres valeurs |
| `insufficient_balance` | Recharger au moins `error.details.missing_amount`, puis rappeler |
| `payer_unknown`, `refund_network_unavailable` | Rembourser le client autrement, prévenir Technas |
| `provider_unavailable`, `unavailable`, `network_error` | Panne passagère : refaire le même appel |
| `rate_limited` | Ralentir, réessayer |
| `card_account_not_ready` | Compte carte pas prêt : `error.details.account_status` ; finir `start_card_onboarding` |
| `currency_required` | Paiement par carte : passer `currency` |
| `test_only` | Fonction de test appelée avec un secret réel |

## Vérifier l'intégration

Avec un secret `tps_test_`, aucun argent ne circule.

1. Déclenchez un paiement : `checkout_url` ouvre une page de test (« réussi », « refusé », « expiration »).
2. Sans navigateur : `pay.simulate_test_payment(id, "completed")`, puis `pay.get_payment_by_reference(reference)`.
3. Relance : sur un autre paiement, `"failed"` (rien ne doit être relancé), puis `"expired"` (`on_expired` incrémente la tentative).
4. Remboursez avec `pay.refund(payment.id)` : en test, terminé aussitôt.
5. Le webhook n'arrive sur votre poste que si Technas connaît une adresse publique. Sinon, testez la route avec `create_test_webhook`, et demandez à l'utilisateur de transmettre à son contact Technas l'adresse d'un tunnel.

## Passer en réel

Remplacez seulement le secret par `tps_live_…`. `return_url` doit être en https. Cette étape revient à l'utilisateur et à Technas : ne la faites pas de votre propre initiative.

