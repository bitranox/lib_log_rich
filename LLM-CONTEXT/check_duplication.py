#!/usr/bin/env python3.13
"""Check for code duplication across Python files."""
import sys
from pathlib import Path
from typing import List, Tuple
import hashlib

def get_code_blocks(filepath: Path, min_lines: int = 5) -> List[Tuple[int, List[str]]]:
    """Extract all code blocks of at least min_lines."""
    with open(filepath) as f:
        lines = [line.rstrip() for line in f.readlines()]
    
    blocks = []
    for start_idx in range(len(lines) - min_lines + 1):
        block = []
        for i in range(start_idx, min(start_idx + 15, len(lines))):  # Max 15 lines
            line = lines[i].strip()
            if line and not line.startswith('#') and not line.startswith('"""') and not line.startswith("'''"):
                block.append(line)
        
        if len(block) >= min_lines:
            blocks.append((start_idx + 1, block))
    
    return blocks

def find_duplicates(files: List[Path], min_lines: int = 5) -> dict:
    """Find duplicated code blocks across files."""
    block_map = {}  # hash -> [(file, line_no, block)]
    
    for filepath in files:
        blocks = get_code_blocks(filepath, min_lines)
        for line_no, block in blocks:
            # Create hash of normalized block
            block_hash = hashlib.md5('\n'.join(block).encode()).hexdigest()
            if block_hash not in block_map:
                block_map[block_hash] = []
            block_map[block_hash].append((filepath, line_no, block))
    
    # Find blocks that appear in multiple locations
    duplicates = {}
    for block_hash, occurrences in block_map.items():
        if len(occurrences) > 1:
            # Only report if same code appears in different files or far apart in same file
            unique_locations = set()
            for filepath, line_no, _ in occurrences:
                unique_locations.add((filepath, line_no // 50))  # Group by 50-line chunks
            
            if len(unique_locations) > 1:
                duplicates[block_hash] = occurrences
    
    return duplicates

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: check_duplication.py <file1.py> <file2.py> ...")
        sys.exit(1)
    
    files = [Path(f) for f in sys.argv[1:] if Path(f).exists()]
    duplicates = find_duplicates(files, min_lines=5)
    
    if not duplicates:
        print("No significant code duplication found.")
        sys.exit(0)
    
    print(f"Found {len(duplicates)} duplicated code blocks:\n")
    
    for idx, (block_hash, occurrences) in enumerate(duplicates.items(), 1):
        print(f"=== Duplication #{idx} ({len(occurrences)} occurrences) ===")
        _, _, block = occurrences[0]
        print(f"Code preview: {block[0][:60]}...")
        print(f"Block size: {len(block)} lines")
        print("Locations:")
        for filepath, line_no, _ in occurrences:
            print(f"  - {filepath}:{line_no}")
        print()
