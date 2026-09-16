"""Record the resolved runtime environment used by Docker builds."""
from importlib.metadata import distributions
from pathlib import Path

excluded = {"ai-coach-data", "pip", "setuptools", "pytest", "iniconfig", "pluggy", "pygments"}
resolved = sorted(str(d.metadata["Name"]) + "==" + d.version for d in distributions()
                  if d.metadata["Name"].lower() not in excluded)
Path(__file__).resolve().parents[1].joinpath("requirements.lock").write_text("\n".join(resolved) + "\n")
