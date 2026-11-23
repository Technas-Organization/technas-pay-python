"""Vérification des webhooks Technas et lecture des événements.

En-tête : ``X-Technas-Payment-Signature: t=<horodatage>,s=<hmac_sha256_hex>``, où le HMAC porte
sur ``f"{t}.{corps_brut}"`` avec la partie webhook du secret. Vérifiez TOUJOURS le corps BRUT
(les octets reçus), jamais un JSON re-sérialisé.
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional, Union

from .errors import WebhookVerificationError
from .models import CardAccount, PaymentFx, PayoutDestinationView, Refund

SIGNATURE_HEADER = "X-Technas-Payment-Signature"
DEFAULT_TOLERANCE_S = 300

_SIGNATURE_RE = re.compile(r"^t=(\d+),s=([0-9a-fA-F]+)$")


@dataclass(frozen=True)
class EventPayment:
    id: str
    reference: str
    status: str
    amount: Any
    currency: Optional[str]
    commission: Any
    net_amount: Any
    metadata: Dict[str, Any]
    completed_at: Optional[str]
    #: Hors zone CFA : le montant demandé en F CFA ; ``amount``/``currency`` = ce qui a été payé.
    original_amount: Any
    original_currency: Optional[str]
    fx: Optional[PaymentFx]
    expires_at: Optional[str]
    expired_at: Optional[str]
    #: ``payment.expired`` : ``canceled`` (annulé par vous, ``cancel_payment``) ou ``timeout`` (délai
    #: dépassé) ; ``None`` dans un avis plus ancien.
    reason: Optional[str] = None
    #: ``payment.expired`` annulé : quand.
    canceled_at: Optional[str] = None
    cancel_reason: Optional[str] = None
    #: Ce que Technas a demandé au réseau de paiement, avant ses frais (= ``amount`` sauf frais au client).
    customer_amount: Any = None
    #: Qui a payé les frais : ``partner`` ou ``customer``.
    fees_paid_by: str = "partner"
    #: Ce que le client a payé, frais du réseau de paiement compris (``None`` : inconnu).
    customer_total: Any = None
    #: Les frais du réseau de paiement dans ``customer_total``.
    processing_fee: Any = None
    #: Le réseau avec lequel le client a payé (``mixx``, ``moov``…) ; ``None`` sinon.
    network: Optional[str] = None


@dataclass(frozen=True)
class TopUpInfo:
    id: str
    amount: Any
    currency: str
    completed_at: Optional[str]


@dataclass(frozen=True)
class SettlementInfo:
    """``settlement.completed`` / ``settlement.failed`` : l'issue d'un reversement de votre solde.

    Sur ``settlement.failed``, ``amount`` est recrédité sur votre solde.
    """

    id: str
    #: Ce qui a quitté votre solde.
    amount: Any
    #: Frais du versement mobile money, à votre charge.
    fee: Any
    #: Ce qui arrive sur votre numéro.
    net_amount: Any
    currency: str
    #: ``completed`` | ``failed``
    status: str
    created_at: Optional[str]


@dataclass(frozen=True)
class Conversion:
    id: str
    from_currency: str
    from_amount: Any
    to_currency: str
    gross_amount: Any
    cost: Any
    credited_amount: Any
    effective_rate: Any


@dataclass(frozen=True)
class FxModeChange:
    mode: str
    previous_mode: str
    markup_pct: Any
    home_currency: Optional[str]


@dataclass(frozen=True)
class KycRequired:
    """``account.kyc_required`` : le seuil d'identité approche ou est atteint.

    ``reason`` : ``amount_80`` (80 % du seuil encaissés), ``deadline_7d`` (échéance dans 7 jours),
    ``blocked`` (premier refus). Une valeur que cette version ne connaît pas est gardée telle quelle.
    Prévenez le RESPONSABLE : il dépose sa pièce d'identité sur ``console_url``.

    L'avis porte aussi l'état du compte, comme ``get_account_kyc`` : ``stage`` (``ok``, ``soon``,
    ``blocked``, ``verified``, ``not_applicable``), ``block_reason`` (``amount`` ou ``duration``,
    si bloqué), ``days_elapsed``, ``grace_days``, ``first_collected_at``. Tous facultatifs : un
    avis plus ancien ne les porte pas, ils valent alors ``None``.
    """

    reason: Optional[str]
    collected_xof: Any
    threshold_xof: Any
    deadline: Optional[str]
    blocked: bool
    console_url: Optional[str]
    stage: Optional[str] = None
    block_reason: Optional[str] = None
    days_elapsed: Optional[int] = None
    grace_days: Optional[int] = None
    first_collected_at: Optional[str] = None


@dataclass(frozen=True)
class Event:
    """Un événement vérifié. Un seul des champs de contenu est rempli selon ``type``.

    ``id`` est stable d'une livraison à l'autre : servez-vous-en pour ignorer un doublon.
    """

    id: str
    type: str
    created_at: Optional[str]
    payment: Optional[EventPayment] = None
    refund: Optional[Refund] = None
    destination: Optional[PayoutDestinationView] = None
    top_up: Optional[TopUpInfo] = None
    #: ``settlement.completed`` / ``settlement.failed`` : l'issue d'un reversement.
    settlement: Optional[SettlementInfo] = None
    conversion: Optional[Conversion] = None
    fx: Optional[FxModeChange] = None
    #: ``card_account.updated`` : le nouvel état de votre compte carte.
    card_account: Optional[CardAccount] = None
    #: ``account.kyc_required`` : où en est le seuil d'identité.
    kyc_required: Optional[KycRequired] = None
    #: Données brutes (toujours présentes), utiles pour un type que cette version ne connaît pas.
    data: Optional[Dict[str, Any]] = None


def parse_event(raw: Any) -> Event:
    if not isinstance(raw, dict) or not raw.get("id") or not raw.get("type") or not isinstance(raw.get("data"), dict):
        raise WebhookVerificationError("Webhook illisible")
    d: Dict[str, Any] = raw["data"]
    t: str = raw["type"]
    base = dict(id=raw["id"], type=t, created_at=raw.get("created_at"), data=d)
    if t in ("payout_destination.change_scheduled", "payout_destination.updated"):
        return Event(**base, destination=PayoutDestinationView.from_api(d))
    if t in ("refund.completed", "refund.failed", "refund.recorded"):
        return Event(**base, refund=Refund.from_api(d))
    if t == "balance.converted":
        return Event(**base, conversion=Conversion(
            id=d.get("id"), from_currency=d.get("from_currency"), from_amount=d.get("from_amount"),
            to_currency=d.get("to_currency"), gross_amount=d.get("gross_amount"), cost=d.get("cost"),
            credited_amount=d.get("credited_amount"), effective_rate=d.get("effective_rate"),
        ))
    if t == "fx_mode.updated":
        return Event(**base, fx=FxModeChange(
            mode=d.get("mode"), previous_mode=d.get("previous_mode"),
            markup_pct=d.get("markup_pct"), home_currency=d.get("home_currency"),
        ))
    if t == "card_account.updated":
        return Event(**base, card_account=CardAccount.from_api(d))
    if t == "account.kyc_required":
        return Event(**base, kyc_required=KycRequired(
            reason=d.get("reason"), collected_xof=d.get("collected_xof"), threshold_xof=d.get("threshold_xof"),
            deadline=d.get("deadline"), blocked=bool(d.get("blocked")), console_url=d.get("console_url"),
            stage=d.get("stage"), block_reason=d.get("block_reason"), days_elapsed=d.get("days_elapsed"),
            grace_days=d.get("grace_days"), first_collected_at=d.get("first_collected_at"),
        ))
    if t == "balance.topped_up":
        return Event(**base, top_up=TopUpInfo(
            id=d.get("id"), amount=d.get("amount"), currency=d.get("currency"), completed_at=d.get("completed_at"),
        ))
    if t in ("settlement.completed", "settlement.failed"):
        return Event(**base, settlement=SettlementInfo(
            id=d.get("id"), amount=d.get("amount"), fee=d.get("fee"), net_amount=d.get("net_amount"),
            currency=d.get("currency"), status=d.get("status"), created_at=d.get("created_at"),
        ))
    if t in ("payment.completed", "payment.failed", "payment.expired"):
        if not d.get("id"):
            raise WebhookVerificationError("Webhook illisible")
        return Event(**base, payment=EventPayment(
            id=d["id"], reference=d.get("reference"), status=d.get("status"), amount=d.get("amount"),
            currency=d.get("currency"), commission=d.get("commission"), net_amount=d.get("net_amount"),
            metadata=d.get("metadata") or {}, completed_at=d.get("completed_at"),
            original_amount=d.get("original_amount"), original_currency=d.get("original_currency"),
            fx=PaymentFx.from_api(d.get("fx")), expires_at=d.get("expires_at"), expired_at=d.get("expired_at"),
            reason=d.get("reason"), canceled_at=d.get("canceled_at"), cancel_reason=d.get("cancel_reason"),
            customer_amount=d.get("amount") if d.get("customer_amount") is None else d.get("customer_amount"),
            fees_paid_by=d.get("fees_paid_by") or "partner",
            customer_total=d.get("customer_total"), processing_fee=d.get("processing_fee"),
            network=d.get("network"),
        ))
    return Event(**base)


def sign(body: Union[str, bytes], webhook_secret: str, timestamp: Optional[int] = None) -> str:
    """Rend la valeur de l'en-tête de signature pour ce corps (sert aux tests)."""
    ts = int(time.time()) if timestamp is None else int(timestamp)
    raw = body.encode("utf-8") if isinstance(body, str) else body
    digest = hmac.new(webhook_secret.encode("utf-8"), f"{ts}.".encode("utf-8") + raw, hashlib.sha256).hexdigest()
    return f"t={ts},s={digest}"


def verify_signature(
    raw_body: Union[str, bytes],
    signature_header: Optional[str],
    webhook_secret: str,
    tolerance_seconds: int = DEFAULT_TOLERANCE_S,
    now: Optional[float] = None,
) -> Event:
    """Vérifie la signature sur le corps BRUT puis rend l'événement. Lève ``WebhookVerificationError``."""
    match = _SIGNATURE_RE.match((signature_header or "").strip())
    if not match:
        raise WebhookVerificationError("Signature absente ou mal formée")
    timestamp = int(match.group(1))
    current = time.time() if now is None else now
    if abs(current - timestamp) > tolerance_seconds:
        raise WebhookVerificationError("Signature trop ancienne")
    raw = raw_body.encode("utf-8") if isinstance(raw_body, str) else bytes(raw_body)
    expected = hmac.new(webhook_secret.encode("utf-8"), f"{timestamp}.".encode("utf-8") + raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, match.group(2).lower()):
        raise WebhookVerificationError("Signature invalide")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise WebhookVerificationError("Webhook illisible") from None
    return parse_event(parsed)


Handler = Callable[[Event], Union[None, Awaitable[None]]]


@dataclass
class WebhookHandlers:
    """Vos fonctions, une par famille d'événements. Toutes facultatives.

    - ``on_completed`` : marquez la commande payée (``event.payment.reference``).
    - ``on_failed`` : PAS définitif ; n'annulez rien, ne relancez rien.
    - ``on_expired`` : définitif ; seul moment où relancer, avec une NOUVELLE référence.
    - ``on_refund`` : ``refund.completed`` / ``refund.failed`` / ``refund.recorded`` ; prévenez le client.
    - ``on_settlement`` : ``settlement.completed`` / ``settlement.failed`` ; l'issue d'un reversement
      (``event.settlement``). Sur ``failed``, la somme est recréditée sur votre solde.
    - ``on_fx_mode_updated`` : si ce n'est pas vous, prévenez Technas (clé compromise ?).
    - ``on_kyc_required`` : prévenez le RESPONSABLE (pas le client) de déposer sa pièce
      (``event.kyc_required.console_url``).

    Une exception doit REMONTER : la réponse n'est pas 2xx et Technas relivre l'événement.
    """

    on_completed: Optional[Handler] = None
    on_failed: Optional[Handler] = None
    on_expired: Optional[Handler] = None
    on_payout_destination: Optional[Handler] = None
    on_refund: Optional[Handler] = None
    on_top_up: Optional[Handler] = None
    on_settlement: Optional[Handler] = None
    on_balance_converted: Optional[Handler] = None
    on_fx_mode_updated: Optional[Handler] = None
    #: Votre compte carte a changé d'état (ouvert, à compléter, suspendu…).
    on_card_account_updated: Optional[Handler] = None
    #: ``account.kyc_required`` : seuil d'identité à 80 %, échéance dans 7 jours, ou premier refus.
    on_kyc_required: Optional[Handler] = None


def handler_for(event: Event, handlers: WebhookHandlers) -> Optional[Handler]:
    t = event.type
    if t == "payment.completed":
        return handlers.on_completed
    if t == "payment.failed":
        return handlers.on_failed
    if t == "payment.expired":
        return handlers.on_expired
    if t.startswith("payout_destination."):
        return handlers.on_payout_destination
    if t.startswith("refund."):
        return handlers.on_refund
    if t == "balance.topped_up":
        return handlers.on_top_up
    if t.startswith("settlement."):
        return handlers.on_settlement
    if t == "balance.converted":
        return handlers.on_balance_converted
    if t == "fx_mode.updated":
        return handlers.on_fx_mode_updated
    if t == "card_account.updated":
        return handlers.on_card_account_updated
    if t == "account.kyc_required":
        return handlers.on_kyc_required
    # Type inconnu : acquitté sans rien appeler (sinon relivré pendant trois jours).
    return None


def is_awaitable(value: Any) -> bool:
    return inspect.isawaitable(value)
