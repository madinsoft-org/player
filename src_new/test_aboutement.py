import gdstk
from shapely.geometry import Polygon
from shapely import touches, intersection, bounds

def to_shapely(gdstk_poly: gdstk.Polygon) -> Polygon:
    """Convertit un polygone gdstk en Polygon Shapely."""
    return Polygon(gdstk_poly.points)

def check_abutment_x(poly_a: Polygon, poly_b: Polygon, tol: float = 1e-4):
    """
    Détermine si poly_a et poly_b s'aboutent horizontalement (selon l'axe X).
    Retourne un tuple (abutting, constraint_type, distance_ref)
    """
    # 1. Vérifier si les polygones se touchent au sens topologique
    if not poly_a.touches(poly_b):
        return False, None

    # 2. Analyser l'intersection de leurs frontières (Boundary Intersection)
    inter = poly_a.boundary.intersection(poly_b.boundary)
    
    # Si l'intersection est une ligne (ou un segment), ils partagent un bord
    if inter.geom_type in ['LineString', 'MultiLineString'] and inter.length > tol:
        minx_a, _miny_a, maxx_a, _maxy_a = poly_a.bounds
        minx_b, _miny_b, maxx_b, _maxy_b = poly_b.bounds

        # Cas A : poly_a est à gauche de poly_b
        if abs(maxx_a - minx_b) < tol:
            return True, "A_LEFT_OF_B", (maxx_a, minx_b)
            
        # Cas B : poly_b est à gauche de poly_a
        elif abs(maxx_b - minx_a) < tol:
            return True, "B_LEFT_OF_A", (maxx_b, minx_a)

    return False, None

def are_rectangles_abutting_x(bbox_a, bbox_b, tol=1e-4):
    """
    bbox_a = ((xmin_a, ymin_a), (xmax_a, ymax_a))
    bbox_b = ((xmin_b, ymin_b), (xmax_b, ymax_b))
    """
    (xmin_a, ymin_a), (xmax_a, ymax_a) = bbox_a
    (xmin_b, ymin_b), (xmax_b, ymax_b) = bbox_b

    # 1. Vérifier le chevauchement vertical (projection Y)
    overlap_y = min(ymax_a, ymax_b) - max(ymin_a, ymin_b)
    
    if overlap_y > tol:  # Ils se font face verticalement
        # 2. Vérifier la contiguïté exacte en X
        if abs(xmax_a - xmin_b) < tol:
            return True, "A_LEFT_OF_B"  # Bord droit de A = Bord gauche de B
        elif abs(xmax_b - xmin_a) < tol:
            return True, "B_LEFT_OF_A"  # Bord droit de B = Bord gauche de A

    return False, None

# --- Exemple d'application ---
if __name__ == "__main__":
    # Polygone A : de x=0 à x=2, y=0 à y=1
    p1 = gdstk.Polygon([(0, 0), (2, 0), (2, 1), (0, 1)])
    # Polygone B : de x=2 à x=5, y=0 à y=1 (abouté à droite de A)
    p2 = gdstk.Polygon([(2, 0), (5, 0), (5, 1), (2, 1)])

    poly_a = to_shapely(p1)
    poly_b = to_shapely(p2)

    is_abutting, direction, coords = check_abutment_x(poly_a, poly_b)

    if is_abutting:
        print(f"Aboutement détecté ! Direction : {direction}")
        print("Contrainte pour le solveur LP : x_edge(B_left) - x_edge(A_right) = 0")
        print(f"Coordonnées de l'intersection : {coords}")