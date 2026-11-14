"""Clients synchrone (``TechnasPay``) et asynchrone (``AsyncTechnasPay``) de la passerelle Technas.

Les deux ont exactement les mêmes méthodes ; seules les secondes sont à ``await``. Chaque appel
est décrit une seule fois (``_Call``), puis exécuté par le client sync ou async : la surface ne
peut pas diverger entre les deux.

Le secret ne voyage que dans l'en-tête ``Authorization`` (jamais dans une URL) et n'apparaît
dans aucun message d'erreur ni aucune représentation de l'objet.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Generic, List, Optional, Sequence, TypeVar, Union
from urllib.parse import quote, urlencode

import httpx

from ._version import __version__
from .errors import TechnasPayError, error_from_response
from .models import (
    Balance,
    CardAccount,
    CardOnboardingLink,
    CountryRules,
    FxMode,
    Payment,
    PayoutDestination,
    PayoutDestinationState,
    PayoutNetwork,
    Refund,
    Settlement,
    TopUp,
    TransactionPage,
    currency_for_country,
)
from .webhooks import (
    DEFAULT_TOLERANCE_S,
    SIGNATURE_HEADER,
    Event,
    WebhookHandlers,
    handler_for,
    is_awaitable,
    sign,
    verify_signature,
)

DEFAULT_BASE_URL = "https://pay-api.technas.fr"
SECRET_ENV = "TECHNAS_PAY_SECRET"
_SECRET_RE = re.compile(r"^tps_(live|test)_([^.\s]+)\.([^.\s]+)$")
_USER_AGENT = f"technas-pay-python/{__version__}"

T = TypeVar("T")


@dataclass(frozen=True)
class _Secret:
    mode: str
    api_key: str
    webhook_secret: str


def _parse_secret(secret: Optional[str]) -> _Secret:
    # Le message ne cite jamais la valeur reçue : elle finirait dans des journaux.
    match = _SECRET_RE.match(secret or "")
    if not match:
        raise TechnasPayError(
            "invalid_secret",
            f"Secret Technas absent ou mal formé. Vérifiez la variable {SECRET_ENV}.",
        )
    return _Secret(mode=match.group(1), api_key=match.group(2), webhook_secret=match.group(3))


def _positive_amount(value: Any) -> None:
    """Positif, décimales permises (carte : 12.5 = 12,50 €)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value <= 0 or value == float("inf"):
        raise TechnasPayError("invalid_amount", "amount doit être positif (5000 = 5 000 FCFA, 12.5 = 12,50 €)")


def _positive_int(value: Any, name: str = "amount") -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise TechnasPayError("invalid_amount", f"{name} doit être un entier positif (5000 = 5 000 FCFA)")


@dataclass(frozen=True)
class _Call(Generic[T]):
    method: str
    path: str
    body: Optional[Dict[str, Any]]
    parse: Callable[[Any], T]


@dataclass(frozen=True)
class TestWebhook:
    """Un webhook signé, à poster sur votre route dans un test : ``client.post(url, content=w.body, headers=w.headers)``."""

    body: str
    headers: Dict[str, str]


@dataclass(frozen=True)
class WebhookResult:
    """À renvoyer en 200 (``body``) quand vos fonctions ont réussi."""

    event: Event
    body: Dict[str, Any]


class _Base:
    def __init__(
        self,
        secret: Optional[str] = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        webhook_tolerance_seconds: int = DEFAULT_TOLERANCE_S,
        default_currency: Optional[str] = None,
    ) -> None:
        # Le secret est lu au premier appel : importer le module sans variables d'environnement
        # (build, tests, collecte statique de Django) ne casse rien.
        self.__raw_secret = secret
        self.__parsed: Optional[_Secret] = None
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._tolerance = webhook_tolerance_seconds
        self._default_currency = (default_currency or "").strip().upper() or None

    def __repr__(self) -> str:
        return f"{type(self).__name__}(base_url={self._base_url!r})"

    def _secrets(self) -> _Secret:
        if self.__parsed is None:
            raw = self.__raw_secret if self.__raw_secret is not None else os.environ.get(SECRET_ENV)
            self.__parsed = _parse_secret(raw)
        return self.__parsed

    @property
    def test_mode(self) -> bool:
        """Vrai avec un secret ``tps_test_`` : aucun argent ne circule."""
        return self._secrets().mode == "test"

    def _headers(self, with_body: bool) -> Dict[str, str]:
        headers = {"Authorization": f"Bearer {self._secrets().api_key}", "User-Agent": _USER_AGENT}
        if with_body:
            headers["Content-Type"] = "application/json"
        return headers

    @staticmethod
    def _result(response: httpx.Response, parse: Callable[[Any], T]) -> T:
        try:
            data = response.json()
        except ValueError:
            data = None
        if response.status_code >= 400:
            raise error_from_response(response.status_code, data)
        return parse(data)

    # --- Description de chaque appel (partagée par les deux clients) -------------------------

    def _ask_for_payment(
        self,
        *,
        amount: Union[int, float],
        reference: str,
        customer: Dict[str, Any],
        return_url: str,
        method: str = "mobile_money",
        currency: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        allowed_countries: Optional[Sequence[str]] = None,
        excluded_countries: Optional[Sequence[str]] = None,
        ask_country: bool = False,
    ) -> _Call[Payment]:
        if method not in ("mobile_money", "card"):
            raise TechnasPayError("invalid_request", 'method vaut "mobile_money" ou "card"')
        if method == "card":
            _positive_amount(amount)
        else:
            _positive_int(amount)
        # Jamais de devise devinée : sans devise ni pays connu, le payeur choisit son pays.
        resolved = (currency or "").strip().upper() or None
        if not resolved:
            country = (customer or {}).get("country")
            resolved = currency_for_country(country) if country and str(country).strip() else self._default_currency
        body: Dict[str, Any] = {
            "amount": amount,
            "reference": reference,
            "customer": customer,
            "return_url": return_url,
            "metadata": metadata or {},
        }
        if method == "card" and not resolved:
            raise TechnasPayError("currency_required", "currency est obligatoire pour un paiement par carte (EUR, XOF…)")
        if method == "card":
            body["method"] = "card"
        if resolved:
            body["currency"] = resolved
        if description is not None:
            body["description"] = description
        if allowed_countries is not None:
            body["allowed_countries"] = list(allowed_countries)
        if excluded_countries is not None:
            body["excluded_countries"] = list(excluded_countries)
        if ask_country:
            body["ask_country"] = True
        return _Call("POST", "/v1/payments", body, Payment.from_api)

    @staticmethod
    def _get_payment(payment_id: str) -> _Call[Payment]:
        return _Call("GET", f"/v1/payments/{quote(payment_id, safe='')}", None, Payment.from_api)

    @staticmethod
    def _get_payment_by_reference(reference: str) -> _Call[Payment]:
        return _Call("GET", f"/v1/payments?{urlencode({'reference': reference})}", None, Payment.from_api)

    @staticmethod
    def _list_transactions(limit: Optional[int], status: Optional[str], cursor: Optional[str]) -> _Call[TransactionPage]:
        query: Dict[str, Any] = {}
        if limit is not None:
            query["limit"] = limit
        if status:
            query["status"] = status
        if cursor:
            query["cursor"] = cursor
        suffix = f"?{urlencode(query)}" if query else ""

        def parse(raw: Any) -> TransactionPage:
            return TransactionPage(
                items=[Payment.from_api(p) for p in raw.get("items") or []],
                next_cursor=raw.get("next_cursor"),
            )

        return _Call("GET", f"/v1/transactions{suffix}", None, parse)

    @staticmethod
    def _refund(payment_id: str, amount: Optional[Union[int, float]], fees: str, reference: Optional[str]) -> _Call[Refund]:
        # Décimales permises pour un paiement par carte ; Technas refuse un remboursement mobile
        # money non entier (`invalid_amount`).
        if amount is not None:
            _positive_amount(amount)
        if fees not in ("partner", "customer"):
            raise TechnasPayError("invalid_request", 'fees vaut "partner" ou "customer"')
        body: Dict[str, Any] = {"fees": fees}
        if amount is not None:
            body["amount"] = amount
        if reference:
            body["reference"] = reference
        return _Call("POST", f"/v1/payments/{quote(payment_id, safe='')}/refunds", body, Refund.from_api)

    @staticmethod
    def _record_external_refund(
        payment_id: str, amount: int, method: str, note: Optional[str], recorded_by: Optional[str], reference: Optional[str]
    ) -> _Call[Refund]:
        _positive_int(amount)
        if not (method or "").strip():
            raise TechnasPayError("invalid_request", 'method est obligatoire : "Flooz", "Espèces"…')
        body: Dict[str, Any] = {"amount": amount, "method": method.strip()}
        if note:
            body["note"] = note
        if recorded_by:
            body["recorded_by"] = recorded_by
        if reference:
            body["reference"] = reference
        return _Call("POST", f"/v1/payments/{quote(payment_id, safe='')}/external-refunds", body, Refund.from_api)

    @staticmethod
    def _get_refund(refund_id: str) -> _Call[Refund]:
        return _Call("GET", f"/v1/refunds/{quote(refund_id, safe='')}", None, Refund.from_api)

    @staticmethod
    def _top_up_balance(amount: int, currency: str, return_url: Optional[str]) -> _Call[TopUp]:
        _positive_int(amount)
        body: Dict[str, Any] = {"amount": amount, "currency": currency}
        if return_url:
            body["return_url"] = return_url
        return _Call("POST", "/v1/balance/top-ups", body, TopUp.from_api)

    @staticmethod
    def _get_fx_mode() -> _Call[FxMode]:
        return _Call("GET", "/v1/fx", None, FxMode.from_api)

    @staticmethod
    def _set_fx_mode(mode: str, accept_risk: bool, markup_pct: Optional[float], home_currency: Optional[str]) -> _Call[FxMode]:
        if mode not in ("none", "local", "converted"):
            raise TechnasPayError("invalid_request", 'mode vaut "none", "local" ou "converted"')
        body: Dict[str, Any] = {"mode": mode, "accept_risk": accept_risk is True}
        if markup_pct is not None:
            body["markup_pct"] = markup_pct
        if home_currency:
            body["home_currency"] = home_currency
        return _Call("PUT", "/v1/fx", body, FxMode.from_api)

    @staticmethod
    def _set_country_rules(allowed: Optional[Sequence[str]], excluded: Optional[Sequence[str]]) -> _Call[CountryRules]:
        body = {"allowed": list(allowed) if allowed is not None else None, "excluded": list(excluded or [])}
        return _Call("PUT", "/v1/country-rules", body, CountryRules.from_api)

    @staticmethod
    def _get_balance() -> _Call[List[Balance]]:
        return _Call("GET", "/v1/balance", None, lambda rows: [Balance(currency=r["currency"], amount=r["amount"]) for r in rows or []])

    @staticmethod
    def _get_settlements() -> _Call[List[Settlement]]:
        return _Call("GET", "/v1/settlements", None, lambda rows: [Settlement.from_api(r) for r in rows or []])

    @staticmethod
    def _get_payout_networks(country: str) -> _Call[List[PayoutNetwork]]:
        return _Call(
            "GET",
            f"/v1/payout-networks?{urlencode({'country': country})}",
            None,
            lambda rows: [PayoutNetwork(code=r.get("code"), name=r.get("name"), currency=r.get("currency")) for r in rows or []],
        )

    @staticmethod
    def _get_payout_destination() -> _Call[PayoutDestinationState]:
        return _Call("GET", "/v1/payout-destination", None, PayoutDestinationState.from_api)

    @staticmethod
    def _set_payout_destination(destination: PayoutDestination) -> _Call[PayoutDestinationState]:
        body = {
            "country": destination.country,
            "network": destination.network,
            "phone": destination.phone,
            "beneficiary_name": destination.beneficiary_name,
        }
        return _Call("PUT", "/v1/payout-destination", body, PayoutDestinationState.from_api)

    @staticmethod
    def _start_card_onboarding(
        return_url: str, refresh_url: str, country: Optional[str], email: Optional[str], business_type: Optional[str]
    ) -> _Call[CardOnboardingLink]:
        body: Dict[str, Any] = {"return_url": return_url, "refresh_url": refresh_url}
        if country:
            body["country"] = country.strip().upper()
        if email:
            body["email"] = email
        if business_type:
            body["business_type"] = business_type
        return _Call("POST", "/v1/card/onboarding", body, CardOnboardingLink.from_api)

    @staticmethod
    def _get_card_account() -> _Call[CardAccount]:
        return _Call("GET", "/v1/card/account", None, CardAccount.from_api)

    @staticmethod
    def _get_card_dashboard_link() -> _Call[str]:
        return _Call("POST", "/v1/card/dashboard-link", {}, lambda raw: raw["url"])

    def _simulate_path(self, payment_id: str, outcome: str) -> str:
        if self._secrets().mode != "test":
            raise TechnasPayError("test_only", "simulate_test_payment exige un secret tps_test_")
        if outcome not in ("completed", "failed", "expired"):
            raise TechnasPayError("invalid_outcome", "outcome : completed, failed ou expired")
        return f"{self._base_url}/test-checkout/{quote(payment_id, safe='')}/{outcome}"

    def app_routes(self, **options: Any) -> Any:
        """Routes toutes faites pour votre app Flutter (``technas_pay_flutter``) : voir ``AppRoutes``.

        ``pay.app_routes(authorize=..., resolve_order=..., return_url=..., webhooks=...)``, puis
        ``.fastapi_router()``, ``.flask_blueprint()`` ou ``.django_view()``.
        """
        from .app_routes import AppRoutes

        return AppRoutes(self, **options)

    # --- Webhooks (sans réseau : identiques dans les deux clients) ----------------------------

    def verify_webhook(self, raw_body: Union[str, bytes], signature_header: Optional[str]) -> Event:
        """Vérifie la signature sur le corps BRUT et rend l'événement. Lève ``WebhookVerificationError`` (répondez 400)."""
        return verify_signature(raw_body, signature_header, self._secrets().webhook_secret, self._tolerance)

    def create_test_webhook(
        self,
        type: str,
        *,
        reference: str,
        amount: int,
        currency: str,
        commission: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TestWebhook:
        """Fabrique un webhook ``payment.*`` signé pour tester votre route. Refusé avec un secret réel."""
        if self._secrets().mode != "test":
            raise TechnasPayError("test_only", "create_test_webhook exige un secret tps_test_")
        if type not in ("payment.completed", "payment.failed", "payment.expired"):
            raise TechnasPayError("invalid_request", "type : payment.completed, payment.failed ou payment.expired")
        completed = type == "payment.completed"
        expired = type == "payment.expired"
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        body = json.dumps({
            "id": str(uuid.uuid4()),
            "type": type,
            "created_at": now,
            "data": {
                "id": str(uuid.uuid4()),
                "reference": reference,
                "status": "completed" if completed else "expired" if expired else "failed",
                "amount": amount,
                "currency": currency,
                "commission": commission,
                "net_amount": amount - commission,
                "metadata": metadata or {},
                "completed_at": now if completed else None,
                "expires_at": now,
                "expired_at": now if expired else None,
            },
        })
        return TestWebhook(
            body=body,
            headers={"Content-Type": "application/json", SIGNATURE_HEADER: sign(body, self._secrets().webhook_secret)},
        )


class TechnasPay(_Base):
    """Client synchrone. ``TechnasPay()`` lit ``TECHNAS_PAY_SECRET`` au premier appel.

    Côté serveur uniquement : le secret ne doit jamais atteindre un navigateur ni une app mobile.
    """

    def __init__(self, secret: Optional[str] = None, *, http_client: Optional[httpx.Client] = None, **options: Any) -> None:
        super().__init__(secret, **options)
        self._client = http_client or httpx.Client(timeout=self._timeout)
        self._owns_client = http_client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "TechnasPay":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _run(self, call: _Call[T]) -> T:
        headers = self._headers(call.body is not None)
        try:
            response = self._client.request(
                call.method, f"{self._base_url}{call.path}", headers=headers,
                content=None if call.body is None else json.dumps(call.body),
            )
        except httpx.HTTPError:
            raise TechnasPayError("network_error", "Service de paiement injoignable") from None
        return self._result(response, call.parse)

    def ask_for_payment(self, *, amount: int, reference: str, customer: Dict[str, Any], return_url: str, **options: Any) -> Payment:
        """Crée la demande de paiement ; redirigez ensuite le client vers ``checkout_url``.

        ``amount`` : entier en F CFA (5000 = 5 000 FCFA, jamais × 100), calculé côté serveur.
        ``reference`` : UNE RÉFÉRENCE = UN ESSAI (``retry_reference(order_id, attempt)``).
        ``customer`` : ``{"email", "name", "phone"?, "country"?}``, de vrais champs du client.
        Options : ``currency``, ``description``, ``metadata``, ``allowed_countries``,
        ``excluded_countries``, ``ask_country``. Ne passez pas ``currency`` en zone franc CFA.
        """
        return self._run(self._ask_for_payment(amount=amount, reference=reference, customer=customer, return_url=return_url, **options))

    def get_payment(self, payment_id: str) -> Payment:
        return self._run(self._get_payment(payment_id))

    def get_payment_by_reference(self, reference: str) -> Payment:
        """Pour rattraper un webhook manqué."""
        return self._run(self._get_payment_by_reference(reference))

    def list_transactions(self, *, limit: Optional[int] = None, status: Optional[str] = None, cursor: Optional[str] = None) -> TransactionPage:
        """Vos paiements, du plus récent au plus ancien, par pages (``next_cursor``)."""
        return self._run(self._list_transactions(limit, status, cursor))

    def refund(self, payment_id: str, *, amount: Optional[Union[int, float]] = None, fees: str = "partner", reference: Optional[str] = None) -> Refund:
        """Rembourse un paiement ``completed`` (au numéro qui a payé). Sans ``amount`` : tout ce qui reste."""
        return self._run(self._refund(payment_id, amount, fees, reference))

    def record_external_refund(
        self, payment_id: str, *, amount: int, method: str, note: Optional[str] = None,
        recorded_by: Optional[str] = None, reference: Optional[str] = None,
    ) -> Refund:
        """Enregistre un remboursement fait HORS Technas (Flooz, espèces…). Aucun argent ne bouge."""
        return self._run(self._record_external_refund(payment_id, amount, method, note, recorded_by, reference))

    def get_refund(self, refund_id: str) -> Refund:
        return self._run(self._get_refund(refund_id))

    def top_up_balance(self, *, amount: int, currency: str, return_url: Optional[str] = None) -> TopUp:
        """Recharge votre solde par mobile money : ouvrez ``checkout_url``. Crédit : webhook ``balance.topped_up``."""
        return self._run(self._top_up_balance(amount, currency, return_url))

    def get_fx_mode(self) -> FxMode:
        """Votre option hors zone CFA, ses risques (``risks``), les pays ouverts."""
        return self._run(self._get_fx_mode())

    def set_fx_mode(
        self, *, mode: str, accept_risk: bool = False, markup_pct: Optional[float] = None, home_currency: Optional[str] = None
    ) -> FxMode:
        """N'appelez avec ``accept_risk=True`` qu'APRÈS avoir montré ``get_fx_mode().risks[mode]`` à l'utilisateur
        et obtenu son accord explicite. ``converted`` lève aujourd'hui ``fx_mode_unavailable``."""
        return self._run(self._set_fx_mode(mode, accept_risk, markup_pct, home_currency))

    def set_country_rules(self, *, allowed: Optional[Sequence[str]] = None, excluded: Optional[Sequence[str]] = None) -> CountryRules:
        return self._run(self._set_country_rules(allowed, excluded))

    def start_card_onboarding(
        self, *, return_url: str, refresh_url: str, country: Optional[str] = None,
        email: Optional[str] = None, business_type: Optional[str] = None,
    ) -> CardOnboardingLink:
        """Ouvre (ou reprend) votre compte carte : le RESPONSABLE ouvre ``url`` et y donne son identité
        et sa banque. À appeler au clic (le lien expire en minutes). ``country`` (le vôtre) la 1ʳᵉ fois."""
        return self._run(self._start_card_onboarding(return_url, refresh_url, country, email, business_type))

    def get_card_account(self) -> CardAccount:
        """``can_accept_payments`` décide de tout pour ``ask_for_payment(method="card")``."""
        return self._run(self._get_card_account())

    def get_card_dashboard_link(self) -> str:
        """https://dashboard.stripe.com/ : le responsable s'y connecte avec SES identifiants Stripe."""
        return self._run(self._get_card_dashboard_link())

    def get_balance(self) -> List[Balance]:
        return self._run(self._get_balance())

    def get_settlements(self) -> List[Settlement]:
        return self._run(self._get_settlements())

    def get_payout_networks(self, country: str) -> List[PayoutNetwork]:
        return self._run(self._get_payout_networks(country))

    def get_payout_destination(self) -> PayoutDestinationState:
        return self._run(self._get_payout_destination())

    def set_payout_destination(self, destination: PayoutDestination) -> PayoutDestinationState:
        """Rien ne change avant le clic du responsable du compte dans l'e-mail reçu. Réservé à un administrateur."""
        return self._run(self._set_payout_destination(destination))

    def simulate_test_payment(self, payment_id: str, outcome: str) -> Payment:
        """Mode test : ``completed``, ``failed`` (pas définitif) ou ``expired`` (définitif), sans navigateur."""
        url = self._simulate_path(payment_id, outcome)
        try:
            response = self._client.post(url, headers={"User-Agent": _USER_AGENT}, follow_redirects=False)
        except httpx.HTTPError:
            raise TechnasPayError("network_error", "Service de paiement injoignable") from None
        if response.status_code == 404:
            raise TechnasPayError("payment_not_found", "Paiement de test introuvable", 404)
        return self.get_payment(payment_id)

    def handle_webhook(self, raw_body: Union[str, bytes], signature_header: Optional[str], handlers: WebhookHandlers) -> WebhookResult:
        """Vérifie, puis appelle la bonne fonction. ``WebhookVerificationError`` → répondez 400 ;
        toute autre exception doit remonter (réponse 5xx, Technas relivre)."""
        event = self.verify_webhook(raw_body, signature_header)
        fn = handler_for(event, handlers)
        if fn is not None:
            result = fn(event)
            if is_awaitable(result):
                close = getattr(result, "close", None)
                if close is not None:
                    close()
                raise TypeError("Fonction async dans TechnasPay : utilisez AsyncTechnasPay.handle_webhook")
        return WebhookResult(event=event, body={"received": True})


class AsyncTechnasPay(_Base):
    """Client asynchrone (FastAPI, Starlette, aiohttp…). Mêmes méthodes que ``TechnasPay``, à ``await``."""

    def __init__(self, secret: Optional[str] = None, *, http_client: Optional[httpx.AsyncClient] = None, **options: Any) -> None:
        super().__init__(secret, **options)
        self._client = http_client or httpx.AsyncClient(timeout=self._timeout)
        self._owns_client = http_client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "AsyncTechnasPay":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def _run(self, call: _Call[T]) -> T:
        headers = self._headers(call.body is not None)
        try:
            response = await self._client.request(
                call.method, f"{self._base_url}{call.path}", headers=headers,
                content=None if call.body is None else json.dumps(call.body),
            )
        except httpx.HTTPError:
            raise TechnasPayError("network_error", "Service de paiement injoignable") from None
        return self._result(response, call.parse)

    async def ask_for_payment(self, *, amount: int, reference: str, customer: Dict[str, Any], return_url: str, **options: Any) -> Payment:
        """Voir ``TechnasPay.ask_for_payment``."""
        return await self._run(self._ask_for_payment(amount=amount, reference=reference, customer=customer, return_url=return_url, **options))

    async def get_payment(self, payment_id: str) -> Payment:
        return await self._run(self._get_payment(payment_id))

    async def get_payment_by_reference(self, reference: str) -> Payment:
        return await self._run(self._get_payment_by_reference(reference))

    async def list_transactions(self, *, limit: Optional[int] = None, status: Optional[str] = None, cursor: Optional[str] = None) -> TransactionPage:
        return await self._run(self._list_transactions(limit, status, cursor))

    async def refund(self, payment_id: str, *, amount: Optional[Union[int, float]] = None, fees: str = "partner", reference: Optional[str] = None) -> Refund:
        return await self._run(self._refund(payment_id, amount, fees, reference))

    async def record_external_refund(
        self, payment_id: str, *, amount: int, method: str, note: Optional[str] = None,
        recorded_by: Optional[str] = None, reference: Optional[str] = None,
    ) -> Refund:
        return await self._run(self._record_external_refund(payment_id, amount, method, note, recorded_by, reference))

    async def get_refund(self, refund_id: str) -> Refund:
        return await self._run(self._get_refund(refund_id))

    async def top_up_balance(self, *, amount: int, currency: str, return_url: Optional[str] = None) -> TopUp:
        return await self._run(self._top_up_balance(amount, currency, return_url))

    async def get_fx_mode(self) -> FxMode:
        return await self._run(self._get_fx_mode())

    async def set_fx_mode(
        self, *, mode: str, accept_risk: bool = False, markup_pct: Optional[float] = None, home_currency: Optional[str] = None
    ) -> FxMode:
        """Voir ``TechnasPay.set_fx_mode`` : jamais ``accept_risk=True`` sans l'accord explicite de l'utilisateur."""
        return await self._run(self._set_fx_mode(mode, accept_risk, markup_pct, home_currency))

    async def set_country_rules(self, *, allowed: Optional[Sequence[str]] = None, excluded: Optional[Sequence[str]] = None) -> CountryRules:
        return await self._run(self._set_country_rules(allowed, excluded))

    async def start_card_onboarding(
        self, *, return_url: str, refresh_url: str, country: Optional[str] = None,
        email: Optional[str] = None, business_type: Optional[str] = None,
    ) -> CardOnboardingLink:
        return await self._run(self._start_card_onboarding(return_url, refresh_url, country, email, business_type))

    async def get_card_account(self) -> CardAccount:
        return await self._run(self._get_card_account())

    async def get_card_dashboard_link(self) -> str:
        return await self._run(self._get_card_dashboard_link())

    async def get_balance(self) -> List[Balance]:
        return await self._run(self._get_balance())

    async def get_settlements(self) -> List[Settlement]:
        return await self._run(self._get_settlements())

    async def get_payout_networks(self, country: str) -> List[PayoutNetwork]:
        return await self._run(self._get_payout_networks(country))

    async def get_payout_destination(self) -> PayoutDestinationState:
        return await self._run(self._get_payout_destination())

    async def set_payout_destination(self, destination: PayoutDestination) -> PayoutDestinationState:
        return await self._run(self._set_payout_destination(destination))

    async def simulate_test_payment(self, payment_id: str, outcome: str) -> Payment:
        url = self._simulate_path(payment_id, outcome)
        try:
            response = await self._client.post(url, headers={"User-Agent": _USER_AGENT}, follow_redirects=False)
        except httpx.HTTPError:
            raise TechnasPayError("network_error", "Service de paiement injoignable") from None
        if response.status_code == 404:
            raise TechnasPayError("payment_not_found", "Paiement de test introuvable", 404)
        return await self.get_payment(payment_id)

    async def handle_webhook(self, raw_body: Union[str, bytes], signature_header: Optional[str], handlers: WebhookHandlers) -> WebhookResult:
        """Voir ``TechnasPay.handle_webhook`` ; accepte des fonctions sync ou async."""
        event = self.verify_webhook(raw_body, signature_header)
        fn = handler_for(event, handlers)
        if fn is not None:
            result = fn(event)
            if is_awaitable(result):
                await result
        return WebhookResult(event=event, body={"received": True})
