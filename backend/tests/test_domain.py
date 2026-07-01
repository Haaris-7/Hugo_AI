import pytest
from hugo.domain import payout_amount, weighted_fit_score


def test_weighted_fit_score_is_bounded_by_inputs():
    score = weighted_fit_score(
        niche_match=90,
        audience_quality=80,
        engagement_rate=5,
        brand_fit=70,
        reputation=60,
    )
    assert score == 73.5


@pytest.mark.parametrize(
    ("model", "rate", "views", "engagements", "conversions", "expected"),
    [
        ("flat", 10_000, 0, 0, 0, 10_000),
        ("cpm", 2_000, 10_000, 0, 0, 20_000),
        ("engagement", 1_000, 0, 500, 0, 500),
        ("hybrid", 5_000, 2_000, 0, 0, 10_000),
        ("affiliate", 2_500, 0, 0, 4, 10_000),
    ],
)
def test_payout_calculators(model, rate, views, engagements, conversions, expected):
    assert (
        payout_amount(
            model,
            rate,
            views=views,
            engagements=engagements,
            conversions=conversions,
        )
        == expected
    )
