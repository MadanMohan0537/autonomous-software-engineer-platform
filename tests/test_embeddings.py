import pytest

from ase.embeddings import HashingEmbedder, cosine


@pytest.mark.asyncio
async def test_hashing_embeddings_are_deterministic() -> None:
    vectors = await HashingEmbedder(32).embed(["refund failure", "refund failure"])
    assert vectors[0] == vectors[1]
    assert cosine(vectors[0], vectors[1]) == pytest.approx(1)


def test_cosine_rejects_dimension_mismatch() -> None:
    with pytest.raises(ValueError):
        cosine([1.0], [1.0, 2.0])
