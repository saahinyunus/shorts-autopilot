"""Make test failures readable in GitHub Actions without downloading logs: each failed test is also
printed as a red workflow annotation (the public API exposes annotations)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def pytest_runtest_logreport(report):
    if report.failed and os.environ.get("GITHUB_ACTIONS") == "true":
        from engine.ci import annotate_error

        annotate_error(f"{report.nodeid} [{report.when}]: {str(report.longreprtext)[-700:]}", title="pytest failure")
