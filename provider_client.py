"""
Provider client for fetching MCP server metadata from marketplace providers.

Supports both static (JSON file) and API-based providers.
"""
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
import time

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

logger = logging.getLogger("SuperMCP.provider_client")

HERE = Path(__file__).resolve().parent
PROVIDER_CONFIG_PATH = HERE / "provider_config.json"
PROVIDER_CACHE_PATH = HERE / ".provider_cache.json"
CACHE_TTL = 3600  # 1 hour default cache TTL


class ProviderClient:
    """Client for interacting with MCP server providers."""

    def __init__(self):
        logger.info("[PROVIDER_CLIENT] Initializing ProviderClient")
        self.providers: Dict[str, Dict[str, Any]] = {}
        self.cache: Dict[str, Dict[str, Any]] = {}
        self._load_providers()
        self._load_cache()
        logger.info("[PROVIDER_CLIENT] Initialization complete: %d provider(s), %d cache entries",
                   len(self.providers), len(self.cache))

    def _load_providers(self):
        """Load provider configuration from provider_config.json."""
        logger.debug("[PROVIDER_LOAD] Loading providers from: %s", PROVIDER_CONFIG_PATH)

        if not PROVIDER_CONFIG_PATH.exists():
            logger.warning("[PROVIDER_LOAD] provider_config.json not found, creating default")
            self._create_default_providers()
            return

        try:
            logger.debug("[PROVIDER_LOAD] Reading provider_config.json...")
            with open(PROVIDER_CONFIG_PATH, 'r') as f:
                config = json.load(f)

            providers_list = config.get("providers", [])
            logger.debug("[PROVIDER_LOAD] Found %d provider(s) in config", len(providers_list))

            for provider in providers_list:
                provider_id = provider.get("id")
                if provider_id:
                    self.providers[provider_id] = provider
                    logger.debug("[PROVIDER_LOAD] Loaded provider: %s (type=%s, enabled=%s)",
                                provider_id, provider.get("type"), provider.get("enabled", True))

            logger.info("[PROVIDER_LOAD] Successfully loaded %d provider(s): %s",
                       len(self.providers), list(self.providers.keys()))
        except json.JSONDecodeError as e:
            logger.error("[PROVIDER_LOAD] Invalid JSON in provider_config.json: %s", e)
            self._create_default_providers()
        except Exception as e:
            logger.error("[PROVIDER_LOAD] Failed to load provider_config.json: %s", e, exc_info=True)
            self._create_default_providers()

    def _create_default_providers(self):
        """Create default provider configuration."""
        logger.info("[PROVIDER_DEFAULT] Creating default provider configuration")

        default_provider = {
            "id": "default",
            "name": "JARVIS Trusted Provider",
            "type": "static",
            "catalog_file": "default_catalog.json",
            "trusted": True,
            "enabled": True,
            "description": "Official trusted provider for JARVIS MCP servers (static catalog)"
        }
        self.providers["default"] = default_provider
        logger.debug("[PROVIDER_DEFAULT] Created default provider: %s", default_provider)

        # Save to file
        config = {
            "providers": [default_provider],
            "default_provider": "default"
        }
        try:
            logger.debug("[PROVIDER_DEFAULT] Saving default config to: %s", PROVIDER_CONFIG_PATH)
            with open(PROVIDER_CONFIG_PATH, 'w') as f:
                json.dump(config, f, indent=2)
            logger.info("[PROVIDER_DEFAULT] Default provider configuration saved")
        except Exception as e:
            logger.error("[PROVIDER_DEFAULT] Failed to save default provider config: %s", e)
    
    def _load_cache(self):
        """Load provider response cache."""
        logger.debug("[CACHE_LOAD] Loading cache from: %s", PROVIDER_CACHE_PATH)

        if not PROVIDER_CACHE_PATH.exists():
            logger.debug("[CACHE_LOAD] Cache file does not exist, starting with empty cache")
            self.cache = {}
            return

        try:
            with open(PROVIDER_CACHE_PATH, 'r') as f:
                self.cache = json.load(f)
            logger.debug("[CACHE_LOAD] Loaded %d cache entries", len(self.cache))

            # Clean expired cache entries
            current_time = time.time()
            expired_keys = []
            for key, entry in self.cache.items():
                expires_at = entry.get("expires_at", 0)
                if expires_at < current_time:
                    expired_keys.append(key)
                    logger.debug("[CACHE_LOAD] Entry expired: %s (expired %d seconds ago)",
                                key, int(current_time - expires_at))

            for key in expired_keys:
                del self.cache[key]

            if expired_keys:
                logger.info("[CACHE_LOAD] Cleaned %d expired cache entries", len(expired_keys))
                self._save_cache()

            logger.debug("[CACHE_LOAD] Cache ready with %d valid entries", len(self.cache))
        except json.JSONDecodeError as e:
            logger.warning("[CACHE_LOAD] Invalid JSON in cache file: %s", e)
            self.cache = {}
        except Exception as e:
            logger.warning("[CACHE_LOAD] Failed to load cache: %s", e)
            self.cache = {}

    def _save_cache(self):
        """Save provider response cache."""
        logger.debug("[CACHE_SAVE] Saving cache with %d entries to: %s", len(self.cache), PROVIDER_CACHE_PATH)
        try:
            PROVIDER_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(PROVIDER_CACHE_PATH, 'w') as f:
                json.dump(self.cache, f, indent=2)
            logger.debug("[CACHE_SAVE] Cache saved successfully")
        except Exception as e:
            logger.warning("[CACHE_SAVE] Failed to save cache: %s", e)

    def _get_cache_key(self, provider_id: str, operation: str, *args) -> str:
        """Generate cache key for provider operation."""
        key_parts = [provider_id, operation] + [str(arg) for arg in args]
        cache_key = ":".join(key_parts)
        logger.debug("[CACHE_KEY] Generated key: %s", cache_key)
        return cache_key

    def _get_from_cache(self, cache_key: str) -> Optional[Any]:
        """Get value from cache if not expired."""
        entry = self.cache.get(cache_key)
        if not entry:
            logger.debug("[CACHE_GET] Cache miss: %s", cache_key)
            return None

        expires_at = entry.get("expires_at", 0)
        current_time = time.time()
        if expires_at < current_time:
            logger.debug("[CACHE_GET] Cache expired: %s (expired %d seconds ago)",
                        cache_key, int(current_time - expires_at))
            del self.cache[cache_key]
            return None

        ttl_remaining = int(expires_at - current_time)
        logger.debug("[CACHE_GET] Cache hit: %s (TTL remaining: %d seconds)", cache_key, ttl_remaining)
        return entry.get("data")

    def _set_cache(self, cache_key: str, data: Any, ttl: int = CACHE_TTL):
        """Set value in cache with TTL."""
        expires_at = time.time() + ttl
        self.cache[cache_key] = {
            "data": data,
            "expires_at": expires_at
        }
        logger.debug("[CACHE_SET] Cached: %s (TTL: %d seconds)", cache_key, ttl)
        self._save_cache()
    
    def list_providers(self) -> List[Dict[str, Any]]:
        """List all configured providers."""
        logger.debug("[PROVIDERS_LIST] Listing all providers")
        result = []
        for provider_id, provider in self.providers.items():
            provider_info = {
                "id": provider_id,
                "name": provider.get("name"),
                "type": provider.get("type"),
                "trusted": provider.get("trusted", False),
                "enabled": provider.get("enabled", True),
                "description": provider.get("description")
            }
            result.append(provider_info)
            logger.debug("[PROVIDERS_LIST] Provider: %s (type=%s, enabled=%s, trusted=%s)",
                        provider_id, provider_info["type"], provider_info["enabled"], provider_info["trusted"])
        logger.info("[PROVIDERS_LIST] Returning %d provider(s)", len(result))
        return result

    def fetch_servers(self, provider_id: str) -> List[Dict[str, Any]]:
        """Fetch server list from provider."""
        logger.info("[FETCH_SERVERS] Fetching servers from provider: %s", provider_id)

        provider = self.providers.get(provider_id)
        if not provider:
            logger.error("[FETCH_SERVERS] Provider not found: %s", provider_id)
            raise ValueError(f"Provider '{provider_id}' not found")

        if not provider.get("enabled", True):
            logger.error("[FETCH_SERVERS] Provider is disabled: %s", provider_id)
            raise ValueError(f"Provider '{provider_id}' is disabled")

        provider_type = provider.get("type", "static")
        logger.debug("[FETCH_SERVERS] Provider type: %s", provider_type)

        if provider_type == "static":
            logger.debug("[FETCH_SERVERS] Using static catalog fetch")
            return self._fetch_static_servers(provider)
        elif provider_type == "api":
            logger.debug("[FETCH_SERVERS] Using API fetch")
            return self._fetch_api_servers(provider)
        else:
            logger.error("[FETCH_SERVERS] Unknown provider type: %s", provider_type)
            raise ValueError(f"Unknown provider type: {provider_type}")
    
    def _fetch_static_servers(self, provider: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Fetch servers from static JSON catalog."""
        provider_id = provider.get("id", "unknown")
        logger.debug("[FETCH_STATIC] Fetching from static catalog for provider: %s", provider_id)

        catalog_file = provider.get("catalog_file")
        if not catalog_file:
            logger.error("[FETCH_STATIC] Static provider missing 'catalog_file'")
            raise ValueError("Static provider missing 'catalog_file'")

        catalog_path = HERE / catalog_file
        logger.debug("[FETCH_STATIC] Catalog path: %s", catalog_path)

        if not catalog_path.exists():
            logger.warning("[FETCH_STATIC] Catalog file not found: %s", catalog_path)
            return []

        try:
            logger.debug("[FETCH_STATIC] Reading catalog file...")
            with open(catalog_path, 'r') as f:
                catalog = json.load(f)

            servers = catalog.get("servers", [])
            logger.info("[FETCH_STATIC] Loaded %d server(s) from static catalog: %s",
                       len(servers), catalog_file)
            for server in servers:
                logger.debug("[FETCH_STATIC] Server: %s (type=%s)",
                            server.get("id"), server.get("type"))
            return servers
        except json.JSONDecodeError as e:
            logger.error("[FETCH_STATIC] Invalid JSON in catalog file: %s", e)
            return []
        except Exception as e:
            logger.error("[FETCH_STATIC] Failed to load static catalog: %s", e, exc_info=True)
            return []

    def _fetch_api_servers(self, provider: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Fetch servers from API provider."""
        provider_id = provider.get("id", "unknown")
        logger.debug("[FETCH_API] Fetching from API for provider: %s", provider_id)

        if not HTTPX_AVAILABLE:
            logger.error("[FETCH_API] httpx not available")
            raise RuntimeError("httpx not available, cannot fetch from API provider")

        url = provider.get("url")
        if not url:
            logger.error("[FETCH_API] API provider missing 'url'")
            raise ValueError("API provider missing 'url'")

        logger.debug("[FETCH_API] API URL: %s", url)

        # Check cache first
        cache_key = self._get_cache_key(provider_id, "servers")
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            logger.info("[FETCH_API] Returning cached servers for provider '%s' (%d servers)",
                       provider_id, len(cached))
            return cached

        try:
            # Fetch from API
            logger.debug("[FETCH_API] Sending GET request to: %s", url)
            response = httpx.get(url, timeout=10.0)
            logger.debug("[FETCH_API] Response status: %d", response.status_code)
            response.raise_for_status()

            data = response.json()
            servers = data.get("servers", [])
            logger.debug("[FETCH_API] Parsed %d server(s) from response", len(servers))

            # Cache the result
            self._set_cache(cache_key, servers)

            logger.info("[FETCH_API] Fetched %d server(s) from API provider '%s'",
                       len(servers), provider_id)
            for server in servers:
                logger.debug("[FETCH_API] Server: %s (type=%s)",
                            server.get("id"), server.get("type"))
            return servers
        except httpx.HTTPStatusError as e:
            logger.error("[FETCH_API] HTTP error %d from provider '%s': %s",
                        e.response.status_code, provider_id, e)
            raise
        except httpx.RequestError as e:
            logger.error("[FETCH_API] Request error for provider '%s': %s", provider_id, e)
            raise
        except Exception as e:
            logger.error("[FETCH_API] Failed to fetch servers from API provider: %s", e, exc_info=True)
            raise
    
    def search_servers(self, provider_id: str, query: str) -> List[Dict[str, Any]]:
        """Search servers in provider."""
        logger.info("[SEARCH] Searching provider '%s' for: '%s'", provider_id, query)

        servers = self.fetch_servers(provider_id)
        logger.debug("[SEARCH] Searching through %d server(s)", len(servers))

        query_lower = query.lower()
        results = []

        for server in servers:
            # Search in name, description, category
            name = server.get("name", "").lower()
            description = server.get("description", "").lower()
            category = server.get("category", "").lower()
            server_id = server.get("id", "").lower()

            if (query_lower in name or
                query_lower in description or
                query_lower in category or
                query_lower in server_id):
                results.append(server)
                logger.debug("[SEARCH] Match found: %s (matched in: %s)",
                            server.get("id"),
                            "name" if query_lower in name else
                            "description" if query_lower in description else
                            "category" if query_lower in category else "id")

        logger.info("[SEARCH] Found %d result(s) for query '%s'", len(results), query)
        return results

    def get_server_details(self, provider_id: str, server_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific server."""
        logger.debug("[GET_SERVER] Getting details for server '%s' from provider '%s'",
                    server_id, provider_id)

        servers = self.fetch_servers(provider_id)
        logger.debug("[GET_SERVER] Searching through %d server(s)", len(servers))

        for server in servers:
            if server.get("id") == server_id:
                logger.info("[GET_SERVER] Found server: %s", server_id)
                logger.debug("[GET_SERVER] Server details: type=%s, description=%s",
                            server.get("type"), server.get("description", "")[:50])
                return server

        logger.warning("[GET_SERVER] Server not found: %s", server_id)
        return None
    
    def add_provider(self, provider_id: str, name: str, provider_type: str,
                    url: Optional[str] = None, catalog_file: Optional[str] = None,
                    trusted: bool = False, enabled: bool = True,
                    description: Optional[str] = None) -> Dict[str, Any]:
        """Add a new provider."""
        logger.info("[ADD_PROVIDER] Adding new provider: %s (type=%s)", provider_id, provider_type)
        logger.debug("[ADD_PROVIDER] Parameters: name=%s, url=%s, catalog_file=%s, trusted=%s, enabled=%s",
                    name, url, catalog_file, trusted, enabled)

        if provider_id in self.providers:
            logger.warning("[ADD_PROVIDER] Provider already exists: %s", provider_id)
            return {"error": f"Provider '{provider_id}' already exists"}

        if provider_type not in ("static", "api"):
            logger.error("[ADD_PROVIDER] Invalid provider type: %s", provider_type)
            return {"error": f"Invalid provider type '{provider_type}'. Must be 'static' or 'api'"}

        if provider_type == "static" and not catalog_file:
            logger.error("[ADD_PROVIDER] Static provider missing catalog_file")
            return {"error": "Static providers require 'catalog_file'"}

        if provider_type == "api" and not url:
            logger.error("[ADD_PROVIDER] API provider missing url")
            return {"error": "API providers require 'url'"}

        provider = {
            "id": provider_id,
            "name": name,
            "type": provider_type,
            "trusted": trusted,
            "enabled": enabled,
            "description": description or f"{provider_type.title()} provider: {name}"
        }

        if url:
            provider["url"] = url
        if catalog_file:
            provider["catalog_file"] = catalog_file

        logger.debug("[ADD_PROVIDER] Provider config: %s", provider)
        self.providers[provider_id] = provider
        self._save_providers()

        logger.info("[ADD_PROVIDER] ✓ Provider '%s' added successfully", provider_id)
        return {
            "success": True,
            "message": f"Provider '{provider_id}' added successfully",
            "provider": provider
        }

    def remove_provider(self, provider_id: str) -> Dict[str, Any]:
        """Remove a provider."""
        logger.info("[REMOVE_PROVIDER] Removing provider: %s", provider_id)

        if provider_id not in self.providers:
            logger.warning("[REMOVE_PROVIDER] Provider not found: %s", provider_id)
            return {"error": f"Provider '{provider_id}' not found"}

        provider = self.providers[provider_id]
        if provider.get("trusted") and provider.get("id") == "default":
            logger.error("[REMOVE_PROVIDER] Cannot remove default trusted provider")
            return {"error": "Cannot remove default trusted provider"}

        logger.debug("[REMOVE_PROVIDER] Removing provider config: %s", provider)
        del self.providers[provider_id]
        self._save_providers()

        logger.info("[REMOVE_PROVIDER] ✓ Provider '%s' removed successfully", provider_id)
        return {
            "success": True,
            "message": f"Provider '{provider_id}' removed successfully"
        }

    def _save_providers(self):
        """Save provider configuration to file."""
        logger.debug("[SAVE_PROVIDERS] Saving %d provider(s) to: %s",
                    len(self.providers), PROVIDER_CONFIG_PATH)
        try:
            providers_list = list(self.providers.values())
            config = {
                "providers": providers_list,
                "default_provider": "default"
            }

            with open(PROVIDER_CONFIG_PATH, 'w') as f:
                json.dump(config, f, indent=2)
            logger.debug("[SAVE_PROVIDERS] Provider configuration saved successfully")
        except Exception as e:
            logger.error("[SAVE_PROVIDERS] Failed to save provider config: %s", e, exc_info=True)
            raise
