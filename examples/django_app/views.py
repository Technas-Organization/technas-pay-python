"""Webhook Technas Pay avec Django (client sync).

urls.py :
    path("api/technas-pay", views.technas_webhook)

settings : TECHNAS_PAY_SECRET dans l'environnement du serveur, jamais dans le dépôt.
"""

from django.http import HttpResponseBadRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from technas_pay import Event, TechnasPay, WebhookHandlers, WebhookVerificationError

pay = TechnasPay()

PAID = set()  # remplacez par votre modèle : Order.objects.filter(reference=...).update(paid=True)


def on_completed(event: Event) -> None:
    PAID.add(event.payment.reference)


HANDLERS = WebhookHandlers(on_completed=on_completed)


@csrf_exempt  # Technas signe la requête : le jeton CSRF n'a pas de sens ici
@require_POST
def technas_webhook(request):
    try:
        result = pay.handle_webhook(request.body, request.headers.get("X-Technas-Payment-Signature"), HANDLERS)
    except WebhookVerificationError:
        return HttpResponseBadRequest("invalid_signature")
    return JsonResponse(result.body)
