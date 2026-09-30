# The two things worth a shortcut from a checkout.
#
#   make studio        drop a rig in the browser, get the source plus every
#                      conversion side by side (jobs land under STUDIO_ROOT)
#   make studio-stop   stop a studio left serving STUDIO_PORT
#   make test          run the test suite
#
# The conversions themselves run in the page; nothing else needs wrapping. The
# CLI is the disk-side path (it writes into a directory of your choice) and is
# what CI drives:
#
#   python3 -m src.cli convert --from spine --to godot rig.json -o out --name rig
#
# `make` runs in this directory, which is what `python3 -m src.cli` requires —
# that is why the harness targets work from anywhere.

PYTHON     ?= python3
# The studio serves its own jobs; keep it off anything else you have running.
STUDIO_PORT ?= 8643
STUDIO_ROOT ?= tmp/studio

.PHONY: studio studio-stop test

# Interactive studio: upload a rig, get the source pane and one pane per
# converted format. Never fights for the port — an instance already serving it
# is reported with the two ways out.
studio:
	@echo "--> studio: http://localhost:$(STUDIO_PORT)/  (Ctrl+C stops)"
	@if $(PYTHON) -c 'import socket,sys; sys.exit(0 if socket.socket().connect_ex(("127.0.0.1", $(STUDIO_PORT))) == 0 else 1)'; then \
	  echo "--> port $(STUDIO_PORT) already in use — a studio is probably still up"; \
	  echo "    use it (open the URL above), stop it:  make studio-stop"; \
	  echo "    or serve on another port:              make studio STUDIO_PORT=8644"; \
	else \
	  $(PYTHON) -m src.studio $(STUDIO_PORT) --root "$(STUDIO_ROOT)"; \
	fi

# Stop the studio serving STUDIO_PORT (a leftover from an earlier run, with no
# terminal left to Ctrl+C in). Only ever kills a process that IS a studio;
# another server on that port is reported, not shot down.
studio-stop:
	@$(PYTHON) -m src.studio $(STUDIO_PORT) --stop

test:
	$(PYTHON) -m pytest tests/ -v
