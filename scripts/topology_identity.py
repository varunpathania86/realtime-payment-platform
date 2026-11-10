"""Canonical identities and compatibility checks for the local three-cluster topology."""

CLUSTER_NUMBERS = ("1", "2", "3")
DATABASE_MEMBERS = tuple(f"pg-{number}" for number in CLUSTER_NUMBERS)
IDENTITY_VERSION = "numbered-v1"


def require_numbered_identity(state):
    marker = state / "cluster-identity"
    if not marker.is_file() or marker.read_text().strip() != IDENTITY_VERSION:
        raise ValueError(
            f"legacy or unsupported cluster identity in {state}; preserve its state and data. "
            "See docs/developer-setup.md."
        )


def database_profiles(base, topology):
    if not isinstance(topology, dict):
        raise ValueError("database topology must be an object")
    expected = [f"{base}-{number}" for number in CLUSTER_NUMBERS]
    if topology.get("profiles") != expected:
        raise ValueError("database topology profiles do not match numbered cluster identities")
    return dict(zip(DATABASE_MEMBERS, expected))


def database_primary(topology):
    primary = topology.get("database_primary", DATABASE_MEMBERS[0])
    if primary not in DATABASE_MEMBERS:
        raise ValueError("invalid database primary identity; refusing topology changes")
    return primary
