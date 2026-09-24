# Shortcuts for the two conversion flows.
#
#   make godot-to-spine     .tscn -> Spine bundle (json+atlas+png+index.html)
#                           then serves it and prints the preview URL
#   make spine-to-godot     Spine JSON -> .tscn + page image (no preview:
#                           a .tscn is a Godot scene, not a browser page)
#   make test               run the test suite
#
# Personal paths and output folders go in local.mk (gitignored), so this file
# stays portable. Anything below can also be overridden on the command line:
#
#   make godot-to-spine GODOT_INPUT=path/to/player.tscn NAME=bot
#
# `make` runs in this directory, which is what `python3 -m src.cli` requires —
# that is why these targets work from anywhere.

-include local.mk

PYTHON ?= python3
PORT   ?= 8642
# True when the port is already bound by another process.
PORT_BUSY = $(PYTHON) -c "import socket; s=socket.socket(); s.bind(('127.0.0.1', $(1))); print('yes') if s else None" 2>/dev/null || echo yes
NAME   ?= animation

# Per-direction ports: a local.mk can separate them (GODOT_PORT/SPINE_PORT)
# to run both servers at once; both fall back to PORT.
GODOT_PORT ?= $(PORT)
SPINE_PORT ?= $(PORT)

GODOT_INPUT ?=
SPINE_INPUT ?=

GODOT_OUT ?= $(HOME)/Desktop/convert-godot-to-spine
SPINE_OUT ?= $(HOME)/Desktop/convert-spine-to-godot

.PHONY: godot-to-spine spine-to-godot godot-preview test

# .tscn -> Spine JSON + .atlas + texture + viewer.html, then serve the folder.
# Ctrl+C stops the server; the output stays on disk.
godot-to-spine:
	@test -n "$(INPUT_GODOT_TO_SPINE)" || { \
	  echo "error: INPUT_GODOT_TO_SPINE is empty. Set it in local.mk, e.g."; \
	  echo "  INPUT_GODOT_TO_SPINE := ~/path/to/player.tscn"; \
	  exit 1; }
	@test -n "$(OUTPUT_GODOT_TO_SPINE)" && test "$(OUTPUT_GODOT_TO_SPINE)" != "/" || { \
	  echo "error: refusing to rm -rf OUTPUT_GODOT_TO_SPINE='$(OUTPUT_GODOT_TO_SPINE)'"; exit 1; }
	@echo "--> clearing old output: $(OUTPUT_GODOT_TO_SPINE)"
	@rm -rf "$(OUTPUT_GODOT_TO_SPINE)"
	@echo "--> converting Godot scene -> Spine bundle"
	@$(PYTHON) -m src.cli convert --from godot --to spine \
	  "$(INPUT_GODOT_TO_SPINE)" -o "$(OUTPUT_GODOT_TO_SPINE)" --name "$(NAME)"
	@echo "--> serving the Spine viewer on http://localhost:$(GODOT_PORT)/  (Ctrl+C stops)"
	@if $(PYTHON) -c 'import socket,sys; sys.exit(0 if socket.socket().connect_ex(("127.0.0.1", $(GODOT_PORT))) == 0 else 1)'; then \
	  echo "--> port $(GODOT_PORT) already in use — a previous server is probably still up; open the URL above"; \
	else \
	  $(PYTHON) -m http.server $(GODOT_PORT) --directory "$(OUTPUT_GODOT_TO_SPINE)"; \
	fi

# Spine JSON -> .tscn + the page image, then serve the Godot web preview. The
# browser renders the real .tscn through the real engine (WASM export).

spine-to-godot:
	@test -n "$(INPUT_SPINE_TO_GODOT)" || { \
	  echo "error: INPUT_SPINE_TO_GODOT is empty. Set it in local.mk, e.g."; \
	  echo "  INPUT_SPINE_TO_GODOT := ~/path/to/hero.json"; \
	  exit 1; }
	@test -n "$(OUTPUT_SPINE_TO_GODOT)" && test "$(OUTPUT_SPINE_TO_GODOT)" != "/" || { \
	  echo "error: refusing to rm -rf OUTPUT_SPINE_TO_GODOT='$(OUTPUT_SPINE_TO_GODOT)'"; exit 1; }
	@echo "--> clearing old output: $(OUTPUT_SPINE_TO_GODOT)"
	@rm -rf "$(OUTPUT_SPINE_TO_GODOT)"
	@echo "--> converting Spine JSON -> Godot scene"
	@$(PYTHON) -m src.cli convert --from spine --to godot \
	  "$(INPUT_SPINE_TO_GODOT)" -o "$(OUTPUT_SPINE_TO_GODOT)" --name "$(NAME)"
	@test -d "$(OUTPUT_SPINE_TO_GODOT)/output" || { \
	  echo "error: web preview was not built (is Godot installed? see GODOT_BIN)"; \
	  exit 1; }
	@echo "--> serving the Godot web preview on http://localhost:$(SPINE_PORT)/  (Ctrl+C stops)"
	@if $(PYTHON) -c 'import socket,sys; sys.exit(0 if socket.socket().connect_ex(("127.0.0.1", $(SPINE_PORT))) == 0 else 1)'; then \
	  echo "--> port $(SPINE_PORT) already in use — a previous server is probably still up; open the URL above"; \
	else \
	  $(PYTHON) -m http.server $(SPINE_PORT) --directory "$(OUTPUT_SPINE_TO_GODOT)"; \
	fi

# Pack an existing Godot scene (.tscn) and run it in the browser: the real
# engine (WASM) loads output/<name>.tscn and its referenced resources
# (textures, scripts) at their original res:// paths. Gameplay scripts are
# frozen (physics/processing) so the character stays in frame; the track
# panel switches the AnimationPlayer's animations. Native editor scenes with
# their own AnimationTree conventions render through this path too — the
# tree is silenced so the preview owns the pose.
# Everything can be overridden: make godot-preview GODOT_INPUT=... GODOT_OUT=... NAME=bot
godot-preview:
	@test -n "$(INPUT_GODOT_PREVIEW)" || { \
	  echo "error: INPUT_GODOT_PREVIEW is empty. Set it in local.mk, e.g."; \
	  echo "  INPUT_GODOT_PREVIEW := ~/path/to/scene.tscn"; \
	  exit 1; }
	@test -f "$(INPUT_GODOT_PREVIEW)" || { \
	  echo "error: scene not found: $(INPUT_GODOT_PREVIEW)"; exit 1; }
	@test -n "$(OUTPUT_GODOT_PREVIEW)" && test "$(OUTPUT_GODOT_PREVIEW)" != "/" || { \
	  echo "error: refusing to rm -rf OUTPUT_GODOT_PREVIEW='$(OUTPUT_GODOT_PREVIEW)'"; exit 1; }
	@echo "--> packing $(INPUT_GODOT_PREVIEW) into $(OUTPUT_GODOT_PREVIEW)"
	@$(PYTHON) -c "from src.godot_preview import preview_scene; \
preview_scene('$(INPUT_GODOT_PREVIEW)', '$(OUTPUT_GODOT_PREVIEW)', '$(NAME)')"
	@echo "--> serving on http://localhost:$(GODOT_PORT)/  (Ctrl+C stops)"
	@if $(PYTHON) -c 'import socket,sys; sys.exit(0 if socket.socket().connect_ex(("127.0.0.1", $(GODOT_PORT))) == 0 else 1)'; then \
	  echo "--> port $(GODOT_PORT) already in use — a previous server is probably still up; open the URL above"; \
	else \
	  $(PYTHON) -m http.server $(GODOT_PORT) --directory "$(OUTPUT_GODOT_PREVIEW)"; \
	fi

# Start every preview server from PREVIEWS (local.mk): "path:port path:port".
# Detached with nohup, so make returns; logs go to /dev/null. Ports already
# in use are skipped (a server for that folder is probably already running).
previews:
	@for pair in $(PREVIEWS); do \
	  dir="$${pair%%:*}"; port="$${pair##*:}"; \
	  test -d "$$dir" || { echo "--> skip $$dir (folder does not exist)"; continue; }; \
	  if $(PYTHON) -c 'import socket,sys; sys.exit(0 if socket.socket().connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)' "$$port"; then \
	    echo "--> port $$port already in use — skipping $$dir"; \
	  else \
	    echo "--> serving $$dir on http://localhost:$$port/"; \
	    nohup $(PYTHON) -m http.server $$port --directory "$$dir" >/dev/null 2>&1 & \
	  fi; \
	done; \
	echo "--> all servers started detached. Stop with: make previews-stop"

# Side-by-side compare shell: one page, both preview bundles in iframes,
# shared play/freeze controls. Serves the bundles' PARENT folder (one origin
# for both iframes). COMPARE_GODOT/COMPARE_SPINE come from local.mk.
COMPARE_PORT ?= 8083
compare:
	@test -f "$(COMPARE_GODOT)/index.html" || { \
	  echo "error: COMPARE_GODOT has no index.html: $(COMPARE_GODOT)"; exit 1; }
	@test -f "$(COMPARE_SPINE)/index.html" || { \
	  echo "error: COMPARE_SPINE has no index.html: $(COMPARE_SPINE)"; exit 1; }
	@$(PYTHON) -c "from src.compare_out import emit_compare; \
emit_compare('$(COMPARE_GODOT)', '$(COMPARE_SPINE)')"
	@echo "--> compare page: http://localhost:$(COMPARE_PORT)/compare.html  (Ctrl+C stops)"
	@if $(PYTHON) -c 'import socket,sys; sys.exit(0 if socket.socket().connect_ex(("127.0.0.1", $(COMPARE_PORT))) == 0 else 1)'; then \
	  echo "--> port $(COMPARE_PORT) already in use — a previous server is probably still up; open the URL above"; \
	else \
	  $(PYTHON) -m http.server $(COMPARE_PORT) --directory "$(COMPARE_GODOT)/.."; \
	fi

previews-stop:
	@for pair in $(PREVIEWS); do \
	  port="$${pair##*:}"; \
	  lsof -ti :$$port 2>/dev/null | xargs kill 2>/dev/null && echo "--> stopped :$$port" || echo "--> :$$port was not running"; \
	done

test:
	$(PYTHON) -m pytest tests/ -v
