"""Transactional e-mail, as code and without leaking a password (issue #132).

Two failures to prevent. A secret reaching the public repository, and an
alerting setup that exists only in the Grafana UI — which a container rebuild
deletes silently, leaving a channel that looks configured and never fires.
"""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")
ALERTING = os.path.join(ROOT, "deploy", "grafana", "provisioning", "alerting")


def _read(*parts):
    return open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def _directives(*parts):
    """The file without its comments.

    Written after tripping three of these tests on their own explanations: a
    comment saying "never do X" contains X, so a naive `X not in file` fails on
    correct code. Assert against what the machine reads, not what humans read.
    """
    out = []
    for line in _read(*parts).splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("//"):
            continue
        out.append(line.split("  #")[0])
    return "\n".join(out)


def test_the_secret_file_is_gitignored():
    assert re.search(r"^deploy/mail\.env$", _read(".gitignore"), re.M), \
        "deploy/mail.env must be ignored in the same commit that introduces it"


def test_no_smtp_password_is_committed():
    # The template may say CHANGE_ME; nothing else may carry a password.
    for rel in ("deploy/mail.env.example",
                "deploy/grafana/provisioning/alerting/contact-points.yml",
                "deploy/grafana/provisioning/alerting/rules.yml",
                "docker-compose.yml",
                "deploy/keycloak/setup-realm.sh"):
        for line in _read(*rel.split("/")).splitlines():
            if line.strip().startswith("#"):
                continue
            if re.search(r"(SMTP_)?PASSWORD\s*[=:]", line, re.I):
                assert "CHANGE_ME" in line or "$" in line or "get " in line, \
                    f"{rel} looks like it carries a literal password: {line.strip()}"


def test_grafana_smtp_comes_from_the_env_file():
    # Not from grafana.ini edited inside the container: a rebuild discards it.
    compose = _directives("docker-compose.yml")
    assert "./deploy/mail.env" in compose, "grafana must read SMTP from the env file"
    assert "grafana.ini" not in compose, \
        "SMTP must not be baked into a config file the container owns"


def test_alerting_is_provisioned_from_files():
    for name in ("contact-points.yml", "notification-policies.yml", "rules.yml"):
        assert os.path.exists(os.path.join(ALERTING, name)), f"{name} missing"


def test_the_colour_probe_rule_targets_the_real_ports():
    rules = _read("deploy", "grafana", "provisioning", "alerting", "rules.yml")
    assert "8091" in rules and "8402" in rules, "both colours must be watched"
    assert "8092" not in rules and "8093" not in rules, "those ports are BURNED"


def test_verify_email_is_not_flipped_automatically():
    # verifyEmail true with broken SMTP fails the registration flow: nobody can
    # sign up, and the cause is three layers away from the symptom.
    sh = _read("deploy", "keycloak", "setup-realm.sh")
    assert "VERIFY_EMAIL" in sh, "flipping verifyEmail must be a deliberate act"
    m = re.search(r'if \[ "\$\{VERIFY_EMAIL:-0\}" = 1 \]', sh)
    assert m, "verifyEmail must default to OFF"
