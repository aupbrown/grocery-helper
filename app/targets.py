from app.models import Goal, Targets

ACTIVITY_FACTORS = {"sedentary": 13, "light": 15, "active": 17}
GOAL_ADJUSTMENT = {Goal.bulk: 350, Goal.maintain: 0, Goal.cut: -500}
PROTEIN_PER_LB = 1.0
# Fraction of calories from fat, by goal; carbs take the remainder. Cutting leans higher-fat
# (satiety on a deficit), bulking lower-fat (room for carbs to fuel training).
FAT_PCT = {Goal.cut: 0.30, Goal.maintain: 0.25, Goal.bulk: 0.20}


def compute_targets(bodyweight_lb: float, goal: Goal, activity_level: str = "light") -> Targets:
    factor = ACTIVITY_FACTORS.get(activity_level, ACTIVITY_FACTORS["light"])
    calories = round(bodyweight_lb * factor + GOAL_ADJUSTMENT[goal])
    protein = round(bodyweight_lb * PROTEIN_PER_LB)
    fat = FAT_PCT[goal] * calories / 9
    carbs = max(0.0, (calories - 4 * protein - 9 * fat) / 4)   # remaining calories
    return Targets(calories=calories, protein=protein, carbs=round(carbs), fat=round(fat))
