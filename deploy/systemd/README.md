# User systemd units for the `confinia` account

Install to `~/.config/systemd/user/`, then:

```sh
systemctl --user daemon-reload
systemctl --user enable --now confinia-runner
```

`loginctl enable-linger confinia` (done by the #99 migration) is what keeps these
running with no login session.

## `confinia-runner.service`

The GitHub Actions runner. It runs **unprivileged** — `svc.sh install` wants
root, which is exactly the privilege issue #114 removed, so the unit is written
by hand instead.

`Delegate=yes` was added while investigating #123, on the theory that podman
could not create its own cgroup scopes from inside a job. **It did not fix the
fault**, and is kept because it is correct regardless: podman should own the
scopes of the containers it starts.
