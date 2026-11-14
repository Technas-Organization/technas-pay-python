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
    - ``risk_not_accepted`` : ``risk``, le texte à montrer à l'utilisateur.
    """

    missing_amount: Optional[float] = None
    currency: Optional[str] = None
    refundable_amount: Optional[float] = None
    reason: Optional[str] = None
    risk: Optional[str] = None


class TechnasPayError(Exception):
    """Une erreur rendue par Technas, ou détectée avant tout appel (``status`` vaut alors 0).

    ``code`` est stable : c'est lui qu'il faut tester (``payment_expired``, ``reference_conflict``,
    ``risk_not_accepted``, ``fx_mode_unavailable``…). ``retryable`` dit si refaire LE MÊME appel,
    avec la même référence, a du sens.

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
        ),
    )


__all__ = ["ErrorDetails", "TechnasPayError", "WebhookVerificationError", "error_from_response"]
