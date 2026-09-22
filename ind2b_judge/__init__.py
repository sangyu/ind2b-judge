"""Independent trust grader for indication2binder runs.

Deliberately a separate package from the pipeline it grades. A judge that can
import the system under test can be made to pass by that system -- reuse one
helper for "consistency" and the grader is scoring its own inputs. The only
input here is persisted run artifacts on disk, so the coupling is the stage
file contract (stage0_disease.json, stage1_evidence.json,
stage2_ranked_targets.csv, stage5_specs/manifest.json) and nothing else.
"""

__version__ = "0.1.0"

from .harness import grade, render                    # noqa: F401
from .bridge import load_run, load_multi              # noqa: F401
from .validators import validate_all                  # noqa: F401
