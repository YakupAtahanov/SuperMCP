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
        self.providers: Dict[str, Dict[str, Any]] = {}
        self.cache: Dict[str, Dict[str, Any]] = {}
        self._load_providers()
        self._load_cache()
    
    def _load_providers(self):
        """Load provider configuration from provider_config.json."""
        if not PROVIDER_CONFIG_PATH.exists():
            logger.warning("provider_config.json not found, creating default")
            self._create_default_providers()
            return
        
        try:
            with open(PROVIDER_CONFIG_PATH, 'r') as f:
                config = json.load(f)
            
            providers_list = config.get("providers", [])
            for provider in providers_list:
                provider_id = provider.get("id")
                if provider_id:
                    self.providers[provider_id] = provider
            
            logger.info("Loaded %d provider(s)", len(self.providers))
        except Exception as e:
            logger.error("Failed to load provider_config.json: %s", e)
            self._create_default_providers()
    
    def _create_default_providers(self):
        """Create default provider configuration."""
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
        
        # Save to file
        config = {
            "providers": [default_provider],
            "default_provider": "default"
        }
        try:
            with open(PROVIDER_CONFIG_PATH, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            logger.error("Failed to save default provider config: %s", e)
    
    def _load_cache(self):
        """Load provider response cache."""
        if not PROVIDER_CACHE_PATH.exists():
            self.cache = {}
            return
        
        try:
            with open(PROVIDER_CACHE_PATH, 'r') as f:
                self.cache = json.load(f)
            
            # Clean expired cache entries
            current_time = time.time()
            expired_keys = []
            for key, entry in self.cache.items():
                if entry.get("expires_at", 0) < current_time:
                    expired_keys.append(key)
            
            for key in expired_keys:
                del self.cache[key]
            
            if expired_keys:
                self._save_cache()
            
            logger.debug("Loaded cache with %d valid entries", len(self.cache))
        except Exception as e:
            logger.warning("Failed to load cache: %s", e)
            self.cache = {}
    
    def _save_cache(self):
        """Save provider response cache."""
        try:
            PROVIDER_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(PROVIDER_CACHE_PATH, 'w') as f:
                json.dump(self.cache, f, indent=2)
        except Exception as e:
            logger.warning("Failed to save cache: %s", e)
    
    def _get_cache_key(self, provider_id: str, operation: str, *args) -> str:
        """Generate cache key for provider operation."""
        key_parts = [provider_id, operation] + [str(arg) for arg in args]
        return ":".join(key_parts)
    
    def _get_from_cache(self, cache_key: str) -> Optional[Any]:
        """Get value from cache if not expired."""
        entry = self.cache.get(cache_key)
        if not entry:
            return None
        
        if entry.get("expires_at", 0) < time.time():
            del self.cache[cache_key]
            return None
        
        return entry.get("data")
    
    def _set_cache(self, cache_key: str, data: Any, ttl: int = CACHE_TTL):
        """Set value in cache with TTL."""
        self.cache[cache_key] = {
            "data": data,
            "expires_at": time.time() + ttl
        }
        self._save_cache()
    
    def list_providers(self) -> List[Dict[str, Any]]:
        """List all configured providers."""
        result = []
        for provider_id, provider in self.providers.items():
            result.append({
                "id": provider_id,
                "name": provider.get("name"),
                "type": provider.get("type"),
                "trusted": provider.get("trusted", False),
                "enabled": provider.get("enabled", True),
                "description": provider.get("description")
            })
        return result
    
    def fetch_servers(self, provider_id: str) -> List[Dict[str, Any]]:
        """Fetch server list from provider."""
        provider = self.providers.get(provider_id)
        if not provider:
            raise ValueError(f"Provider '{provider_id}' not found")
        
        if not provider.get("enabled", True):
            raise ValueError(f"Provider '{provider_id}' is disabled")
        
        provider_type = provider.get("type", "static")
        
        if provider_type == "static":
            return self._fetch_static_servers(provider)
        elif provider_type == "api":
            return self._fetch_api_servers(provider)
        else:
            raise ValueError(f"Unknown provider type: {provider_type}")
    
    def _fetch_static_servers(self, provider: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Fetch servers from static JSON catalog."""
        catalog_file = provider.get("catalog_file")
        if not catalog_file:
            raise ValueError("Static provider missing 'catalog_file'")
        
        catalog_path = HERE / catalog_file
        if not catalog_path.exists():
            logger.warning("Catalog file not found: %s", catalog_path)
            return []
        
        try:
            with open(catalog_path, 'r') as f:
                catalog = json.load(f)
            
            servers = catalog.get("servers", [])
            logger.info("Loaded %d server(s) from static catalog", len(servers))
            return servers
        except Exception as e:
            logger.error("Failed to load static catalog: %s", e)
            return []
    
    def _fetch_api_servers(self, provider: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Fetch servers from API provider."""
        if not HTTPX_AVAILABLE:
            raise RuntimeError("httpx not available, cannot fetch from API provider")
        
        url = provider.get("url")
        if not url:
            raise ValueError("API provider missing 'url'")
        
        # Check cache first
        cache_key = self._get_cache_key(provider["id"], "servers")
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            logger.debug("Returning cached servers for provider '%s'", provider["id"])
            return cached
        
        try:
            # Fetch from API
            response = httpx.get(url, timeout=10.0)
            response.raise_for_status()
            
            data = response.json()
            servers = data.get("servers", [])
            
            # Cache the result
            self._set_cache(cache_key, servers)
            
            logger.info("Fetched %d server(s) from API provider '%s'", len(servers), provider["id"])
            return servers
        except Exception as e:
            logger.error("Failed to fetch servers from API provider: %s", e)
            raise
    
    def search_servers(self, provider_id: str, query: str) -> List[Dict[str, Any]]:
        """Search servers in provider."""
        servers = self.fetch_servers(provider_id)
        
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
        
        return results
    
    def get_server_details(self, provider_id: str, server_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific server."""
        servers = self.fetch_servers(provider_id)
        
        for server in servers:
            if server.get("id") == server_id:
                return server
        
        return None
    
    def add_provider(self, provider_id: str, name: str, provider_type: str, 
                    url: Optional[str] = None, catalog_file: Optional[str] = None,
                    trusted: bool = False, enabled: bool = True,
                    description: Optional[str] = None) -> Dict[str, Any]:
        """Add a new provider."""
        if provider_id in self.providers:
            return {"error": f"Provider '{provider_id}' already exists"}
        
        if provider_type not in ("static", "api"):
            return {"error": f"Invalid provider type '{provider_type}'. Must be 'static' or 'api'"}
        
        if provider_type == "static" and not catalog_file:
            return {"error": "Static providers require 'catalog_file'"}
        
        if provider_type == "api" and not url:
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
        
        self.providers[provider_id] = provider
        self._save_providers()
        
        return {
            "success": True,
            "message": f"Provider '{provider_id}' added successfully",
            "provider": provider
        }
    
    def remove_provider(self, provider_id: str) -> Dict[str, Any]:
        """Remove a provider."""
        if provider_id not in self.providers:
            return {"error": f"Provider '{provider_id}' not found"}
        
        provider = self.providers[provider_id]
        if provider.get("trusted") and provider.get("id") == "default":
            return {"error": "Cannot remove default trusted provider"}
        
        del self.providers[provider_id]
        self._save_providers()
        
        return {
            "success": True,
            "message": f"Provider '{provider_id}' removed successfully"
        }
    
    def _save_providers(self):
        """Save provider configuration to file."""
        try:
            providers_list = list(self.providers.values())
            config = {
                "providers": providers_list,
                "default_provider": "default"
            }
            
            with open(PROVIDER_CONFIG_PATH, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            logger.error("Failed to save provider config: %s", e)
            raise
