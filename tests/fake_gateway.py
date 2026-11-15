"""Faux serveur de la passerelle Technas, en mémoire, branché par ``httpx.MockTransport``.

Il reproduit les règles que le SDK doit respecter : authentification par en-tête, une
référence = un essai (rejouer rend le même paiement, d'autres valeurs → ``reference_conflict``,
essai expiré → ``payment_expired``), plafond de remboursement, solde, option de change.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs

import httpx

API_KEY = "pk_abc-DEF_123"
WEBHOOK_SECRET = "ws_webhook-secret_456"
SECRET = f"tps_test_{API_KEY}.{WEBHOOK_SECRET}"
BASE = "https://gw.test"
RISKS = {"none": "Rien.", "local": "Vous portez le change.", "converted": "Fermé.", "markup": "Supplément."}


def _err(status: int, code: str, message: str = "", **extra: Any) -> httpx.Response:
    return httpx.Response(status, json={"code": code, "message": message or code, **extra})


class FakeGateway:
    def __init__(self) -> None:
        self.payments: Dict[str, Dict[str, Any]] = {}
        self.refunds: Dict[str, Dict[str, Any]] = {}
        self.balance: Dict[str, int] = {"XOF": 0}
        self.fx_mode = "none"
        self.requests: List[httpx.Request] = []
        self.fail_next: Optional[httpx.Response] = None

    # --- transports ----------------------------------------------------------------------
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def async_transport(self) -> httpx.MockTransport:
        async def handler(request: httpx.Request) -> httpx.Response:
            await request.aread()
            return self.handle(request)

        return httpx.MockTransport(handler)

    # --- routage -------------------------------------------------------------------------
    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.startswith("/test-checkout/"):
            return self._simulate(path)
        if request.headers.get("authorization") != f"Bearer {API_KEY}":
            return _err(401, "invalid_api_key")
        if self.fail_next is not None:
            response, self.fail_next = self.fail_next, None
            return response
        body = json.loads(request.content) if request.content else None
        query = {k: v[0] for k, v in parse_qs(request.url.query.decode() if isinstance(request.url.query, bytes) else request.url.query).items()}
        m = request.method
        parts = path.strip("/").split("/")
        if m == "POST" and path == "/v1/payments":
            return self._create(body)
        if m == "GET" and path == "/v1/payments":
            p = next((p for p in self.payments.values() if p["reference"] == query.get("reference")), None)
            return httpx.Response(200, json=p) if p else _err(404, "payment_not_found")
        if m == "GET" and len(parts) == 3 and parts[1] == "payments":
            p = self.payments.get(parts[2])
            return httpx.Response(200, json=p) if p else _err(404, "payment_not_found")
        if m == "POST" and len(parts) == 4 and parts[3] == "refunds":
            return self._refund(parts[2], body)
        if m == "POST" and len(parts) == 4 and parts[3] == "external-refunds":
            return self._refund(parts[2], body, external=True)
        if m == "GET" and len(parts) == 3 and parts[1] == "refunds":
            r = self.refunds.get(parts[2])
            return httpx.Response(200, json=r) if r else _err(404, "refund_not_found")
        if m == "GET" and path == "/v1/transactions":
            items = sorted(self.payments.values(), key=lambda p: p["created_at"], reverse=True)
            if query.get("status"):
                items = [p for p in items if p["status"] == query["status"]]
            limit = int(query.get("limit", 50))
            return httpx.Response(200, json={"items": items[:limit], "next_cursor": "suite" if len(items) > limit else None})
        if m == "GET" and path == "/v1/balance":
            return httpx.Response(200, json=[{"currency": c, "amount": a} for c, a in self.balance.items()])
        if m == "POST" and path == "/v1/balance/top-ups":
            return httpx.Response(201, json={"id": "top_1", "amount": body["amount"], "currency": body["currency"], "status": "pending",
                                             "checkout_url": f"{BASE}/checkout/top_1", "test_mode": True, "created_at": "2026-11-14T10:00:00Z"})
        if path == "/v1/fx":
            if m == "PUT":
                if body["mode"] == "converted":
                    return _err(409, "fx_mode_unavailable")
                if body["mode"] != "none" and not body["accept_risk"]:
                    return _err(409, "risk_not_accepted", risk=RISKS[body["mode"]])
                self.fx_mode = body["mode"]
            return httpx.Response(200, json={
                "mode": self.fx_mode, "available_modes": ["none", "local"], "home_currency": "XOF", "markup_pct": 0,
                "risk_accepted_at": None, "risk_version": "2026-11", "risks": RISKS,
                "country_rules": {"allowed": None, "excluded": []},
                "countries": [{"country": "GH", "name": "Ghana", "currency": "GHS", "available": False, "reason": "no_payout_number",
                               "rate": 0.0222, "rate_source": "open.er-api.com", "rate_published_at": "2026-11-14T00:00:00Z"}],
            })
        if m == "PUT" and path == "/v1/country-rules":
            return httpx.Response(200, json=body)
        if m == "GET" and path == "/v1/settlements":
            return httpx.Response(200, json=[{"id": "s1", "amount": 10000, "fee": 150, "currency": "XOF", "status": "completed", "created_at": "x"}])
        if m == "GET" and path == "/v1/payout-networks":
            return httpx.Response(200, json=[{"code": "moov_tg", "name": "Moov Togo", "currency": "XOF"}] if query.get("country") == "TG" else [])
        if path == "/v1/payout-destination":
            pending = None
            if m == "PUT":
                pending = {**body, "currency": "XOF", "status": "awaiting_confirmation", "effective_at": None, "confirm_expires_at": "y"}
            return httpx.Response(200, json={"destinations": [{"currency": "XOF", "current": None, "pending": pending}]})
        return _err(404, "not_found")

    # --- règles --------------------------------------------------------------------------
    def _create(self, body: Dict[str, Any]) -> httpx.Response:
        if not isinstance(body.get("amount"), int) or body["amount"] <= 0:
            return _err(422, "invalid_amount")
        existing = next((p for p in self.payments.values() if p["reference"] == body["reference"]), None)
        if existing:
            if existing["amount"] != body["amount"]:
                return _err(409, "reference_conflict")
            if existing["status"] == "expired":
                return _err(409, "payment_expired")
            return httpx.Response(200, json=existing)
        pid = f"pay_{uuid.uuid4().hex[:8]}"
        commission = body["amount"] * 2 // 100
        payment = {
            "id": pid, "reference": body["reference"], "amount": body["amount"], "currency": body.get("currency"),
            "commission": commission, "net_amount": body["amount"] - commission, "status": "pending",
            "checkout_url": f"{BASE}/test-checkout/{pid}", "metadata": body.get("metadata", {}), "test_mode": True,
            "created_at": f"2026-11-14T10:{len(self.payments):02d}:00Z", "refunded_amount": 0,
            "expires_at": "2026-11-14T11:00:00Z", "expired_at": None, "completed_at": None,
        }
        self.payments[pid] = payment
        return httpx.Response(201, json=payment)

    def _simulate(self, path: str) -> httpx.Response:
        _, _, pid, outcome = path.split("/")
        p = self.payments.get(pid)
        if not p:
            return httpx.Response(404, text="introuvable")
        p["status"] = outcome
        if outcome == "completed":
            p["completed_at"] = "2026-11-14T10:30:00Z"
            cur = p["currency"] or "XOF"
            p["currency"] = cur
            self.balance[cur] = self.balance.get(cur, 0) + p["net_amount"]
            return httpx.Response(303, headers={"location": "https://partenaire.test/merci"})
        if outcome == "expired":
            p["checkout_url"] = None
            p["expired_at"] = "2026-11-14T11:00:00Z"
        return httpx.Response(200, text="ok")

    def _refund(self, pid: str, body: Dict[str, Any], external: bool = False) -> httpx.Response:
        p = self.payments.get(pid)
        if not p:
            return _err(404, "payment_not_found")
        if p["status"] != "completed":
            return _err(409, "payment_not_refundable")
        remaining = p["amount"] - p["refunded_amount"]
        amount = body.get("amount", remaining)
        if amount > remaining:
            return _err(409, "refund_exceeds_payment", refundable_amount=remaining)
        fee = 0 if external else 100
        fees = body.get("fees", "partner")
        debit = 0 if external else (amount + fee if fees == "partner" else amount)
        cur = p["currency"]
        if debit > self.balance.get(cur, 0):
            return _err(409, "insufficient_balance", missing_amount=debit - self.balance.get(cur, 0), currency=cur)
        self.balance[cur] -= debit
        p["refunded_amount"] += amount
        rid = f"re_{uuid.uuid4().hex[:8]}"
        refund = {
            "id": rid, "payment_id": pid, "payment_reference": p["reference"], "amount": amount,
            "sent_amount": amount if fees == "partner" else amount - fee, "fee": fee, "fees": fees,
            "balance_debit": debit, "currency": cur, "status": "completed", "failure": None,
            "kind": "external" if external else "payout", "method": body.get("method"), "note": body.get("note"),
            "recorded_by": body.get("recorded_by"), "created_at": "2026-11-14T12:00:00Z",
        }
        self.refunds[rid] = refund
        return httpx.Response(201, json=refund)
