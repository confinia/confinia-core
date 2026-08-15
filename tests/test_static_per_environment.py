"""Each environment serves its own static files (issue #111).

Until 2026-08-12 www, staging and sandbox all served `/srv/site`, so:

  - editing ./deploy/site or ./demo changed PRODUCTION immediately;
  - staging showed the same bytes as production, so a static change could not be
    validated before it was live -- the ⚠️ that RULES 13 warns about;
  - it took the map down on 2026-08-03, when a vendored path our own scanner
    filter blocked went straight to www with nowhere to catch it.
"""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _read(*parts):
    return open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def _roots():
    """{hostname: {roots it serves}} from the project Caddyfile."""
    host, out = None, {}
    for line in _read("deploy", "caddy", "Caddyfile").splitlines():
        # A vhost line may list several addresses, with or without a port:
        #   http://www.confinia.io {
        #   http://www.confinia.io:8085, http://www.confinia.io:11000 {
        # The second form arrived with the 1PESI dual-publish; parsing only the
        # first made this test read "www serves nothing" and pass or fail for
        # the wrong reason.
        if line.startswith("http://") and line.rstrip().endswith("{"):
            hosts = re.findall(r"http://([a-z0-9.-]+)(?::\d+)?", line)
            if hosts:
                host = hosts[0]
        m = re.search(r"root \* (\S+)", line)
        if m and host:
            out.setdefault(host, set()).add(m.group(1))
    return out


def test_no_environment_serves_productions_files():
    roots = _roots()
    prod = {r for r in roots.get("www.confinia.io", set())}
    assert prod, "www serves nothing? the Caddyfile changed shape"
    for host, rs in roots.items():
        if host.startswith("www."):
            continue
        shared = {r for r in rs if any(r == p or r.startswith(p + "/") for p in prod)}
        assert not shared, (
            f"{host} serves production's files ({shared}), so a static change "
            "there cannot be validated before it is live")


def test_staging_and_sandbox_roots_are_mounted():
    compose = _read("docker-compose.yml")
    for host, rs in _roots().items():
        if host.startswith("www."):
            continue
        for r in rs:
            # A root may be a SUBdirectory of the mount (/srv/sbx-site/sbx),
            # so check the mount point, which is the first two components.
            parts = r.strip("/").split("/")
            mount = "/" + "/".join(parts[:2]) if len(parts) >= 2 else r
            assert f":{mount}:ro" in compose, \
                f"{host} serves {r} but nothing mounts {mount}"


def test_the_checkout_script_refuses_production():
    sh = _read("deploy", "checkout-env.sh")
    assert "REFUSING" in sh, "it must refuse anything but staging/sandbox"
    assert "staging|sandbox)" in sh


def test_staging_checkout_is_synced_by_ci():
    wf = _read(".github", "workflows", "deploy-staging.yml")
    assert "checkout-env.sh staging" in wf, \
        "staging's static files must follow the deployed commit, or they drift"
