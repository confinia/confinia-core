"""Environments view (issue #85): the collector probes every environment, the
promote script maintains the active-color marker, and the provisioned Grafana
dashboard queries exactly those metrics. Static checks — the live values were
verified against Prometheus at build time."""
import json
import os

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _read(*parts):
    return open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def test_collector_probes_every_environment():
    cfg = _read("deploy", "otel-collector.yaml")
    for target in ("http://127.0.0.1:8091/healthz",      # blue
                   "http://127.0.0.1:11220/healthz",      # green
                   "http://127.0.0.1:11420/healthz",      # sandbox
                   "http://127.0.0.1:8095/auth/realms/confinia",  # keycloak
                   "https://api.confinia.io/healthz"):   # public edge -> active
        assert target in cfg, f"missing probe: {target}"
    assert "filestats" in cfg and "/edge-state/active-*" in cfg
    assert 'attributes["file_name"]' in cfg              # label promotion transform


def test_collector_runs_on_host_network_with_state_mount():
    compose = _read("docker-compose.yml")
    otel = compose.split("otel-collector:")[1].split("prometheus:")[0]
    assert "network_mode: host" in otel                  # loopback probes need it
    assert "confinia-edge-state:/edge-state:ro" in otel


def test_promote_maintains_active_marker():
    sh = _read("deploy", "stacks.sh")
    assert 'active-$c' in sh                             # marker created on promote
    assert "rm -f ~/confinia-edge-state/active-blue ~/confinia-edge-state/active-green" in sh


def test_prometheus_scrapes_host_collector():
    # The scrape target and the collector's prometheus exporter are one pair:
    # change either alone and metrics stop with both processes reporting healthy.
    assert "host.containers.internal:11061" in _read("deploy", "prometheus.yml")
    assert "endpoint: 0.0.0.0:11061" in _read("deploy", "otel-collector.yaml")


def test_dashboard_shows_roles_and_liveness():
    d = json.loads(_read("deploy", "grafana", "provisioning", "dashboards",
                         "confinia-environments.json"))
    assert d["uid"] == "confinia-environments"
    exprs = " ".join(t["expr"] for p in d["panels"] for t in p.get("targets", []))
    assert 'file_name="active-blue"' in exprs and 'file_name="active-green"' in exprs
    for url in ("127.0.0.1:8091", "127.0.0.1:8402", "127.0.0.1:11420",
                "127.0.0.1:8095", "api.confinia.io"):
        assert url in exprs, f"liveness query missing for {url}"
    titles = [p["title"] for p in d["panels"]]
    assert any("BLUE" in t for t in titles) and any("GREEN" in t for t in titles)
    assert any("Sandbox" in t for t in titles)
