PY ?= python3
SEEDS ?= 301-310

.PHONY: demo app test bench bench-claude gate clean

demo:          ## reconcile the sample batch, write out/demo/report.html
	$(PY) -m reconloop demo

app:           ## run the web demo at http://127.0.0.1:7860
	$(PY) app.py

test:
	$(PY) -m pytest -q

bench:         ## held-out benchmark: rules only, greedy baseline, oracle ceiling
	$(PY) -m reconloop bench --seeds $(SEEDS) --difficulty standard,hard --tie-breaker offline,greedy,oracle --out bench

bench-claude:  ## same batches with Claude as tie-breaker (needs ANTHROPIC_API_KEY)
	$(PY) -m reconloop bench --seeds $(SEEDS) --difficulty standard,hard --tie-breaker offline,claude,oracle --out bench-claude

gate:          ## CI gate: rules alone must make zero wrong links
	$(PY) -m reconloop bench --seeds $(SEEDS) --difficulty standard,hard --tie-breaker offline --out .gate --max-wrong-links 0

clean:
	rm -rf out .gate .cache bench/data bench-claude/data .pytest_cache
