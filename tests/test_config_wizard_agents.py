"""Wizard-facing Agent integration catalog contracts."""

from powercontext.cli.config_wizard_agents import AGENT_SPECS
from powercontext.cli.hosts import FIRST_CLASS_HOSTS


def test_wizard_catalog_covers_first_class_hosts_and_workbuddy() -> None:
    assert tuple(spec.identifier for spec in AGENT_SPECS) == (
        *(host.name for host in FIRST_CLASS_HOSTS),
        "workbuddy",
    )


def test_openclaw_keeps_its_plugin_configuration_contract() -> None:
    spec = next(spec for spec in AGENT_SPECS if spec.identifier == "openclaw")
    assert spec.environment_prefix is None
    assert spec.capture_setting == "autoCapture"
    assert spec.scope_setting == "scopeId"
