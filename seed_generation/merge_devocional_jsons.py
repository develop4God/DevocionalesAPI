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

    def recursive_merge(a, b):
        """
        Recursively merge dict b into dict a (in-place), combining lists and nested dicts.
        """
        for k, v in b.items():
            if k in a:
                if isinstance(a[k], dict) and isinstance(v, dict):
                    recursive_merge(a[k], v)
                elif isinstance(a[k], list) and isinstance(v, list):
                    a[k].extend(v)
                else:
                    a[k] = v  # Overwrite if types differ
            else:
                a[k] = v
        return a

    merged = None
    for path in input_files:
        data = load_json(path)
        if merged is None:
            merged = data
        else:
            if isinstance(merged, dict) and isinstance(data, dict):
                merged = recursive_merge(merged, data)
            elif isinstance(merged, list) and isinstance(data, list):
                merged.extend(data)
            else:
                print(f"Type mismatch: {path} is {type(data)}, expected {type(merged)}")
                sys.exit(1)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(f"Merged {len(input_files)} files into {output_path}")

if __name__ == "__main__":
    main()
