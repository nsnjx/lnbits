from http import HTTPStatus
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lnbits.core.crud import get_wallet, get_wallet_for_key
from lnbits.core.models import WalletTypeInfo
from lnbits.core.services import create_invoice, pay_invoice
from lnbits.decorators import require_admin_key
from lnbits.helpers import urlsafe_short_hash
from loguru import logger

split_payment_router = APIRouter(prefix="/api/v1/split-payment", tags=["Split Payment"])


class SplitTarget(BaseModel):
    """Target wallet for split payment"""
    wallet: str = Field(..., description="Wallet ID, LNURL, or LNaddress")
    percent: float = Field(..., ge=0, le=100, description="Percentage to split (0-100)")
    alias: Optional[str] = Field(None, description="Alias for identification")


class CreateSplitPaymentRequest(BaseModel):
    """Request to create a split payment"""
    amount: int = Field(..., gt=0, description="Amount in satoshis to split")
    memo: str = Field("", description="Payment memo/description")
    targets: List[SplitTarget] = Field(..., min_items=1, description="List of target wallets and percentages")
    internal: bool = Field(True, description="Whether to use internal wallet (FakeWallet)")


class SplitPaymentResponse(BaseModel):
    """Response for split payment creation"""
    payment_hash: str
    bolt11: str
    amount: int
    memo: str
    targets: List[SplitTarget]
    total_percent: float


@split_payment_router.post(
    "/create",
    summary="Create a split payment",
    description="""
    Create a payment that will be automatically split across multiple wallets.
    
    The payment will be created in the source wallet, and when paid, it will
    automatically distribute the specified percentages to the target wallets.
    
    Features:
    - Supports internal wallets (wallet IDs) and external (LNURL/LNaddress)
    - Automatic percentage validation (must not exceed 100%)
    - Internal payments use FakeWallet for instant, feeless transfers
    - External payments include routing fees
    """,
    status_code=HTTPStatus.CREATED,
    response_model=SplitPaymentResponse,
    responses={
        400: {"description": "Invalid request parameters"},
        401: {"description": "Admin key required"},
        520: {"description": "Payment creation error"},
    },
)
async def create_split_payment(
    request: CreateSplitPaymentRequest,
    source_wallet: WalletTypeInfo = Depends(require_admin_key),
) -> SplitPaymentResponse:
    """
    Create a split payment that will distribute funds across multiple wallets.
    """
    try:
        # Validate total percentage
        total_percent = sum(target.percent for target in request.targets)
        if total_percent > 100:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"Total percentage ({total_percent}%) cannot exceed 100%"
            )
        
        if total_percent <= 0:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="Total percentage must be greater than 0"
            )
        
        # Validate target wallets
        validated_targets = []
        for target in request.targets:
            # Check if it's an internal wallet
            if "@" not in target.wallet and "LNURL" not in target.wallet:
                wallet = await get_wallet(target.wallet)
                if not wallet:
                    wallet = await get_wallet_for_key(target.wallet)
                    if not wallet:
                        raise HTTPException(
                            status_code=HTTPStatus.BAD_REQUEST,
                            detail=f"Invalid wallet '{target.wallet}'"
                        )
                
                # Allow same wallet but log a warning
                if wallet.id == source_wallet.wallet.id:
                    logger.warning(f"Target wallet {wallet.id} is the same as source wallet - this will create a self-payment")
                
                # Use wallet ID for internal wallets
                validated_target = SplitTarget(
                    wallet=wallet.id,
                    percent=target.percent,
                    alias=target.alias or wallet.name
                )
            else:
                # External wallet (LNURL/LNaddress)
                validated_target = target
            
            validated_targets.append(validated_target)
        
        # Check if we have any valid targets
        if not validated_targets:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="No valid target wallets found"
            )
        
        # Create the main invoice
        memo = request.memo or f"Split payment: {total_percent}% to {len(validated_targets)} targets"
        
        payment = await create_invoice(
            wallet_id=source_wallet.wallet.id,
            amount=request.amount,
            memo=memo,
            internal=request.internal,
            extra={
                "tag": "split_payment",
                "split_targets": [
                    {
                        "wallet": target.wallet,
                        "percent": target.percent,
                        "alias": target.alias
                    }
                    for target in validated_targets
                ],
                "total_percent": total_percent
            }
        )
        
        logger.info(f"Created split payment {payment.payment_hash} for {request.amount} sats")
        
        return SplitPaymentResponse(
            payment_hash=payment.payment_hash,
            bolt11=payment.bolt11,
            amount=request.amount,
            memo=memo,
            targets=validated_targets,
            total_percent=total_percent
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create split payment: {e}")
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail=f"Failed to create split payment: {str(e)}"
        ) from e


@split_payment_router.post(
    "/execute",
    summary="Execute split payment manually",
    description="""
    Manually execute a split payment from an existing payment.
    
    This endpoint allows you to split an already received payment
    across multiple target wallets.
    """,
    status_code=HTTPStatus.OK,
    responses={
        400: {"description": "Invalid payment or targets"},
        401: {"description": "Admin key required"},
        404: {"description": "Payment not found"},
        520: {"description": "Split execution error"},
    },
)
async def execute_split_payment(
    payment_hash: str,
    targets: List[SplitTarget],
    source_wallet: WalletTypeInfo = Depends(require_admin_key),
) -> dict:
    """
    Execute a split payment from an existing payment.
    """
    try:
        from lnbits.core.crud import get_standalone_payment
        
        # Get the payment
        payment = await get_standalone_payment(payment_hash, wallet_id=source_wallet.wallet.id)
        if not payment:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND,
                detail="Payment not found"
            )
        
        if not payment.success:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="Payment is not successful"
            )
        
        # Validate total percentage
        total_percent = sum(target.percent for target in targets)
        if total_percent > 100:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail=f"Total percentage ({total_percent}%) cannot exceed 100%"
            )
        
        # Execute the split
        split_results = []
        for target in targets:
            if target.percent > 0:
                amount_msat = int(payment.amount * target.percent / 100)
                amount_sats = amount_msat // 1000
                
                if amount_sats > 0:
                    memo = f"Split payment: {target.percent}% for {target.alias or target.wallet};{payment.memo};{payment.payment_hash}"
                    
                    try:
                        if "@" in target.wallet or "LNURL" in target.wallet:
                            # External payment via LNURL
                            from lnbits.core.services import get_pr_from_lnurl
                            from lnbits.core.services import fee_reserve
                            
                            safe_amount_msat = amount_msat - fee_reserve(amount_msat)
                            payment_request = await get_pr_from_lnurl(
                                target.wallet, source_wallet.wallet.id, safe_amount_msat, memo
                            )
                            
                            if payment_request:
                                await pay_invoice(
                                    wallet_id=source_wallet.wallet.id,
                                    payment_request=payment_request,
                                    extra={"splitted": True, "split_from": payment.payment_hash}
                                )
                                split_results.append({
                                    "target": target.wallet,
                                    "amount_sats": amount_sats,
                                    "status": "success"
                                })
                        else:
                            # Internal payment
                            wallet = await get_wallet_for_key(target.wallet)
                            if wallet:
                                target.wallet = wallet.id
                            
                            new_payment = await create_invoice(
                                wallet_id=target.wallet,
                                amount=amount_sats,
                                internal=True,
                                memo=memo,
                            )
                            
                            await pay_invoice(
                                wallet_id=source_wallet.wallet.id,
                                payment_request=new_payment.bolt11,
                                extra={"splitted": True, "split_from": payment.payment_hash}
                            )
                            
                            split_results.append({
                                "target": target.wallet,
                                "amount_sats": amount_sats,
                                "status": "success"
                            })
                    
                    except Exception as e:
                        logger.error(f"Failed to split to {target.wallet}: {e}")
                        split_results.append({
                            "target": target.wallet,
                            "amount_sats": amount_sats,
                            "status": "failed",
                            "error": str(e)
                        })
        
        logger.info(f"Executed split payment {payment.payment_hash} to {len(split_results)} targets")
        
        return {
            "payment_hash": payment.payment_hash,
            "original_amount": payment.amount,
            "split_results": split_results,
            "total_percent": total_percent
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to execute split payment: {e}")
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail=f"Failed to execute split payment: {str(e)}"
        ) from e
