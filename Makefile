.PHONY: run test lint fmt clean

run:            ## start the bridge daemon (dashboard on http://localhost:8765)
	uv run so101-bridge

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
