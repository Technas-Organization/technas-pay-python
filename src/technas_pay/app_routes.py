"""Routes toutes faites pour une app (technas_pay_flutter, ``TechnasPayClient``).

Contrat : ``contract/app-contract.v1.json``, rejoué par les tests de ce paquet ET par ceux du
client Flutter et des SDK Node et Dart.

Vous ne fournissez que deux crochets (sync ou async) :

- ``authorize(request)`` : VOTRE authentification ; rend l'utilisateur, ou ``None`` (401) ;
- ``resolve_order(order_id, user)`` : la commande relue EN BASE (montant, client), ou ``None``
  si elle n'existe pas ou n'est pas à cet utilisateur (404). Le montant ne vient jamais de l'app.

Adaptateurs : ``fastapi_router()``, ``flask_blueprint()``, ``django_view()``, ou ``handle()``
sans framework. Un client ``TechnasPay`` (sync) ou ``AsyncTechnasPay`` convient.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Union
from urllib.parse import unquote

from .errors import TechnasPayError, WebhookVerificationError
from .models import retry_reference, to_app_json
from .webhooks import WebhookHandlers


@dataclass
class AppOrder:
    """Ce que ``resolve_order`` rend (un ``dict`` aux mêmes clés convient aussi)."""

    #: Entier en unité principale (5000 = 5 000 FCFA).
    amount: int
    #: ``{"email", "name", "phone"?, "country"?}``
    customer: Dict[str, str]
    #: Facultative : sans elle, la devise suit le pays du client (zone franc CFA).
    currency: Optional[str] = None
    description: Optional[str] = None
    #: Page de retour (lien qui rouvre l'app) ; sinon ``return_url`` des routes.
    return_url: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AppRequest:
    method: str
    #: Relatif au préfixe de montage : ``/payments``, ``/methods``…
    path: str
    query: Mapping[str, str] = field(default_factory=dict)
    #: Clés en minuscules.
    headers: Mapping[str, str] = field(default_factory=dict)
    raw_body: bytes = b""
    #: L'objet de requête du framework, passé tel quel à ``authorize``.
    original: Any = None


@dataclass
class AppResponse:
    status: int
    body: Any


async def _maybe(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _err(status: int, code: str, message: str, retryable: bool = False) -> AppResponse:
    return AppResponse(status, {"code": code, "message": message, "retryable": retryable})


def _from_pay_error(e: TechnasPayError) -> AppResponse:
    # Panne ou réseau côté Technas : 502, à relire plus tard. Sinon le refus est transmis tel quel.
    if e.code == "network_error" or e.status >= 500 or e.status == 0:
        return _err(502, e.code, e.message, True)
    return _err(e.status, e.code, e.message, e.retryable)


_ESSAI = re.compile(r"^(.+?)-(?:card-)?\d+$")


def _order_id_of(reference: str) -> str:
    """``42-3`` ou ``42-card-3`` → ``42`` (la commande d'un essai)."""
    m = _ESSAI.match(reference)
    return m.group(1) if m else reference


def _app_account(a: Any) -> Dict[str, Any]:
    """Ce que l'app voit du compte carte : ni pays, ni identifiants."""
    return {
        "status": a.status,
        "can_accept_payments": a.can_accept_payments,
        "payouts_enabled": a.payouts_enabled,
        "requirements": a.requirements,
        "disabled_reason": a.disabled_reason,
    }


def _as_order(raw: Any) -> AppOrder:
    if isinstance(raw, AppOrder):
        return raw
    return AppOrder(
        amount=raw["amount"], customer=raw["customer"], currency=raw.get("currency"),
        description=raw.get("description"), return_url=raw.get("return_url"), metadata=raw.get("metadata") or {},
    )


_PAYMENT_PATH = re.compile(r"^/payments/([^/]+)$")
_CANCEL_PATH = re.compile(r"^/payments/([^/]+)/cancel$")
# Un identifiant de commande ou de paiement : hors de ce motif, « introuvable » sans aucun appel.
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


class AppRoutes:
    def __init__(
        self,
        pay: Any,
        *,
        authorize: Callable[[AppRequest], Union[Any, Awaitable[Any]]],
        resolve_order: Callable[[str, Any], Union[Any, Awaitable[Any]]],
        return_url: Optional[str] = None,
        webhooks: Optional[WebhookHandlers] = None,
        max_attempts: int = 20,
        card: bool = False,
        authorize_admin: Optional[Callable[[AppRequest], Union[Any, Awaitable[Any]]]] = None,
    ) -> None:
        """``card=True`` propose la carte (si votre compte carte est prêt et que la commande a une
        ``currency``). ``authorize_admin`` (le MARCHAND, ou ``None`` → 403) ouvre ``GET /card/account``
        et ``POST /card/onboarding`` ; sans lui, ces routes n'existent pas."""
        self._pay = pay
        self._authorize = authorize
        self._resolve = resolve_order
        self._return_url = return_url
        self._webhooks = webhooks
        self._max_attempts = max_attempts
        self._card = card
        self._authorize_admin = authorize_admin

    async def _card_method(self, order: AppOrder) -> Dict[str, Any]:
        if not self._card:
            return {"method": "card", "available": False, "reason": "not_enabled"}
        if not order.currency:
            return {"method": "card", "available": False, "reason": "currency_required"}
        account = await _maybe(self._pay.get_card_account())
        if account.can_accept_payments:
            return {"method": "card", "available": True, "reason": None}
        return {"method": "card", "available": False, "reason": "card_account_not_ready"}

    async def _start(self, order_id: str, order: AppOrder, method: str = "mobile_money") -> Any:
        # Une référence = un essai : `order_id-1`, `-2`… Un essai expiré (payment_expired) est sauté.
        # Un essai d'un autre montant (reference_conflict : le client a changé d'offre) est annulé
        # s'il est encore ouvert, puis l'essai suivant est créé ; payé ou demande en cours chez
        # l'opérateur : AUCUN second paiement, l'erreur remonte à l'app. Un essai en cours (ou payé)
        # au même montant est rendu tel quel.
        attempt = 1
        while True:
            options: Dict[str, Any] = {"metadata": {**order.metadata, "order_id": order_id}}
            if method == "card":
                options["method"] = "card"
            if order.currency:
                options["currency"] = order.currency
            if order.description:
                options["description"] = order.description
            try:
                return await _maybe(self._pay.ask_for_payment(
                    amount=order.amount,
                    # La carte a ses propres essais : un essai mobile money en cours n'est jamais heurté.
                    reference=f"{order_id}-card-{attempt}" if method == "card" else retry_reference(order_id, attempt),
                    customer=order.customer,
                    return_url=order.return_url or self._return_url,
                    **options,
                ))
            except TechnasPayError as e:
                if attempt >= self._max_attempts or e.code not in ("payment_expired", "reference_conflict"):
                    raise
                if e.code == "reference_conflict":
                    await self._close_previous(f"{order_id}-card-{attempt}" if method == "card" else retry_reference(order_id, attempt))
                attempt += 1

    async def _close_previous(self, reference: str) -> None:
        """L'ancien essai (autre montant) : annulé s'il est encore ouvert. Payé → 409
        ``payment_already_completed`` ; ``payment_in_progress`` à l'annulation remonte tel quel ; un 404
        à l'annulation (serveur qui ne l'a pas encore) laisse passer à l'essai suivant."""
        try:
            previous = await _maybe(self._pay.get_payment_by_reference(reference))
        except TechnasPayError as e:
            if e.code == "payment_not_found":
                return
            raise
        if previous.status == "completed":
            raise TechnasPayError("payment_already_completed", "L'essai précédent de cette commande est déjà payé", 409)
        if previous.status != "expired":
            try:
                await _maybe(self._pay.cancel_payment(previous.id))
            except TechnasPayError as e:
                # Serveur sans l'annulation (404, quel que soit le code) : essai suivant, comme avant.
                # Échoué, rien en cours, page encore ouverte jusqu'à son échéance : on ne la
                # déclare pas close, essai suivant (deux pages payées = remboursement de l'essai en trop).
                if e.status != 404 and e.code != "payment_failed_page_open":
                    raise

    async def handle(self, req: AppRequest) -> AppResponse:
        """Le cœur, sans framework."""
        path = "/" + req.path.strip("/")
        method = req.method.upper()
        headers = {k.lower(): v for k, v in req.headers.items()}

        if method == "POST" and path == "/webhook":
            if self._webhooks is None:
                return _err(404, "not_found", "Webhook non branché")
            try:
                result = await _maybe(self._pay.handle_webhook(
                    req.raw_body, headers.get("x-technas-payment-signature"), self._webhooks))
            except WebhookVerificationError:
                return AppResponse(400, {"error": "invalid_signature"})
            # Une erreur de vos fonctions remonte (500) : Technas relivrera.
            return AppResponse(200, result.body)

        if (method == "GET" and path == "/card/account") or (method == "POST" and path == "/card/onboarding"):
            return await self._card_routes(req, path)

        cancel = _CANCEL_PATH.match(path) if method == "POST" else None
        known = (method == "GET" and (path == "/methods" or _PAYMENT_PATH.match(path))) or (method == "POST" and (path == "/payments" or cancel))
        if not known:
            return _err(404, "not_found", "Route inconnue")

        user = await _maybe(self._authorize(req))
        if user is None:
            return _err(401, "unauthorized", "Connexion requise")

        try:
            if path == "/methods":
                order_id = req.query.get("orderId")
                if not order_id:
                    return _err(400, "invalid_request", "orderId manquant")
                if not _ID.fullmatch(order_id):
                    return _err(404, "order_not_found", "Commande introuvable")
                raw = await _maybe(self._resolve(order_id, user))
                if raw is None:
                    return _err(404, "order_not_found", "Commande introuvable")
                order = _as_order(raw)
                return AppResponse(200, {
                    "amount": order.amount,
                    "currency": order.currency,
                    # La carte s'ouvre seulement quand le compte carte du marchand est prêt.
                    "methods": [{"method": "mobile_money", "available": True, "reason": None}, await self._card_method(order)],
                })

            if path == "/payments":
                try:
                    body = json.loads(req.raw_body or b"{}")
                except ValueError:
                    return _err(400, "invalid_request", "Corps JSON illisible")
                order_id = body.get("orderId") if isinstance(body, dict) else None
                if not isinstance(order_id, (str, int)) or isinstance(order_id, bool) or str(order_id) == "":
                    return _err(400, "invalid_request", "orderId manquant")
                order_id = str(order_id)
                if not _ID.fullmatch(order_id):
                    return _err(404, "order_not_found", "Commande introuvable")
                pay_method = body.get("method") or "mobile_money"
                if pay_method not in ("mobile_money", "card"):
                    return _err(400, "invalid_request", 'method vaut "mobile_money" ou "card"')
                if pay_method == "card" and not self._card:
                    return _err(400, "method_not_available", "Le paiement par carte n'est pas ouvert")
                raw = await _maybe(self._resolve(order_id, user))
                if raw is None:
                    return _err(404, "order_not_found", "Commande introuvable")
                order = _as_order(raw)
                if pay_method == "card" and not order.currency:
                    return _err(400, "currency_required", "La commande n'a pas de devise : carte impossible")
                payment = await self._start(order_id, order, pay_method)
                return AppResponse(200, {"payment": to_app_json(payment)})

            # GET /payments/{id} et POST /payments/{id}/cancel : seulement si la commande de ce
            # paiement est à cet utilisateur.
            payment_id = unquote((cancel or _PAYMENT_PATH.match(path)).group(1))  # type: ignore[union-attr]
            if not _ID.fullmatch(payment_id):
                return _err(404, "payment_not_found", "Paiement introuvable")
            try:
                payment = await _maybe(self._pay.get_payment(payment_id))
            except TechnasPayError as e:
                if e.code == "payment_not_found":
                    return _err(404, "payment_not_found", "Paiement introuvable")
                raise
            if await _maybe(self._resolve(_order_id_of(payment.reference), user)) is None:
                return _err(404, "payment_not_found", "Paiement introuvable")
            if cancel:
                # Le client change d'offre : l'essai affiché est annulé avant d'en démarrer un autre.
                payment = await _maybe(self._pay.cancel_payment(payment.id))
            return AppResponse(200, {"payment": to_app_json(payment)})
        except TechnasPayError as e:
            return _from_pay_error(e)

    async def _card_routes(self, req: AppRequest, path: str) -> AppResponse:
        """Routes du MARCHAND (compte carte), derrière ``authorize_admin``."""
        if self._authorize_admin is None:
            return _err(404, "not_found", "Route inconnue")
        admin = await _maybe(self._authorize_admin(req))
        if admin is None or admin is False:
            return _err(403, "forbidden", "Réservé au marchand")
        try:
            if path == "/card/account":
                return AppResponse(200, {"account": _app_account(await _maybe(self._pay.get_card_account()))})
            try:
                body = json.loads(req.raw_body or b"{}")
            except ValueError:
                return _err(400, "invalid_request", "Corps JSON illisible")
            if not isinstance(body, dict) or not isinstance(body.get("returnUrl"), str) or not isinstance(body.get("refreshUrl"), str):
                return _err(400, "invalid_request", "returnUrl et refreshUrl sont obligatoires")
            link = await _maybe(self._pay.start_card_onboarding(
                return_url=body["returnUrl"], refresh_url=body["refreshUrl"], country=body.get("country"),
                email=body.get("email"), business_type=body.get("businessType"),
            ))
            return AppResponse(200, {"url": link.url, "kind": link.kind, "expires_at": link.expires_at,
                                     "account": _app_account(link.account)})
        except TechnasPayError as e:
            return _from_pay_error(e)

    def handle_sync(self, req: AppRequest) -> AppResponse:
        """Pour un serveur synchrone (Flask, Django) avec le client ``TechnasPay``."""
        return asyncio.run(self.handle(req))

    # --- Adaptateurs -------------------------------------------------------------------------

    def fastapi_router(self, prefix: str = "/api/technas-pay") -> Any:
        """``app.include_router(routes.fastapi_router())``."""
        from fastapi import APIRouter, Request
        from fastapi.responses import JSONResponse

        router = APIRouter(prefix=prefix)

        async def endpoint(request: Request, rest: str) -> JSONResponse:
            r = await self.handle(AppRequest(
                method=request.method, path="/" + rest, query=dict(request.query_params),
                headers={k.lower(): v for k, v in request.headers.items()}, raw_body=await request.body(), original=request,
            ))
            return JSONResponse(r.body, status_code=r.status)

        # Annotations réelles : avec `from __future__ import annotations`, FastAPI ne retrouverait pas
        # `Request` (importé ici, hors du module) et lirait `request` comme un paramètre de requête.
        endpoint.__annotations__ = {"request": Request, "rest": str, "return": JSONResponse}
        router.add_api_route("/{rest:path}", endpoint, methods=["GET", "POST"], include_in_schema=False)
        return router

    def flask_blueprint(self, name: str = "technas_pay", url_prefix: str = "/api/technas-pay") -> Any:
        """``app.register_blueprint(routes.flask_blueprint())``."""
        from flask import Blueprint, jsonify, request

        bp = Blueprint(name, __name__, url_prefix=url_prefix)

        def endpoint(rest: str):  # type: ignore[no-untyped-def]
            r = self.handle_sync(AppRequest(
                method=request.method, path="/" + rest, query=request.args.to_dict(),
                headers={k.lower(): v for k, v in request.headers.items()}, raw_body=request.get_data(), original=request,
            ))
            return jsonify(r.body), r.status

        bp.add_url_rule("/<path:rest>", "technas_pay_app", endpoint, methods=["GET", "POST"])
        return bp

    def django_view(self) -> Callable[..., Any]:
        """``path("api/technas-pay/<path:rest>", routes.django_view())`` dans ``urls.py``."""
        from django.http import JsonResponse
        from django.views.decorators.csrf import csrf_exempt

        @csrf_exempt  # l'app s'authentifie par jeton (authorize), le webhook par signature
        def view(request: Any, rest: str) -> Any:
            r = self.handle_sync(AppRequest(
                method=request.method, path="/" + rest, query=request.GET.dict(),
                headers={k.lower(): v for k, v in request.headers.items()}, raw_body=request.body, original=request,
            ))
            return JsonResponse(r.body, status=r.status, safe=False)

        return view
