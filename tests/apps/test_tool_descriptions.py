from __future__ import annotations

import sys
from pathlib import Path

# Add project root to path so 'tools' package is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import tools.mcp_mt5_wrapper as wrapper


class TestToolDescriptions:
    """Validate that every tool in TOOL_SPECS follows the technical contract format."""

    PRESCRIPTIVE_PATTERNS = [
        "always validate",
        "use before",
        "use after",
        "call at session start",
        "best for",
        "recommended risk",
    ]

    def test_all_tools_present(self) -> None:
        """TOOL_SPECS should have at least 45 tools (currently 45, will be 49 after upgrade)."""
        assert len(wrapper.TOOL_SPECS) >= 45

    def test_all_tools_have_description(self) -> None:
        """Every tool must have a non-empty description field."""
        for name, spec in wrapper.TOOL_SPECS.items():
            desc = spec.get("description", "")
            assert desc, f"Tool '{name}' is missing a description"

    def test_descriptions_have_what_section(self) -> None:
        """Every description must contain a 'What:' section."""
        for name, spec in wrapper.TOOL_SPECS.items():
            desc = spec.get("description", "")
            assert "What:" in desc, (
                f"Tool '{name}' description missing 'What:' section: {desc!r}"
            )

    def test_descriptions_have_input_section(self) -> None:
        """Every description must contain an 'Input:' section."""
        for name, spec in wrapper.TOOL_SPECS.items():
            desc = spec.get("description", "")
            assert "Input:" in desc, (
                f"Tool '{name}' description missing 'Input:' section: {desc!r}"
            )

    def test_descriptions_have_output_section(self) -> None:
        """Every description must contain an 'Output:' section."""
        for name, spec in wrapper.TOOL_SPECS.items():
            desc = spec.get("description", "")
            assert "Output:" in desc, (
                f"Tool '{name}' description missing 'Output:' section: {desc!r}"
            )

    def test_descriptions_have_assumptions_section(self) -> None:
        """Every description must contain an 'Assumptions:' section."""
        for name, spec in wrapper.TOOL_SPECS.items():
            desc = spec.get("description", "")
            assert "Assumptions:" in desc, (
                f"Tool '{name}' description missing 'Assumptions:' section: {desc!r}"
            )

    def test_descriptions_have_composition_section(self) -> None:
        """Every description must contain a 'Composition:' section."""
        for name, spec in wrapper.TOOL_SPECS.items():
            desc = spec.get("description", "")
            assert "Composition:" in desc, (
                f"Tool '{name}' description missing 'Composition:' section: {desc!r}"
            )

    def test_no_prescriptive_language_in_descriptions(self) -> None:
        """No description should contain prescriptive phrases before the Composition: section.

        Prescriptive phrases like 'ALWAYS validate', 'Use BEFORE', etc. are forbidden
        in the What/Input/Output/Assumptions sections. They ARE allowed after 'Composition:'.
        """
        for name, spec in wrapper.TOOL_SPECS.items():
            desc = spec.get("description", "")
            # Only check text before Composition:
            pre_composition = (
                desc.split("Composition:")[0] if "Composition:" in desc else desc
            )

            for pattern in self.PRESCRIPTIVE_PATTERNS:
                assert pattern not in pre_composition.lower(), (
                    f"Tool '{name}' contains prescriptive language '{pattern}' "
                    f"before Composition section: {desc!r}"
                )
