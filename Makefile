# Purpose: Developer shortcuts: install, test, lint, tf-validate, fe-dev, seed.
.PHONY: install test lint tf-validate fe-dev seed
install:   ; pip install -r backend/agent/requirements-dev.txt && cd frontend && npm install
test:      ; pytest -q
lint:      ; ruff check backend scripts && cd frontend && npm run lint
tf-validate: ; cd infra/terraform && terraform init -backend=false && terraform validate
fe-dev:    ; cd frontend && npm run dev
seed:      ; python scripts/seed_data.py $(PREFIX)
