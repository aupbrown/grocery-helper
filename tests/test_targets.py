from app.models import Goal
from app.targets import compute_targets


def test_maintain_light_activity():
    # 180 lb * 15 (light) + 0 = 2700; protein 1g/lb = 180
    t = compute_targets(bodyweight_lb=180, goal=Goal.maintain, activity_level="light")
    assert t.calories == 2700
    assert t.protein == 180


def test_bulk_adds_surplus():
    t = compute_targets(bodyweight_lb=180, goal=Goal.bulk, activity_level="light")
    assert t.calories == 3050  # 2700 + 350


def test_cut_applies_deficit():
    t = compute_targets(bodyweight_lb=180, goal=Goal.cut, activity_level="light")
    assert t.calories == 2200  # 2700 - 500


def test_activity_level_changes_maintenance():
    sed = compute_targets(bodyweight_lb=200, goal=Goal.maintain, activity_level="sedentary")
    act = compute_targets(bodyweight_lb=200, goal=Goal.maintain, activity_level="active")
    assert sed.calories == 2600   # 200 * 13
    assert act.calories == 3400   # 200 * 17


def test_maintain_carbs_and_fat():
    t = compute_targets(bodyweight_lb=180, goal=Goal.maintain, activity_level="light")
    assert t.fat == 75      # 0.25 * 2700 / 9
    assert t.carbs == 326   # (2700 - 4*180 - 9*75) / 4 = 326.25 -> 326


def test_fat_percent_varies_by_goal():
    cut = compute_targets(bodyweight_lb=180, goal=Goal.cut, activity_level="light")
    bulk = compute_targets(bodyweight_lb=180, goal=Goal.bulk, activity_level="light")
    assert cut.fat == 73    # 0.30 * 2200 / 9
    assert bulk.fat == 68   # 0.20 * 3050 / 9


def test_macro_targets_are_calorie_consistent():
    for goal in (Goal.cut, Goal.maintain, Goal.bulk):
        t = compute_targets(bodyweight_lb=180, goal=goal, activity_level="light")
        kcal = 4 * t.protein + 4 * t.carbs + 9 * t.fat
        assert abs(kcal - t.calories) <= 10   # rounding only
