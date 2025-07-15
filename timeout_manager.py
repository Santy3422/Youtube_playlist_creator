import asyncio
import ssl
import logging
import time
from typing import List, Dict, Tuple, Any, Callable
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

# Configuration
SEARCH_TIMEOUT_SEC = 20          # Per-search timeout
GLOBAL_TIMEOUT_SEC = 60 * 75     # Total batch timeout (15 minutes)
MAX_SSL_RETRIES = 4
INITIAL_DELAY = 1.5

class TimeoutManager:
    """Manages timeouts and SSL recovery for YouTube operations"""
    
    @staticmethod
    async def async_timeout(coro, seconds: int, operation_name: str):
        """
        Run coroutine with timeout protection
        Returns (success: bool, result_or_exception)
        """
        try:
            result = await asyncio.wait_for(coro, timeout=seconds)
            return True, result
        except asyncio.TimeoutError:
            logger.error(f"⏱️ TIMEOUT: {operation_name} exceeded {seconds}s")
            return False, TimeoutError(f"Operation '{operation_name}' timed out")
        except Exception as e:
            logger.error(f"❌ ERROR in {operation_name}: {e}")
            return False, e

    @staticmethod
    def create_robust_ssl_context():
        """Create a robust SSL context for API calls"""
        try:
            import ssl
            ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.set_ciphers("DEFAULT@SECLEVEL=1")
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            logger.info("Created robust SSL context")
            return ctx
        except Exception as e:
            logger.warning(f"Could not create custom SSL context: {e}")
            return None

class RobustSearchMixin:
    """Mixin to add robust search capabilities to YouTube agents"""
    
    async def robust_search_with_recovery(self, query: str) -> List[Dict]:
        """
        Robust search with SSL recovery and exponential backoff
        """
        delay = INITIAL_DELAY
        last_exception = None
        
        for attempt in range(1, MAX_SSL_RETRIES + 1):
            try:
                # Wrap the search in timeout protection
                success, result = await TimeoutManager.async_timeout(
                    self._perform_search(query),
                    SEARCH_TIMEOUT_SEC,
                    f"YouTube search '{query}' (attempt {attempt}/{MAX_SSL_RETRIES})"
                )
                
                if success:
                    logger.info(f"✅ Search successful for '{query}' on attempt {attempt}")
                    return result
                
                last_exception = result
                
                # Handle SSL errors specifically
                if isinstance(result, ssl.SSLError):
                    logger.warning(f"🔧 SSL error on attempt {attempt}: {result}")
                    await self._recreate_ssl_context()
                
            except Exception as e:
                last_exception = e
                logger.warning(f"Search attempt {attempt} failed: {e}")
            
            # Don't sleep after the last attempt
            if attempt < MAX_SSL_RETRIES:
                logger.info(f"⏳ Waiting {delay:.1f}s before retry...")
                await asyncio.sleep(delay)
                delay *= 2  # Exponential backoff
        
        # All retries failed
        logger.error(f"❌ All {MAX_SSL_RETRIES} search attempts failed for '{query}'")
        raise last_exception or Exception(f"Search failed after {MAX_SSL_RETRIES} attempts")
    
    async def _perform_search(self, query: str) -> List[Dict]:
        """Override this method in your actual implementation"""
        raise NotImplementedError("Subclass must implement _perform_search")
    
    async def _recreate_ssl_context(self):
        """Override this method to recreate SSL context in your implementation"""
        logger.info("🔄 Recreating SSL context...")
        # This should be implemented based on your YouTube client library
        pass

class SafeBatchProcessor:
    """Processes batches with global timeout protection"""
    
    @staticmethod
    async def safe_batch_process(operation_coro, operation_name: str = "batch operation"):
        """
        Execute a batch operation with global timeout protection
        Cancels all running tasks if timeout is exceeded
        """
        success, result = await TimeoutManager.async_timeout(
            operation_coro,
            GLOBAL_TIMEOUT_SEC,
            f"Global {operation_name}"
        )
        
        if success:
            return result
        
        # Cancel all running tasks to prevent hanging
        current_task = asyncio.current_task()
        for task in asyncio.all_tasks():
            if task is not current_task and not task.done():
                logger.warning(f"🛑 Cancelling task: {task.get_name()}")
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"Error cancelling task: {e}")
        
        # Re-raise the timeout or error
        raise result