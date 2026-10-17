#!/usr/bin/env python3
"""Script pour filtrer un fichier GDSII et ne conserver que les layers demandés.

Prend en entrée :
- Un fichier GDSII (path)
- Une liste de noms (ou numéros) de layers à conserver
- En sortie : un fichier GDSII contenant uniquement les layers spécifiés

Supporte les correspondances de noms de layers via :
- Fichiers de propriétés KLayout (.lyp)
- Fichiers JSON de technologie / layermap
- Numéros directs de layers (ex: 49, 46, ou 49/0)
- Dictionnaires Python en import de module

Utilisation en ligne de commande :
    python scripts/filter_gds_layers.py input.gds -l metal1 poly -o output.gds --map data/scmos_180nm_mosis.lyp
    python scripts/filter_gds_layers.py input.gds -l 49 46 -o output.gds
    python scripts/filter_gds_layers.py input.gds --list-layers --map data/scmos_180nm_mosis.lyp

Utilisation en module Python :
    from scripts.filter_gds_layers import filter_gds_layers

    filter_gds_layers("input.gds", "output.gds", ["metal1", "poly"], layer_map="tech.lyp")
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable

import gdstk

_LAYER_SOURCE_RE = re.compile(r"^\s*(?:(.*?)\s+)?(-?\d+)\s*/\s*(\d+)(?:@\d+)?\s*$")
_NUMERIC_LAYER_RE = re.compile(r"^(\d+)(?:[/:](\d+))?$")


def parse_lyp_file(path: Path) -> dict[str, set[tuple[int, int]]]:
    """Extrait la correspondance nom -> ensemble de (layer, datatype) depuis un fichier KLayout .lyp."""
    if not path.is_file():
        raise FileNotFoundError(f"Fichier .lyp introuvable: {path}")

    tree = ET.parse(path)
    root = tree.getroot()
    layer_map: dict[str, set[tuple[int, int]]] = {}

    for prop in root.findall(".//properties"):
        source = prop.findtext("source", default="").strip()
        name = prop.findtext("name", default="").strip()

        match = _LAYER_SOURCE_RE.fullmatch(source)
        if match is None:
            continue

        layer = int(match.group(2))
        datatype = int(match.group(3))
        pair = (layer, datatype)

        # Extraire les noms possibles
        possible_names: list[str] = []
        if name:
            possible_names.append(name)
            # Cas KLayout avec suffixe "name - L/D"
            if " - " in name:
                base_part = name.split(" - ")[0].strip()
                if base_part:
                    possible_names.append(base_part)
            # Cas avec point (ex: metal1.drawing -> metal1)
            if "." in name:
                base_part = name.split(".")[0].strip()
                if base_part:
                    possible_names.append(base_part)

        if match.group(1):
            src_name = match.group(1).strip()
            possible_names.append(src_name)
            if "." in src_name:
                possible_names.append(src_name.split(".")[0].strip())

        for n in possible_names:
            key = n.lower()
            layer_map.setdefault(key, set()).add(pair)

    return layer_map


def parse_json_layer_map(path: Path) -> dict[str, set[tuple[int, int]]]:
    """Extrait la correspondance nom -> ensemble de (layer, datatype) depuis un fichier JSON."""
    if not path.is_file():
        raise FileNotFoundError(f"Fichier JSON introuvable: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    layer_map: dict[str, set[tuple[int, int]]] = {}

    entries = data.get("layers", data) if isinstance(data, dict) else data

    if isinstance(entries, dict):
        for name, val in entries.items():
            key = str(name).lower()
            if isinstance(val, int):
                layer_map.setdefault(key, set()).add((val, -1))
            elif isinstance(val, (list, tuple)) and len(val) >= 2:
                layer_map.setdefault(key, set()).add((int(val[0]), int(val[1])))
            elif isinstance(val, dict) and "layer" in val:
                lyr = int(val["layer"])
                dt = int(val.get("datatype", -1))
                layer_map.setdefault(key, set()).add((lyr, dt))
    elif isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict) and "name" in entry and "layer" in entry:
                key = str(entry["name"]).lower()
                lyr = int(entry["layer"])
                dt = int(entry.get("datatype", -1))
                layer_map.setdefault(key, set()).add((lyr, dt))

    return layer_map


def load_layer_map(source: str | Path | dict | None) -> dict[str, set[tuple[int, int]]]:
    """Charge un mapping de couches depuis un fichier (.lyp, .json) ou un dictionnaire Python."""
    if source is None:
        return {}

    if isinstance(source, dict):
        normalized: dict[str, set[tuple[int, int]]] = {}
        for name, val in source.items():
            key = str(name).lower()
            if isinstance(val, int):
                normalized.setdefault(key, set()).add((val, -1))
            elif isinstance(val, (list, tuple)) and len(val) >= 2:
                normalized.setdefault(key, set()).add((int(val[0]), int(val[1])))
            elif isinstance(val, set):
                for item in val:
                    if isinstance(item, tuple) and len(item) == 2:
                        normalized.setdefault(key, set()).add((int(item[0]), int(item[1])))
                    elif isinstance(item, int):
                        normalized.setdefault(key, set()).add((item, -1))
            elif isinstance(val, (list, tuple)) and len(val) == 1:
                normalized.setdefault(key, set()).add((int(val[0]), -1))
        return normalized

    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"Fichier de correspondance de layer introuvable: {path}")

    suffix = path.suffix.lower()
    if suffix == ".lyp":
        return parse_lyp_file(path)
    elif suffix == ".json":
        return parse_json_layer_map(path)
    else:
        # Essayer comme fichier texte layermap (format Cadence/Calibre)
        return parse_text_layermap(path)


def parse_text_layermap(path: Path) -> dict[str, set[tuple[int, int]]]:
    """Parse un fichier texte de layermap (colonnes: nom [purpose] layer datatype)."""
    layer_map: dict[str, set[tuple[int, int]]] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            # Cas: name layer datatype
            if len(parts) >= 3 and parts[-2].isdigit() and parts[-1].isdigit():
                name = parts[0].lower()
                layer = int(parts[-2])
                dt = int(parts[-1])
                layer_map.setdefault(name, set()).add((layer, dt))
            elif len(parts) >= 2 and parts[-1].isdigit():
                name = parts[0].lower()
                layer = int(parts[-1])
                layer_map.setdefault(name, set()).add((layer, -1))
    return layer_map


def find_candidate_layer_maps(gds_path: Path) -> list[Path]:
    """Cherche automatiquement des fichiers .lyp ou .json pertinents dans les dossiers voisins."""
    candidates: list[Path] = []
    # Même dossier que le GDS
    parent = gds_path.resolve().parent
    candidates.extend(parent.glob("*.lyp"))
    candidates.extend(parent.glob("*.json"))

    # Dossier data/ du projet si présent
    data_dir = Path("data")
    if data_dir.is_dir():
        candidates.extend(data_dir.glob("*.lyp"))

    return [c for c in candidates if c.is_file()]


def resolve_requested_layers(
    layer_names: Iterable[str | int],
    layer_map: dict[str, set[tuple[int, int]]],
    available_in_gds: set[tuple[int, int]] | None = None,
) -> tuple[set[int], set[tuple[int, int]]]:
    """Résout une liste de noms ou numéros de couches en :
    - keep_layers: ensemble de numéros de layer (où tous les datatypes sont conservés)
    - keep_layer_datatypes: ensemble précis de (layer, datatype) à conserver

    Lève une ValueError détaillée si un nom n'est pas reconnu.
    """
    keep_layers: set[int] = set()
    keep_layer_datatypes: set[tuple[int, int]] = set()
    unresolved: list[str] = []

    for item in layer_names:
        if isinstance(item, int):
            keep_layers.add(item)
            continue

        raw = str(item).strip()
        if not raw:
            continue

        # 1. Vérifier si c'est une notation numérique directe: "49" ou "49/0" ou "49:0"
        num_match = _NUMERIC_LAYER_RE.fullmatch(raw)
        if num_match:
            layer_num = int(num_match.group(1))
            if num_match.group(2) is not None:
                dt_num = int(num_match.group(2))
                keep_layer_datatypes.add((layer_num, dt_num))
            else:
                keep_layers.add(layer_num)
            continue

        # 2. Chercher dans le mapping de layers
        key = raw.lower()
        if key in layer_map:
            pairs = layer_map[key]
            for lyr, dt in pairs:
                if dt < 0:
                    keep_layers.add(lyr)
                else:
                    keep_layer_datatypes.add((lyr, dt))
            continue

        # 3. Tentative de correspondance préfixe / sous-nom (ex: "metal1" dans "metal1.drawing")
        prefix_matches = {
            k: v for k, v in layer_map.items() if k.startswith(f"{key}.") or f".{key}." in k or k.endswith(f".{key}")
        }
        if prefix_matches:
            for pairs in prefix_matches.values():
                for lyr, dt in pairs:
                    if dt < 0:
                        keep_layers.add(lyr)
                    else:
                        keep_layer_datatypes.add((lyr, dt))
            continue

        unresolved.append(raw)

    if unresolved:
        available_names = sorted(layer_map.keys())
        msg = (
            f"Les noms de layers suivants n'ont pas pu être résolus : {', '.join(unresolved)}.\n"
        )
        if available_names:
            preview = available_names[:25]
            msg += f"Noms connus dans le layer map ({len(available_names)} au total) : {', '.join(preview)}"
            if len(available_names) > 25:
                msg += " ..."
        else:
            msg += (
                "Aucun layer map fourni ou le layer map est vide. "
                "Veuillez spécifier un fichier de correspondance avec --map ou fournir des numéros de layers (ex: 49 ou 49/0)."
            )
        raise ValueError(msg)

    return keep_layers, keep_layer_datatypes


def get_gds_layers(library: gdstk.Library) -> set[tuple[int, int]]:
    """Retourne l'ensemble des paires (layer, datatype/texttype) présentes dans la bibliothèque GDS."""
    layers: set[tuple[int, int]] = set()
    for cell in library.cells:
        for p in cell.polygons:
            layers.add((p.layer, p.datatype))
        for path in cell.paths:
            for lyr, dt in zip(path.layers, path.datatypes):
                layers.add((lyr, dt))
        for lbl in cell.labels:
            layers.add((lbl.layer, lbl.texttype))
    return layers


def filter_gds_layers(
    input_gds: str | Path,
    output_gds: str | Path,
    layer_names: list[str | int],
    layer_map: str | Path | dict | None = None,
    keep_empty_cells: bool = True,
    verbose: bool = False,
) -> Path:
    """Filtre un fichier GDSII pour ne conserver que les layers demandés.

    Args:
        input_gds: Chemin vers le fichier GDSII source.
        output_gds: Chemin du fichier GDSII généré.
        layer_names: Liste de noms ou numéros de layers à conserver (ex: ['metal1', 'poly'] ou [49, 46]).
        layer_map: Chemin vers un fichier .lyp, .json ou dictionnaire associant noms -> layers.
        keep_empty_cells: Conserver les cellules même si elles ne contiennent plus d'éléments
                          (recommandé pour préserver la hiérarchie).
        verbose: Afficher des logs détaillés.

    Returns:
        Path du fichier GDSII écrit.
    """
    input_path = Path(input_gds)
    if not input_path.is_file():
        raise FileNotFoundError(f"Fichier GDSII introuvable : {input_path}")

    output_path = Path(output_gds)

    # 1. Charger la bibliothèque GDS
    if verbose:
        print(f"Lecture du fichier GDSII : {input_path}")
    library = gdstk.read_gds(str(input_path))
    available_gds_layers = get_gds_layers(library)

    if verbose:
        print(f"Trouvé {len(library.cells)} cellules, {len(available_gds_layers)} couples (layer, datatype) dans le GDS.")

    # 2. Charger le mapping de layers
    loaded_map: dict[str, set[tuple[int, int]]] = {}
    if layer_map is not None:
        loaded_map = load_layer_map(layer_map)
    else:
        # Essayer de trouver automatiquement un .lyp si des noms textuels sont demandés
        has_non_numeric = any(
            isinstance(name, str) and not _NUMERIC_LAYER_RE.fullmatch(name.strip())
            for name in layer_names
        )
        if has_non_numeric:
            candidates = find_candidate_layer_maps(input_path)
            best_cand_map = None
            best_score = -1

            for cand in candidates:
                try:
                    cand_map = load_layer_map(cand)
                    # Compter combien de noms demandés sont trouvés
                    matched_names = sum(
                        1 for n in layer_names
                        if (
                            str(n).strip().lower() in cand_map
                            or any(k.startswith(f"{str(n).strip().lower()}.") for k in cand_map)
                        )
                    )
                    if matched_names == 0:
                        continue

                    # Vérifier l'intersection des layers avec ceux présents dans le GDS
                    mapped_layers = {
                        pair for n in layer_names
                        for pair in cand_map.get(str(n).strip().lower(), set())
                    }
                    gds_overlap = len(mapped_layers.intersection(available_gds_layers))

                    # Score priorise le nombre de noms résolus puis l'overlap réel
                    score = matched_names * 100 + gds_overlap
                    if score > best_score:
                        best_score = score
                        best_cand_map = cand_map
                        if verbose:
                            print(f"Candidat détecté : {cand} (score: {score})")
                except Exception:
                    continue

            if best_cand_map is not None:
                loaded_map = best_cand_map

    # 3. Résoudre les layers demandés
    keep_layers, keep_layer_datatypes = resolve_requested_layers(
        layer_names, loaded_map, available_gds_layers
    )

    if verbose:
        print(f"Layers complets à conserver : {sorted(keep_layers)}")
        if keep_layer_datatypes:
            print(f"Paires (layer, datatype) spécifiques à conserver : {sorted(keep_layer_datatypes)}")

    # 4. Filtrer cellule par cellule
    total_poly_before = 0
    total_poly_after = 0
    total_paths_before = 0
    total_paths_after = 0
    total_labels_before = 0
    total_labels_after = 0

    for cell in library.cells:
        # Compter avant
        poly_before = len(cell.polygons)
        paths_before = len(cell.paths)
        labels_before = len(cell.labels)
        total_poly_before += poly_before
        total_paths_before += paths_before
        total_labels_before += labels_before

        # Identifier les (layer, datatype) présents dans cette cellule
        cell_specs: set[tuple[int, int]] = set()
        for p in cell.polygons:
            cell_specs.add((p.layer, p.datatype))
        for path in cell.paths:
            for lyr, dt in zip(path.layers, path.datatypes):
                cell_specs.add((lyr, dt))
        for lbl in cell.labels:
            cell_specs.add((lbl.layer, lbl.texttype))

        # Déterminer les spécifications à conserver
        specs_to_keep = [
            (lyr, dt)
            for (lyr, dt) in cell_specs
            if lyr in keep_layers or (lyr, dt) in keep_layer_datatypes
        ]

        # Appliquer le filtre gdstk (modifie la cellule en place)
        cell.filter(specs_to_keep, remove=False, polygons=True, paths=True, labels=True)

        poly_after = len(cell.polygons)
        paths_after = len(cell.paths)
        labels_after = len(cell.labels)
        total_poly_after += poly_after
        total_paths_after += paths_after
        total_labels_after += labels_after

    # Si suppression des cellules complètement vides (sans références ni formes) demandée
    if not keep_empty_cells:
        # Ne supprimer que si ni forme ni référence
        cells_to_keep = [
            cell for cell in library.cells
            if len(cell.polygons) > 0 or len(cell.paths) > 0 or len(cell.labels) > 0 or len(cell.references) > 0
        ]
        # Recréer la bibliothèque avec les cellules restantes
        new_lib = gdstk.Library(name=library.name, unit=library.unit, precision=library.precision)
        for c in cells_to_keep:
            new_lib.add(c)
        library = new_lib

    # 5. Écrire le résultat
    output_path.parent.mkdir(parents=True, exist_ok=True)
    library.write_gds(str(output_path))

    if verbose or not sys.flags.interactive:
        print(f"GDS filtré écrit dans : {output_path}")
        print(
            f"Statistiques : "
            f"Polygones : {total_poly_before} -> {total_poly_after} | "
            f"Paths : {total_paths_before} -> {total_paths_after} | "
            f"Labels : {total_labels_before} -> {total_labels_after}"
        )

    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Filtre un fichier GDSII pour ne conserver que les layers demandés."
    )
    parser.add_argument("input", type=str, help="Fichier GDSII d'entrée (.gds)")
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=None,
        help="Fichier GDSII de sortie (défaut: <input>_filtered.gds)",
    )
    parser.add_argument(
        "-l",
        "--layers",
        nargs="+",
        required=False,
        default=[],
        help="Liste des noms ou numéros de layers à conserver (ex: -l metal1 poly OU -l 49 46)",
    )
    parser.add_argument(
        "-m",
        "--map",
        "--lyp",
        dest="layer_map",
        type=str,
        default=None,
        help="Fichier de correspondance des layers (.lyp ou .json)",
    )
    parser.add_argument(
        "--list-layers",
        action="store_true",
        help="Affiche les layers présents dans le GDS d'entrée et quitte.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Affichage détaillé",
    )
    parser.add_argument(
        "--remove-empty-cells",
        action="store_true",
        help="Supprime les cellules devenues totalement vides",
    )

    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Erreur : Fichier d'entrée introuvable : {input_path}", file=sys.stderr)
        sys.exit(1)

    # Mode affichage des layers
    if args.list_layers:
        library = gdstk.read_gds(str(input_path))
        layers_in_gds = sorted(get_gds_layers(library))
        loaded_map = load_layer_map(args.layer_map) if args.layer_map else {}

        # Inverser le map pour lookup layer -> noms
        layer_to_names: dict[tuple[int, int], list[str]] = {}
        for name, pairs in loaded_map.items():
            for p in pairs:
                layer_to_names.setdefault(p, []).append(name)

        print(f"Layers présents dans {input_path} ({len(layers_in_gds)} couples layer/datatype) :")
        for lyr, dt in layers_in_gds:
            names = layer_to_names.get((lyr, dt), [])
            if not names:
                names = layer_to_names.get((lyr, -1), [])
            names_str = f" -> {', '.join(names)}" if names else ""
            print(f"  Layer {lyr}, Datatype {dt}{names_str}")
        sys.exit(0)

    # Vérification des layers spécifiés
    if not args.layers:
        print("Erreur : Aucun layer spécifié. Utilisez -l / --layers ou --list-layers.", file=sys.stderr)
        sys.exit(1)

    # Aplatir les éventuelles séparations par virgules ("metal1,poly")
    expanded_layers: list[str] = []
    for item in args.layers:
        for part in item.split(","):
            part = part.strip()
            if part:
                expanded_layers.append(part)

    # Déterminer le fichier de sortie
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = input_path.with_stem(input_path.stem + "_filtered")

    try:
        filter_gds_layers(
            input_gds=input_path,
            output_gds=output_path,
            layer_names=expanded_layers,
            layer_map=args.layer_map,
            keep_empty_cells=not args.remove_empty_cells,
            verbose=args.verbose,
        )
    except Exception as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
