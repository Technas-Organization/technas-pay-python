# technas-pay (Python)

Encaissez des paiements mobile money via Technas depuis un serveur Python : FastAPI, Django, Flask, ou tout autre framework.

Vous intégrez avec un assistant de code (Codex, Claude Code…) ? Dites-lui de lire [AGENTS.md](AGENTS.md), livré dans le paquet avec [llms.txt](llms.txt).

C'est la même API que le SDK TypeScript [`@technas/pay-next`](https://github.com/Technas-Organization/technas-pay-next), en snake_case.

## 1. Installer

```bash
pip install "technas-pay @ git+https://github.com/Technas-Organization/technas-pay-python@v0.4.0"
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

Fonctions disponibles : `on_completed`, `on_failed` (pas définitif), `on_expired` (définitif), `on_refund`, `on_top_up`, `on_payout_destination`, `on_balance_converted`, `on_fx_mode_updated`. Un type inconnu est acquitté sans rien appeler. Une exception dans votre fonction doit remonter : Technas relivrera.

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

Routes : `GET /methods?orderId=`, `POST /payments` `{orderId}`, `GET /payments/{id}`, `POST /webhook` (adresse à déclarer à Technas). Les crochets peuvent être sync ou async. Les essais expirés sont sautés tout seuls (`42-1`, `42-2`…) : rien à stocker. Contrat : [`contract/app-contract.v1.json`](contract/app-contract.v1.json), rejoué par ce paquet et par le client Flutter.

## 4. Tout le reste

| Méthode | Rôle |
|---|---|
| `get_payment(id)`, `get_payment_by_reference(ref)` | Statut ; rattraper un webhook manqué |
| `list_transactions(limit=, status=, cursor=)` | Historique paginé (`next_cursor`) |
| `refund(payment_id, amount=?, fees="partner"\|"customer", reference=?)` | Rembourser au numéro qui a payé |
| `record_external_refund(payment_id, amount=, method=, note=?, recorded_by=?)` | Noter un remboursement fait hors Technas |
| `get_refund(id)` | Statut d'un remboursement |
| `top_up_balance(amount=, currency=, return_url=?)` | Recharger le solde (ouvrir `checkout_url`) |
| `get_balance()`, `get_settlements()` | Solde par devise, reversements |
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
    elif e.retryable:
        ...                         # refaire le même appel, mêmes valeurs
    else:
        raise
```

La liste complète des codes et quoi faire est dans [AGENTS.md](AGENTS.md#erreurs).

## La règle de relance

**Une référence = un essai.** `failed` n'est pas définitif : le client peut encore payer sur la même page pendant une heure. Ne relancez qu'après `payment.expired`, avec une nouvelle référence (`retry_reference(order_id, attempt + 1)`).

## Paiement par carte

1. **Compte carte, une fois** : au clic du RESPONSABLE sur « Activer la carte », `link = pay.start_card_onboarding(country="TG", return_url=..., refresh_url=...)`, puis redirigez-le vers `link.url` (il y donne son identité et sa banque ; le lien expire en minutes). `refresh_url` : rappelez la méthode et redirigez de nouveau.
2. **Attendez `pay.get_card_account().can_accept_payments`** (ou le webhook `on_card_account_updated`). Avant, `method="card"` lève `card_account_not_ready` (`error.details.account_status`).
3. **Faire payer** : `pay.ask_for_payment(method="card", amount=12.5, currency="EUR", ...)`. Devise obligatoire, décimales permises (jamais × 100).
4. **L'argent va directement sur le compte carte**, commission Technas déjà retirée : il n'apparaît pas dans `get_balance()`. Un remboursement repart sur la carte depuis ce compte (`refund.kind == "stripe"`).
5. **Tableau de bord** (virements, litiges) : `pay.get_card_dashboard_link()` → https://dashboard.stripe.com/, où le responsable se connecte avec SES identifiants Stripe.
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
