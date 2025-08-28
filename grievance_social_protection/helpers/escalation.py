# ————————————————————————————————————————————————————————————————
# Routes et SLA par type de plainte
# ————————————————————————————————————————————————————————————————
ROUTE_SENSITIVE:  list[str] = ["ETM", "DEVOPS"]
SLA_SENSITIVE:    list[int] = [1, 1]  # jours / niveau

ROUTE_NON_SENSITIVE: list[str] = ["CGR", "AC", "RAC", "ETM", "DEVOPS"]
SLA_NON_SENSITIVE:   list[int] = [2, 2, 3, 3, 5]