"""Skill applicability selection may omit every coarse candidate."""

import asyncio

from powercontext.builtin.artifacts.skill.reranking import (
    LLMSkillReranker,
    SkillRerankCandidate,
    SkillRerankOutput,
)
from powercontext.builtin.inference import GenerationResult, InferenceUsage


def test_skill_applicability_preserves_empty_and_only_exposes_metadata():
    async def scenario():
        observed = []

        class Generator:
            async def generate(self, value, /):
                observed.append(value.model_dump())
                return GenerationResult(output=SkillRerankOutput(selected_ranks=()), usage=InferenceUsage(requests=1))

        result = await LLMSkillReranker(Generator()).rerank(
            "Find the shortest route.",
            (SkillRerankCandidate(rank=1, name="station-count", description="Count stations by country."),),
        )
        assert result.selected_ranks == ()
        assert result.usage.requests == 1
        assert set(observed[0]["candidates"][0]) == {"rank", "name", "description"}

    asyncio.run(scenario())


def test_skill_applicability_keeps_original_identity_and_rejects_invented_ranks():
    async def scenario():
        class Generator:
            async def generate(self, value, /):
                return GenerationResult(output=SkillRerankOutput(selected_ranks=(2, 2, 99, 1)))

        result = await LLMSkillReranker(Generator()).rerank(
            "Count stations.",
            tuple(
                SkillRerankCandidate(rank=rank, name=f"method-{rank}", description="A counting method.")
                for rank in (1, 2)
            ),
        )
        assert result.selected_ranks == (2, 1)
        assert result.discarded_rank_count == 2

    asyncio.run(scenario())
