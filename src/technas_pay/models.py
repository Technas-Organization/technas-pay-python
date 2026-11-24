"""Modèles typés, en snake_case. Les montants sont en unité principale (5000 = 5 000 FCFA).

Hors zone franc CFA, un montant payé peut avoir des centimes (``121.96`` GHS) : stockez-le en
décimal, ne l'arrondissez pas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

PaymentStatus = Literal["creating", "pending", "completed", "failed", "amount_mismatch", "expired"]
"""``failed`` n'est PAS définitif (le client peut encore payer sur la même page pendant une heure).
``expired`` l'est : seulement alors, relancez avec une NOUVELLE référence (``can_retry``)."""

FxModeName = Literal["none", "local", "converted"]
RefundFees = Literal["partner", "customer"]
FeesPaidBy = Literal["partner", "customer"]
"""Qui paie les frais : le client (``customer``) ou vous (``partner``)."""
RefundKind = Literal["payout", "external", "card"]
PaymentMethod = Literal["mobile_money", "card"]
"""``mobile_money`` (défaut) ou ``card`` (exige un compte carte actif et une devise)."""
Number = Union[int, float]

PAYMENT_METHODS: Tuple[PaymentMethod, ...] = ("mobile_money", "card")
"""Tous les moyens de paiement, à énumérer (``for m in PAYMENT_METHODS``)."""

Network = Literal[
    "mixx", "moov", "mtn_momo", "orange_money", "wave", "airtel_money", "free_money", "celtiis_cash",
    "djamo", "expresso", "wizall", "mobicash", "mpesa", "telecel_cash", "airteltigo_money", "afrimoney",
    "qmoney", "tnm_mpamba", "emola", "zamtel_kwacha", "ezypesa", "halopesa", "tpesa",
]
"""Un réseau mobile money : le porte-monnaie que le client a dans sa poche, jamais l'intermédiaire
qui fait passer le paiement. ``mixx`` = Mixx by Yas (ex-T-Money, Togocel), ``moov`` = Moov Money
(Flooz au Togo). Ceux réellement ouverts, par pays : ``pay.get_payment_methods(country)``."""

NETWORKS: Tuple[Network, ...] = (
    "mixx", "moov", "mtn_momo", "orange_money", "wave", "airtel_money", "free_money", "celtiis_cash",
    "djamo", "expresso", "wizall", "mobicash", "mpesa", "telecel_cash", "airteltigo_money", "afrimoney",
    "qmoney", "tnm_mpamba", "emola", "zamtel_kwacha", "ezypesa", "halopesa", "tpesa",
)
"""Tous les réseaux mobile money (voir ``Network``), à énumérer."""

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
    #: ``mobile_money`` ou ``card``.
    method: str
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
    #: Quand vous l'avez annulé (``cancel_payment``) ; ``None`` sinon. Un paiement annulé reste ``expired``.
    canceled_at: Optional[str] = None
    #: ``canceled_by_partner``, ou ``None``.
    cancel_reason: Optional[str] = None
    #: Dans l'avis ``payment.expired`` : ``canceled`` (annulé par vous) ou ``timeout`` (délai
    #: dépassé) ; ``None`` ailleurs et dans un avis plus ancien.
    reason: Optional[str] = None
    #: Ce que Technas demande au réseau de paiement, avant ses frais (= ``amount`` sauf frais au
    #: client). ``amount`` reste VOTRE prix. Toujours rempli par ``from_api``.
    customer_amount: Optional[Number] = None
    #: Qui a payé les frais : ``partner`` ou ``customer``.
    fees_paid_by: str = "partner"
    #: Ce que le client paie, frais du réseau de paiement compris (le plus haut des réseaux tant
    #: qu'il n'a pas choisi le sien). ``None`` : inconnu (carte, hors franc CFA, serveur ancien).
    customer_total: Optional[Number] = None
    #: Les frais du réseau de paiement dans ``customer_total``.
    processing_fee: Optional[Number] = None
    #: Le réseau avec lequel le client a payé, une fois payé (``None`` avant, par carte, ou sur un
    #: serveur ancien). Un réseau plus récent que ce SDK est gardé tel quel.
    network: Optional[Network] = None
    #: Les réseaux permis pour ce paiement, s'il les a restreints (``None`` sinon).
    networks: Optional[List[Network]] = None

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
            method=raw.get("method") or "mobile_money",
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
            canceled_at=raw.get("canceled_at"),
            cancel_reason=raw.get("cancel_reason"),
            reason=raw.get("reason"),
            customer_amount=_or(raw.get("customer_amount"), raw.get("amount")),
            fees_paid_by=raw.get("fees_paid_by") or "partner",
            customer_total=raw.get("customer_total"),
            processing_fee=raw.get("processing_fee"),
            network=raw.get("network"),
            networks=list(raw["networks"]) if raw.get("networks") is not None else None,
        )


def _or(value: Any, default: Any) -> Any:
    """``value``, sauf s'il est absent (``None``) : alors ``default``. Un 0 reste 0."""
    return default if value is None else value


@dataclass(frozen=True)
class QuoteNetwork:
    """Un réseau du devis : ce que le client paie s'il paie avec lui."""

    #: Le réseau (voir ``NETWORKS``), ex. ``mixx``. Un réseau plus récent que ce SDK est gardé tel quel.
    network: Network
    #: Le nom que le client connaît (ex. « Mixx by Yas »).
    network_name: str
    #: Ce que le client paie, frais du réseau de paiement compris : le montant à afficher.
    customer_total: Number
    #: Les frais du réseau de paiement dans ``customer_total``.
    processing_fee: Number
    #: La commission Technas.
    commission: Number
    #: Ce que vous recevez.
    net_amount: Number
    #: Les frais propres de l'opérateur du client, prélevés sur son téléphone EN PLUS, hors
    #: Technas : une estimation (« environ »), jamais promise. ``None`` : inconnus.
    operator_fee_estimate: Optional[Number]
    #: Le client doit obtenir un code sur son téléphone avant de payer (``otp_instructions``).
    otp_required: bool
    otp_instructions: Optional[str]

    @classmethod
    def from_api(cls, raw: Dict[str, Any]) -> "QuoteNetwork":
        return cls(
            network=raw.get("network"),
            network_name=raw.get("network_name") or raw.get("network"),
            customer_total=raw.get("customer_total"),
            processing_fee=raw.get("processing_fee"),
            commission=raw.get("commission"),
            net_amount=raw.get("net_amount"),
            operator_fee_estimate=raw.get("operator_fee_estimate"),
            otp_required=bool(raw.get("otp_required")),
            otp_instructions=raw.get("otp_instructions"),
        )


@dataclass(frozen=True)
class Quote:
    """Le devis d'un paiement (``quote``, ``get_fees``) : rien n'est créé."""

    #: ``mobile_money`` ou ``card``.
    method: str
    currency: str
    country: Optional[str]
    #: Qui paie les frais : ``partner`` ou ``customer``.
    fees_paid_by: str
    #: Votre prix.
    amount: Number
    #: Ce que Technas demande au réseau de paiement (avant ses frais).
    customer_amount: Number
    #: Ce que le client paie, frais du réseau compris (le plus haut des réseaux possibles) : le
    #: montant à afficher. Par carte : ``amount`` (les frais de la carte sont pris sur votre compte carte).
    customer_total: Number
    #: Les frais du réseau de paiement (le plus haut des réseaux) ; ``None`` par carte.
    processing_fee: Optional[Number]
    commission: Number
    #: Ce que vous recevez ; ``None`` par carte (dépend de la carte du client).
    net_amount: Optional[Number]
    #: Réseau par réseau (mobile money) ; vide par carte.
    networks: List[QuoteNetwork] = field(default_factory=list)

    @classmethod
    def from_api(cls, raw: Dict[str, Any]) -> "Quote":
        raw = raw or {}
        return cls(
            method=raw.get("method") or "mobile_money",
            currency=raw.get("currency"),
            country=raw.get("country"),
            fees_paid_by=raw.get("fees_paid_by") or "partner",
            amount=raw.get("amount"),
            customer_amount=_or(raw.get("customer_amount"), raw.get("amount")),
            customer_total=_or(raw.get("customer_total"), raw.get("amount")),
            processing_fee=raw.get("processing_fee"),
            commission=raw.get("commission"),
            net_amount=raw.get("net_amount"),
            networks=[QuoteNetwork.from_api(n) for n in raw.get("networks") or []],
        )


@dataclass(frozen=True)
class DisplayPriceNetwork:
    """Ce que vous recevez, réseau par réseau, au prix affiché."""

    country: Optional[str]
    #: Le réseau (voir ``NETWORKS``). Un réseau plus récent que ce SDK est gardé tel quel.
    network: Network
    #: Le nom que le client connaît (ex. « Mixx by Yas »).
    network_name: str
    #: Ce que le client paie : jamais plus que le prix affiché.
    customer_total: Number
    processing_fee: Number
    commission: Number
    #: Ce que vous recevez (au moins ``receive``).
    net_amount: Number
    #: Frais propres de l'opérateur du client, hors Technas : « environ », ``None`` = inconnus.
    operator_fee_estimate: Optional[Number]

    @classmethod
    def from_api(cls, raw: Dict[str, Any]) -> "DisplayPriceNetwork":
        raw = raw or {}
        return cls(
            country=raw.get("country"),
            network=raw.get("network"),
            network_name=raw.get("network_name") or raw.get("network"),
            customer_total=raw.get("customer_total"),
            processing_fee=raw.get("processing_fee"),
            commission=raw.get("commission"),
            net_amount=raw.get("net_amount"),
            operator_fee_estimate=raw.get("operator_fee_estimate"),
        )


@dataclass(frozen=True)
class DisplayPriceForCountry:
    """Un prix affiché, avec le réseau qui vous laisse le moins."""

    price: Number
    currency: Optional[str]
    worst_network: DisplayPriceNetwork
    net_by_network: List[DisplayPriceNetwork] = field(default_factory=list)

    @classmethod
    def from_api(cls, raw: Dict[str, Any]) -> "DisplayPriceForCountry":
        raw = raw or {}
        return cls(
            price=raw.get("price"),
            currency=raw.get("currency"),
            worst_network=DisplayPriceNetwork.from_api(raw.get("worst_network")),
            net_by_network=[DisplayPriceNetwork.from_api(n) for n in raw.get("net_by_network") or []],
        )


@dataclass(frozen=True)
class DisplayPrice:
    """Le prix rond à afficher pour recevoir au moins ``receive``, quel que soit le réseau du client
    (``display_price``). Créez ensuite le paiement avec ``amount=price`` et ``fees="partner"`` : le
    client paie exactement ce prix, et le surplus vous reste."""

    price: Number
    currency: Optional[str]
    worst_network: DisplayPriceNetwork
    net_by_network: List[DisplayPriceNetwork]
    #: Toujours ``partner`` : un prix affiché fixe se paie en « vous payez les frais ».
    fees_paid_by: Literal["partner"]
    receive: Number
    round_to: Number
    countries: List[str]
    #: Un prix par pays (avec ``country`` ou ``countries``).
    by_country: Dict[str, DisplayPriceForCountry]
    #: Un seul prix valable dans tous ces pays ; ``None`` si XOF et XAF sont mélangés.
    everywhere: Optional[DisplayPriceForCountry]

    @classmethod
    def from_api(cls, raw: Dict[str, Any]) -> "DisplayPrice":
        raw = raw or {}
        top = DisplayPriceForCountry.from_api(raw)
        return cls(
            price=top.price,
            currency=top.currency,
            worst_network=top.worst_network,
            net_by_network=top.net_by_network,
            fees_paid_by="partner",
            receive=raw.get("receive"),
            round_to=raw.get("round_to"),
            countries=list(raw.get("countries") or []),
            by_country={pays: DisplayPriceForCountry.from_api(v) for pays, v in (raw.get("by_country") or {}).items()},
            everywhere=DisplayPriceForCountry.from_api(raw["everywhere"]) if raw.get("everywhere") else None,
        )


@dataclass(frozen=True)
class FeeTier:
    """Un palier de la grille du réseau de paiement."""

    min_amount: Number
    #: ``None`` : sans plafond.
    max_amount: Optional[Number]
    percent: Number
    fixed: Number


@dataclass(frozen=True)
class RateExample:
    """Un exemple à 1 000 dans un mode de frais."""

    customer_total: Number
    processing_fee: Number
    commission: Number
    net_amount: Number

    @classmethod
    def from_api(cls, raw: Any) -> "Optional[RateExample]":
        if not raw:
            return None
        return cls(
            customer_total=raw.get("customer_total"),
            processing_fee=raw.get("processing_fee"),
            commission=raw.get("commission"),
            net_amount=raw.get("net_amount"),
        )


@dataclass(frozen=True)
class RateExamples:
    """L'exemple à 1 000 dans chaque mode (``None`` : pas payable dans ce mode)."""

    customer: Optional[RateExample]
    partner: Optional[RateExample]


@dataclass(frozen=True)
class RateCommission:
    """La commission Technas."""

    percent: Number
    fixed: Number


@dataclass(frozen=True)
class RateNetwork:
    """Un réseau de la grille des frais."""

    network: Network
    network_name: str
    #: Pourcentage du réseau de paiement (palier de 1 000).
    percent: Number
    fixed: Number
    fee_tiers: List[FeeTier]
    #: Le plus petit montant que le réseau accepte.
    minimum_amount: Optional[Number]
    #: Frais propres de l'opérateur du client sur 1 000, hors Technas : « environ », ``None`` = inconnus.
    operator_fee_estimate: Optional[Number]
    #: Exemple à 1 000, dans chaque mode.
    example_1000: RateExamples


@dataclass(frozen=True)
class RateCountry:
    """Un pays de la grille des frais."""

    country: str
    currency: str
    commission: RateCommission
    networks: List[RateNetwork]


@dataclass(frozen=True)
class Rates:
    """La grille des frais, par pays ouvert et par réseau (``get_rates``)."""

    #: Le mode de votre compte : ``partner`` ou ``customer``.
    fees_paid_by: str
    countries: List[RateCountry]

    @classmethod
    def from_api(cls, raw: Any) -> "Rates":
        raw = raw or {}
        countries = []
        for c in raw.get("countries") or []:
            com = c.get("commission") or {}
            networks = []
            for n in c.get("networks") or []:
                ex = n.get("example_1000") or {}
                networks.append(RateNetwork(
                    network=n.get("network"),
                    network_name=n.get("network_name") or n.get("network"),
                    percent=n.get("percent"),
                    fixed=_or(n.get("fixed"), 0),
                    fee_tiers=[
                        FeeTier(min_amount=t.get("min_amount"), max_amount=t.get("max_amount"),
                                percent=t.get("percent"), fixed=_or(t.get("fixed"), 0))
                        for t in n.get("fee_tiers") or []
                    ],
                    minimum_amount=n.get("minimum_amount"),
                    operator_fee_estimate=n.get("operator_fee_estimate"),
                    example_1000=RateExamples(
                        customer=RateExample.from_api(ex.get("customer")),
                        partner=RateExample.from_api(ex.get("partner")),
                    ),
                ))
            countries.append(RateCountry(
                country=c.get("country"),
                currency=c.get("currency"),
                commission=RateCommission(percent=_or(com.get("percent"), 0), fixed=_or(com.get("fixed"), 0)),
                networks=networks,
            ))
        return cls(fees_paid_by=raw.get("fees_paid_by") or "partner", countries=countries)


def to_app_json(payment: "Payment") -> Dict[str, Any]:
    """Ce que VOTRE backend renvoie à votre app (``{"payment": to_app_json(p)}``) : ni commission,
    ni montant net, ni métadonnées. C'est le contrat que lit ``technas_pay_flutter``."""
    return {
        "id": payment.id,
        "reference": payment.reference,
        "status": payment.status,
        "checkout_url": payment.checkout_url,
        "amount": payment.amount,
        "currency": payment.currency,
        "expires_at": payment.expires_at,
        "method": payment.method,
    }


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
    #: ``payout`` (renvoyé par Technas sur le numéro qui a payé) | ``card`` (paiement par carte, remboursé
    #: sur la carte depuis VOTRE compte carte) | ``external`` (fait hors plateforme, seulement enregistré).
    #: Une ancienne valeur ``stripe`` renvoyée par l'API est lue comme ``card``.
    kind: RefundKind
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
            status=raw.get("status"), failure=raw.get("failure"), kind=_refund_kind(raw.get("kind")),
            method=raw.get("method"), note=raw.get("note"), recorded_by=raw.get("recorded_by"),
            created_at=raw.get("created_at"),
        )


def _refund_kind(raw: Optional[str]) -> RefundKind:
    """``payout`` | ``external`` | ``card`` ; l'ancien ``stripe`` (transition) devient ``card``."""
    if not raw:
        return "payout"
    return "card" if raw == "stripe" else raw  # type: ignore[return-value]


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
class MethodSetting:
    """Un moyen de paiement ouvert pour votre compte, et s'il est activé."""

    method: PaymentMethod
    enabled: bool


@dataclass(frozen=True)
class NetworkSetting:
    """Un réseau ouvert pour votre compte, dans un pays, et s'il est activé (votre réglage)."""

    network: Network
    #: Le nom que le client connaît (ex. « Mixx by Yas »).
    name: str
    country: str
    currency: Optional[str]
    enabled: bool


@dataclass(frozen=True)
class PaymentMethods:
    """Les moyens et réseaux ouverts pour votre compte, ce que vous avez activé, et votre ordre."""

    methods: List[MethodSetting]
    #: Réseaux ouverts, par pays, dans VOTRE ordre d'affichage.
    networks: List[NetworkSetting]
    network_order: List[Network]

    @classmethod
    def from_api(cls, raw: Any) -> "PaymentMethods":
        raw = raw or {}
        return cls(
            methods=[MethodSetting(method=m.get("method"), enabled=bool(m.get("enabled"))) for m in raw.get("methods") or []],
            networks=[
                NetworkSetting(
                    network=n.get("network"), name=n.get("name") or n.get("network"), country=n.get("country"),
                    currency=n.get("currency"), enabled=bool(n.get("enabled")),
                )
                for n in raw.get("networks") or []
            ],
            network_order=list(raw.get("network_order") or []),
        )


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


@dataclass(frozen=True)
class CardAccount:
    """Où en est votre compte de paiement par carte.

    ``status`` : ``not_started`` (appelez ``start_card_onboarding``), ``onboarding`` (inscription à
    finir), ``under_review``, ``restricted`` (``requirements``, ``disabled_reason``), ``enabled``.
    ``can_accept_payments`` est la seule condition pour ``ask_for_payment(method="card")``.
    """

    status: str
    can_accept_payments: bool
    payouts_enabled: bool
    details_submitted: bool
    country: Optional[str]
    requirements: List[str]
    disabled_reason: Optional[str]
    test_mode: bool
    updated_at: Optional[str]

    @classmethod
    def from_api(cls, raw: Any) -> "CardAccount":
        raw = raw or {}
        return cls(
            status=raw.get("status"), can_accept_payments=bool(raw.get("can_accept_payments")),
            payouts_enabled=bool(raw.get("payouts_enabled")), details_submitted=bool(raw.get("details_submitted")),
            country=raw.get("country"), requirements=list(raw.get("requirements") or []),
            disabled_reason=raw.get("disabled_reason"), test_mode=bool(raw.get("test_mode")), updated_at=raw.get("updated_at"),
        )


@dataclass(frozen=True)
class AccountKyc:
    """Le seuil d'identité de votre compte (``get_account_kyc``), à montrer au marchand, pas au client.

    En réel, un compte sans pièce d'identité vérifiée voit ses NOUVEAUX paiements mobile money
    refusés (``kyc_required``) au-delà de ``threshold_xof`` F CFA encaissés en cumul, ou
    ``grace_days`` jours après son premier paiement réel encaissé. Lisez ces chiffres ici, ne les
    écrivez jamais en dur. Le responsable dépose sa pièce sur ``console_url``.

    ``stage`` : ``ok``, ``soon`` (seuil proche), ``blocked``, ``verified``, ``not_applicable``.
    ``level`` : ``none``, ``identity``, ``business``. ``reason`` (si bloqué) : ``amount`` ou
    ``duration``. Une valeur que cette version ne connaît pas est gardée telle quelle.
    """

    applies: bool
    stage: str
    level: str
    days_elapsed: Optional[int]
    warn_from_xof: int
    warn_from_date: Optional[str]
    collected_xof: int
    threshold_xof: int
    grace_days: int
    first_collected_at: Optional[str]
    deadline: Optional[str]
    waiver_until: Optional[str]
    blocked: bool
    reason: Optional[str]
    console_url: Optional[str]

    @classmethod
    def from_api(cls, raw: Any) -> "AccountKyc":
        raw = raw or {}
        return cls(
            applies=bool(raw.get("applies")), stage=raw.get("stage"), level=raw.get("level"),
            days_elapsed=raw.get("days_elapsed"), warn_from_xof=raw.get("warn_from_xof"),
            warn_from_date=raw.get("warn_from_date"), collected_xof=raw.get("collected_xof"),
            threshold_xof=raw.get("threshold_xof"), grace_days=raw.get("grace_days"),
            first_collected_at=raw.get("first_collected_at"), deadline=raw.get("deadline"),
            waiver_until=raw.get("waiver_until"), blocked=bool(raw.get("blocked")),
            reason=raw.get("reason"), console_url=raw.get("console_url"),
        )


@dataclass(frozen=True)
class CardOnboardingLink:
    #: À ouvrir par le RESPONSABLE du compte (pas le développeur). Expire vite : à générer au clic.
    url: str
    #: ``onboarding`` (inscription à faire ou finir) ou ``dashboard`` (déjà fini).
    kind: str
    expires_at: Optional[str]
    account: CardAccount

    @classmethod
    def from_api(cls, raw: Dict[str, Any]) -> "CardOnboardingLink":
        return cls(url=raw.get("url"), kind=raw.get("kind"), expires_at=raw.get("expires_at"),
                   account=CardAccount.from_api(raw.get("account")))
