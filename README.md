# technas-pay (Python)

Encaissez des paiements mobile money et par carte via Technas depuis un serveur Python : FastAPI, Django, Flask, ou tout autre framework.

**Technas Pay réunit le mobile money et la carte dans un seul SDK, du serveur jusqu'à l'app Flutter.** Carte : 1 % (plus les frais de votre compte carte, selon le pays : environ 1,5 % + 0,25 € en Europe). Mobile money : 2 % (les frais de l'opérateur sont payés par le client). Sans abonnement, et une clé de test sans papier pour commencer.

Devises et pays, pièces demandées, versements, comparaison avec les autres prestataires : [le hub Technas Pay](https://github.com/Technas-Organization/technas-pay).

Vous intégrez avec un assistant de code ? Dites-lui de lire [AGENTS.md](AGENTS.md), livré dans le paquet avec [llms.txt](llms.txt).

C'est la même API que le SDK TypeScript [`@technas/pay-next`](https://github.com/Technas-Organization/technas-pay-next), en snake_case.

## 1. Installer

```bash
pip install "technas-pay @ git+https://github.com/Technas-Organization/technas-pay-python@v0.7.1"
```

Seule dépendance : `httpx`. Python 3.9 ou plus récent.

Technas vous remet **un secret**. Mettez-le dans les variables d'environnement du serveur :

```bash
TECHNAS_PAY_SECRET=tps_test_...
```

Un secret `tps_test_` ne déplace aucun argent. Un secret `tps_live_` encaisse pour de vrai. Le code est le même.

## 2. Demander un paiement

```python
from technas_pay import TechnasPay, retry_reference

pay = TechnasPay()  # lit TECHNAS_PAY_SECRET au premier appel

payment = pay.ask_for_payment(
    amount=5000,                                         # int : 5 000 FCFA
    reference=retry_reference(order.id, order.attempt),  # "42-1" : une référence = un essai
    customer={"email": "client@exemple.com", "name": "Awa Mensah"},
    return_url="https://votre-site.com/merci",
)
order.payment_id = payment.id
# redirigez le client vers payment.checkout_url
```

En asynchrone (FastAPI…), même chose avec `AsyncTechnasPay` et `await`.

Calculez le montant côté serveur. Ne passez pas `currency` si vous vendez en francs CFA : la devise suit le pays du client (`customer["country"]`), ou le client choisit son pays sur la page de paiement. Options : `currency`, `description`, `metadata` (4 Ko), `allowed_countries`, `excluded_countries`, `ask_country`.

## 3. Recevoir le webhook

Technas signe chaque webhook (`X-Technas-Payment-Signature: t=…,s=…`). Le SDK vérifie la signature sur le **corps brut**, puis appelle votre fonction.

```python
from technas_pay import WebhookHandlers, WebhookVerificationError

def on_completed(event):
    mark_paid(event.payment.reference)      # idempotent : un webhook peut arriver deux fois

def on_expired(event):
    increment_attempt(event.payment.reference)  # seul moment où relancer, avec une NOUVELLE référence

HANDLERS = WebhookHandlers(on_completed=on_completed, on_expired=on_expired)

# dans votre vue POST /api/technas-pay
try:
    result = pay.handle_webhook(raw_body, signature_header, HANDLERS)
except WebhookVerificationError:
    return 400
return 200, result.body
```

Exemples complets, testés : [FastAPI](examples/fastapi_app.py), [Flask](examples/flask_app.py), [Django](examples/django_app/views.py).

Fonctions disponibles : `on_completed`, `on_failed` (pas définitif), `on_expired` (définitif), `on_refund`, `on_top_up`, `on_payout_destination`, `on_balance_converted`, `on_fx_mode_updated`, `on_card_account_updated`, `on_kyc_required` (seuil d'identité, voir plus bas). Un type inconnu est acquitté sans rien appeler. Une exception dans votre fonction doit remonter : Technas relivrera.

## Pour une app mobile : les routes toutes faites

Votre app Flutter ([`technas_pay_flutter`](https://github.com/Technas-Organization/technas-pay-dart/tree/main/technas_pay_flutter)) appelle ces routes sans glue. Vous ne fournissez que votre authentification et la lecture de la commande en base :

```python
routes = pay.app_routes(
    authorize=lambda req: user_from_token(req.headers.get("authorization")),   # None → 401
    resolve_order=lambda order_id, user: (                                     # relue EN BASE, None → 404
        (o := Order.get(id=order_id, user=user)) and
        {"amount": o.total, "customer": {"email": user.email, "name": user.name}}
    ),
    return_url="https://votre-app.com/paiement/retour",
    webhooks=WebhookHandlers(on_completed=lambda e: Order.mark_paid(e.payment.reference)),
)
app.include_router(routes.fastapi_router())          # FastAPI → /api/technas-pay/...
# Flask : app.register_blueprint(routes.flask_blueprint())
# Django : path("api/technas-pay/<path:rest>", routes.django_view())
```

Routes : `GET /methods?orderId=`, `POST /payments` `{orderId}`, `GET /payments/{id}`, `POST /payments/{id}/cancel` (le client change d'offre : l'essai affiché est annulé), `POST /webhook` (adresse à déclarer à Technas). Les crochets peuvent être sync ou async. Les essais expirés sont sautés tout seuls (`42-1`, `42-2`…) : rien à stocker. Si la commande a changé de montant, `POST /payments` annule lui-même l'ancien essai encore ouvert avant d'en créer un autre ; s'il est déjà payé (`payment_already_completed`) ou qu'une demande est en cours chez l'opérateur (`payment_in_progress`), aucun second paiement n'est créé et l'erreur est rendue à l'app. Contrat : [`contract/app-contract.v1.json`](contract/app-contract.v1.json), rejoué par ce paquet et par le client Flutter.

## 4. Tout le reste

| Méthode | Rôle |
|---|---|
| `get_payment(id)`, `get_payment_by_reference(ref)` | Statut ; rattraper un webhook manqué |
| `cancel_payment(id)` | Annuler un paiement que personne n'a commencé à payer (le client change d'offre) |
| `list_transactions(limit=, status=, cursor=)` | Historique paginé (`next_cursor`) |
| `refund(payment_id, amount=?, fees="partner"\|"customer", reference=?)` | Rembourser au numéro qui a payé |
| `record_external_refund(payment_id, amount=, method=, note=?, recorded_by=?)` | Noter un remboursement fait hors Technas |
| `get_refund(id)` | Statut d'un remboursement |
| `top_up_balance(amount=, currency=, return_url=?)` | Recharger le solde (ouvrir `checkout_url`) |
| `get_balance()`, `get_settlements()` | Solde par devise, reversements |
| `get_account_kyc()` | Seuil d'identité du compte (à montrer au marchand) |
| `get_fx_mode()`, `set_fx_mode(mode=, accept_risk=, markup_pct=?)` | Clients hors zone franc CFA (option à risque) |
| `set_country_rules(allowed=?, excluded=?)` | Où vous vendez |
| `get_payout_networks(country)`, `get_payout_destination()`, `set_payout_destination(...)` | Numéro de reversement |
| `verify_webhook(raw, signature)` | Vérifier sans dispatcher |
| `create_test_webhook(type, reference=, amount=, currency=)` | Webhook signé pour vos tests (secret test seulement) |
| `simulate_test_payment(id, "completed"\|"failed"\|"expired")` | Jouer le payeur en test |

Pour une app mobile : `to_app_json(payment)` est ce que votre backend lui renvoie (voir `technas_pay_flutter` et l'exemple FastAPI).

Aides : `can_retry(payment)` (vrai seulement pour `expired`), `is_final(payment)`, `retry_reference(order_id, attempt)`, `currency_for_country("TG")`.

## Erreurs

```python
from technas_pay import TechnasPayError

try:
    pay.ask_for_payment(...)
except TechnasPayError as e:
    if e.code == "payment_expired":
        order.attempt += 1          # nouvelle référence, jamais le même appel
    elif e.code == "payment_already_completed":
        ...                         # cancel_payment : déjà payé, relire, ne rien créer
    elif e.retryable:
        ...                         # refaire le même appel, mêmes valeurs
    else:
        raise
```

`kyc_required` (409) : seuil d'identité dépassé, les nouveaux paiements mobile money sont refusés. Ne réessayez jamais : proposez la carte, ou dites au client que le mobile money n'est pas disponible pour le moment. `e.details.console_url` : où le responsable dépose sa pièce ; `e.details.reason` : `amount` ou `duration`. `contract_required` (409) : le responsable accepte le contrat `e.details.version` dans la console (`e.details.console_url`). Aucun des deux n'est `retryable`.

La liste complète des codes et quoi faire est dans [AGENTS.md](AGENTS.md#erreurs).

## Seuil d'identité

En réel, un compte sans pièce d'identité vérifiée voit ses **nouveaux** paiements mobile money refusés (`kyc_required`) au-delà de 1 000 000 F CFA encaissés en cumul, ou 30 jours après son premier paiement réel encaissé. Rien de ce qui existe n'est bloqué : paiement en cours, remboursement, reversement, lecture. La carte n'est pas concernée. Le responsable dépose sa pièce dans la console (`console_url`).

```python
kyc = pay.get_account_kyc()   # à montrer au marchand, jamais au client final
if kyc.stage in ("soon", "blocked"):
    show_banner(f"{kyc.collected_xof} / {kyc.threshold_xof} F CFA, échéance {kyc.deadline}", kyc.console_url)
```

Les chiffres (seuil, délai) viennent de l'API (`threshold_xof`, `grace_days`) : ne les écrivez jamais en dur. Branchez `on_kyc_required` pour prévenir le responsable : l'avis `account.kyc_required` part à 80 % du seuil (`reason="amount_80"`), 7 jours avant l'échéance (`"deadline_7d"`), puis au premier refus (`"blocked"`), avec `event.kyc_required.collected_xof`, `threshold_xof`, `deadline`, `blocked`, `console_url`. L'avis porte aussi l'état du compte, comme `get_account_kyc()` : `stage`, `block_reason` (`amount` ou `duration`), `days_elapsed`, `grace_days`, `first_collected_at` (facultatifs : `None` sur un avis plus ancien).

## La règle de relance

**Une référence = un essai.** `failed` n'est pas définitif : le client peut encore payer sur la même page pendant une heure. Ne relancez qu'après `payment.expired`, avec une nouvelle référence (`retry_reference(order_id, attempt + 1)`).

Le client change d'offre ou revient plus tard : c'est un NOUVEL essai (`retry_reference(facture_id, n + 1)`), jamais un refus, même si l'essai précédent est encore `pending` ou `failed`. Autre offre : ANNULEZ d'abord l'ancien essai (`pay.cancel_payment(ancien_id)`), puis créez le nouveau avec une NOUVELLE référence. Si l'annulation répond `payment_in_progress` (une demande est en cours chez l'opérateur), ne créez PAS de second paiement : attendez l'avis de l'issue ; `payment_already_completed` : le client a payé l'ancienne offre, relisez le paiement et ne créez rien. Un paiement annulé reste `expired` (`can_retry` marche tel quel), avec `canceled_at` et `cancel_reason` ; son avis `payment.expired` porte `reason` = `canceled` (`timeout` pour une échéance passée). Réutiliser une référence avec un autre montant lève `reference_conflict` ; les routes toutes faites annulent l'ancien essai et passent seules à l'essai suivant. Au retour du client, relisez le dernier essai (`get_payment`) avant de dire quoi que ce soit. La facture n'est payée que sur `payment.completed` ; si le client a payé deux pages, remboursez l'essai en trop (`refund`).

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

## Paiement par carte

1. **Compte carte, une fois** : au clic du RESPONSABLE sur « Activer la carte », `link = pay.start_card_onboarding(country="TG", return_url=..., refresh_url=...)`, puis redirigez-le vers `link.url` : il y donne son identité et sa banque sur la page sécurisée de notre partenaire carte (Stripe), qui vous fera accepter son contrat (le lien expire en minutes). `refresh_url` : rappelez la méthode et redirigez de nouveau.
2. **Attendez `pay.get_card_account().can_accept_payments`** (ou le webhook `on_card_account_updated`). Avant, `method="card"` lève `card_account_not_ready` (`error.details.account_status`).
3. **Faire payer** : `pay.ask_for_payment(method="card", amount=12.5, currency="EUR", ...)`. Devise obligatoire, décimales permises (jamais × 100).
4. **L'argent va directement sur le compte carte**, commission Technas déjà retirée : il n'apparaît pas dans `get_balance()`. Un remboursement repart sur la carte depuis ce compte (`refund.kind == "card"`).
5. **Tableau de bord** (virements, litiges) : `pay.get_card_dashboard_link()` → le tableau de bord de votre compte carte, où le responsable se connecte avec SES identifiants.
6. **App mobile** : `pay.app_routes(..., card=True, authorize_admin=...)` ; `/methods` n'ouvre la carte que si le compte est prêt et que `resolve_order` rend une `currency`.

## Sécurité

- Le secret reste côté serveur. Jamais dans une app mobile, un navigateur, un log ou une URL : le SDK ne l'envoie que dans l'en-tête `Authorization`.
- Seul le webhook prouve le paiement, jamais la page `return_url`.
- Remboursements et numéro de reversement : réservés à un administrateur.

## Développer

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"
pytest
```

Les tests tournent contre un faux serveur en mémoire ([tests/fake_gateway.py](tests/fake_gateway.py)) qui reproduit les règles de la passerelle.

## Licence

MIT.
