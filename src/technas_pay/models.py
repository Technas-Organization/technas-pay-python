"""Modèles typés, en snake_case. Les montants sont en unité principale (5000 = 5 000 FCFA).

Hors zone franc CFA, un montant payé peut avoir des centimes (``121.96`` GHS) : stockez-le en
décimal, ne l'arrondissez pas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Union

PaymentStatus = Literal["creating", "pending", "completed", "failed", "amount_mismatch", "expired"]
"""``failed`` n'est PAS définitif (le client peut encore payer sur la même page pendant une heure).
``expired`` l'est : seulement alors, relancez avec une NOUVELLE référence (``can_retry``)."""

FxModeName = Literal["none", "local", "converted"]
RefundFees = Literal["partner", "customer"]
Number = Union[int, float]

_FINAL_STATUSES = ("completed", "amount_mismatch", "expired")


def _status_of(payment: Any) -> str:
    if isinstance(payment, dict):
        return str(payment.get("status", ""))
    return str(getattr(payment, "status", ""))


def is_final(payment: Any) -> bool:
    """Vrai si le statut ne changera plus : ``completed``, ``expired``, ``amount_mismatch``. Jamais ``failed``."""
    return _status_of(payment) in _FINAL_STATUSES


def can_retry(payment: Any) -> bool:
    """Vrai seulement pour ``expired`` : relancez le client avec une NOUVELLE référence. Jamais sur ``failed``."""
    return _status_of(payment) == "expired"


def retry_reference(order_id: str, attempt: int) -> str:
    """Une référence = un essai : ``f"{order_id}-{attempt}"``. Gardez ``attempt`` en base (1, 2, 3…)."""
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        raise ValueError("attempt est un entier >= 1")
    return f"{order_id}-{attempt}"


CURRENCY_BY_COUNTRY: Dict[str, str] = {
    # UEMOA
    "BJ": "XOF", "BF": "XOF", "CI": "XOF", "GW": "XOF", "ML": "XOF", "NE": "XOF", "SN": "XOF", "TG": "XOF",
    # CEMAC
    "CM": "XAF", "CF": "XAF", "CG": "XAF", "GA": "XAF", "GQ": "XAF", "TD": "XAF",
}
"""Zone franc CFA : pays → devise. XOF et XAF sont à parité fixe : même montant dans les deux."""


def currency_for_country(country: Optional[str]) -> Optional[str]:
    """La devise d'un pays de la zone franc CFA (code ISO à deux lettres), sinon ``None``."""
    return CURRENCY_BY_COUNTRY.get(country.strip().upper()) if country else None


@dataclass(frozen=True)
class PaymentFx:
    """Hors zone franc CFA : le change appliqué à un paiement."""

    mode: str
    rate: float
    franc_per_unit: float
    margin_pct: float
    source: Optional[str]
    credited_currency: str

    @classmethod
    def from_api(cls, raw: Any) -> Optional["PaymentFx"]:
        if not raw:
            return None
        return cls(
            mode=raw.get("mode"),
            rate=raw.get("rate"),
            franc_per_unit=raw.get("franc_per_unit"),
            margin_pct=raw.get("margin_pct") or 0,
            source=raw.get("source"),
            credited_currency=raw.get("credited_currency"),
        )


@dataclass(frozen=True)
class Payment:
    id: str
    reference: str
    amount: Number
    #: ``None`` tant que le payeur n'a pas choisi son pays sur la page de paiement.
    currency: Optional[str]
    commission: Number
    #: Ce qui vous revient.
    net_amount: Number
    status: str
    refunded_amount: Number
    #: Hors zone CFA : le montant demandé, en F CFA.
    original_amount: Optional[Number]
    original_currency: Optional[str]
    fx: Optional[PaymentFx]
    #: ``None`` après expiration : n'envoyez jamais un ancien lien au client.
    checkout_url: Optional[str]
    metadata: Dict[str, Any]
    test_mode: bool
    created_at: Optional[str]
    completed_at: Optional[str]
    expires_at: Optional[str]
    expired_at: Optional[str]

    @classmethod
    def from_api(cls, raw: Dict[str, Any]) -> "Payment":
        return cls(
            id=raw.get("id"),
            reference=raw.get("reference"),
            amount=raw.get("amount"),
            currency=raw.get("currency"),
            commission=raw.get("commission") or 0,
            net_amount=raw.get("net_amount"),
            status=raw.get("status"),
            refunded_amount=raw.get("refunded_amount") or 0,
            original_amount=raw.get("original_amount"),
            original_currency=raw.get("original_currency"),
            fx=PaymentFx.from_api(raw.get("fx")),
            checkout_url=raw.get("checkout_url"),
            metadata=raw.get("metadata") or {},
            test_mode=bool(raw.get("test_mode")),
            created_at=raw.get("created_at"),
            completed_at=raw.get("completed_at"),
            expires_at=raw.get("expires_at"),
            expired_at=raw.get("expired_at"),
        )


@dataclass(frozen=True)
class TransactionPage:
    items: List[Payment]
    next_cursor: Optional[str]


@dataclass(frozen=True)
class Balance:
    currency: str
    amount: Number


@dataclass(frozen=True)
class Settlement:
    id: str
    #: Ce qui a quitté votre solde.
    amount: Number
    #: Frais du versement, à votre charge : vous recevez ``amount - fee``.
    fee: Number
    currency: str
    status: str
    created_at: Optional[str]

    @classmethod
    def from_api(cls, raw: Dict[str, Any]) -> "Settlement":
        return cls(
            id=raw.get("id"), amount=raw.get("amount"), fee=raw.get("fee") or 0,
            currency=raw.get("currency"), status=raw.get("status"), created_at=raw.get("created_at"),
        )


@dataclass(frozen=True)
class PayoutDestination:
    """Le numéro mobile money où Technas vous reverse votre solde."""

    #: Code pays ISO à deux lettres : "TG", "BJ"…
    country: str
    #: Code réseau de ``get_payout_networks(country)`` : "moov_tg", "togocel"…
    network: str
    #: Format international : "+22890000000".
    phone: str
    beneficiary_name: str


@dataclass(frozen=True)
class PayoutDestinationView:
    currency: str
    country: str
    network: str
    phone: str
    beneficiary_name: str
    #: ``awaiting_confirmation`` | ``scheduled`` | ``active``
    status: str
    effective_at: Optional[str]
    confirm_expires_at: Optional[str]

    @classmethod
    def from_api(cls, raw: Dict[str, Any]) -> "PayoutDestinationView":
        return cls(
            currency=raw.get("currency"), country=raw.get("country"), network=raw.get("network"),
            phone=raw.get("phone"), beneficiary_name=raw.get("beneficiary_name"), status=raw.get("status"),
            effective_at=raw.get("effective_at"), confirm_expires_at=raw.get("confirm_expires_at"),
        )


@dataclass(frozen=True)
class CurrencyDestinations:
    currency: str
    #: ``None`` : aucun reversement ne part dans cette devise ; l'argent attend sur le solde.
    current: Optional[PayoutDestinationView]
    pending: Optional[PayoutDestinationView]


@dataclass(frozen=True)
class PayoutDestinationState:
    destinations: List[CurrencyDestinations]

    @classmethod
    def from_api(cls, raw: Any) -> "PayoutDestinationState":
        rows = (raw or {}).get("destinations") or []
        return cls(destinations=[
            CurrencyDestinations(
                currency=d.get("currency"),
                current=PayoutDestinationView.from_api(d["current"]) if d.get("current") else None,
                pending=PayoutDestinationView.from_api(d["pending"]) if d.get("pending") else None,
            )
            for d in rows
        ])


@dataclass(frozen=True)
class PayoutNetwork:
    code: str
    name: str
    currency: Optional[str]


@dataclass(frozen=True)
class Refund:
    id: str
    payment_id: str
    payment_reference: str
    #: Décompté du paiement : jamais plus que le montant payé, au total.
    amount: Number
    #: Ce qui arrive sur le téléphone du client.
    sent_amount: Number
    fee: Number
    fees: str
    #: Ce qui a quitté votre solde.
    balance_debit: Number
    currency: str
    #: ``pending`` | ``completed`` | ``failed``
    status: str
    failure: Optional[str]
    #: ``payout`` (renvoyé par Technas) | ``external`` (fait hors plateforme, seulement enregistré)
    kind: str
    method: Optional[str]
    note: Optional[str]
    recorded_by: Optional[str]
    created_at: Optional[str]

    @classmethod
    def from_api(cls, raw: Dict[str, Any]) -> "Refund":
        return cls(
            id=raw.get("id"), payment_id=raw.get("payment_id"), payment_reference=raw.get("payment_reference"),
            amount=raw.get("amount"), sent_amount=raw.get("sent_amount"), fee=raw.get("fee"),
            fees=raw.get("fees"), balance_debit=raw.get("balance_debit"), currency=raw.get("currency"),
            status=raw.get("status"), failure=raw.get("failure"), kind=raw.get("kind") or "payout",
            method=raw.get("method"), note=raw.get("note"), recorded_by=raw.get("recorded_by"),
            created_at=raw.get("created_at"),
        )


@dataclass(frozen=True)
class TopUp:
    id: str
    amount: Number
    currency: str
    status: str
    #: Ouvrez cette page pour payer la recharge.
    checkout_url: str
    test_mode: bool
    created_at: Optional[str]
    completed_at: Optional[str]

    @classmethod
    def from_api(cls, raw: Dict[str, Any]) -> "TopUp":
        return cls(
            id=raw.get("id"), amount=raw.get("amount"), currency=raw.get("currency"), status=raw.get("status"),
            checkout_url=raw.get("checkout_url"), test_mode=bool(raw.get("test_mode")),
            created_at=raw.get("created_at"), completed_at=raw.get("completed_at"),
        )


@dataclass(frozen=True)
class FxCountry:
    country: str
    name: str
    currency: str
    available: bool
    #: Ce qui manque si ``available`` est faux : ``no_operator``, ``no_fresh_rate``,
    #: ``no_payout_number``, ``no_fx_channel``, ``currency_not_enabled``.
    reason: Optional[str]
    rate: Optional[float]
    rate_source: Optional[str]
    rate_published_at: Optional[str]
    markup_pct: float
    estimated_conversion_cost_pct: Optional[float]


@dataclass(frozen=True)
class CountryRules:
    #: ``None`` : tous les pays.
    allowed: Optional[List[str]]
    excluded: List[str]

    @classmethod
    def from_api(cls, raw: Any) -> "CountryRules":
        raw = raw or {}
        return cls(allowed=raw.get("allowed"), excluded=raw.get("excluded") or [])


@dataclass(frozen=True)
class FxMode:
    """Votre option hors zone CFA, ses risques en clair, vos règles de pays, les pays ouverts."""

    mode: str
    #: Options choisissables aujourd'hui (``none``, ``local``). ``converted`` lève ``fx_mode_unavailable``.
    available_modes: List[str]
    home_currency: Optional[str]
    markup_pct: float
    risk_accepted_at: Optional[str]
    risk_version: Optional[str]
    #: Textes à MONTRER à l'utilisateur avant ``set_fx_mode`` : ``none``, ``local``, ``converted``, ``markup``.
    risks: Dict[str, str]
    country_rules: CountryRules
    countries: List[FxCountry] = field(default_factory=list)

    @classmethod
    def from_api(cls, raw: Dict[str, Any]) -> "FxMode":
        return cls(
            mode=raw.get("mode"),
            available_modes=raw.get("available_modes") or ["none", "local"],
            home_currency=raw.get("home_currency"),
            markup_pct=raw.get("markup_pct") or 0,
            risk_accepted_at=raw.get("risk_accepted_at"),
            risk_version=raw.get("risk_version"),
            risks=raw.get("risks") or {},
            country_rules=CountryRules.from_api(raw.get("country_rules")),
            countries=[
                FxCountry(
                    country=c.get("country"), name=c.get("name"), currency=c.get("currency"),
                    available=bool(c.get("available")), reason=c.get("reason"), rate=c.get("rate"),
                    rate_source=c.get("rate_source"), rate_published_at=c.get("rate_published_at"),
                    markup_pct=c.get("markup_pct") or 0,
                    estimated_conversion_cost_pct=c.get("estimated_conversion_cost_pct"),
                )
                for c in raw.get("countries") or []
            ],
        )
