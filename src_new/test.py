import gdstk


def extract_polygons_from_gds(gds_filename: str, target_layer: int, target_datatype: int = 0):
    """
    Charge un GDSII et extrait les sommets (vertices) et boîtes englobantes
    de tous les polygones d'une couche spécifique.
    """
    # 1. Charger la bibliothèque GDSII
    library = gdstk.read_gds(gds_filename)
    
    # Récupérer la cellule supérieure (top cell)
    top_cell = library.top_level()[0]
    print(f"Top cell chargée : {top_cell.name}")

    # 2. Aplatir la hiérarchie pour obtenir les coordonnées absolues des polygones
    # (prend en compte les décalages/rotations des instances de sous-cellules)
    flat_cell = top_cell.flatten()

    extracted_polygons = []

    # 3. Parcourir tous les polygones de la cellule
    for poly in flat_cell.polygons:
        # Filtrer selon la couche et le datatype cibles
        if poly.layer == target_layer and poly.datatype == target_datatype:
            # poly.points contient la liste des sommets [[x1, y1], [x2, y2], ...]
            vertices = poly.points
            
            # Calcul de la bounding box ((xmin, ymin), (xmax, ymax))
            bbox = poly.bounding_box()

            extracted_polygons.append({
                "vertices": vertices,
                "bbox": bbox
            })

    return extracted_polygons


# --- Exemple d'utilisation ---
if __name__ == "__main__":
    # Remplacer par le chemin de ton fichier GDSII
    gds_file = "sram_cell.gds"
    
    # Exemple : Couche Poly (Sky130 : layer 68, datatype 20 par exemple)
    LAYER_TO_EXTRACT = 68
    DATATYPE_TO_EXTRACT = 20

    try:
        polys = extract_polygons_from_gds(gds_file, LAYER_TO_EXTRACT, DATATYPE_TO_EXTRACT)
        print(f"\nNombre de polygones trouvés sur la couche {LAYER_TO_EXTRACT}/{DATATYPE_TO_EXTRACT} : {len(polys)}\n")

        # Afficher les informations des 3 premiers polygones
        for idx, p in enumerate(polys[:3]):
            xmin, ymin = p["bbox"][0]
            xmax, ymax = p["bbox"][1]
            width = xmax - xmin
            height = ymax - ymin
            
            print(f"--- Poly gdstk #{idx + 1} ---")
            print(f"  Bounding Box : xmin={xmin:.3f}, ymin={ymin:.3f}, xmax={xmax:.3f}, ymax={ymax:.3f}")
            print(f"  Dimensions   : Largeur={width:.3f} µm, Hauteur={height:.3f} µm")
            print(f"  Nb sommets   : {len(p['vertices'])}")
            print(f"  Coordonnées  :\n{p['vertices']}\n")

    except FileNotFoundError:
        print(f"Fichier {gds_file} introuvable.")