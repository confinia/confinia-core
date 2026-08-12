# `containers.conf` — podman settings for the `confinia` user

Install to `~/.config/containers/containers.conf` on the VM:

```sh
rsync deploy/containers/containers.conf confinia:~/.config/containers/containers.conf
ssh confinia 'podman info --format "{{.Host.EventLogger}}"'   # must print: file
```

## Why this file exists

`podman events` returned **zero lines across twelve hours** during which
containers were demonstrably created and removed. The logger was `journald`,
which in rootless mode needs a session bus that is not reliably present here —
and it fails **silently**. So "no events" read as "nothing happened", and issue
#123 was diagnosed blind for a week: three successive hypotheses, none testable.

The `file` backend writes to `$XDG_RUNTIME_DIR/libpod/tmp/events/events.log` and
has no such dependency.

**This change was impossible before 2026-08-11.** `containers.conf` was shared
with five other products under the `debian` account, so touching it meant
touching their podman too. Moving Confinia to its own user (#99) is what made a
one-line fix available.

⚠️ `podman events --until now` is rejected by this backend ("unable to interpret
time value"). Use `--since <duration>` alone, or read the file.
