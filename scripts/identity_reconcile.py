"""Explicit idempotent reconciliation of the managed rpp realm, including existing databases."""

import argparse
import base64
import json
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path


class RealmAdmin:
    def __init__(self, url, credentials):
        self.url = url.rstrip("/")
        form = urllib.parse.urlencode({
            "client_id": "admin-cli", "grant_type": "password", "username": "bootstrap-admin",
            "password": credentials["KC_BOOTSTRAP_ADMIN_PASSWORD"],
        }).encode()
        request = urllib.request.Request(self.url + "/realms/master/protocol/openid-connect/token",
                                        data=form, headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(request, timeout=10) as response:  # nosec B310
            self.token = json.load(response)["access_token"]

    def call(self, method, path, data=None):
        request = urllib.request.Request(self.url + "/admin/realms/rpp" + path, method=method,
            data=None if data is None else json.dumps(data).encode(),
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:  # nosec B310
            raw = response.read(1 << 20)
            return json.loads(raw) if raw else None


def reconcile(admin, realm):
    """Own only declared clients, roles, groups and users; never reset existing passwords."""
    admin.call("PUT", "", {key: value for key, value in realm.items() if key not in {"clients", "groups", "roles", "users"}})
    clients = {}
    scopes = {scope["name"]: scope["id"] for scope in admin.call("GET", "/client-scopes")} \
        if any("defaultClientScopes" in client for client in realm["clients"]) else {}
    for desired in realm["clients"]:
        found = admin.call("GET", "/clients?clientId=" + urllib.parse.quote(desired["clientId"]))
        if not found:
            admin.call("POST", "/clients", desired)
            found = admin.call("GET", "/clients?clientId=" + urllib.parse.quote(desired["clientId"]))
        else:
            admin.call("PUT", "/clients/" + found[0]["id"], desired)
        clients[desired["clientId"]] = found[0]["id"]
        if "defaultClientScopes" in desired:
            path = "/clients/" + found[0]["id"] + "/default-client-scopes"
            attached = {scope["name"]: scope["id"] for scope in admin.call("GET", path)}
            wanted = set(desired["defaultClientScopes"])
            for name in sorted(wanted - attached.keys()):
                # Client PUT does not update scope bindings on existing Keycloak databases.
                admin.call("PUT", path + "/" + scopes[name])
            for name in sorted(attached.keys() - wanted):
                admin.call("DELETE", path + "/" + attached[name])
    for name, roles in realm["roles"]["client"].items():
        path = "/clients/" + clients[name] + "/roles"
        existing = {role["name"] for role in admin.call("GET", path)}
        for role in roles:
            if role["name"] not in existing:
                admin.call("POST", path, role)

    def mappings(path, desired):
        for name, declared in realm["roles"]["client"].items():
            mapping = path + "/role-mappings/clients/" + clients[name]
            existing = admin.call("GET", mapping)
            wanted = set(desired.get(name, []))
            managed = {role["name"] for role in declared}
            removed = [role for role in existing if role["name"] in managed and role["name"] not in wanted]
            if removed:
                admin.call("DELETE", mapping, removed)
            current = {role["name"] for role in existing}
            added = [admin.call("GET", "/clients/" + clients[name] + "/roles/" + urllib.parse.quote(role, safe=""))
                    for role in sorted(wanted - current)]
            if added:
                admin.call("POST", mapping, added)

    groups = {}
    for desired in realm["groups"]:
        existing = {group["name"]: group for group in admin.call("GET", "/groups")}
        name = desired["name"]
        if name not in existing:
            admin.call("POST", "/groups", {"name": name})
            existing = {group["name"]: group for group in admin.call("GET", "/groups")}
        groups["/" + name] = existing[name]["id"]
        mappings("/groups/" + existing[name]["id"], desired["clientRoles"])
    for desired in realm["users"]:
        path = "/users?exact=true&username=" + urllib.parse.quote(desired["username"])
        existing = admin.call("GET", path)
        if not existing:
            admin.call("POST", "/users", desired)
            existing = admin.call("GET", path)
        userpath = "/users/" + existing[0]["id"]
        admin.call("PUT", userpath, {key: value for key, value in desired.items()
                                    if key not in {"credentials", "groups", "clientRoles"}})
        mappings(userpath, desired.get("clientRoles", {}))
        current = {group["path"]: group["id"] for group in admin.call("GET", userpath + "/groups")}
        wanted = set(desired.get("groups", []))
        for name, identifier in groups.items():
            if name in wanted and name not in current:
                admin.call("PUT", userpath + "/groups/" + identifier)
            elif name in current and name not in wanted:
                admin.call("DELETE", userpath + "/groups/" + identifier)


def render_realm(text, credentials):
    def replace(match):
        return json.dumps(credentials[match[1]])[1:-1]
    return json.loads(re.sub(r"\$\{([A-Z_]+)\}", replace, text))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True)
    parser.add_argument("--url", required=True, help="Loopback Keycloak forwarding URL")
    args = parser.parse_args()
    parsed = urllib.parse.urlparse(args.url)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise SystemExit("identity reconciliation requires a loopback HTTP forward")
    result = subprocess.run(["kubectl", "--context", args.context, "-n", "security", "get", "secret",
                            "identity-provider", "-o", "json"], check=True, capture_output=True, text=True)
    credentials = {key: base64.b64decode(value).decode() for key, value in json.loads(result.stdout)["data"].items()}
    realm = render_realm(Path("platform/terraform/modules/identity/realm.json").read_text(), credentials)
    reconcile(RealmAdmin(args.url, credentials), realm)
    print("Managed rpp realm reconciled (existing credentials preserved).")


if __name__ == "__main__":
    main()
