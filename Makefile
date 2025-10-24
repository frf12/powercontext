.PHONY: help install install-dev test test-unit test-integration test-e2e lint format clean build upload docs

help: ## Show help information
	@echo "powermem Project Build Tools"
	@echo ""
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install project dependencies
	pip install -e .

install-dev: ## Install development dependencies
	pip install -e ".[dev]"

test: ## Run all tests
	pytest

format-check: ## Check code formatting
	black --check src tests
	isort --check-only src tests

clean: ## Clean build files
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf .pytest_cache/
	rm -rf .coverage
	rm -rf htmlcov/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

build: ## Build package
	python -m build --outdir build

benchmark: ## Run performance tests
	python scripts/benchmark.py

setup-env: ## Setup development environment
	python scripts/setup.py
