"""Process-scoped inference policy; never writes operator configuration."""
import os

MODES = ('collection-only', 'analysis-enabled', 'manual-analysis')
ANALYSIS_KINDS = ('analyse', 'analysis')


def resolve_mode(value=None):
    # Library callers retain compatibility; launchers explicitly default to
    # collection-only and pass the same policy to both child processes.
    mode = value if value is not None else os.environ.get('RADAR_EXECUTION_MODE', 'analysis-enabled')
    if mode not in MODES:
        raise ValueError('invalid_execution_mode')
    return mode
