from evaluation.embedding_latency import nearest_rank_p95


def test_nearest_rank_p95_bei_180_werten():
    assert nearest_rank_p95(list(range(1, 181))) == 171
