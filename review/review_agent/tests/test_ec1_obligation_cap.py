"""EC-1 obligation extract cap tests."""

from __future__ import annotations

from review_agent.graph.obligation_nodes import (
    _cap_obligations_fair,
    _cap_obligations_round_robin,
)
from review_agent.schemas.obligation import ContractObligation


def _obligation(section_id: str, index: int, **kwargs) -> ContractObligation:
  defaults = {
      "obligation_id": f"{section_id}-o{index}",
      "section_id": section_id,
      "text": f"obligation {index}",
  }
  defaults.update(kwargs)
  return ContractObligation(**defaults)


def test_round_robin_covers_all_sections_before_repeat():
    obligations = [
        _obligation(sid, i)
        for sid in (f"s{i}" for i in range(27))
        for i in range(5)
    ]
    capped, dropped_count, dropped_ids = _cap_obligations_round_robin(
        obligations,
        max_total=27,
        max_per_section=6,
        section_order=[f"s{i}" for i in range(27)],
    )
    assert len(capped) == 27
    assert dropped_count == 27 * 5 - 27
    assert len({ob.section_id for ob in capped}) == 27
    assert len(dropped_ids) == 27


def test_boilerplate_deprioritized_under_cap():
    obligations = [
        _obligation("1", 0, is_boilerplate=True, text="boilerplate notice"),
        _obligation("1", 1, text="substantive security control"),
    ]
    capped, dropped_count, _ = _cap_obligations_round_robin(
        obligations,
        max_total=1,
        max_per_section=6,
        section_order=["1"],
    )
    assert len(capped) == 1
    assert dropped_count == 1
    assert capped[0].is_boilerplate is False


def test_atlassian_like_drop_count():
    section_ids = [f"s{i}" for i in range(19)]
    obligations = []
    for sid in section_ids:
        count = 10 if sid == "s0" else 7
        obligations.extend(_obligation(sid, i) for i in range(count))
    assert len(obligations) == 136

    capped, dropped_count, dropped_ids = _cap_obligations_round_robin(
        obligations,
        max_total=120,
        max_per_section=6,
        section_order=section_ids,
    )
    assert len(capped) == 114
    assert dropped_count == 22
    assert len({ob.section_id for ob in capped}) == 19
    assert len(dropped_ids) == 19


def test_sequential_mode_legacy_unchanged():
    obligations = [
        _obligation(sid, i)
        for sid in ("1", "2", "3")
        for i in range(10)
    ]
    capped, dropped_count, dropped_ids = _cap_obligations_fair(
        obligations,
        max_total=12,
        max_per_section=4,
        section_order=["1", "2", "3"],
    )
    assert len(capped) == 12
    assert dropped_count == 18
    assert len({ob.section_id for ob in capped}) == 3
    assert dropped_ids
