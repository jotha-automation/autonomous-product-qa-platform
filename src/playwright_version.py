"""Single source of truth for Playwright npm, Python, and Docker image alignment."""

PLAYWRIGHT_VERSION = "1.61.1"
PLAYWRIGHT_DOCKER_IMAGE = f"mcr.microsoft.com/playwright:v{PLAYWRIGHT_VERSION}-jammy"
# Exact npm pin — no caret — so `npm install` cannot drift ahead of preinstalled browsers.
PLAYWRIGHT_NPM_SPEC = PLAYWRIGHT_VERSION

BROWSER_MISMATCH_MARKERS = (
    "Executable doesn't exist",
    "browserType.launch",
    "Failed to launch",
    "browser has been closed",
    "Please run the following command to download new browsers",
    "Executable path is not set",
)
