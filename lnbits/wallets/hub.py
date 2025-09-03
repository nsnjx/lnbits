import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx
from loguru import logger

from lnbits.helpers import normalize_endpoint
from lnbits.settings import settings
from lnbits.utils.crypto import random_secret_and_hash

from .base import (
    Feature,
    InvoiceResponse,
    PaymentFailedStatus,
    PaymentPendingStatus,
    PaymentResponse,
    PaymentStatus,
    PaymentSuccessStatus,
    StatusResponse,
    Wallet,
)


class HubWallet(Wallet):
    """Alby Hub wallet implementation using HTTP API"""

    features = [Feature.nodemanager, Feature.holdinvoice]

    def __init__(self):
        if not settings.hub_api_endpoint:
            raise ValueError(
                "cannot initialize HubWallet: missing hub_api_endpoint"
            )

        if not settings.hub_api_key:
            raise ValueError(
                "cannot initialize HubWallet: missing hub_api_key"
            )

        self.endpoint = normalize_endpoint(settings.hub_api_endpoint)
        
        headers = {
            "Authorization": f"Bearer {settings.hub_api_key}",
            "Content-Type": "application/json",
            "User-Agent": settings.user_agent,
        }
        
        self.client = httpx.AsyncClient(
            base_url=self.endpoint, 
            headers=headers,
            timeout=30.0
        )

    async def cleanup(self):
        try:
            await self.client.aclose()
        except RuntimeError as e:
            logger.warning(f"Error closing hub wallet connection: {e}")

    async def status(self) -> StatusResponse:
        """Get wallet status and balance"""
        try:
            r = await self.client.get("/api/balances")
            r.raise_for_status()
            
            data = r.json()
            # Hub returns balance in sats, convert to msats
            # Get total spendable balance from lightning section
            lightning_balance = data.get("lightning", {})
            balance_sats = lightning_balance.get("totalSpendable", 0)
            balance_msat = balance_sats * 1000
            
            return StatusResponse(None, balance_msat)
            
        except httpx.HTTPStatusError as e:
            logger.warning(f"Hub API error: {e.response.status_code} - {e.response.text}")
            return StatusResponse(f"Hub API error: {e.response.status_code}", 0)
        except Exception as exc:
            logger.warning(f"Error connecting to Hub: {exc}")
            return StatusResponse(f"Unable to connect to {self.endpoint}", 0)

    async def create_invoice(
        self,
        amount: int,
        memo: str | None = None,
        description_hash: bytes | None = None,
        unhashed_description: bytes | None = None,
        expiry: int | None = None,
        payment_secret: bytes | None = None,
        **kwargs,
    ) -> InvoiceResponse:
        """Create a Lightning invoice"""
        try:
            # LNbits passes amount in sats, but Hub expects msats
            # Convert sats to msats
            amount_sats = max(1, amount)
            amount_msats = amount_sats * 1000
            
            logger.debug(f"Creating invoice: {amount} sats -> {amount_msats} msats for Hub")
            
            payload = {
                "amount": amount_msats,
                "description": memo or "",
            }
            
            r = await self.client.post("/api/invoices", json=payload)
            r.raise_for_status()
            
            data = r.json()
            
            return InvoiceResponse(
                ok=True,
                checking_id=data.get("paymentHash"),
                payment_request=data.get("invoice"),
                preimage=data.get("preimage"),
            )
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Hub create invoice error: {e.response.status_code} - {e.response.text}")
            return InvoiceResponse(
                ok=False,
                error_message=f"Hub API error: {e.response.status_code}"
            )
        except Exception as exc:
            logger.error(f"Error creating invoice with Hub: {exc}")
            return InvoiceResponse(
                ok=False,
                error_message=f"Error creating invoice: {str(exc)}"
            )

    async def pay_invoice(self, bolt11: str, fee_limit_msat: int) -> PaymentResponse:
        """Pay a Lightning invoice"""
        try:
            payload = {
                "amount": None,  # Hub will parse amount from invoice
                "metadata": {}
            }
            
            r = await self.client.post(f"/api/payments/{bolt11}")
            r.raise_for_status()
            
            data = r.json()
            
            if data.get("state") == "failed":
                return PaymentResponse(
                    ok=False,
                    error_message=data.get("failureReason", "Payment failed")
                )
            
            return PaymentResponse(
                ok=True,
                checking_id=data.get("paymentHash"),
                fee_msat=data.get("feesPaid", 0) * 1000,  # Convert sats to msats
                preimage=data.get("preimage"),
            )
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Hub pay invoice error: {e.response.status_code} - {e.response.text}")
            return PaymentResponse(
                ok=False,
                error_message=f"Hub API error: {e.response.status_code}"
            )
        except Exception as exc:
            logger.error(f"Error paying invoice with Hub: {exc}")
            return PaymentResponse(
                ok=False,
                error_message=f"Error paying invoice: {str(exc)}"
            )

    async def get_invoice_status(self, checking_id: str) -> PaymentStatus:
        """Get invoice status by payment hash"""
        try:
            r = await self.client.get(f"/api/transactions")
            r.raise_for_status()
            
            data = r.json()
            transactions = data.get("transactions", [])
            
            # Find transaction with matching payment hash
            for tx in transactions:
                if tx.get("paymentHash") == checking_id:
                    if tx.get("state") == "settled":
                        return PaymentSuccessStatus()
                    elif tx.get("state") == "pending":
                        return PaymentPendingStatus()
                    else:
                        return PaymentFailedStatus()
            
            # Transaction not found
            return PaymentFailedStatus()
                
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return PaymentFailedStatus()
            logger.warning(f"Hub get invoice status error: {e.response.status_code}")
            return PaymentFailedStatus()
        except Exception as exc:
            logger.warning(f"Error getting invoice status from Hub: {exc}")
            return PaymentFailedStatus()

    async def get_payment_status(self, checking_id: str) -> PaymentStatus:
        """Get payment status by payment hash"""
        try:
            r = await self.client.get(f"/api/transactions")
            r.raise_for_status()
            
            data = r.json()
            transactions = data.get("transactions", [])
            
            # Find transaction with matching payment hash
            for tx in transactions:
                if tx.get("paymentHash") == checking_id:
                    if tx.get("state") == "settled":
                        return PaymentSuccessStatus()
                    elif tx.get("state") == "pending":
                        return PaymentPendingStatus()
                    else:
                        return PaymentFailedStatus()
            
            # Transaction not found
            return PaymentFailedStatus()
                
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return PaymentFailedStatus()
            logger.warning(f"Hub get payment status error: {e.response.status_code}")
            return PaymentFailedStatus()
        except Exception as exc:
            logger.warning(f"Error getting payment status from Hub: {exc}")
            return PaymentFailedStatus()

    async def paid_invoices_stream(self) -> AsyncGenerator[str, None]:
        """Stream of paid invoices - polls Hub API for new payments"""
        last_checked = {}  # Track last checked timestamp per wallet
        
        while settings.lnbits_running:
            try:
                # Poll every 5 seconds for new payments
                await asyncio.sleep(5)
                
                # Get all transactions from Hub
                r = await self.client.get("/api/transactions")
                r.raise_for_status()
                
                data = r.json()
                transactions = data.get("transactions", [])
                
                # Debug: log transaction structure for first few calls
                if len(transactions) > 0:
                    logger.debug(f"Hub returned {len(transactions)} transactions")
                    if len(transactions) > 0:
                        logger.debug(f"Sample transaction: {transactions[0]}")
                
                # Check for new settled payments
                for tx in transactions:
                    if tx.get("state") == "settled" and tx.get("type") == "incoming":
                        payment_hash = tx.get("paymentHash")
                        if payment_hash:
                            # Check if this is a new payment we haven't seen before
                            wallet_id = getattr(self, 'wallet_id', 'default')
                            last_timestamp = last_checked.get(wallet_id, 0)
                            
                            # Convert timestamp to int if it's a string
                            tx_timestamp = tx.get("createdAt", 0)
                            if isinstance(tx_timestamp, str):
                                try:
                                    tx_timestamp = int(tx_timestamp)
                                except (ValueError, TypeError):
                                    tx_timestamp = 0
                            
                            if tx_timestamp > last_timestamp:
                                last_checked[wallet_id] = tx_timestamp
                                yield payment_hash
                                
            except Exception as exc:
                logger.warning(f"Error polling Hub for payments: {exc}")
                await asyncio.sleep(15)  # Wait longer on error
