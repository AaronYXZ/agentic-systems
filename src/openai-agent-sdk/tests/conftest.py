"""Register the opt-in model evaluation marker for either project root."""


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: opt-in real model calls (may incur charges)"
    )
