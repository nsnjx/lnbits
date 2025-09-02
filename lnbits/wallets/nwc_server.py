"""
Nostr Wallet Connect (NWC) Server Implementation for LNBits
Handles NWC protocol events and implements custom methods like get_nwc_uri
"""

import asyncio
import json
import time
from typing import Dict, Any, Optional
from loguru import logger

from lnbits.utils.nostr import (
    decrypt_content,
    encrypt_content,
    json_dumps,
    verify_event,
)
from lnbits.core.crud.users import get_user_nwc_config_by_pubkey, create_user_nwc_config
from lnbits.core.crud.users import get_accounts
from lnbits.db import Filters
from lnbits.settings import settings


class NWCServer:
    """
    NWC Server that handles incoming NWC events and implements custom methods
    """
    
    def __init__(self):
        self.supported_methods = [
            "get_info",
            "get_nwc_uri",  # Custom method for getting user's NWC URI
            "make_invoice",
            "pay_invoice",
            "lookup_invoice",
            "get_balance",
        ]
    
    async def handle_event(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Handle incoming NWC event and return response if needed
        
        Args:
            event: The NWC event to handle
            
        Returns:
            Response event dict or None if no response needed
        """
        try:
            # Verify event signature
            if not verify_event(event):
                logger.warning("Invalid event signature")
                return None
            
            # Check if this is a request event (kind 23194)
            if event.get("kind") != 23194:
                return None
            
            # Extract service pubkey from tags
            service_pubkey = None
            for tag in event.get("tags", []):
                if tag[0] == "p" and len(tag) > 1:
                    service_pubkey = tag[1]
                    break
            
            if not service_pubkey:
                logger.warning("No service pubkey found in event tags")
                return None
            
            # Decrypt content
            try:
                # For now, we'll use a simple approach - in production you'd need proper key management
                decrypted_content = self._decrypt_content(event["content"], service_pubkey)
                content_data = json.loads(decrypted_content)
            except Exception as e:
                logger.error(f"Failed to decrypt event content: {e}")
                return self._create_error_response(
                    event["id"], 
                    service_pubkey, 
                    "DECRYPTION_FAILED", 
                    "Failed to decrypt event content"
                )
            
            # Handle the method
            method = content_data.get("method")
            params = content_data.get("params", {})
            
            if method == "get_nwc_uri":
                return await self._handle_get_nwc_uri(event, service_pubkey, params)
            elif method == "get_info":
                return await self._handle_get_info(event, service_pubkey)
            elif method == "make_invoice":
                return await self._handle_make_invoice(event, service_pubkey, params)
            elif method == "pay_invoice":
                return await self._handle_pay_invoice(event, service_pubkey, params)
            elif method == "lookup_invoice":
                return await self._handle_lookup_invoice(event, service_pubkey, params)
            elif method == "get_balance":
                return await self._handle_get_balance(event, service_pubkey, params)
            else:
                return self._create_error_response(
                    event["id"], 
                    service_pubkey, 
                    "METHOD_NOT_FOUND", 
                    f"Method {method} not supported"
                )
                
        except Exception as e:
            logger.error(f"Error handling NWC event: {e}")
            return None
    
    async def _handle_get_nwc_uri(self, event: Dict[str, Any], service_pubkey: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle get_nwc_uri method - get or create NWC URI for a user
        
        Args:
            event: The original request event
            service_pubkey: The service public key
            params: Method parameters
            
        Returns:
            Response event with NWC URI
        """
        try:
            # Extract user pubkey from the event author
            user_pubkey = event.get("pubkey")
            if not user_pubkey:
                return self._create_error_response(
                    event["id"], 
                    service_pubkey, 
                    "INVALID_REQUEST", 
                    "No user pubkey found"
                )
            
            # Check if user exists and has NWC config
            user_nwc = await get_user_nwc_config_by_pubkey(user_pubkey)
            
            if not user_nwc:
                # User doesn't exist, create account and NWC config
                try:
                    # Check if user account exists
                    filters = Filters(filters=[{"field": "pubkey", "values": {"eq": user_pubkey}}])
                    accounts = await get_accounts(filters)
                    
                    if not accounts.items:
                        # Create new user account
                        from lnbits.core.crud.users import create_account
                        from lnbits.core.models.users import Account
                        from datetime import datetime, timezone
                        from uuid import uuid4
                        
                        user_id = uuid4().hex
                        now = datetime.now(timezone.utc)
                        account = Account(
                            id=user_id,
                            pubkey=user_pubkey,
                            created_at=now,
                            updated_at=now
                        )
                        await create_account(account)
                    else:
                        user_id = accounts.items[0].id
                    
                    # Create NWC config for user
                    import secrets
                    new_secret = secrets.token_hex(32)
                    new_relay = getattr(settings, 'DEFAULT_RELAY', 'ws://localhost:7447')
                    
                    user_nwc = await create_user_nwc_config(
                        user_id=user_id,
                        pubkey=user_pubkey,
                        secret=new_secret,
                        relay=new_relay
                    )
                    
                    logger.info(f"Created new NWC config for user {user_pubkey}")
                    
                except Exception as e:
                    logger.error(f"Failed to create user account/NWC config: {e}")
                    return self._create_error_response(
                        event["id"], 
                        service_pubkey, 
                        "ACCOUNT_CREATION_FAILED", 
                        "Failed to create user account"
                    )
            
            # Create NWC URI
            nwc_uri = f"nostr+walletconnect://{user_nwc['pubkey']}?relay={user_nwc['relay']}&secret={user_nwc['secret']}"
            
            # Create success response
            return self._create_success_response(
                event["id"],
                service_pubkey,
                "get_nwc_uri",
                {"nwc_uri": nwc_uri}
            )
            
        except Exception as e:
            logger.error(f"Error in get_nwc_uri: {e}")
            return self._create_error_response(
                event["id"], 
                service_pubkey, 
                "INTERNAL_ERROR", 
                "Internal server error"
            )
    
    async def _handle_get_info(self, event: Dict[str, Any], service_pubkey: str) -> Dict[str, Any]:
        """Handle get_info method"""
        info = {
            "supported_methods": self.supported_methods,
            "max_sendable": 1000000000,  # 1 BTC in msats
            "min_sendable": 1000,        # 1 sat in msats
            "max_receivable": 1000000000,
            "min_receivable": 1000,
            "fees": {
                "base": 0,
                "proportional": 0
            }
        }
        
        return self._create_success_response(
            event["id"],
            service_pubkey,
            "get_info",
            info
        )
    
    async def _handle_make_invoice(self, event: Dict[str, Any], service_pubkey: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle make_invoice method"""
        # This would integrate with LNBits invoice creation
        # For now, return a placeholder response
        return self._create_success_response(
            event["id"],
            service_pubkey,
            "make_invoice",
            {"payment_hash": "placeholder", "invoice": "placeholder"}
        )
    
    async def _handle_pay_invoice(self, event: Dict[str, Any], service_pubkey: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle pay_invoice method"""
        # This would integrate with LNBits payment processing
        # For now, return a placeholder response
        return self._create_success_response(
            event["id"],
            service_pubkey,
            "pay_invoice",
            {"preimage": "placeholder", "fees_paid": 0}
        )
    
    async def _handle_lookup_invoice(self, event: Dict[str, Any], service_pubkey: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle lookup_invoice method"""
        # This would integrate with LNBits invoice lookup
        # For now, return a placeholder response
        return self._create_success_response(
            event["id"],
            service_pubkey,
            "lookup_invoice",
            {"settled": False, "fees_paid": 0}
        )
    
    async def _handle_get_balance(self, event: Dict[str, Any], service_pubkey: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle get_balance method"""
        # This would integrate with LNBits balance checking
        # For now, return a placeholder response
        return self._create_success_response(
            event["id"],
            service_pubkey,
            "get_balance",
            {"balance": 0}
        )
    
    def _create_success_response(self, request_id: str, service_pubkey: str, result_type: str, result: Dict[str, Any]) -> Dict[str, Any]:
        """Create a success response event"""
        response_content = {
            "result_type": result_type,
            "error": None,
            "result": result
        }
        
        # Encrypt content (in production, use proper encryption)
        encrypted_content = self._encrypt_content(json.dumps(response_content), service_pubkey)
        
        return {
            "kind": 23195,
            "content": encrypted_content,
            "created_at": int(time.time()),
            "tags": [
                ["p", service_pubkey],
                ["e", request_id]
            ]
        }
    
    def _create_error_response(self, request_id: str, service_pubkey: str, error_code: str, error_message: str) -> Dict[str, Any]:
        """Create an error response event"""
        response_content = {
            "result_type": "error",
            "error": {
                "code": error_code,
                "message": error_message
            },
            "result": None
        }
        
        # Encrypt content (in production, use proper encryption)
        encrypted_content = self._encrypt_content(json.dumps(response_content), service_pubkey)
        
        return {
            "kind": 23195,
            "content": encrypted_content,
            "created_at": int(time.time()),
            "tags": [
                ["p", service_pubkey],
                ["e", request_id]
            ]
        }
    
    def _decrypt_content(self, encrypted_content: str, service_pubkey: str) -> str:
        """
        Decrypt NWC content
        Note: This is a simplified implementation. In production, you need proper key management.
        """
        # For now, assume content is base64 encoded and not actually encrypted
        # In production, implement proper NIP-04 decryption
        import base64
        try:
            return base64.b64decode(encrypted_content).decode('utf-8')
        except:
            return encrypted_content
    
    def _encrypt_content(self, content: str, service_pubkey: str) -> str:
        """
        Encrypt NWC content
        Note: This is a simplified implementation. In production, you need proper key management.
        """
        # For now, base64 encode the content
        # In production, implement proper NIP-04 encryption
        import base64
        return base64.b64encode(content.encode('utf-8')).decode('utf-8')
