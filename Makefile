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
NAME   ?= animation

GODOT_INPUT ?=
SPINE_INPUT ?=

GODOT_OUT ?= $(HOME)/Desktop/convert-godot-to-spine
SPINE_OUT ?= $(HOME)/Desktop/convert-spine-to-godot

.PHONY: godot-to-spine spine-to-godot test

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
	@echo "--> serving $(GODOT_OUT) on http://localhost:$(PORT)/  (Ctrl+C stops)"
	@$(PYTHON) -m http.server $(PORT) --directory "$(GODOT_OUT)"

# Spine JSON -> .tscn + the page image. No server: nothing here renders in a
# browser, so the target says where to load the scene instead of pretending.
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

test:
	$(PYTHON) -m pytest tests/ -v
