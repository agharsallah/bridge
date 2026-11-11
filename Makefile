.PHONY: run init test lint fmt clean lab

run:            ## start the bridge daemon (dashboard on http://localhost:8765)
	uv run so101-bridge

init:           ## start in init mode: health checks + calibration at http://localhost:8765/init
	uv run so101-bridge --init

lab:            ## scaffold a new lab on the template: make lab NAME=pour TITLE="Pouring lab" DESC="..." (docs/lab.md)
	@test -n "$(NAME)" || (echo "usage: make lab NAME=<name> [TITLE=...] [DESC=...]"; exit 1)
	uv run python scripts/new_lab.py $(NAME) --title "$(TITLE)" --description "$(DESC)"

test:           ## unit tests (no hardware needed)
	uv run pytest -q

lint:           ## static checks
	uv run ruff check .

fmt:            ## sort imports / fix what ruff can fix
	uv run ruff check --fix .

stop:           ## freeze the arm right now
	touch ESTOP

resume:         ## clear the emergency stop
	rm -f ESTOP

clean:          ## drop runtime artifacts (keeps config/)
	rm -rf var/cmd/* var/done/* var/recordings/* var/*.jpg var/state.json
