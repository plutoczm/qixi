"""Provider package layout invariants; no provider calls or credential reads."""
from pathlib import Path

from stay_agent.providers.data import flyai, rollinggo, travel_clients, tuniu
from stay_agent.providers.llm import adapters


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_moved_provider_modules_keep_project_root_configuration_anchor():
    assert rollinggo.ROOT == PROJECT_ROOT
    assert travel_clients.ROOT == PROJECT_ROOT
    assert adapters.ROOT == PROJECT_ROOT


def test_provider_categories_are_distinct_packages():
    assert rollinggo.__name__.startswith("stay_agent.providers.data.")
    assert travel_clients.__name__.startswith("stay_agent.providers.data.")
    assert tuniu.__name__.startswith("stay_agent.providers.data.")
    assert flyai.__name__.startswith("stay_agent.providers.data.")
    assert adapters.__name__.startswith("stay_agent.providers.llm.")
