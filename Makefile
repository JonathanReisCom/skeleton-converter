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
	@test -n "$(GODOT_INPUT)" || { \
	  echo "error: GODOT_INPUT is empty. Set it in local.mk, e.g."; \
	  echo "  GODOT_INPUT := ~/path/to/player.tscn"; \
	  exit 1; }
	@test -n "$(GODOT_OUT)" && test "$(GODOT_OUT)" != "/" || { \
	  echo "error: refusing to rm -rf GODOT_OUT='$(GODOT_OUT)'"; exit 1; }
	@echo "--> clearing old output: $(GODOT_OUT)"
	@rm -rf "$(GODOT_OUT)"
	@echo "--> converting Godot scene -> Spine bundle"
	@$(PYTHON) -m src.cli convert --from godot --to spine \
	  "$(GODOT_INPUT)" -o "$(GODOT_OUT)" --name "$(NAME)"
	@echo "--> serving the Spine viewer on http://localhost:$(GODOT_PORT)/  (Ctrl+C stops)"
	@if $(PYTHON) -c 'import socket,sys; sys.exit(0 if socket.socket().connect_ex(("127.0.0.1", $(GODOT_PORT))) == 0 else 1)'; then \
	  echo "--> port $(GODOT_PORT) already in use — a previous server is probably still up; open the URL above"; \
	else \
	  $(PYTHON) -m http.server $(GODOT_PORT) --directory "$(GODOT_OUT)"; \
	fi

# Spine JSON -> .tscn + the page image, then serve the Godot web preview. The
# browser renders the real .tscn through the real engine (WASM export).

spine-to-godot:
	@test -n "$(SPINE_INPUT)" || { \
	  echo "error: SPINE_INPUT is empty. Set it in local.mk, e.g."; \
	  echo "  SPINE_INPUT := ~/path/to/hero.json"; \
	  exit 1; }
	@test -n "$(SPINE_OUT)" && test "$(SPINE_OUT)" != "/" || { \
	  echo "error: refusing to rm -rf SPINE_OUT='$(SPINE_OUT)'"; exit 1; }
	@echo "--> clearing old output: $(SPINE_OUT)"
	@rm -rf "$(SPINE_OUT)"
	@echo "--> converting Spine JSON -> Godot scene"
	@$(PYTHON) -m src.cli convert --from spine --to godot \
	  "$(SPINE_INPUT)" -o "$(SPINE_OUT)" --name "$(NAME)"
	@test -d "$(SPINE_OUT)/output" || { \
	  echo "error: web preview was not built (is Godot installed? see GODOT_BIN)"; \
	  exit 1; }
	@echo "--> serving the Godot web preview on http://localhost:$(SPINE_PORT)/  (Ctrl+C stops)"
	@if $(PYTHON) -c 'import socket,sys; sys.exit(0 if socket.socket().connect_ex(("127.0.0.1", $(SPINE_PORT))) == 0 else 1)'; then \
	  echo "--> port $(SPINE_PORT) already in use — a previous server is probably still up; open the URL above"; \
	else \
	  $(PYTHON) -m http.server $(SPINE_PORT) --directory "$(SPINE_OUT)"; \
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
	@test -n "$(GODOT_INPUT)" || { \
	  echo "error: GODOT_INPUT is empty. Set it in local.mk, e.g."; \
	  echo "  GODOT_INPUT := ~/path/to/scene.tscn"; \
	  exit 1; }
	@test -f "$(GODOT_INPUT)" || { \
	  echo "error: scene not found: $(GODOT_INPUT)"; exit 1; }
	@test -n "$(GODOT_OUT)" && test "$(GODOT_OUT)" != "/" || { \
	  echo "error: refusing to rm -rf GODOT_OUT='$(GODOT_OUT)'"; exit 1; }
	@echo "--> packing $(GODOT_INPUT) into $(GODOT_OUT)"
	@$(PYTHON) -c "from src.godot_preview import preview_scene; \
preview_scene('$(GODOT_INPUT)', '$(GODOT_OUT)', '$(NAME)')"
	@echo "--> serving on http://localhost:$(GODOT_PORT)/  (Ctrl+C stops)"
	@if $(PYTHON) -c 'import socket,sys; sys.exit(0 if socket.socket().connect_ex(("127.0.0.1", $(GODOT_PORT))) == 0 else 1)'; then \
	  echo "--> port $(GODOT_PORT) already in use — a previous server is probably still up; open the URL above"; \
	else \
	  $(PYTHON) -m http.server $(GODOT_PORT) --directory "$(GODOT_OUT)"; \
	fi

test:
	$(PYTHON) -m pytest tests/ -v
