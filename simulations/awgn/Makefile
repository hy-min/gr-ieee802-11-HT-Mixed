REPETITION ?= 0
ENCODING ?= 0
SNR ?= 0.0
SHELL=/bin/bash

MESSAGES=100

# 使用Python 3语法生成文件列表
PCAPFILES=$(shell python3 -c "import itertools; import numpy as np; print(' '.join(['results/sim_%d_%d_%.1f_.pcap' % x for x in itertools.product(range(10), range(8), np.arange(0, 30, 0.5))]))")

all: pdr.pdf

pdr.pdf: plot.py results/all.csv
	python3 ./plot.py

sim.py: sim.grc
	grcc -d . sim.grc

results/all.csv: $(PCAPFILES)
	./parse.sh

# 修复规则，正确提取参数
results/sim_%.pcap: sim.py
	@mkdir -p results
	$(eval PARAMS := $(subst _, ,$*))
	$(eval REPETITION := $(word 1,$(PARAMS)))
	$(eval ENCODING := $(word 2,$(PARAMS)))
	$(eval SNR := $(word 3,$(PARAMS)))
	export GR_CONF_DEFAULT_MAX_MESSAGES=123; \
	python3 ./sim.py --messages=$(MESSAGES) --encoding=$(ENCODING) --snr=$(SNR) --repetition=$(REPETITION)

.PHONY: all clean

clean:
	rm -f sim.py
	rm -rf results/
