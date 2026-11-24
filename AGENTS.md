# technas-pay (Python) : règles pour un agent de code

Consignes pour un assistant de code. Appliquez-les dans l'application de l'utilisateur, pas dans ce dépôt. Le README détaille chaque fonction ; des exemples complets sont dans [`examples/`](examples) (FastAPI, Flask, Django). Ce fichier est aussi livré dans le paquet : `python -c "import technas_pay, pathlib; print(pathlib.Path(technas_pay.__file__).with_name('AGENTS.md'))"`.

Même API, mêmes règles que le SDK TypeScript `@technas/pay-next` : seuls les noms passent en snake_case (`askForPayment` → `ask_for_payment`, `checkoutUrl` → `checkout_url`).

## Avant de commencer

1. Python 3.9+. Application web côté serveur (FastAPI, Django, Flask, Starlette, Litestar…). **Jamais** dans une app mobile, un navigateur ou un script distribué : le secret y serait exposé.
2. `TECHNAS_PAY_SECRET` doit exister dans l'environnement serveur (ou `.env`, ignoré par git) et commencer par `tps_test_`. S'il manque, demandez-le à l'utilisateur. **N'en inventez pas, ne l'affichez pas, ne le commitez pas, ne le loggez pas.**
3. Repérez le modèle de commande : un identifiant, un montant calculé côté serveur, un état « payée », un numéro de tentative (entier, 1 par défaut) et une colonne pour garder `payment.id` (nécessaire pour rembourser).

## Installer

```bash
pip install "technas-pay @ git+https://github.com/Technas-Organization/technas-pay-python@v0.11.0"
```


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
| Savoir combien le client paiera et combien vous recevrez, avant de créer le paiement | `pay.quote(amount=..., country=..., fees=?)` (ou `pay.get_fees`) → `customer_total` (à afficher au client), `processing_fee`, `commission`, `net_amount`, `networks` |
| Afficher un prix FIXE (le même quel que soit le réseau) et recevoir au moins X | `pay.display_price(receive=..., country=? \| countries=[...], round_to=?)` → `by_country[pays].price`, `everywhere.price` ; puis `ask_for_payment(amount=price, fees="partner")` (jamais `"customer"`) |
| La grille des frais par pays et réseau (back-office) | `pay.get_rates(country=?)` (ou `pay.rates`) → `countries[].networks[]` : `percent`, `minimum_amount`, `commission`, `operator_fee_estimate`, `example_1000.customer/partner` |
| Faire payer TOUS les frais au client (vous recevez exactement `amount`) | `fees="customer"` dans `ask_for_payment` (ou réglage du compte) ; `"partner"` : le client paie `amount`, frais compris |
| Lister les moyens et réseaux ouverts, ce qui est activé, l'ordre | `pay.get_payment_methods(country=?)` ; constantes `PAYMENT_METHODS`, `NETWORKS` (types `PaymentMethod`, `Network`) |
| Activer / éteindre des moyens et réseaux, fixer l'ordre d'affichage | `pay.set_payment_methods(methods=?, networks=?, network_order=?)` ; par paiement : `networks=[...]` dans `ask_for_payment` |
| Savoir qu'elle est payée | webhook, `WebhookHandlers(on_completed=...)` |
| Rattraper un webhook manqué | `pay.get_payment_by_reference(reference)` |
| Savoir si on peut relancer | `on_expired`, ou `can_retry(pay.get_payment(id))` |
| Le client change d'offre ou revient plus tard | `pay.cancel_payment(ancien_id)`, puis nouvel essai `retry_reference(facture_id, n + 1)` ; au retour, `pay.get_payment(dernier_paiement_id)` d'abord (voir la section dédiée) |
| Rembourser | `pay.refund(payment_id, amount=?, fees=?, reference=?)` |
| Noter un remboursement fait hors Technas | `pay.record_external_refund(payment_id, amount=..., method="Flooz")` |
| Savoir que le remboursement est arrivé | `on_refund` (`refund.completed`, `refund.failed`, `refund.recorded`) |
| Recharger le solde | `pay.top_up_balance(amount=..., currency="XOF")`, ouvrir `checkout_url` ; `on_top_up` |
| Solde, reversements | `pay.get_balance()`, `pay.get_settlements()` ; issue d'un reversement : `on_settlement` (`settlement.completed`, `settlement.failed`) |
| Seuil d'identité du compte (à montrer au marchand) | `pay.get_account_kyc()` ; avis `on_kyc_required` |
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

Ne réécrivez pas de routes : `pay.app_routes(authorize=..., resolve_order=..., return_url=..., webhooks=...)`, puis `.fastapi_router()`, `.flask_blueprint()` ou `.django_view()`. `resolve_order(order_id, user)` relit la commande EN BASE et rend `None` si elle n'est pas à cet utilisateur. N'y mettez jamais un montant reçu de l'app. Routes : `GET /methods?orderId=`, `POST /payments`, `GET /payments/{id}`, `POST /payments/{id}/cancel`, `POST /webhook`. Le client change d'offre : l'app appelle `POST /payments/{id}/cancel` sur l'essai affiché, puis `POST /payments` ; si la commande a changé de montant, `POST /payments` annule lui-même l'ancien essai encore ouvert. Côté app : `TechnasPayClient(baseUrl: '.../api/technas-pay')`.

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

1. **Compte carte, une fois** : au clic du RESPONSABLE sur « Activer la carte », `link = pay.start_card_onboarding(country="TG", return_url=..., refresh_url=...)`, puis redirigez-le vers `link.url` : il y donne son identité et sa banque sur la page sécurisée de notre partenaire carte (Stripe), qui vous fera accepter son contrat (le lien expire en minutes). `refresh_url` : rappelez la méthode et redirigez de nouveau.
2. **Attendez `pay.get_card_account().can_accept_payments`** (ou le webhook `on_card_account_updated`). Avant, `method="card"` lève `card_account_not_ready` (`error.details.account_status`).
3. **Faire payer** : `pay.ask_for_payment(method="card", amount=12.5, currency="EUR", ...)`. Devise obligatoire, décimales permises (jamais × 100).
4. **L'argent va directement sur le compte carte**, commission Technas déjà retirée : il n'apparaît pas dans `get_balance()`. Un remboursement repart sur la carte depuis ce compte (`refund.kind == "card"`).
5. **Tableau de bord** (virements, litiges) : `pay.get_card_dashboard_link()` → le tableau de bord de votre compte carte, où le responsable se connecte avec SES identifiants.
6. **App mobile** : `pay.app_routes(..., card=True, authorize_admin=...)` ; `/methods` n'ouvre la carte que si le compte est prêt et que `resolve_order` rend une `currency`.

## Seuil d'identité

- **La règle** : en réel, un compte sans pièce d'identité vérifiée voit ses NOUVEAUX paiements mobile money refusés (`kyc_required`) au-delà de 1 000 000 F CFA encaissés en cumul ou de 30 jours après son premier paiement réel encaissé. Rien de ce qui existe n'est bloqué (paiement en cours, remboursement, reversement, lecture). La carte n'est pas concernée. Le responsable dépose sa pièce dans la console (`console_url`).
- **Afficher l'état** : `pay.get_account_kyc()` dans l'espace d'administration du MARCHAND (jamais au client final) : `stage` (`ok`, `soon`, `blocked`, `verified`, `not_applicable`), `collected_xof` / `threshold_xof`, `deadline`, `console_url`. Une valeur inconnue de `stage`, `level` ou `reason` est une chaîne : traitez-la comme « à vérifier », sans planter.
- **Prévenir le responsable** : branchez `WebhookHandlers(on_kyc_required=...)` (avis `account.kyc_required`, à 80 % du seuil, 7 jours avant l'échéance, puis au premier refus) ; `event.kyc_required.reason` vaut `amount_80`, `deadline_7d` ou `blocked`. L'avis porte aussi `stage`, `block_reason` (`amount` | `duration`), `days_elapsed`, `grace_days`, `first_collected_at`, comme `get_account_kyc()` ; tous facultatifs (`None` sur un avis plus ancien).
- **Sur `kyc_required`** : proposez la carte, ou expliquez au client que le paiement mobile money n'est pas disponible pour le moment. **Ne réessayez jamais.**
- **Les chiffres viennent de l'API** (`threshold_xof`, `grace_days`, `warn_from_xof`) : n'écrivez jamais 1 000 000 ni 30 dans le code.

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
- Le client choisit lui-même une AUTRE offre (autre montant) : c'est un nouvel essai tout de suite, voir la section suivante.

## Le client change d'offre ou revient plus tard

Le client choisit une offre, arrive sur la page de paiement, ne paie pas, puis revient choisir une AUTRE offre (ou revient plus tard). Ce n'est jamais « un paiement existe déjà ».

- **Une référence = un essai.** Nouvelle offre, ou retour après expiration : un NOUVEL essai, avec une nouvelle référence `retry_reference(facture_id, n + 1)` (`n` gardé en base). Réutiliser la référence avec un autre montant lève `reference_conflict` (409) ; celle d'un essai expiré, `payment_expired` (409). Les routes toutes faites (`app_routes`) le font seules.
- **Une facture n'est payée QUE sur `payment.completed`** (avis signé, `on_completed`). Jamais sur `return_url`, jamais à l'ouverture de la page.
- **Un essai non payé ne bloque jamais un nouvel essai.** Même offre et essai `pending` ou `failed` : renvoyez le client sur la même page (`checkout_url`). Autre offre : ANNULEZ l'ancien essai (`pay.cancel_payment(ancien_id)`), puis créez le nouveau avec une NOUVELLE référence ; ne refusez pas. `expired` : marquez la tentative expirée et laissez recommencer.
- **`cancel_payment` refuse quand c'est trop tard** : `payment_already_completed` (le client a payé l'ancienne offre : relisez le paiement, ne créez rien) ; `payment_in_progress` (une demande est en cours chez l'opérateur : ne créez PAS de second paiement, attendez l'avis de l'issue). Un paiement annulé reste `expired` (`canceled_at`, `cancel_reason` ; `can_retry` marche tel quel), et son avis `payment.expired` porte `event.payment.reason` = `canceled` (`timeout` pour une échéance passée, `None` dans un avis plus ancien).
- **Au retour du client**, vérifiez avec `pay.get_payment(dernier_paiement_id)` avant de dire quoi que ce soit ; ne vous fiez jamais seulement à votre base.
- **Un ancien essai payé APRÈS le nouveau** (le client a payé les deux pages) : n'activez rien deux fois ; remboursez l'essai en trop avec `pay.refund(payment_id)`.

```python
from technas_pay import retry_reference

def payer(facture, montant):
    """Au clic « payer » : `montant` est celui de l'offre choisie MAINTENANT."""
    if facture.dernier_paiement_id:
        dernier = pay.get_payment(facture.dernier_paiement_id)  # la vérité est chez Technas
        if dernier.status == "completed":
            return "deja_payee"
        meme_offre = dernier.amount == montant
        if meme_offre and dernier.status != "expired" and dernier.checkout_url:
            return dernier.checkout_url  # pending ou failed : même page, le client peut encore payer
        if dernier.status != "expired":
            # Autre offre : l'ancien lien est fermé d'abord. payment_in_progress (demande en cours
            # chez l'opérateur) ou payment_already_completed (déjà payé) remontent : on ne crée
            # alors AUCUN second paiement.
            pay.cancel_payment(dernier.id)
    facture.essai += 1  # nouvel essai, nouvelle référence, jamais un refus
    paiement = pay.ask_for_payment(
        amount=montant,
        reference=retry_reference(facture.id, facture.essai),
        customer={"email": facture.email, "name": facture.nom},
        return_url="https://votre-site.example/merci",
    )
    facture.dernier_paiement_id = paiement.id
    facture.save()
    return paiement.checkout_url

def on_completed(event):
    facture = Facture.get(event.payment.reference.rsplit("-", 1)[0])
    if facture.payee_par and facture.payee_par != event.payment.id:
        pay.refund(event.payment.id)  # le client a payé deux pages : on rend l'essai en trop
        return
    facture.payee_par = event.payment.id  # payée ici, et seulement ici
    facture.save()
```

## Webhooks

- **Ils peuvent arriver plusieurs fois** : vos fonctions doivent le supporter (vérifiez l'état en base ou gardez `event.id`).
- **Ne rattrapez pas leurs exceptions pour répondre 200** : elles doivent remonter (5xx) pour que Technas relivre.
- `on_refund` : prévenez le client (e-mail, SMS). Technas ne lui écrit pas. `refund.failed` : rien n'est parti, votre solde est recrédité.
- `on_settlement` : l'issue d'un reversement de votre solde vers votre numéro mobile money. `event.settlement` : `amount` (ce qui a quitté le solde), `fee` (frais du versement, à votre charge), `net_amount` (ce qui arrive sur le numéro), `currency`, `status` (`completed` | `failed`). `settlement.failed` : `amount` est recrédité sur votre solde.
- Avec `TechnasPay` (sync), les fonctions sont sync. Des fonctions `async def` → `AsyncTechnasPay`.

## Rembourser

- `pay.refund(payment_id)` rembourse tout ce qui reste ; `amount=` une partie. L'argent repart au numéro qui a payé.
- Plafond : jamais plus que le montant payé. La commission Technas n'est pas rendue.
- `fees="partner"` (défaut : le client reçoit tout) ou `"customer"` (frais retirés de `sent_amount`).
- Rejouer le même appel ne rembourse pas deux fois. Deux remboursements partiels identiques voulus : une `reference` chacun.
- `insufficient_balance` : `error.details.missing_amount` ; `top_up_balance` puis rappeler `refund`.
- Remboursement déjà fait à la main : `record_external_refund`, jamais `refund` (le client serait remboursé deux fois).

## Erreurs

Attrapez `TechnasPayError` et testez `error.code` (jamais le message). `error.retryable` vrai : refaites **le même appel**, mêmes valeurs. `error.details` : `missing_amount`, `refundable_amount`, `reason`, `risk`, `account_status`, `console_url`, `version`.

| `error.code` | Que faire |
|---|---|
| `invalid_secret` | Variable absente ou mal copiée : demandez à l'utilisateur de la vérifier |
| `invalid_api_key` | Secret refusé : contacter Technas |
| `invalid_request` | Corriger le champ indiqué dans `error.errors` |
| `invalid_amount`, `amount_too_low` | Montant `int` positif, assez grand pour couvrir les frais ; `amount_too_low` : `e.details.minimum_amount` |
| `currency_not_enabled` | Devise non ouverte sur ce compte : contacter Technas |
| `currency_conversion_unavailable` | Client hors zone CFA en mode `none` : ne pas proposer ce pays ; voir `get_fx_mode()` |
| `country_not_allowed` | Le marchand ne vend pas dans ce pays : ne pas contourner |
| `method_not_enabled` | Moyen éteint sur ce compte : ne pas le proposer ; voir `get_payment_methods()` |
| `network_not_enabled` | Réseau éteint sur ce compte : ne pas le proposer ; voir `get_payment_methods()` |
| `network_not_available` | Réseau pas ouvert dans ce pays : ne pas le proposer ; voir `get_payment_methods(country)` |
| `country_not_available` | Pays pas prêt : `error.details.reason` |
| `risk_not_accepted` | Montrer `error.details.risk` à l'utilisateur ; n'activer qu'avec son accord explicite |
| `fx_mode_unavailable`, `fx_channel_unavailable` | `converted` pas ouvert : proposer `local` (avec accord) ou rester en `none` |
| `quote_expired` | Le client rechoisit son pays sur la page, rien à faire |
| `reference_conflict` | Même référence, autres valeurs : ne pas réutiliser la référence |
| `payment_in_progress` | Réessayer dans une minute, même appel. Sur `cancel_payment` : ne créez PAS de second paiement, attendez l'avis de l'issue |
| `payment_already_completed` | `cancel_payment` : le paiement est déjà payé. Relire le paiement ; ne créer aucun nouvel essai |
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
| `kyc_required` | Seuil d'identité dépassé (`error.details.reason` : `amount` ou `duration`) : proposer la carte ou dire au client que le mobile money n'est pas disponible pour le moment, JAMAIS réessayer ; le responsable dépose sa pièce sur `error.details.console_url` |
| `contract_required` | Le responsable accepte le contrat `error.details.version` dans la console (`error.details.console_url`) ; ne pas réessayer avant |
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

