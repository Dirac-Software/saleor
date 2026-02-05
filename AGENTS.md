# Saleor

# Testing

## Running tests

- Run tests using `uv run poe test`
- The `--reuse-db` flag is included by default to speed up tests
- Select tests to run by passing test file path as an argument: `uv run poe test path/to/test_file.py`
- Additional pytest flags can be passed: `uv run poe test path/to/test.py -v -k test_name`
- The test command automatically uses `SUPERUSER_DATABASE_URL` if set in `.env`, otherwise falls back to `DATABASE_URL`

## Writing tests

- Use given/when/then structure for clarity
- Use `pytest` fixtures for setup and teardown
- Declare test suites flat in file. Do not wrapp in classes
- Prefer using fixtures over mocking. Fixtures are usually within directory "tests/fixtures" and are functions decorated with`@pytest.fixture`

# Code Style

- Do not add comments or docstrings unless explicitly requested
- Keep code self-explanatory through clear naming and simple logic
