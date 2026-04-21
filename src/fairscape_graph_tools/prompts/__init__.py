"""System + synthesis prompts for the AI-interpretation pipeline."""

from fairscape_graph_tools.prompts.biostat import BIOSTAT_SYNTHESIS_PROMPT
from fairscape_graph_tools.prompts.clinician import CLINICIAN_SYNTHESIS_PROMPT
from fairscape_graph_tools.prompts.datasci import (
    DATASCI_SYNTHESIS_PROMPT,
    DATASCI_SYSTEM_PROMPT,
)

# Audience configuration for synthesis (mirrors previous AUDIENCE_CONFIGS
# in fairscape_mds.crud.interpretation).
AUDIENCE_CONFIGS = [
    {
        "key": "biostat",
        "label": "Biostatistician",
        "prompt": BIOSTAT_SYNTHESIS_PROMPT,
    },
    {
        "key": "clinician",
        "label": "Clinician",
        "prompt": CLINICIAN_SYNTHESIS_PROMPT,
    },
]

__all__ = [
    "AUDIENCE_CONFIGS",
    "BIOSTAT_SYNTHESIS_PROMPT",
    "CLINICIAN_SYNTHESIS_PROMPT",
    "DATASCI_SYNTHESIS_PROMPT",
    "DATASCI_SYSTEM_PROMPT",
]
