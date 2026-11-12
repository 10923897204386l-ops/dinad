PKGS = lilv-0 lv2 jack sndfile
CFLAGS ?= -O2 -Wall -Wextra
CPPFLAGS += $(shell pkg-config --cflags $(PKGS))
LDLIBS += $(shell pkg-config --libs $(PKGS))

dinad-epiano: epiano.c
	$(CC) -std=c11 $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ $< $(LDLIBS)
