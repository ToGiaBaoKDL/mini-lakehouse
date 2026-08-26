DOCS_DIR := docs
DOCS_PNPM := pnpm --dir $(DOCS_DIR)

.PHONY: docs-install docs-dev docs-build docs-check docs-deploy cloudflare-docs-ci-secret-sync

docs-install: ## Install the locked documentation dependencies.
	$(DOCS_PNPM) install --frozen-lockfile

docs-dev: ## Start the local Fumadocs development server.
	$(DOCS_PNPM) dev

docs-build: ## Build the static documentation site into docs/out.
	$(DOCS_PNPM) build

docs-check: ## Validate formatting, types, links, and the static documentation build.
	$(DOCS_PNPM) check

docs-deploy: ## Deploy docs/out to Cloudflare Workers Static Assets.
	$(DOCS_PNPM) deploy

cloudflare-docs-ci-secret-sync: ## Store the local Cloudflare docs deployment token in Secrets Manager.
	docs/deploy/sync-ci-secret ".secrets/$(LAKEHOUSE_ENVIRONMENT)/cloudflare/docs-ci.json"
