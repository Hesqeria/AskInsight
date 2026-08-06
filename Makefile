.PHONY: help dev build up down logs init test lint clean

help: ## Show help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN{FS=":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

dev: ## Local development mode (start frontend and backend separately)
	@echo "Starting backend..."
	cd backend && python -m uvicorn main:app --host 0.0.0.0 --port 8000 &
	@echo "Starting frontend..."
	cd frontend && npm run dev

build: ## Build Docker images
	docker compose build

up: ## Start all services with one command
	docker compose up -d

down: ## Stop all services
	docker compose down

logs: ## View logs
	docker compose logs -f --tail=50

init: ## Initialize the knowledge base (requires `up` first)
	docker compose exec backend python app/scripts/build_meta_knowledge.py

test: ## Run tests
	cd backend && pytest tests/ -x -q

lint: ## Code linting
	cd backend && ruff check app/
	cd frontend && npm run build -- --mode check

clean: ## Clean up
	docker compose down -v
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
	rm -rf frontend/node_modules frontend/dist backend/logs
