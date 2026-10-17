#!/usr/bin/env python3
"""Script to read a GDS file and merge overlapping polygons layer by layer.

This script:
1. Reads an input GDS file
2. For each cell, flattens the hierarchy (optional)
3. For each layer, merges overlapping polygons using boolean union
4. Writes the result to a new GDS file with _flatten suffix

Usage:
    python flatten_gds.py input.gds [--output output.gds] [--flatten-hierarchy]
    python flatten_gds.py input.gds --cell CELL_NAME [--output output.gds] [--flatten-hierarchy]
"""

import argparse
import sys
from pathlib import Path
from collections import defaultdict

import gdstk


def merge_polygons(polygons: list[gdstk.Polygon], 
                   layer: int, 
                   datatype: int) -> list[gdstk.Polygon]:
    """Merge overlapping polygons using boolean union.
    
    This implementation uses a simple iterative approach with gdstk.boolean.
    For better performance with many polygons, consider using a polygon clipping library.
    
    Args:
        polygons: List of polygons to merge (must all be on same layer)
        layer: The layer number
        datatype: The datatype number
        
    Returns:
        List of merged polygons
    """
    if not polygons:
        return []
    
    if len(polygons) == 1:
        return [polygons[0]]
    
    # Use gdstk's built-in boolean operation
    # Start with the first polygon
    result = polygons[0]
    
    for polygon in polygons[1:]:
        # Perform union
        result = gdstk.boolean(result, polygon, 'or', layer=layer, datatype=datatype)
    
    # Handle different return types
    if result is None:
        return polygons  # Return original if union failed
    elif isinstance(result, list):
        return result
    elif isinstance(result, gdstk.Polygon):
        return [result]
    else:
        return [result]


def get_all_polygons_from_cell(cell: gdstk.Cell, 
                               flatten_hierarchy: bool = False,
                               visited: set | None = None) -> dict[tuple[int, int], list[gdstk.Polygon]]:
    """Extract all polygons from a cell, optionally flattening the hierarchy.
    
    Args:
        cell: The GDS cell to process
        flatten_hierarchy: If True, include polygons from all instances
        visited: Set of visited cell names to prevent infinite recursion
        
    Returns:
        Dictionary mapping (layer, datatype) to list of polygons
    """
    if visited is None:
        visited = set()
    
    # Prevent infinite recursion
    if cell.name in visited:
        return defaultdict(list)
    visited.add(cell.name)
    
    all_polygons = defaultdict(list)
    
    # Add polygons from this cell
    for polygon in cell.polygons:
        key = (polygon.layer, polygon.datatype)
        all_polygons[key].append(polygon)
    
    # If flattening hierarchy, process instances
    if flatten_hierarchy:
        for reference in cell.references:
            ref_cell = reference.cell
            if ref_cell.name in visited:
                continue
            
            # Get polygons from the referenced cell
            ref_polygons = get_all_polygons_from_cell(
                ref_cell, flatten_hierarchy, visited
            )
            
            # Transform the polygons
            for (layer, datatype), polygons in ref_polygons.items():
                for polygon in polygons:
                    transformed = polygon.copy()
                    
                    # Apply transformations in correct order
                    # Scale first, then rotation, then mirror, then translate
                    if reference.magnification != 1:
                        transformed.scale(reference.magnification, reference.origin)
                    if reference.rotation != 0:
                        transformed.rotate(reference.rotation, reference.origin)
                    if reference.x_reflection:
                        transformed.mirror(reference.origin, 'x')
                    transformed.translate(*reference.origin)
                    
                    all_polygons[(layer, datatype)].append(transformed)
    
    return all_polygons


def process_cell(cell: gdstk.Cell, 
                 flatten_hierarchy: bool = False,
                 verbose: bool = False) -> gdstk.Cell:
    """Process a cell to merge overlapping polygons layer by layer.
    
    Args:
        cell: The GDS cell to process
        flatten_hierarchy: If True, flatten the hierarchy
        verbose: If True, print detailed information
        
    Returns:
        A new cell with merged polygons
    """
    # Create a new cell for the result
    new_cell = gdstk.Cell(cell.name)
    
    # Get all polygons from this cell
    all_polygons = get_all_polygons_from_cell(cell, flatten_hierarchy)
    
    # If not flattening, add references
    if not flatten_hierarchy:
        for reference in cell.references:
            new_cell.add(gdstk.Reference(
                reference.cell,
                origin=reference.origin,
                rotation=reference.rotation,
                x_reflection=reference.x_reflection,
                magnification=reference.magnification
            ))
    
    # Merge and add polygons for each layer/datatype
    for (layer, datatype), polygons in all_polygons.items():
        if len(polygons) > 0:
            if verbose:
                print(f"  Processing layer {layer}, datatype {datatype}: {len(polygons)} polygons")
            
            merged = merge_polygons(polygons, layer, datatype)
            
            if verbose:
                print(f"    -> Merged to {len(merged)} polygons")
            
            for merged_poly in merged:
                new_cell.add(merged_poly)
    
    return new_cell


def flatten_and_merge_gds(input_path: Path, 
                          output_path: Path,
                          cell_name: str | None = None,
                          flatten_hierarchy: bool = False,
                          verbose: bool = False) -> None:
    """Read a GDS file, merge overlapping polygons layer by layer, and write the result.
    
    Args:
        input_path: Path to the input GDS file
        output_path: Path to the output GDS file
        cell_name: Optional specific cell name to process (if None, process all)
        flatten_hierarchy: If True, flatten the hierarchy
        verbose: If True, print detailed information
    """
    print(f"Reading GDS file: {input_path}")
    
    # Read the input library
    library = gdstk.read_gds(str(input_path))
    
    print(f"Found {len(library.cells)} cells in library")
    
    # Create a new library for the output
    new_library = gdstk.Library(unit=library.unit, precision=library.precision)
    
    total_input_polygons = 0
    total_output_polygons = 0
    
    # Process each cell
    for gdstk_cell in library.cells:
        if cell_name is not None and gdstk_cell.name != cell_name:
            continue
        
        print(f"\nProcessing cell: {gdstk_cell.name}")
        
        # Count input polygons
        input_count = len(gdstk_cell.polygons)
        total_input_polygons += input_count
        print(f"  Input: {input_count} polygons")
        
        # Process the cell
        processed_cell = process_cell(gdstk_cell, flatten_hierarchy, verbose)
        
        # Count output polygons
        output_count = len(processed_cell.polygons)
        total_output_polygons += output_count
        print(f"  Output: {output_count} polygons")
        
        # Add to the new library
        new_library.add(processed_cell)
    
    # Write the output
    print(f"\nWriting output to: {output_path}")
    new_library.write_gds(str(output_path))
    print(f"Done! Output written to {output_path}")
    print(f"\nSummary: {total_input_polygons} -> {total_output_polygons} polygons ({total_input_polygons - total_output_polygons} merged)")


def main():
    parser = argparse.ArgumentParser(
        description='Flatten GDS file by merging overlapping polygons layer by layer'
    )
    parser.add_argument('input', type=str, help='Input GDS file path')
    parser.add_argument('--output', type=str, default=None, 
                       help='Output GDS file path (default: input_flatten.gds)')
    parser.add_argument('--cell', type=str, default=None,
                       help='Specific cell name to process (default: process all cells)')
    parser.add_argument('--flatten-hierarchy', action='store_true',
                       help='Flatten the hierarchy (copy all instances into parent cells)')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Verbose output')
    
    args = parser.parse_args()
    
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)
    
    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        # Add _flatten suffix before the extension
        output_path = input_path.with_stem(input_path.stem + '_flatten')
    
    # Process the GDS file
    try:
        flatten_and_merge_gds(
            input_path=input_path,
            output_path=output_path,
            cell_name=args.cell,
            flatten_hierarchy=args.flatten_hierarchy,
            verbose=args.verbose
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
