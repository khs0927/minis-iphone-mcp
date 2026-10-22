"""Client scope policy.

The phone can be reached from three places: the device itself, the local
network, and the public internet through a tunnel.  Tunnel traffic arrives at
the server from *loopback*, so the peer address cannot tell the LAN and the
tunnel apart - and HTTP headers are attacker controlled, so a public client
could simply claim a private address.

The scope is therefore decided by **which port the request came in on**:

  * ``MINIS_BRIDGE_ROLE=private`` (port 8900) - device/LAN only, full tools.
  * ``MINIS_BRIDGE_ROLE=public``  (port 8901) - the port the tunnel points at.

A request that reaches the private port from a public peer address (e.g. a
router port-forward) is still downgraded, and headers can never *widen* a
scope - only narrow it.
"""
import ipaddress

SCOPE_PRIVATE = "private"
SCOPE_PUBLIC = "public"

PRIVATE_ONLY = {"iphone_shell", "shell"}


def ip_is_private(ip):
    try:
        addr = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback or addr.is_link_local


def classify(peer_ip, host=None, role="private", public_hosts=(), tunnel_suffix="trycloudflare.com"):
    """Return the scope for a request.  Headers may only narrow the scope."""
    if (role or "private").lower() == "public":
        return SCOPE_PUBLIC

    peer_private = ip_is_private(peer_ip)
    h = (host or "").lower()

    host_is_public = False
    if h:
        if h in {x.lower() for x in public_hosts}:
            host_is_public = True
        elif tunnel_suffix and (h.endswith("." + tunnel_suffix) or h == tunnel_suffix):
            host_is_public = True
        else:
            bare = h.split(":")[0]
            try:
                host_is_public = not ipaddress.ip_address(bare).is_private
            except ValueError:
                host_is_public = True

    if not peer_private:
        return SCOPE_PUBLIC
    if host_is_public:
        return SCOPE_PUBLIC
    return SCOPE_PRIVATE


def tool_allowed(tool_name, scope, private_only_tools=PRIVATE_ONLY, allow_public_shell=False):
    """Return (allowed, reason)."""
    if scope == SCOPE_PUBLIC and tool_name in private_only_tools and not allow_public_shell:
        return False, ("tool %r is restricted to private (device/LAN) clients; "
                       "use the private endpoint or grant access explicitly" % tool_name)
    return True, None
