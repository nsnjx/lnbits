import asyncio
from math import floor
from typing import Optional

from lnbits.core.crud import get_standalone_payment
from lnbits.core.crud.wallets import get_wallet_for_key
from lnbits.core.models import Payment
from lnbits.core.services import (
    create_invoice,
    fee_reserve,
    get_pr_from_lnurl,
    pay_invoice,
)
from lnbits.tasks import register_invoice_listener
from loguru import logger


async def wait_for_split_payments():
    """Wait for paid invoices and process split payments"""
    invoice_queue = asyncio.Queue()
    register_invoice_listener(invoice_queue, "core_split_payment_listener")
    
    while True:
        payment = await invoice_queue.get()
        await on_split_payment_paid(payment)


async def on_split_payment_paid(payment: Payment) -> None:
    """Handle split payment when invoice is paid"""
    
    # Skip if this is already a split payment or splitted payment
    if (payment.extra.get("tag") == "split_payment" or 
        payment.extra.get("splitted")):
        return
    
    # Check if this payment has split targets
    split_targets = payment.extra.get("split_targets")
    if not split_targets:
        return
    
    total_percent = payment.extra.get("total_percent", 0)
    if total_percent > 100:
        logger.error("split_payment: total percent adds up to more than 100%")
        return
    
    logger.info(f"split_payment: processing split payment {payment.payment_hash} to {len(split_targets)} targets")
    
    # Process each target
    for target in split_targets:
        percent = target.get("percent", 0)
        if percent > 0:
            amount_msat = int(payment.amount * percent / 100)
            amount_sats = amount_msat // 1000
            
            if amount_sats > 0:
                target_wallet = target.get("wallet")
                target_alias = target.get("alias", target_wallet)
                
                memo = (
                    f"Split payment: {percent}% "
                    f"for {target_alias}"
                    f";{payment.memo};{payment.payment_hash}"
                )
                
                try:
                    if "@" in target_wallet or "LNURL" in target_wallet:
                        # External payment via LNURL
                        safe_amount_msat = amount_msat - fee_reserve(amount_msat)
                        payment_request = await get_lnurl_invoice(
                            target_wallet, payment.wallet_id, safe_amount_msat, memo
                        )
                    else:
                        # Internal payment
                        wallet = await get_wallet_for_key(target_wallet)
                        if wallet is not None:
                            target_wallet = wallet.id
                        
                        new_payment = await create_invoice(
                            wallet_id=target_wallet,
                            amount=amount_sats,
                            internal=True,
                            memo=memo,
                        )
                        payment_request = new_payment.bolt11
                    
                    extra = {**payment.extra, "splitted": True, "split_from": payment.payment_hash}
                    
                    if payment_request:
                        task = asyncio.create_task(
                            pay_invoice_in_background(
                                payment_request=payment_request,
                                wallet_id=payment.wallet_id,
                                description=memo,
                                extra=extra,
                            )
                        )
                        task.add_done_callback(lambda fut: logger.success(fut.result()))
                
                except Exception as e:
                    logger.error(f"Failed to process split payment to {target_wallet}: {e}")


async def pay_invoice_in_background(payment_request: str, wallet_id: str, description: str, extra: dict):
    """Pay invoice in background task"""
    try:
        await pay_invoice(
            wallet_id=wallet_id,
            payment_request=payment_request,
            extra=extra,
        )
        return f"Split payment: paid invoice for {description}"
    except Exception as e:
        logger.error(f"Failed to pay split payment invoice: {e}")
        return f"Split payment: failed to pay invoice for {description} - {e}"


async def get_lnurl_invoice(
    payoraddress: str, wallet_id: str, amount_msat: int, memo: str
) -> Optional[str]:
    """Get LNURL invoice for external payment"""
    
    rounded_amount = floor(amount_msat / 1000) * 1000
    
    try:
        payment_request = await get_pr_from_lnurl(payoraddress, rounded_amount, memo)
    except Exception as e:
        logger.error(f"Error getting LNURL invoice: {e}")
        return None
    
    # Check if this is a self-payment
    from lnbits import bolt11
    invoice = bolt11.decode(payment_request)
    
    lnurlp_payment = await get_standalone_payment(invoice.payment_hash)
    
    if lnurlp_payment and lnurlp_payment.wallet_id == wallet_id:
        logger.error("split payment failed: cannot split payments to yourself via LNURL")
        return None
    
    return payment_request
