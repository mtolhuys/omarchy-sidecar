VERSION := 0.2.2
GRAPH := v1013
DIST_NAME := omarchy-sidecar-$(VERSION)
ARTIFACT := dist/$(DIST_NAME).tar.gz

.PHONY: check test test-python test-web local-update marketing-gif lab-check phase0 lab lab-missing lab-marketing performance dist security

LAB_ROOT ?= ../../omarchy/plugin-lab
LAB_RUNNER := $(abspath $(LAB_ROOT))/bin/lab

check:
	python3 -B -c 'import ast, pathlib; paths = list(pathlib.Path("sidecar").glob("*.py")) + list(pathlib.Path("tests").rglob("*.py")) + [pathlib.Path("helper/sidecard"), pathlib.Path("helper/sidecarctl")]; [ast.parse(path.read_text(), filename=str(path)) for path in paths]'
	node --check web/dist/app.$(GRAPH).js
	node --check web/dist/model.$(GRAPH).js
	node --check web/dist/sw.$(GRAPH).js
	python3 -m json.tool manifest.json >/dev/null
	python3 -m json.tool web/dist/manifest.webmanifest >/dev/null

test: check test-python test-web

test-python:
	python3 -B -m unittest discover -s tests -p 'test_*.py' -v

test-web:
	node tests/test_web.mjs

local-update: test dist
	bash scripts/local-update

marketing-gif:
	bash scripts/build-marketing-gif

lab-check:
	@test -x "$(LAB_RUNNER)" || { \
		echo "Omarchy Plugin Lab was not found at $(abspath $(LAB_ROOT))."; \
		echo "Clone https://github.com/mtolhuys/omarchy-plugin-lab beside this checkout or pass LAB_ROOT=/path/to/omarchy-plugin-lab."; \
		exit 1; \
	}

phase0: lab-check
	OMARCHY_PLUGIN_LAB_ROOT=$(abspath $(LAB_ROOT)) $(LAB_RUNNER) plugin $(CURDIR)/tests/lab/phase0-contract-spike.sh

lab: lab-check
	OMARCHY_PLUGIN_LAB_ROOT=$(abspath $(LAB_ROOT)) $(LAB_RUNNER) plugin $(CURDIR)/tests/lab/v1-lifecycle.sh

lab-missing: lab-check
	OMARCHY_PLUGIN_LAB_ROOT=$(abspath $(LAB_ROOT)) $(LAB_RUNNER) plugin $(CURDIR)/tests/lab/missing-tailscale.sh

lab-marketing: lab-check
	OMARCHY_PLUGIN_LAB_ROOT=$(abspath $(LAB_ROOT)) $(LAB_RUNNER) plugin $(CURDIR)/tests/lab/marketing-captures.sh

performance:
	python3 -B tests/performance_probe.py

dist:
	mkdir -p dist
	tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner \
		--exclude='*/__pycache__' --exclude='*.pyc' \
		--transform='s,^,$(DIST_NAME)/,' -cf - \
		manifest.json service bar-widget helper sidecar web docs preview.png README.md LICENSE CHANGELOG.md \
		CONTRIBUTING.md SECURITY.md PRIVACY.md SUPPORT.md \
		| gzip -n > $(ARTIFACT)
	sha256sum $(ARTIFACT) > $(ARTIFACT).sha256

security: test dist
	python3 -B tests/release_security.py
