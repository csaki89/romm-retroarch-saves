import datetime as dt

UTC = dt.timezone.utc


def parse_iso(value):
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def iso_from_mtime(mtime):
    return dt.datetime.fromtimestamp(mtime, tz=UTC).isoformat()


def pick_winner(policy, local_mtime, server_updated_at):
    """Conflict rule: returns "local" or "server".

    "local"/"server" policies always pick that side. "newer" compares the local
    file's mtime with the server's updated_at; ties and unparseable or
    missing server timestamps go to local, so nothing is overwritten
    on a guess.
    """
    if policy in ("local", "server"):
        return policy
    if not server_updated_at:
        return "local"
    try:
        server_dt = parse_iso(server_updated_at)
    except ValueError:
        return "local"
    local_dt = dt.datetime.fromtimestamp(local_mtime, tz=UTC)
    return "local" if local_dt >= server_dt else "server"
