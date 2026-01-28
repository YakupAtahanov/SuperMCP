"""
Server management utilities for SuperMCP.

Handles SSE connections, Git cloning, and dependency installation.
"""
import sys
import subprocess
import shutil
from pathlib import Path
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger("SuperMCP.server_manager")


def connect_sse_server(url: str, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    Test connection to an SSE server.

    Args:
        url: SSE endpoint URL (e.g., "http://example.com:8000/sse")
        env: Optional environment variables to pass as HTTP headers

    Returns:
        Dict with connection status and info
    """
    logger.info("[SSE_CONNECT] Testing connection to SSE server: %s", url)
    logger.debug("[SSE_CONNECT] Environment variables provided: %s", list(env.keys()) if env else None)

    try:
        import httpx
        logger.debug("[SSE_CONNECT] httpx module available")

        # Validate URL format
        if not url.startswith(("http://", "https://")):
            logger.error("[SSE_CONNECT] Invalid URL format: %s", url)
            return {
                "success": False,
                "error": f"Invalid URL format: {url}. Must start with http:// or https://"
            }

        # Prepare headers from environment variables
        headers = {}
        if env:
            logger.debug("[SSE_CONNECT] Converting %d env vars to HTTP headers", len(env))
            # Convert env vars to HTTP headers (X-MCP-{VAR_NAME} format)
            for key, value in env.items():
                header_name = f"X-MCP-{key.upper().replace('_', '-')}"
                headers[header_name] = value
                logger.debug("[SSE_CONNECT] Header: %s = ***", header_name)

        # Try to connect (HEAD request to test connectivity)
        try:
            logger.debug("[SSE_CONNECT] Sending HEAD request with timeout=5.0s...")
            response = httpx.head(url, headers=headers, timeout=5.0, follow_redirects=True)
            logger.info("[SSE_CONNECT] Connection successful - status: %d", response.status_code)
            return {
                "success": True,
                "status_code": response.status_code,
                "url": url
            }
        except httpx.ConnectError as e:
            logger.error("[SSE_CONNECT] Connection failed - server unreachable: %s", e)
            return {
                "success": False,
                "error": f"Could not connect to {url}. Server may be down or URL incorrect."
            }
        except httpx.TimeoutException as e:
            logger.error("[SSE_CONNECT] Connection timeout after 5 seconds: %s", e)
            return {
                "success": False,
                "error": f"Connection to {url} timed out."
            }
        except Exception as e:
            logger.error("[SSE_CONNECT] Unexpected error during connection: %s", e, exc_info=True)
            return {
                "success": False,
                "error": f"Error connecting to {url}: {str(e)}"
            }
    except ImportError:
        logger.warning("[SSE_CONNECT] httpx not available - skipping connection test")
        # Return success anyway - connection will be tested when actually used
        return {
            "success": True,
            "warning": "httpx not installed, connection not tested",
            "url": url
        }


def clone_git_repo(repo_url: str, target_dir: Path) -> Path:
    """
    Clone a Git repository to the target directory.

    Args:
        repo_url: Git repository URL
        target_dir: Target directory for cloning

    Returns:
        Path to cloned repository

    Raises:
        RuntimeError: If git clone fails
    """
    logger.info("[GIT_CLONE] === Starting Git clone operation ===")
    logger.info("[GIT_CLONE] Repository URL: %s", repo_url)
    logger.info("[GIT_CLONE] Target directory: %s", target_dir)

    try:
        # Ensure target directory parent exists
        parent_dir = target_dir.parent
        logger.debug("[GIT_CLONE] Ensuring parent directory exists: %s", parent_dir)
        parent_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("[GIT_CLONE] Parent directory ready")

        # Remove existing directory if it exists
        if target_dir.exists():
            logger.warning("[GIT_CLONE] Target directory already exists, removing: %s", target_dir)
            shutil.rmtree(target_dir)
            logger.debug("[GIT_CLONE] Existing directory removed")

        # Run git clone
        logger.info("[GIT_CLONE] Executing: git clone %s %s", repo_url, target_dir)
        logger.debug("[GIT_CLONE] Timeout set to 300 seconds (5 minutes)")
        result = subprocess.run(
            ["git", "clone", repo_url, str(target_dir)],
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )

        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "Unknown error"
            logger.error("[GIT_CLONE] Git clone failed with return code %d", result.returncode)
            logger.error("[GIT_CLONE] Error output: %s", error_msg)
            raise RuntimeError(f"Git clone failed: {error_msg}")

        logger.info("[GIT_CLONE] ✓ Successfully cloned repository")
        logger.debug("[GIT_CLONE] Clone stdout: %s", result.stdout[:500] if result.stdout else "(empty)")
        return target_dir

    except subprocess.TimeoutExpired:
        logger.error("[GIT_CLONE] Clone operation timed out after 5 minutes")
        raise RuntimeError(f"Git clone timed out after 5 minutes for {repo_url}")
    except FileNotFoundError:
        logger.error("[GIT_CLONE] Git command not found in PATH")
        raise RuntimeError("Git command not found. Please install Git.")
    except Exception as e:
        logger.error("[GIT_CLONE] Unexpected error during clone: %s", e, exc_info=True)
        raise RuntimeError(f"Failed to clone repository: {str(e)}")


def install_dependencies(repo_path: Path) -> Dict[str, Any]:
    """
    Install dependencies for a cloned repository.

    Checks for requirements.txt or pyproject.toml and installs dependencies.

    Args:
        repo_path: Path to the cloned repository

    Returns:
        Dict with installation status
    """
    logger.info("[DEPS_INSTALL] === Starting dependency installation ===")
    logger.info("[DEPS_INSTALL] Repository path: %s", repo_path)

    result = {
        "success": False,
        "method": None,
        "message": None
    }

    try:
        # Check for requirements.txt
        requirements_file = repo_path / "requirements.txt"
        logger.debug("[DEPS_INSTALL] Checking for requirements.txt at: %s", requirements_file)
        if requirements_file.exists():
            logger.info("[DEPS_INSTALL] Found requirements.txt")
            try:
                pip_cmd = [sys.executable, "-m", "pip", "install", "-r", str(requirements_file)]
                logger.debug("[DEPS_INSTALL] Executing: %s", ' '.join(pip_cmd))
                logger.debug("[DEPS_INSTALL] Timeout: 600 seconds (10 minutes)")
                subprocess.run(
                    pip_cmd,
                    check=True,
                    capture_output=True,
                    timeout=600  # 10 minute timeout
                )
                result["success"] = True
                result["method"] = "requirements.txt"
                result["message"] = "Dependencies installed from requirements.txt"
                logger.info("[DEPS_INSTALL] ✓ %s", result["message"])
                return result
            except subprocess.CalledProcessError as e:
                error_output = e.stderr.decode() if e.stderr else str(e)
                result["message"] = f"Failed to install from requirements.txt: {error_output}"
                logger.error("[DEPS_INSTALL] pip install failed with return code %d", e.returncode)
                logger.error("[DEPS_INSTALL] Error: %s", error_output[:500])
                return result
            except subprocess.TimeoutExpired:
                result["message"] = "Dependency installation timed out after 10 minutes"
                logger.error("[DEPS_INSTALL] Installation timed out")
                return result

        # Check for pyproject.toml
        pyproject_file = repo_path / "pyproject.toml"
        logger.debug("[DEPS_INSTALL] Checking for pyproject.toml at: %s", pyproject_file)
        if pyproject_file.exists():
            logger.info("[DEPS_INSTALL] Found pyproject.toml")
            try:
                pip_cmd = [sys.executable, "-m", "pip", "install", "-e", str(repo_path)]
                logger.debug("[DEPS_INSTALL] Executing: %s", ' '.join(pip_cmd))
                logger.debug("[DEPS_INSTALL] Timeout: 600 seconds (10 minutes)")
                subprocess.run(
                    pip_cmd,
                    check=True,
                    capture_output=True,
                    timeout=600  # 10 minute timeout
                )
                result["success"] = True
                result["method"] = "pyproject.toml"
                result["message"] = "Dependencies installed from pyproject.toml"
                logger.info("[DEPS_INSTALL] ✓ %s", result["message"])
                return result
            except subprocess.CalledProcessError as e:
                error_output = e.stderr.decode() if e.stderr else str(e)
                result["message"] = f"Failed to install from pyproject.toml: {error_output}"
                logger.error("[DEPS_INSTALL] pip install failed with return code %d", e.returncode)
                logger.error("[DEPS_INSTALL] Error: %s", error_output[:500])
                return result
            except subprocess.TimeoutExpired:
                result["message"] = "Dependency installation timed out after 10 minutes"
                logger.error("[DEPS_INSTALL] Installation timed out")
                return result

        # No dependency file found
        logger.debug("[DEPS_INSTALL] No requirements.txt found")
        logger.debug("[DEPS_INSTALL] No pyproject.toml found")
        result["message"] = "No requirements.txt or pyproject.toml found, skipping dependency installation"
        logger.info("[DEPS_INSTALL] %s", result["message"])
        result["success"] = True  # Not an error, just no dependencies to install
        return result

    except Exception as e:
        result["message"] = f"Error during dependency installation: {str(e)}"
        logger.error("[DEPS_INSTALL] Unexpected error: %s", e, exc_info=True)
        return result
