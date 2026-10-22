"""Runtime configuration (environment driven, so the same package runs on a
phone, in CI and on a developer machine without edits)."""
import os
import secrets

DEFAULT_STATE_DIR = os.environ.get("MINIS_BRIDGE_STATE_DIR", "/var/minis/shared/claude-bridge")


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class Config:
    def __init__(self):
        self.host = os.environ.get("MINIS_BRIDGE_HOST", "0.0.0.0")
        self.port = _int("MINIS_BRIDGE_PORT", 8900)
        self.state_dir = os.environ.get("MINIS_BRIDGE_STATE_DIR", DEFAULT_STATE_DIR)
        self.token_file = os.path.join(self.state_dir, "token")
        self.db_file = os.environ.get("MINIS_BRIDGE_DB", os.path.join(self.state_dir, "relay.db"))
        self.log_file = os.environ.get("MINIS_BRIDGE_LOG", os.path.join(self.state_dir, "access.log"))
        self.log_level = os.environ.get("MINIS_BRIDGE_LOG_LEVEL", "INFO").upper()
        # tools that only the private (device/LAN) endpoint may expose
        self.private_only_tools = {
            t.strip() for t in os.environ.get(
                "MINIS_BRIDGE_PRIVATE_ONLY_TOOLS", "iphone_shell,shell").split(",") if t.strip()
        }
        self.allow_public_shell = os.environ.get("MINIS_BRIDGE_ALLOW_PUBLIC_SHELL", "0") == "1"
        self.rate_limit_per_min = _int("MINIS_BRIDGE_RATE_LIMIT", 240)
        self.max_tool_output = _int("MINIS_BRIDGE_MAX_OUTPUT", 60000)
        self.tool_timeout = _int("MINIS_BRIDGE_TOOL_TIMEOUT", 90)
        self.protocol_version = os.environ.get("MINIS_BRIDGE_PROTOCOL", "2025-06-18")
        self.server_name = "minis-iphone-bridge"
        self.public_hosts = {
            h.strip().lower() for h in os.environ.get("MINIS_BRIDGE_PUBLIC_HOSTS", "").split(",")
            if h.strip()
        }
        self.tunnel_host_suffix = os.environ.get("MINIS_BRIDGE_TUNNEL_SUFFIX", "trycloudflare.com")
        self.tunnel_suffix = self.tunnel_host_suffix
        self.proxy_upstream_base = os.environ.get("MINIS_BRIDGE_UPSTREAM_BASE", "https://api.deepseek.com")
        self.proxy_upstream_key_var = os.environ.get("MINIS_BRIDGE_UPSTREAM_KEY", "DEEPSEEK_API_KEY")
        self.proxy_upstream_model = os.environ.get("MINIS_BRIDGE_UPSTREAM_MODEL", "deepseek-chat")
        self.proxy_port = _int("MINIS_BRIDGE_PROXY_PORT", 8788)
        self.oauth_enabled = os.environ.get("MINIS_BRIDGE_OAUTH", "1") == "1"
        self.oauth_issuer = os.environ.get("MINIS_BRIDGE_ISSUER", "")

    def token(self):
        """Static bearer token, created on first use."""
        try:
            with open(self.token_file) as f:
                tok = f.read().strip()
            if tok:
                return tok
        except OSError:
            pass
        tok = "mk-" + "-".join(
            "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(5))
            for _ in range(4))
        os.makedirs(os.path.dirname(self.token_file), exist_ok=True)
        with open(self.token_file, "w") as f:
            f.write(tok)
        try:
            os.chmod(self.token_file, 0o600)
        except OSError:
            pass
        return tok

    def pc_token(self):
        """Long-lived token for the paired PC agent.

        The main bridge token gets rotated whenever the connector is
        re-registered, which would instantly invalidate an agent that lives
        on the user's PC.  This token is separate, stable, and only accepted
        for the PC channel (relay boxes pc/pcreply/pchb and the two PC
        bootstrap downloads).
        """
        path = os.path.join(self.state_dir, "pc-token")
        try:
            with open(path) as f:
                tok = f.read().strip()
            if tok:
                return tok
        except OSError:
            pass
        tok = "pc-" + "-".join(
            "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(5))
            for _ in range(4))
        os.makedirs(self.state_dir, exist_ok=True)
        with open(path, "w") as f:
            f.write(tok)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return tok

    def rotate_token(self):
        try:
            os.remove(self.token_file)
        except OSError:
            pass
        return self.token()


CONFIG = Config()
