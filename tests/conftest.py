import sys
from pathlib import Path

# The probe under test lives in scripts/, which is not a package; put the repo
# root on the path so `scripts.measure_badge_contrast` and `app.*` both import.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
