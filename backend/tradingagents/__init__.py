"""TradingAgents: multi-agent LLM financial trading framework."""

__version__ = "0.7.0"

from pathlib import Path

from dotenv import find_dotenv, load_dotenv

# Load .env at package import so DEFAULT_CONFIG's env-var overlay and every LLM
# client see the user's keys whichever entry point started the process.
# usecwd=True walks from the working directory, so the installed console script
# finds the project's .env rather than looking beside site-packages. Values the
# caller has already exported are never overridden.
_backend_root = Path(__file__).resolve().parent.parent
_local_env = _backend_root / ".env"
_enterprise_env = _backend_root / ".env.enterprise"
load_dotenv(_local_env if _local_env.is_file() else find_dotenv(usecwd=True))
load_dotenv(
    _enterprise_env if _enterprise_env.is_file() else find_dotenv(".env.enterprise", usecwd=True),
    override=False,
)
