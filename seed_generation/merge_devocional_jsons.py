import json
import sys
import glob
import os

"""
merge_devocional_jsons.py

Usage:
    python merge_devocional_jsons.py <output_file.json> <input1.json> <input2.json> [...]
    OR to merge all in a folder:
    python merge_devocional_jsons.py <output_file.json> folder/*.json

This script merges multiple Devocional year JSON files into a single JSON file.
Assumes each input JSON is a dict or list (auto-detects and merges accordingly).
"""

def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def main():
    if len(sys.argv) < 3:
        print("Usage: python merge_devocional_jsons.py <output_file.json> <input1.json> <input2.json> [...]")
        sys.exit(1)
    output_path = sys.argv[1]
    input_files = sys.argv[2:]

    merged = None
    for path in input_files:
        data = load_json(path)
        if merged is None:
            merged = data
        else:
            # Merge lists or dicts
            if isinstance(merged, list) and isinstance(data, list):
                merged.extend(data)
            elif isinstance(merged, dict) and isinstance(data, dict):
                merged.update(data)
            else:
                print(f"Type mismatch: {path} is {type(data)}, expected {type(merged)}")
                sys.exit(1)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(f"Merged {len(input_files)} files into {output_path}")

if __name__ == "__main__":
    main()
