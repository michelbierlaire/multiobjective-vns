"""Tests for replaceable policy callback protocols.

Created: 2026-10-29T13:19:17+00:00
Author: Michel Bierlaire, EPFL
"""

from multiobjective_vns.policies import ArchivePolicy
from multiobjective_vns.records.policies import ArchiveDecision, ArchiveRequest


class DefaultArchive:
    component_id = 'archive'
    configuration_id = 'archive-v1'

    def decide(self, request: ArchiveRequest) -> ArchiveDecision:
        return ArchiveDecision(admit=True, remove_ids=())


def test_policy_contracts_are_structural_and_identity_bearing() -> None:
    policy: ArchivePolicy = DefaultArchive()

    assert policy.component_id == 'archive'
    assert policy.configuration_id == 'archive-v1'
