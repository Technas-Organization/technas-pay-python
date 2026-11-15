"""Erreurs du SDK. Leur message ne cite jamais le secret."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ErrorDetails:
    """Précisions selon le code d'erreur.

    - ``insufficient_balance`` : ``missing_amount`` et ``currency`` (rechargez au moins ce montant
      avec ``top_up_balance``, puis rappelez ``refund``) ;
    - ``refund_exceeds_payment`` : ``refundable_amount`` ;
    - ``country_not_available`` : ``reason`` (``no_payout_number``…) ;
    - ``risk_not_accepted`` : ``risk``, le texte à montrer à l'utilisateur ;
    - ``kyc_required`` : ``console_url`` (où le responsable dépose sa pièce d'identité) et
      ``reason`` (``amount`` ou ``duration``) ;
    - ``contract_required`` : ``version`` (le contrat à accepter dans la console) et ``console_url``.
    """

    missing_amount: Optional[float] = None
    currency: Optional[str] = None
    refundable_amount: Optional[float] = None
    reason: Optional[str] = None
    risk: Optional[str] = None
    #: ``card_account_not_ready`` : où en est votre compte carte (``not_started``, ``onboarding``…).
    account_status: Optional[str] = None
    #: ``kyc_required``, ``contract_required`` : la page de la console où agir.
    console_url: Optional[str] = None
    #: ``contract_required`` : la version du contrat à accepter dans la console.
    version: Optional[str] = None


class TechnasPayError(Exception):
    """Une erreur rendue par Technas, ou détectée avant tout appel (``status`` vaut alors 0).

    ``code`` est stable : c'est lui qu'il faut tester (``payment_expired``, ``reference_conflict``,
    ``risk_not_accepted``, ``fx_mode_unavailable``…). ``retryable`` dit si refaire LE MÊME appel,
    avec la même référence, a du sens.

    ``kyc_required`` (409) : seuil d'identité dépassé, les NOUVEAUX paiements mobile money sont
    refusés. Ne réessayez pas : proposez la carte, ou dites au client que le mobile money n'est pas
    disponible pour le moment ; le responsable dépose sa pièce sur ``details.console_url``.
    ``contract_required`` (409) : le responsable accepte le contrat ``details.version`` dans la console.

    ``payment_expired`` : la référence est celle d'un essai expiré. Ne réessayez pas le même appel :
    créez un nouveau paiement avec une NOUVELLE référence (``retry_reference``).
    """

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 0,
        errors: Optional[List[Dict[str, str]]] = None,
        details: Optional[ErrorDetails] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.errors = errors
        self.details = details or ErrorDetails()

    @property
    def retryable(self) -> bool:
        return (
            self.code in ("network_error", "payment_in_progress")
            or self.status == 429
            or self.status >= 500
        )

    def __repr__(self) -> str:  # pragma: no cover - confort de lecture
        return f"TechnasPayError(code={self.code!r}, status={self.status}, message={self.message!r})"


class WebhookVerificationError(Exception):
    """Signature absente, mal formée, trop ancienne, fausse, ou corps illisible. Répondez 400."""


def error_from_response(status: int, data: Any) -> TechnasPayError:
    data = data if isinstance(data, dict) else {}
    return TechnasPayError(
        code=data.get("code") or "request_failed",
        message=data.get("message") or f"Réponse HTTP {status}",
        status=status,
        errors=data.get("errors"),
        details=ErrorDetails(
            missing_amount=data.get("missing_amount"),
            currency=data.get("currency"),
            refundable_amount=data.get("refundable_amount"),
            reason=data.get("reason"),
            risk=data.get("risk"),
            account_status=data.get("account_status"),
            console_url=data.get("console_url"),
            version=data.get("version"),
        ),
    )


__all__ = ["ErrorDetails", "TechnasPayError", "WebhookVerificationError", "error_from_response"]
