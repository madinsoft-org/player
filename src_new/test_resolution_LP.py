import pulp

def solve_1d_compaction():
    # 1. Création du problème de Programmation Linéaire (Minimisation)
    prob = pulp.LpProblem("Layout_1D_Compaction_X", pulp.LpMinimize)

    # 2. Définition des variables de décision (coordonnées X des arêtes)
    # Les coordonnées doivent être positives ou nulles (lowBound=0)
    x0 = pulp.LpVariable("x0_A_left", lowBound=0)
    x1 = pulp.LpVariable("x1_A_right", lowBound=0)
    x2 = pulp.LpVariable("x2_B_right", lowBound=0)
    x3 = pulp.LpVariable("x3_C_left", lowBound=0)
    x4 = pulp.LpVariable("x4_C_right", lowBound=0)

    # 3. Fonction Objectif : Minimiser l'encombrement total (largeur de la cellule)
    # On minimise la position du bord le plus à droite (x4)
    prob += x4 - x0, "Minimize_Total_Width"

    # 4. Ancrage : Fixer l'origine
    prob += x0 == 0, "Origin_Anchor"

    # 5. Ajout des contraintes géométriques (DRC & Topologie)

    # --- Contraintes de largeur minimale (Min Width) ---
    prob += x1 - x0 >= 0.14, "MinWidth_Comp_A"  # Largeur A >= 0.14 um
    prob += x2 - x1 >= 0.18, "MinWidth_Comp_B"  # Largeur B >= 0.18 um
    prob += x4 - x3 >= 0.14, "MinWidth_Comp_C"  # Largeur C >= 0.14 um

    # --- Contrainte d'aboutement (Abutment) ---
    # Le bord droit de A et le bord gauche de B partagent la même coordonnée x1
    # Si A et B étaient deux objets distincts x1_right et x2_left :
    # prob += x2_left - x1_right == 0

    # --- Contrainte d'espacement minimal DRC (Min Space) ---
    # Espacement entre le bord droit de B (x2) et le bord gauche de C (x3)
    prob += x3 - x2 >= 0.20, "MinSpace_B_to_C"  # Espace DRC >= 0.20 um

    # 6. Résolution du problème LP
    # PuLP utilise par défaut le solveur CBC inclus
    status = prob.solve(pulp.PULP_CBC_CMD(msg=False))

    # 7. Affichage des résultats
    print(f"Statut de la résolution : {pulp.LpStatus[status]}")
    print(f"Largeur totale optimisée : {pulp.value(prob.objective):.3f} µm\n")

    print("--- Coordonnées optimisées des arêtes ---")
    variables = [x0, x1, x2, x3, x4]
    for var in variables:
        print(f"  {var.name:<15} = {var.varValue:.3f} µm")

if __name__ == "__main__":
    solve_1d_compaction()