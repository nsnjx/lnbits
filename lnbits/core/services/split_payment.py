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


def calculate_priority_split(payment_amount_msat: int, targets: list, fee_percent: float = 10.0) -> list:
    """
    Calculate priority-based split distribution
    
    Args:
        payment_amount_msat: Total payment amount in msat
        targets: List of split targets with priority info
        fee_percent: Fee percentage (default 10%)
    
    Returns:
        List of processed targets with actual amounts
    """
    total_amount_sats = payment_amount_msat // 1000
    # Use more precise calculation for small amounts
    fee_amount_msat = int(payment_amount_msat * fee_percent / 100)
    fee_amount_sats = fee_amount_msat // 1000
    available_amount_sats = total_amount_sats - fee_amount_sats
    
    # Sort by priority: lower number = higher priority
    sorted_targets = sorted(targets, key=lambda x: x.get("priority", 1))
    
    results = []
    remaining_amount = available_amount_sats
    
    logger.info(f"split_payment: total={total_amount_sats}sats, fee={fee_amount_sats}sats, available={available_amount_sats}sats")
    
    for target in sorted_targets:
        percent = target.get("percent", 0)
        priority = target.get("priority", "admin")
        
        if percent <= 0:
            continue
            
        # Calculate theoretical amount
        theoretical_amount = int(total_amount_sats * percent / 100)
        
        # Allocate based on priority and remaining amount
        if priority == 0:  # Highest priority
            # Priority: give to highest priority first
            actual_amount = min(theoretical_amount, remaining_amount)
        else:
            # Lower priority gets remaining amount
            actual_amount = remaining_amount
            
        if actual_amount > 0:
            results.append({
                **target,
                "theoretical_amount_sats": theoretical_amount,
                "actual_amount_sats": actual_amount,
                "actual_amount_msat": actual_amount * 1000
            })
            remaining_amount -= actual_amount
            
            logger.info(f"split_payment: {target.get('wallet', 'unknown')} "
                       f"priority={priority} "
                       f"theoretical={theoretical_amount}sats "
                       f"actual={actual_amount}sats")
    
    return results


async def wait_for_split_payments():
    """Wait for paid invoices and process split payments"""
    invoice_queue = asyncio.Queue()
    register_invoice_listener(invoice_queue, "core_split_payment_listener")
    
    while True:
        payment = await invoice_queue.get()
        await on_split_payment_paid(payment)


async def on_split_payment_paid(payment: Payment) -> None:
    """Handle split payment when invoice is paid"""
    
    logger.info(f"split_payment: received payment {payment.payment_hash}, checking for split targets...")
    
    # Skip if this is already a splitted payment (not the original split payment)
    if payment.extra.get("splitted"):
        logger.info(f"split_payment: skipping {payment.payment_hash} - this is a splitted payment")
        return
    
    # Check if this payment has split targets
    split_targets = payment.extra.get("split_targets")
    if not split_targets:
        logger.info(f"split_payment: no split targets found for {payment.payment_hash}")
        return
    
    total_percent = payment.extra.get("total_percent", 0)
    if total_percent > 100:
        logger.error("split_payment: total percent adds up to more than 100%")
        return
    
    logger.info(f"split_payment: processing split payment {payment.payment_hash} to {len(split_targets)} targets")
    logger.info(f"split_payment: payment amount = {payment.amount} msats ({payment.amount // 1000} sats)")
    logger.info(f"split_payment: split targets = {split_targets}")
    
    # Calculate priority-based split distribution
    split_results = calculate_priority_split(payment.amount, split_targets)
    logger.info(f"split_payment: calculated results = {split_results}")
    
    # Process each target with calculated amounts
    for result in split_results:
        target_wallet = result.get("wallet")
        target_alias = result.get("alias", target_wallet)
        actual_amount_sats = result.get("actual_amount_sats", 0)
        theoretical_amount_sats = result.get("theoretical_amount_sats", 0)
        priority = result.get("priority", "admin")
        
        if actual_amount_sats > 0:
            memo = (
                f"Split payment: {actual_amount_sats}sats "
                f"for {target_alias} (priority: {priority})"
                f";{payment.memo};{payment.payment_hash}"
            )
            
            try:
                if "@" in target_wallet or "LNURL" in target_wallet:
                    # External payment via LNURL
                    actual_amount_msat = actual_amount_sats * 1000
                    safe_amount_msat = actual_amount_msat - fee_reserve(actual_amount_msat)
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
                        amount=actual_amount_sats,
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
