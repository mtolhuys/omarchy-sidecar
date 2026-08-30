# Contributing

Read `AGENTS.md` and the relevant contract documents before changing Sidecar. Security, protocol, product, and test behavior must stay synchronized.

Run `make test` for every change. Use the disposable [Omarchy Plugin Lab](https://github.com/mtolhuys/omarchy-plugin-lab) for all manifest, Quickshell, Hyprland, plugin lifecycle, route, or graphical behavior; never activate a development build on the real host desktop. Clone it beside Sidecar or pass its location as `LAB_ROOT=/path/to/omarchy-plugin-lab`. A visible guest control needs public QMP input plus an observable assertion.

New remote operations require an explicit protocol schema, scope, target eligibility rule, fixed adapter, lock/pause checks, security review, and tests. Generic commands, dispatch strings from clients, paths, URLs, key/pointer input, clipboard, files, screen data, agent content/input, power actions, and privilege changes are out of scope.

Keep runtime dependencies local and reviewed. The helper intentionally uses only Python's standard library. Update `CHANGELOG.md`, `docs/DECISIONS.md`, and `docs/RELEASE-EVIDENCE.md` when behavior or evidence changes.
