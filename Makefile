# URST MicroPython build helpers
#
# Requires mpy-cross to be installed and on PATH.
# Install: pip install mpy-cross  (or download from micropython.org/download)
#
# Usage:
#   make mpy        Build pre-compiled .mpy files into dist/urst/
#   make clean      Remove dist/ directory
#   make deploy     Copy dist/urst/ to a connected device via mpremote

SOURCES := $(wildcard urst/*.py)
DIST    := dist/urst
MPY_CROSS ?= mpy-cross

MPY_FILES := $(patsubst urst/%.py,$(DIST)/%.mpy,$(SOURCES))

.PHONY: mpy clean deploy

mpy: $(MPY_FILES)
	@echo "Built $(words $(MPY_FILES)) .mpy files in $(DIST)/"

$(DIST)/%.mpy: urst/%.py | $(DIST)
	$(MPY_CROSS) -o $@ $<

$(DIST):
	mkdir -p $(DIST)

clean:
	rm -rf dist/

deploy: mpy
	mpremote cp -r dist/urst :
	@echo "Deployed urst/ to device"
