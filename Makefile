.PHONY: test test-quick test-post-deploy install-dev

install-dev:
	cd backend && pip install -e ".[dev]"

test:
	cd backend && ./scripts/run_tests.sh all

test-quick:
	cd backend && ./scripts/run_tests.sh quick

test-post-deploy:
	cd backend && ./scripts/run_tests.sh post_deploy
