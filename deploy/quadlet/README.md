# Quadlet units for the colour APIs

```sh
rsync -a deploy/quadlet/*.container confinia:~/.config/containers/systemd/
ssh confinia 'systemctl --user daemon-reload && systemctl --user start confinia-green-api'
```

## Why these exist

Issue #123. A container created by a CI job is a **child of that job**. Roughly
two minutes after the job finished, the passive colour was killed — `exit=-1`,
which is neither a crash nor a clean stop — and staging stayed dead until the
watchdog or a human noticed. The identical command run over ssh survived nine
hours. That difference is the whole bug.

Six hypotheses were tested and disproved before this one: a dying port
publisher, churn from other tenants on the shared `debian` podman, an orphaned
publisher, port squatting by another product, the host-network smoke container,
and missing cgroup delegation on the runner unit (`Delegate=yes` changed
nothing). What finally identified it was `exit=-1` plus the controlled
comparison of ssh versus CI.

With Quadlet the container belongs to systemd, so CI only ever runs
`systemctl --user restart confinia-<colour>-api`. It outlives the job **by
construction**.

`loginctl enable-linger confinia` — already set by the #99 migration — is what
keeps these alive with no login session.

## What is deliberately NOT here

The **databases**. They are long-lived, started by hand, and never recreated by a
deployment, so they never hit this fault. Converting them would be churn for no
benefit and would put the precious volumes behind a second mechanism.
