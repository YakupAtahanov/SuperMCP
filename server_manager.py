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
    try:
        import httpx
        
        # Validate URL format
        if not url.startswith(("http://", "https://")):
            return {
                "success": False,
                "error": f"Invalid URL format: {url}. Must start with http:// or https://"
            }
        
        # Prepare headers from environment variables
        headers = {}
        if env:
            # Convert env vars to HTTP headers (X-MCP-{VAR_NAME} format)
            for key, value in env.items():
                header_name = f"X-MCP-{key.upper().replace('_', '-')}"
                headers[header_name] = value
        
        # Try to connect (HEAD request to test connectivity)
        try:
            response = httpx.head(url, headers=headers, timeout=5.0, follow_redirects=True)
            return {
                "success": True,
                "status_code": response.status_code,
                "url": url
            }
        except httpx.ConnectError:
            return {
                "success": False,
                "error": f"Could not connect to {url}. Server may be down or URL incorrect."
            }
        except httpx.TimeoutException:
            return {
                "success": False,
                "error": f"Connection to {url} timed out."
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Error connecting to {url}: {str(e)}"
            }
    except ImportError:
        logger.warning("httpx not available, cannot test SSE connection")
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
    try:
        # Ensure target directory parent exists
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        
        # Remove existing directory if it exists
        if target_dir.exists():
            logger.warning(f"Target directory {target_dir} already exists, removing...")
            shutil.rmtree(target_dir)
        
        # Run git clone
        logger.info(f"Cloning {repo_url} to {target_dir}")
        result = subprocess.run(
            ["git", "clone", repo_url, str(target_dir)],
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )
        
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "Unknown error"
            raise RuntimeError(f"Git clone failed: {error_msg}")
        
        logger.info(f"Successfully cloned {repo_url} to {target_dir}")
        return target_dir
        
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Git clone timed out after 5 minutes for {repo_url}")
    except FileNotFoundError:
        raise RuntimeError("Git command not found. Please install Git.")
    except Exception as e:
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
    result = {
        "success": False,
        "method": None,
        "message": None
    }
    
    try:
        # Check for requirements.txt
        requirements_file = repo_path / "requirements.txt"
        if requirements_file.exists():
            logger.info(f"Found requirements.txt at {requirements_file}")
            try:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-r", str(requirements_file)],
                    check=True,
                    capture_output=True,
                    timeout=600  # 10 minute timeout
                )
                result["success"] = True
                result["method"] = "requirements.txt"
                result["message"] = "Dependencies installed from requirements.txt"
                logger.info(result["message"])
                return result
            except subprocess.CalledProcessError as e:
                result["message"] = f"Failed to install from requirements.txt: {e.stderr.decode() if e.stderr else str(e)}"
                logger.warning(result["message"])
                return result
            except subprocess.TimeoutExpired:
                result["message"] = "Dependency installation timed out"
                logger.warning(result["message"])
                return result
        
        # Check for pyproject.toml
        pyproject_file = repo_path / "pyproject.toml"
        if pyproject_file.exists():
            logger.info(f"Found pyproject.toml at {pyproject_file}")
            try:
                import sys
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-e", str(repo_path)],
                    check=True,
                    capture_output=True,
                    timeout=600  # 10 minute timeout
                )
                result["success"] = True
                result["method"] = "pyproject.toml"
                result["message"] = "Dependencies installed from pyproject.toml"
                logger.info(result["message"])
                return result
            except subprocess.CalledProcessError as e:
                result["message"] = f"Failed to install from pyproject.toml: {e.stderr.decode() if e.stderr else str(e)}"
                logger.warning(result["message"])
                return result
            except subprocess.TimeoutExpired:
                result["message"] = "Dependency installation timed out"
                logger.warning(result["message"])
                return result
        
        # No dependency file found
        result["message"] = "No requirements.txt or pyproject.toml found, skipping dependency installation"
        logger.info(result["message"])
        result["success"] = True  # Not an error, just no dependencies to install
        return result
        
    except Exception as e:
        result["message"] = f"Error during dependency installation: {str(e)}"
        logger.error(result["message"], exc_info=True)
        return result
