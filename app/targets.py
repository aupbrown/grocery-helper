from app.models import Goal, Targets

ACTIVITY_FACTORS = {"sedentary": 13, "light": 15, "active": 17}
GOAL_ADJUSTMENT = {Goal.bulk: 350, Goal.maintain: 0, Goal.cut: -500}
PROTEIN_PER_LB = 1.0


def compute_targets(bodyweight_lb: float, goal: Goal, activity_level: str = "light") -> Targets:
    factor = ACTIVITY_FACTORS.get(activity_level, ACTIVITY_FACTORS["light"])
    maintenance = bodyweight_lb * factor
    calories = maintenance + GOAL_ADJUSTMENT[goal]
    protein = bodyweight_lb * PROTEIN_PER_LB
    return Targets(calories=round(calories), protein=round(protein))
