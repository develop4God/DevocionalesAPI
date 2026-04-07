import json
import tkinter as tk
from tkinter import filedialog, ttk, messagebox
from pathlib import Path
import os
import glob

class MergeApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Devocional JSON Merger")
        self.resizable(True, True)
        self.minsize(700, 400)
        self._build()

    def _build(self):
        pad = {"padx": 10, "pady": 6}
        ff = ttk.LabelFrame(self, text="Input JSON Files", padding=8)
        ff.pack(fill="x", **pad)
        self.files_var = tk.StringVar()
        ttk.Entry(ff, textvariable=self.files_var, width=60).pack(side="left", fill="x", expand=True)
        ttk.Button(ff, text="Browse…", command=self._browse_files).pack(side="left", padx=(6,0))

        of = ttk.LabelFrame(self, text="Output File", padding=8)
        of.pack(fill="x", **pad)
        self.output_var = tk.StringVar()
        ttk.Entry(of, textvariable=self.output_var, width=60).pack(side="left", fill="x", expand=True)
        ttk.Button(of, text="Browse…", command=self._browse_output).pack(side="left", padx=(6,0))

        bf = tk.Frame(self); bf.pack(pady=(4,0))
        ttk.Button(bf, text="▶  Merge",    command=self._run).pack(side="left", padx=4)
        ttk.Button(bf, text="🗑  Clear", command=self._clear).pack(side="left", padx=4)

        tf = ttk.LabelFrame(self, text="Merge Results", padding=8)
        tf.pack(fill="both", expand=True, **pad)
        self.output = tk.Text(tf, wrap="word", font=("Courier", 10), state="disabled", height=10)
        self.output.pack(fill="both", expand=True)

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(self, textvariable=self.status_var, relief="sunken", anchor="w").pack(
            fill="x", side="bottom", padx=10, pady=(0,6))

    def _browse_files(self):
        files = filedialog.askopenfilenames(title="Select JSON files to merge",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if files:
            self.files_var.set(";".join(files))

    def _browse_output(self):
        f = filedialog.asksaveasfilename(title="Select output file",
            defaultextension=".json", filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if f:
            self.output_var.set(f)

    def _run(self):
        files = [f for f in self.files_var.get().split(";") if f.strip()]
        output_path = self.output_var.get().strip()
        if not files or not output_path:
            self.status_var.set("⚠️  Please select input and output files"); return
        self.status_var.set("Merging…"); self.update()
        try:
            def recursive_merge(a, b):
                for k, v in b.items():
                    if k in a:
                        if isinstance(a[k], dict) and isinstance(v, dict):
                            recursive_merge(a[k], v)
                        elif isinstance(a[k], list) and isinstance(v, list):
                            a[k].extend(v)
                        else:
                            a[k] = v
                    else:
                        a[k] = v
                return a

            merged = None
            for path in files:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                if merged is None:
                    merged = data
                else:
                    if isinstance(merged, dict) and isinstance(data, dict):
                        merged = recursive_merge(merged, data)
                    elif isinstance(merged, list) and isinstance(data, list):
                        merged.extend(data)
                    else:
                        raise Exception(f"Type mismatch: {path} is {type(data)}, expected {type(merged)}")
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)
            self._set_output(f"Merged {len(files)} files into {output_path}\n")
            self.status_var.set("✅ Merge complete")
        except Exception as e:
            self._set_output(f"❌ Error: {e}\n")
            self.status_var.set("❌ Merge failed")
            messagebox.showerror("Merge Error", str(e))

    def _set_output(self, text):
        self.output.config(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("end", text)
        self.output.config(state="disabled")

    def _clear(self):
        self.files_var.set("")
        self.output_var.set("")
        self._set_output("")
        self.status_var.set("Ready")

if __name__ == "__main__":
    app = MergeApp()
    app.mainloop()
